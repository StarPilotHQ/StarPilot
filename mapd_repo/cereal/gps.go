package cereal

import (
	"encoding/json"
	"log/slog"
	"math"
	"os"
	"regexp"

	"capnproto.org/go/capnp/v3"
	"pfeifer.dev/mapd/cereal/custom"

	"pfeifer.dev/mapd/cereal/log"
	ms "pfeifer.dev/mapd/settings"
)

const (
	externalFixMaxAge     = 500_000_000    // 10 Hz source: 500 ms
	internalFixMaxAge     = 2_000_000_000  // 1 Hz source: 2 s
	carFixMaxAge          = 2_500_000_000  // Optional CAN observation: 2.5 s
	phoneFixMaxAge        = 10_000_000_000 // Companion app in the background sends sparsely: 10 s
	gpsModeRefresh        = 1_000_000_000  // Galaxy navigation setting reread interval
	maxHorizontalAccuracy = 50.0           // input sanity ceiling, not road-match qualification
	maxClockPairSkew      = 1_000_000      // an interrupted pair cannot date a source event
	maxClockOffsetDrift   = 1_000_000      // a larger BOOTTIME/MONOTONIC step fences queued fixes
)

type clockSample struct {
	monoBefore uint64
	boot       uint64
	monoAfter  uint64
}

func pairedClockSample() clockSample {
	before := GetMonotonicTime()
	boot := GetTime()
	return clockSample{before, boot, GetMonotonicTime()}
}

type GpsSource uint8

const (
	GpsSourceNone GpsSource = iota
	GpsSourceInternal
	GpsSourceExternal
	GpsSourceCar
	GpsSourcePhone
)

// Galaxy navigation's GPS source setting: backup uses the phone only without
// comma or car GPS, comma never uses it, phone prefers it whenever it has a fix.
const (
	GpsModeBackup = "backup"
	GpsModeComma  = "comma"
	GpsModePhone  = "phone"
)

var storagePrefix = regexp.MustCompile(`^[A-Za-z0-9_-]{1,64}$`)

func navigationSettingsPath() string {
	prefix := os.Getenv("OPENPILOT_PREFIX")
	if prefix == "" {
		return "/data/starpilot/navigation/settings.json"
	}
	if !storagePrefix.MatchString(prefix) {
		return ""
	}
	return "/data/starpilot-" + prefix + "/navigation/settings.json"
}

// ReadGpsMode returns the saved choice, or backup when it is missing or invalid.
func ReadGpsMode() string {
	path := navigationSettingsPath()
	if path == "" {
		return GpsModeBackup
	}
	raw, err := os.ReadFile(path)
	if err != nil || len(raw) > 1<<20 {
		return GpsModeBackup
	}
	var settings struct {
		GpsSource string `json:"gpsSource"`
	}
	if json.Unmarshal(raw, &settings) != nil {
		return GpsModeBackup
	}
	switch settings.GpsSource {
	case GpsModeComma, GpsModePhone:
		return settings.GpsSource
	}
	return GpsModeBackup
}

type GpsSample struct {
	Location         log.GpsLocationData
	Source           GpsSource
	FixMonoTime      uint64
	SourceGeneration uint64
	SourceChanged    bool
	NewFix           bool
}

type gpsCandidate struct {
	location         log.GpsLocationData
	fixMonoTime      uint64
	lastSeenMonoTime uint64
}

type GpsSub struct {
	gpsLocation         Subscriber[log.GpsLocationData]
	gpsLocationExternal Subscriber[log.GpsLocationData]
	carGps              *Subscriber[custom.StarPilotCarState_Gps]
	phoneGps            *Subscriber[custom.StarPilotCarState_Gps]
	car                 gpsCandidate
	phone               gpsCandidate
	mode                func() string
	modeValue           string
	modeReadAt          uint64
	internal            gpsCandidate
	external            gpsCandidate
	selected            GpsSource
	generation          uint64
	clock               func() clockSample
	clockInitialized    bool
	offsetLow           int64
	offsetHigh          int64
	barrierMono         uint64
}

// GpsFixFreshAt is shared by source selection and the final producer output
// gate. Both timestamps use the host's boot-time clock domain.
func GpsFixFreshAt(source GpsSource, fixMonoTime, now uint64) bool {
	if fixMonoTime == 0 || fixMonoTime > now {
		return false
	}
	switch source {
	case GpsSourceExternal:
		return now-fixMonoTime <= externalFixMaxAge
	case GpsSourceCar:
		return now-fixMonoTime <= carFixMaxAge
	case GpsSourcePhone:
		return now-fixMonoTime <= phoneFixMaxAge
	case GpsSourceInternal:
		return now-fixMonoTime <= internalFixMaxAge
	default:
		return false
	}
}

func validLocation(location log.GpsLocationData) bool {
	lat, lon := location.Latitude(), location.Longitude()
	accuracy := float64(location.HorizontalAccuracy())
	bearing := float64(location.BearingDeg())
	return location.HasFix() &&
		!math.IsNaN(lat) && !math.IsInf(lat, 0) && lat >= -90 && lat <= 90 &&
		!math.IsNaN(lon) && !math.IsInf(lon, 0) && lon >= -180 && lon <= 180 &&
		!math.IsNaN(bearing) && !math.IsInf(bearing, 0) && bearing >= 0 && bearing < 360 &&
		!math.IsNaN(accuracy) && !math.IsInf(accuracy, 0) && accuracy > 0 && accuracy <= maxHorizontalAccuracy
}

func (c *gpsCandidate) update(event DecodedEvent[log.GpsLocationData], clock clockSample, barrierMono uint64) bool {
	return c.updateWithAge(event, clock, barrierMono, internalFixMaxAge)
}

func (c *gpsCandidate) updateWithAge(event DecodedEvent[log.GpsLocationData], clock clockSample, barrierMono, maxAge uint64) bool {
	time := event.LogMonoTime // Python GPS Event.logMonoTime is CLOCK_MONOTONIC.
	// A duplicated, out-of-order, future, or already expired event cannot
	// renew the age of a cached fix or invalidate a newer one.
	if time == 0 || time <= barrierMono || time <= c.lastSeenMonoTime ||
		time > clock.monoAfter || clock.monoAfter-time > maxAge {
		return false
	}
	c.lastSeenMonoTime = time
	if !event.Valid || !validLocation(event.Value) {
		c.location = log.GpsLocationData{}
		c.fixMonoTime = 0
		return false
	}
	// Convert only a post-start/resume source event using the stable current
	// clock epoch. A pre-resume event cannot be re-dated with this offset.
	delta := clock.monoAfter - time
	if delta >= clock.boot {
		return false
	}
	c.location = event.Value
	c.fixMonoTime = clock.boot - delta
	return true
}

func (s *GpsSub) clearCandidates() {
	s.internal = gpsCandidate{}
	s.external = gpsCandidate{}
	s.car = gpsCandidate{}
	s.phone = gpsCandidate{}
}

func (s *GpsSub) gpsMode(now uint64) string {
	if s.modeValue == "" || now < s.modeReadAt || now-s.modeReadAt >= gpsModeRefresh {
		read := s.mode
		if read == nil {
			read = ReadGpsMode
		}
		s.modeValue, s.modeReadAt = read(), now
	}
	return s.modeValue
}

// updateWrapped admits a StarPilotCarState.Gps-shaped fix dated by its own source time.
func (c *gpsCandidate) updateWrapped(event DecodedEvent[custom.StarPilotCarState_Gps], sensor log.GpsLocationData_SensorSource,
	clock clockSample, barrierMono, maxAge uint64) bool {
	sourceTime := event.Value.SourceMonoTime()
	if sourceTime == 0 || sourceTime > event.LogMonoTime || event.LogMonoTime > clock.monoAfter {
		return false
	}
	location, err := carGpsLocation(event.Value)
	if err != nil {
		return false
	}
	location.SetSource(sensor)
	valid := event.Valid && !math.IsNaN(float64(location.Speed())) && !math.IsInf(float64(location.Speed()), 0) && location.Speed() >= 0 &&
		!math.IsNaN(location.Altitude()) && !math.IsInf(location.Altitude(), 0)
	return c.updateWithAge(DecodedEvent[log.GpsLocationData]{Value: location, Valid: valid, LogMonoTime: sourceTime}, clock, barrierMono, maxAge)
}

func (s *GpsSub) clockReady(clock clockSample) bool {
	if clock.monoAfter < clock.monoBefore || clock.boot < clock.monoBefore ||
		clock.monoAfter-clock.monoBefore > maxClockPairSkew || clock.boot > math.MaxInt64 || clock.monoAfter > math.MaxInt64 {
		s.clearCandidates()
		s.barrierMono = clock.monoAfter
		s.clockInitialized = false
		return false
	}
	low := int64(clock.boot) - int64(clock.monoAfter)
	high := int64(clock.boot) - int64(clock.monoBefore)
	if !s.clockInitialized || low > s.offsetHigh+maxClockOffsetDrift || high < s.offsetLow-maxClockOffsetDrift {
		// Startup and suspend both require a newly stamped Event. A queued
		// pre-resume Event may appear fresh in MONOTONIC time alone.
		s.clearCandidates()
		s.barrierMono = clock.monoAfter
		s.offsetLow, s.offsetHigh = low, high
		s.clockInitialized = true
	}
	return true
}

// ReadSample retains a recently received fix across 20 Hz map loops. Its
// FixMonoTime is the original GPS Event observation converted to BOOTTIME,
// never the current loop or reuse time.
func (s *GpsSub) ReadSample() (sample GpsSample, success bool) {
	readClock := pairedClockSample
	if s.clock != nil {
		readClock = s.clock
	}
	clock := readClock()
	// A scheduler interruption between three clock reads is not evidence of
	// GPS loss. Retry the measurement at most twice before failing closed.
	for attempt := 0; attempt < 2 && clock.monoAfter >= clock.monoBefore &&
		clock.monoAfter-clock.monoBefore > maxClockPairSkew; attempt++ {
		clock = readClock()
	}
	ready := s.clockReady(clock)
	internalNew, externalNew, carNew, phoneNew := false, false, false, false
	if event, ok := s.gpsLocation.ReadEvent(); ok && ready {
		internalNew = s.internal.update(event, clock, s.barrierMono)
	}
	if event, ok := s.gpsLocationExternal.ReadEvent(); ok && ready {
		externalNew = s.external.update(event, clock, s.barrierMono)
	}

	if s.carGps != nil {
		if event, ok := s.carGps.ReadEvent(); ok && ready {
			carNew = s.car.updateWrapped(event, log.GpsLocationData_SensorSource_car, clock, s.barrierMono, carFixMaxAge)
		}
	}
	if s.phoneGps != nil {
		if event, ok := s.phoneGps.ReadEvent(); ok && ready {
			// The enum has no phone value; android is the closest.
			phoneNew = s.phone.updateWrapped(event, log.GpsLocationData_SensorSource_android, clock, s.barrierMono, phoneFixMaxAge)
		}
	}

	mode := s.gpsMode(clock.boot)
	phoneFresh := ready && mode != GpsModeComma && GpsFixFreshAt(GpsSourcePhone, s.phone.fixMonoTime, clock.boot)
	source := GpsSourceNone
	if phoneFresh && mode == GpsModePhone {
		source = GpsSourcePhone
	} else if ready && GpsFixFreshAt(GpsSourceExternal, s.external.fixMonoTime, clock.boot) {
		source = GpsSourceExternal
	} else if ready && GpsFixFreshAt(GpsSourceInternal, s.internal.fixMonoTime, clock.boot) {
		source = GpsSourceInternal
	} else if ready && GpsFixFreshAt(GpsSourceCar, s.car.fixMonoTime, clock.boot) {
		source = GpsSourceCar
	} else if phoneFresh {
		source = GpsSourcePhone
	}
	sample.SourceChanged = source != s.selected
	if sample.SourceChanged {
		s.generation++
		s.selected = source
		slog.Info("GPS source changed", "source", source, "generation", s.generation)
	}
	sample.Source = source
	sample.SourceGeneration = s.generation
	switch source {
	case GpsSourceExternal:
		sample.Location = s.external.location
		sample.FixMonoTime = s.external.fixMonoTime
		sample.NewFix = externalNew
	case GpsSourceInternal:
		sample.Location = s.internal.location
		sample.FixMonoTime = s.internal.fixMonoTime
		sample.NewFix = internalNew
	case GpsSourceCar:
		sample.Location = s.car.location
		sample.FixMonoTime = s.car.fixMonoTime
		sample.NewFix = carNew
	case GpsSourcePhone:
		sample.Location = s.phone.location
		sample.FixMonoTime = s.phone.fixMonoTime
		sample.NewFix = phoneNew
	default:
		return sample, false
	}
	return sample, true
}

// Read preserves the existing location-only call site until the main loop
// adopts source-generation and sample-freshness handling.
func (s *GpsSub) Read() (locationData log.GpsLocationData, success bool) {
	sample, success := s.ReadSample()
	return sample.Location, success
}

func (s *GpsSub) Close() {
	s.gpsLocation.Sub.Msgq.Close()
	s.gpsLocationExternal.Sub.Msgq.Close()
	if s.carGps != nil {
		s.carGps.Sub.Msgq.Close()
	}
	if s.phoneGps != nil {
		s.phoneGps.Sub.Msgq.Close()
	}
}

func GetGpsSub() (gpsSub GpsSub) {
	return GpsSub{
		carGps:              optionalWrappedGpsSubscriber("starpilotCarState", CarGpsReader),
		phoneGps:            optionalWrappedGpsSubscriber("starpilotNavigation", PhoneGpsReader),
		gpsLocation:         NewSubscriber("gpsLocation", GpsLocationReader, true, ms.Settings.SubscriberSettings.ShadowGpsLocation),
		gpsLocationExternal: NewSubscriber("gpsLocationExternal", GpsLocationExternalReader, true, ms.Settings.SubscriberSettings.ShadowGpsLocationExternal),
	}
}

func optionalWrappedGpsSubscriber(service string, reader func(log.Event) (custom.StarPilotCarState_Gps, error)) (subscriber *Subscriber[custom.StarPilotCarState_Gps]) {
	defer func() {
		if recover() != nil {
			subscriber = nil
			slog.Warn("Optional GPS subscriber unavailable", "service", service)
		}
	}()
	value := NewSubscriber(service, reader, true, false)
	return &value
}

func carGpsLocation(source custom.StarPilotCarState_Gps) (log.GpsLocationData, error) {
	_, segment, err := capnp.NewMessage(capnp.SingleSegment(nil))
	if err != nil {
		return log.GpsLocationData{}, err
	}
	location, err := log.NewRootGpsLocationData(segment)
	if err != nil {
		return location, err
	}
	location.SetLatitude(source.Latitude())
	location.SetLongitude(source.Longitude())
	location.SetAltitude(source.Altitude())
	location.SetSpeed(source.Speed())
	location.SetBearingDeg(source.BearingDeg())
	location.SetHorizontalAccuracy(source.HorizontalAccuracy())
	location.SetVerticalAccuracy(source.VerticalAccuracy())
	location.SetBearingAccuracyDeg(source.BearingAccuracyDeg())
	location.SetSpeedAccuracy(source.SpeedAccuracy())
	location.SetUnixTimestampMillis(source.UnixTimestampMillis())
	location.SetHasFix(source.HasFix())
	location.SetSource(log.GpsLocationData_SensorSource_car)
	velocity, err := source.VNED()
	if err != nil {
		return location, err
	}
	if err := location.SetVNED(velocity); err != nil {
		return location, err
	}
	return location, nil
}
