package main

import (
	"crypto/rand"
	"encoding/binary"
	"errors"
	"math"
	"time"

	"capnproto.org/go/capnp/v3"
	"pfeifer.dev/mapd/cereal"
	"pfeifer.dev/mapd/cereal/car"
	"pfeifer.dev/mapd/cereal/custom"
	"pfeifer.dev/mapd/cereal/log"
	"pfeifer.dev/mapd/maps"
	m "pfeifer.dev/mapd/math"
	ms "pfeifer.dev/mapd/settings"
)

const mapLoadRetryDelay = time.Second

type MapLoader func(m.Position) (maps.Offline, error)

type State struct {
	Publisher                 *cereal.Publisher[custom.MapdOut]
	ShadowOnly                bool
	SnapshotID                string
	Data                      maps.Offline
	Car                       CarState
	CurrentWay                CurrentWay
	SpeedLimit                SpeedLimitState
	NextWays                  []maps.NextWayResult
	Position                  m.Position
	Curvatures                []m.Curvature
	TargetVelocities          []Velocity
	DistanceSinceLastPosition float32
	VisionCurveSpeed          float32
	MapCurveSpeed             float32
	VisionCurveMA             m.MovingAverage
	NextAdvisorySpeed         Upcoming[float32]
	NextHazard                Upcoming[string]
	RoadStatus                custom.MapdOut_SampleStatus
	GpsSource                 custom.MapdOut_GpsSource
	GpsMonoTime               uint64
	ComputedMonoTime          uint64
	SourceGeneration          uint64
	ProducerSession           uint64
	MatchedLimitMps           float32
	LimitPreview              LimitPreview
	lastMapLoadAttempt        time.Time
	bootNow                   func() uint64
	// The fix the current road result was computed from. The loop runs at
	// 20 Hz and GPS arrives at 1-10 Hz; a repeated fix has the same answer.
	roadFixMonoTime   uint64
	roadFixGeneration uint64
}

func (s *State) Init() {
	s.Car.Init()
	s.VisionCurveMA.Init(20)
	s.NextHazard = NewUpcoming(10, "", checkWayForHazardChange)
	s.NextAdvisorySpeed = NewUpcoming(10, 0, checkWayForAdvisorySpeedChange)
	s.SpeedLimit.Init()
	s.RoadStatus = custom.MapdOut_SampleStatus_noGps
	for s.ProducerSession == 0 {
		var bytes [8]byte
		if _, err := rand.Read(bytes[:]); err != nil {
			panic(err)
		}
		s.ProducerSession = binary.LittleEndian.Uint64(bytes[:])
	}
}

func (s *State) RoadMatched() bool {
	return s.RoadStatus == custom.MapdOut_SampleStatus_matchedNoLimit ||
		s.RoadStatus == custom.MapdOut_SampleStatus_matchedLimit
}

func (s *State) clearSpeedLimitEvidence() {
	s.SpeedLimit = SpeedLimitState{}
	s.SpeedLimit.Init()
	if !s.ShadowOnly {
		ms.Settings.ResetSpeedLimitAccepted()
	}
}

func (s *State) clearRoadDerived() {
	s.CurrentWay = CurrentWay{}
	s.NextWays = nil
	s.Curvatures = nil
	s.TargetVelocities = nil
	s.MapCurveSpeed = 0
	s.MatchedLimitMps = 0
	s.LimitPreview = LimitPreview{}
	s.NextHazard.Reset()
	s.NextAdvisorySpeed.Reset()
	s.clearSpeedLimitEvidence()
}

func (s *State) ResetRoadEvidence() {
	s.clearRoadDerived()
	s.roadFixMonoTime = 0
	s.Data = maps.Offline{}
	s.Position = m.Position{}
	s.DistanceSinceLastPosition = 0
	s.lastMapLoadAttempt = time.Time{}
}

// expireRoadEvidence drops everything derived from an expired fix but keeps
// the verified tile. A dense tile can take longer to load than the fix TTL;
// discarding it here made every following fix reload it, so the loop never
// published a match (~600 ms per load, ~60 % CPU, in Las Vegas 2026-10-15).
// Tile currency is still checked every loop by invalidateChangedTile.
func (s *State) expireRoadEvidence() {
	s.clearRoadDerived()
	s.roadFixMonoTime = 0
	s.Position = m.Position{}
	s.DistanceSinceLastPosition = 0
}

func (s *State) invalidateChangedTile() bool {
	if s.Data.Loaded && !s.Data.SourceCurrentFor(s.SnapshotID) {
		s.ResetRoadEvidence()
		s.RoadStatus = custom.MapdOut_SampleStatus_noCoverage
		return true
	}
	return false
}

func (s *State) bootTime() uint64 {
	if s.bootNow != nil {
		return s.bootNow()
	}
	return cereal.GetTime()
}

// ExpireGpsEvidence gates output using the same source-specific TTL as the
// GPS selector. Source generation and original fix time remain unchanged
// until the selector reports a transition.
func (s *State) ExpireGpsEvidence() {
	s.expireGpsEvidenceAt(s.bootTime())
}

func (s *State) expireGpsEvidenceAt(now uint64) {
	if !cereal.GpsFixFreshAt(cereal.GpsSource(s.GpsSource), s.GpsMonoTime, now) && s.RoadStatus != custom.MapdOut_SampleStatus_noGps {
		s.expireRoadEvidence()
		s.RoadStatus = custom.MapdOut_SampleStatus_noGps
	}
}

// ProcessGps updates road evidence before any output is serialized. A cached
// fix may be used across map loops, but its original timestamp is preserved.
func (s *State) ProcessGps(sample cereal.GpsSample, success bool, load MapLoader, now time.Time) {
	defer func() {
		s.ComputedMonoTime = s.bootTime()
		s.expireGpsEvidenceAt(s.ComputedMonoTime)
	}()
	s.GpsMonoTime = sample.FixMonoTime
	s.SourceGeneration = sample.SourceGeneration
	s.GpsSource = custom.MapdOut_GpsSource(sample.Source)
	if sample.SourceChanged {
		s.ResetRoadEvidence()
	}
	if !success {
		s.ResetRoadEvidence()
		s.GpsSource = custom.MapdOut_GpsSource_none
		s.GpsMonoTime = 0
		s.RoadStatus = custom.MapdOut_SampleStatus_noGps
		return
	}
	// A replaced cached tile cannot lend its old road or accepted limit to
	// either a new fix or another loop using the same fix.
	s.invalidateChangedTile()
	if sample.NewFix || sample.SourceChanged {
		s.DistanceSinceLastPosition = 0
	}
	location := sample.Location
	pos := m.PosFromLocation(location)
	s.Position = pos
	// Unknown CAN direction or speed uncertainty cannot create confident road evidence.
	if (sample.Source == cereal.GpsSourceCar || sample.Source == cereal.GpsSourcePhone) && (math.IsNaN(float64(location.BearingAccuracyDeg())) || math.IsInf(float64(location.BearingAccuracyDeg()), 0) || location.BearingAccuracyDeg() <= 0 || location.BearingAccuracyDeg() >= 180 || math.IsNaN(float64(location.SpeedAccuracy())) || math.IsInf(float64(location.SpeedAccuracy()), 0) || location.SpeedAccuracy() >= 100) {
		s.clearRoadDerived()
		s.roadFixMonoTime = 0
		s.RoadStatus = custom.MapdOut_SampleStatus_noMatch
		return
	}
	if !sample.NewFix && !sample.SourceChanged && s.Data.Loaded && s.roadFixMonoTime != 0 &&
		s.roadFixMonoTime == sample.FixMonoTime && s.roadFixGeneration == sample.SourceGeneration {
		// Same fix, same verified tile: matching again would scan the tile for
		// an identical answer. Freshness still expires in the deferred gate.
		return
	}
	s.roadFixMonoTime = 0
	box := s.Data.Box()
	if !s.Data.Loaded || !box.PosInside(pos) {
		// Retry missing coverage at most once a second while a fix remains in
		// the same region. A changed source resets the retry timer above.
		if s.Data.Loaded || s.lastMapLoadAttempt.IsZero() || now.Sub(s.lastMapLoadAttempt) >= mapLoadRetryDelay {
			loaded, err := load(pos)
			if err != nil {
				s.Data = maps.Offline{}
			} else {
				s.Data = loaded
			}
			s.lastMapLoadAttempt = now
		}
	}
	box = s.Data.Box()
	if !s.Data.Loaded || !box.PosInside(pos) {
		s.clearRoadDerived()
		s.RoadStatus = custom.MapdOut_SampleStatus_noCoverage
		return
	}

	s.roadFixMonoTime, s.roadFixGeneration = sample.FixMonoTime, sample.SourceGeneration
	way, err := GetCurrentWay(s.CurrentWay, s.NextWays, &s.Data, location)
	if err != nil || way.Way.Nodes.Len() < 2 {
		s.clearRoadDerived()
		if errors.Is(err, ErrAmbiguousWay) {
			s.RoadStatus = custom.MapdOut_SampleStatus_unknown
		} else {
			s.RoadStatus = custom.MapdOut_SampleStatus_noMatch
		}
		return
	}
	s.CurrentWay = way
	if !s.ShadowOnly {
		s.NextWays, err = NextWays(location, way, &s.Data, way.OnWay.IsForward)
		if err != nil {
			s.NextWays = nil
		}
		s.Curvatures, err = GetStateCurvatures(s)
		if err != nil {
			s.Curvatures = nil
			s.TargetVelocities = nil
			s.MapCurveSpeed = 0
		} else {
			s.TargetVelocities = GetTargetVelocities(s.Curvatures, s.TargetVelocities)
		}
	} else {
		s.NextWays = nil
		s.Curvatures = nil
		s.TargetVelocities = nil
		s.MapCurveSpeed = 0
	}
	limit := way.EffectiveMaxSpeed()
	if math.IsNaN(limit) || math.IsInf(limit, 0) || limit < 0 || limit > math.MaxFloat32 {
		s.clearRoadDerived()
		s.RoadStatus = custom.MapdOut_SampleStatus_noMatch
		return
	}
	if limit == 0 {
		s.clearSpeedLimitEvidence()
		s.MatchedLimitMps = 0
		s.RoadStatus = custom.MapdOut_SampleStatus_matchedNoLimit
	} else {
		s.MatchedLimitMps = float32(limit)
		s.RoadStatus = custom.MapdOut_SampleStatus_matchedLimit
	}
	s.LimitPreview = s.previewLimit()
}

func (s *State) SuggestedSpeed() float32 {
	suggestedSpeed := min(s.Car.VCruise*ms.KPH_TO_MS, ms.MAX_OP_SPEED)

	if ms.Settings.SpeedLimitControlEnabled || ms.Settings.ExternalSpeedLimitControlEnabled {
		slSuggestedSpeed := s.SpeedLimit.SpeedLimitFinalSuggestion(s.Car.EnableSpeedActive, s.Car.SetSpeedChanging, s.Car.VEgo)
		if suggestedSpeed > slSuggestedSpeed && slSuggestedSpeed > 0 {
			suggestedSpeed = slSuggestedSpeed
		}
	}
	if ms.Settings.VisionCurveSpeedControlEnabled && s.VisionCurveSpeed > 0 && (s.VisionCurveSpeed < suggestedSpeed || suggestedSpeed == 0) && (!ms.Settings.VisionCurveUseEnableSpeed || s.Car.EnableSpeedActive) {
		suggestedSpeed = s.VisionCurveSpeed
	}
	if ms.Settings.MapCurveSpeedControlEnabled && s.MapCurveSpeed > 0 && (s.MapCurveSpeed < suggestedSpeed || suggestedSpeed == 0) && (!ms.Settings.MapCurveUseEnableSpeed || s.Car.EnableSpeedActive) {
		suggestedSpeed = s.MapCurveSpeed
	}
	if suggestedSpeed < 0 {
		suggestedSpeed = 0
	}
	return suggestedSpeed
}

func (s *State) UpdateCarState(carData car.CarState) {
	s.Car.Update(carData)
	s.DistanceSinceLastPosition += float32(s.Car.UpdateTime.DiffMA.Estimate) * s.Car.VEgo
}

func (s *State) UpdateRoadDependentCar() {
	s.SpeedLimit.NextLimit.Update(s)
	s.NextAdvisorySpeed.Update(s)
	s.NextHazard.Update(s)
	if s.RoadStatus == custom.MapdOut_SampleStatus_matchedLimit {
		s.SpeedLimit.Update(s.CurrentWay, s.Car)
	}
}

func (s *State) Send() error {
	msg, err := s.BuildMessage()
	if err != nil {
		return err
	}
	// mapdOut is sent from this loop, so a valid sample cannot wait in an
	// asynchronous queue after its final freshness check.
	return s.Publisher.Send(msg)
}

func (s *State) BuildMessage() (*capnp.Message, error) {
	now := s.bootTime()
	s.expireGpsEvidenceAt(now)
	s.invalidateChangedTile()
	msg, err := s.buildMessageAt(now)
	if err != nil {
		return nil, err
	}
	// Text/list serialization can be expensive with a large selected way.
	// If it crossed the source TTL, discard the valid message and encode an
	// invalid one with neutral road fields.
	endNow := s.bootTime()
	if s.invalidateChangedTile() {
		return s.buildMessageAt(endNow)
	}
	if s.RoadStatus != custom.MapdOut_SampleStatus_noGps && !cereal.GpsFixFreshAt(cereal.GpsSource(s.GpsSource), s.GpsMonoTime, endNow) {
		s.expireGpsEvidenceAt(endNow)
		return s.buildMessageAt(endNow)
	}
	return msg, nil
}

func (s *State) buildMessageAt(now uint64) (*capnp.Message, error) {
	msg, seg, err := capnp.NewMessage(capnp.SingleSegment(nil))
	if err != nil {
		return nil, err
	}
	event, err := log.NewRootEvent(seg)
	if err != nil {
		return nil, err
	}
	event.SetLogMonoTime(now)
	event.SetValid(s.RoadMatched())
	output, err := event.NewMapdOut()
	if err != nil {
		return nil, err
	}
	output.SetSampleVersion(2)
	output.SetRoadStatus(s.RoadStatus)
	output.SetGpsSource(s.GpsSource)
	output.SetGpsMonoTime(s.GpsMonoTime)
	output.SetComputedMonoTime(s.ComputedMonoTime)
	output.SetSourceGeneration(s.SourceGeneration)
	output.SetProducerSession(s.ProducerSession)
	if !s.RoadMatched() {
		return msg, nil
	}
	id := s.CurrentWay.Way.Id()
	output.SetWayId(id)

	name := s.CurrentWay.Way.WayName()
	output.SetWayName(name)

	ref := s.CurrentWay.Way.WayRef()
	output.SetWayRef(ref)

	output.SetRoadName(s.CurrentWay.Way.Name())

	output.SetSpeedLimit(s.MatchedLimitMps)
	if s.LimitPreview.BoundaryID != "" {
		output.SetPreviewSpeedLimit(s.LimitPreview.Speed)
		output.SetPreviewDistance(s.LimitPreview.Distance)
		output.SetPreviewBoundaryId(s.LimitPreview.BoundaryID)
		output.SetPreviewWayId(s.LimitPreview.WayID)
	}

	output.SetConditionalSpeedLimit(s.CurrentWay.ConditionalMaxSpeedRaw())

	if !s.ShadowOnly {
		output.SetSpeedLimitSuggestedSpeed(s.SpeedLimit.Suggestion.Value)
		output.SetNextSpeedLimit(s.SpeedLimit.NextLimit.Value)
		output.SetNextSpeedLimitDistance(s.SpeedLimit.NextLimit.Distance)
	}

	hazard := s.CurrentWay.Way.Hazard()
	output.SetHazard(hazard)

	if !s.ShadowOnly {
		output.SetNextHazard(s.NextHazard.Value)
		output.SetNextHazardDistance(s.NextHazard.Distance)
	}

	advisorySpeed := s.CurrentWay.Way.AdvisorySpeed()
	output.SetAdvisorySpeed(float32(advisorySpeed))

	if !s.ShadowOnly {
		output.SetNextAdvisorySpeed(s.NextAdvisorySpeed.Value)
		output.SetNextAdvisorySpeedDistance(s.NextAdvisorySpeed.Distance)
	}

	oneWay := s.CurrentWay.Way.OneWay()
	output.SetOneWay(oneWay)

	lanes := s.CurrentWay.Way.Lanes()
	output.SetLanes(uint8(lanes))

	output.SetTileLoaded(s.Data.Loaded)

	output.SetRoadContext(custom.RoadContext(s.CurrentWay.Way.Context()))
	output.SetHighwayClass(custom.HighwayClass(s.CurrentWay.Way.HighwayClass()))
	output.SetEstimatedRoadWidth(s.CurrentWay.Way.Width())
	if !s.ShadowOnly {
		output.SetVisionCurveSpeed(s.VisionCurveSpeed)
		output.SetMapCurveSpeed(s.MapCurveSpeed)
		output.SetSuggestedSpeed(s.SuggestedSpeed())
	}
	output.SetDistanceFromWayCenter(float32(s.CurrentWay.OnWay.Distance.Distance))

	output.SetWaySelectionType(s.CurrentWay.SelectionType)
	if !s.ShadowOnly {
		output.SetSpeedLimitAccepted(ms.Settings.SpeedLimitAccepted())
	}

	if s.RoadStatus == custom.MapdOut_SampleStatus_matchedNoLimit {
		output.SetSpeedLimitSuggestedSpeed(0)
		output.SetSpeedLimitAccepted(false)
	}
	return msg, nil
}
