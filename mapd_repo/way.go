package main

import (
	"errors"
	"time"

	"pfeifer.dev/mapd/cereal/custom"
	"pfeifer.dev/mapd/cereal/log"
	"pfeifer.dev/mapd/maps"
	m "pfeifer.dev/mapd/math"
	ms "pfeifer.dev/mapd/settings"
	"pfeifer.dev/mapd/utils"
)

type CurrentWay struct {
	Way               maps.Way
	Distance          maps.DistanceResult
	OnWay             maps.OnWayResult
	StartPosition     m.Position
	EndPosition       m.Position
	ConfidenceCounter int
	LastChangeTime    time.Time
	StableDistance    float32
	SelectionType     custom.WaySelectionType
	maxSpeed          utils.Curry[float64]
}

func (w *CurrentWay) _maxSpeed() float64 {
	maxSpeed := w.Way.MaxSpeed()
	if w.OnWay.IsForward && w.Way.MaxSpeedForward() > 0 {
		maxSpeed = w.Way.MaxSpeedForward()
	} else if !w.OnWay.IsForward && w.Way.MaxSpeedBackward() > 0 {
		maxSpeed = w.Way.MaxSpeedBackward()
	}
	return maxSpeed
}

func (w *CurrentWay) MaxSpeed() float64 {
	return w.maxSpeed.Value(w._maxSpeed)
}

// the raw maxspeed:conditional tag for the direction of travel
func (w *CurrentWay) ConditionalMaxSpeedRaw() string {
	return w.Way.ConditionalMaxSpeedRaw(w.OnWay.IsForward)
}

// MaxSpeed with an applying conditional speed limit folded in when
// conditional speed limit control is enabled. Not memoized because the
// applying rule changes with the time of day.
func (w *CurrentWay) EffectiveMaxSpeed() float64 {
	if ms.Settings.ConditionalSpeedLimitControlEnabled {
		rules := w.Way.ConditionalSpeedRules(w.OnWay.IsForward)
		if conditional := maps.ConditionalSpeedAt(rules, time.Now()); conditional > 0 {
			return conditional
		}
	}
	return w.MaxSpeed()
}

// ErrAmbiguousWay means more than one distinct loaded-tile road fits the same
// GPS uncertainty envelope. The rank scorer must not turn that into a limit.
var ErrAmbiguousWay = errors.New("multiple plausible road IDs")

// plausibleLoadedWays considers every way in the current tile before sticky or
// predicted selection. A conservative existing OnWay(..., 2) envelope admits
// candidates; direction/one-way constraints are applied inside OnWay. Duplicate
// records for the same OSM way ID do not create artificial ambiguity.
func plausibleLoadedWays(offlineMaps *maps.Offline, location log.GpsLocationData) []maps.Way {
	if offlineMaps == nil || !offlineMaps.Loaded {
		return nil
	}
	ways := make([]maps.Way, 0, 2)
	seen := make(map[int64]struct{})
	// The grid lists, in tile order, every way that could pass OnWay here.
	candidates, indexed := offlineMaps.WaysNear(m.NewPosition(location.Latitude(), location.Longitude()))
	count := len(candidates)
	if !indexed {
		count = offlineMaps.Ways.Len()
	}
	for c := range count {
		i := c
		if indexed {
			i = int(candidates[c])
		}
		if !offlineMaps.WayMayMatch(i, location, 2) {
			continue
		}
		way := offlineMaps.Ways.At(i)
		if way.Id() <= 0 || way.Nodes.Len() < 2 {
			continue
		}
		if _, exists := seen[way.Id()]; exists {
			continue
		}
		onWay, err := way.OnWay(location, 2)
		if err != nil || !onWay.OnWay {
			continue
		}
		seen[way.Id()] = struct{}{}
		ways = append(ways, way)
	}
	return ways
}

// Resolve a close aligned interior fix, not every overlapping search envelope.
// Crossings, parallel roads and uncertain heading still remain ambiguous.
func geometricallyUnique(candidates []maps.Way, location log.GpsLocationData) []maps.Way {
	if location.Speed() < 5 || location.HorizontalAccuracy() > 10 {
		return candidates
	}
	accuracy := max(location.HorizontalAccuracy(), float32(5))
	for _, chosen := range candidates {
		n, err := chosen.OnWay(location, chosen.DistanceMultiplier())
		alignment, aerr := chosen.BearingAlignment(location)
		if err != nil || aerr != nil || !n.OnWay || n.Distance.Distance > accuracy ||
			n.Distance.LinePosition.T <= 0 || n.Distance.LinePosition.T >= 1 || alignment > .35 {
			continue
		}
		unique := true
		for _, other := range candidates {
			if other.Id() == chosen.Id() {
				continue
			}
			rival, rerr := other.OnWay(location, 2)
			angle, berr := other.BearingAlignment(location)
			if rerr != nil || berr != nil {
				unique = false
				break
			}
			// A transverse road outside the fix uncertainty cannot contain this fix.
			if angle >= .7 && rival.Distance.Distance > accuracy {
				continue
			}
			// Connected records may be consecutive segments of the same road.
			// Require the rival projection beyond its endpoint by the fix uncertainty.
			connected := false
			for _, a := range []m.Position{chosen.Nodes.At(0), chosen.Nodes.At(chosen.Nodes.Len() - 1)} {
				for _, b := range []m.Position{other.Nodes.At(0), other.Nodes.At(other.Nodes.Len() - 1)} {
					if a == b {
						connected = true
					}
				}
			}
			endpoint := rival.Distance.LinePosition.T == 0 || rival.Distance.LinePosition.T == 1
			if connected && endpoint && rival.Distance.Distance > accuracy {
				continue
			}
			unique = false
			break
		}
		if unique {
			return []maps.Way{chosen}
		}
	}
	return candidates
}

func GetCurrentWay(currentWay CurrentWay, nextWays []maps.NextWayResult, offlineMaps *maps.Offline, location log.GpsLocationData) (CurrentWay, error) {
	candidates := geometricallyUnique(plausibleLoadedWays(offlineMaps, location), location)
	if len(candidates) == 0 {
		return CurrentWay{SelectionType: custom.WaySelectionType_fail}, errors.New("no plausible loaded-tile way")
	}
	if len(candidates) > 1 {
		return CurrentWay{SelectionType: custom.WaySelectionType_fail}, ErrAmbiguousWay
	}
	// Use the loaded tile's representation, even when a prior sticky way has the
	// same ID. This prevents an old tile's geometry/limit from being republished.
	way := candidates[0]
	onWay, err := way.OnWay(location, 2)
	if err != nil || !onWay.OnWay {
		return CurrentWay{SelectionType: custom.WaySelectionType_fail}, errors.New("sole way lost geographic match")
	}
	selection := custom.WaySelectionType_fail
	confidence := 1
	lastChange := time.Now()
	stableDistance := onWay.Distance.Distance
	narrow, narrowErr := way.OnWay(location, way.DistanceMultiplier())
	sameCurrent := currentWay.Way.Nodes.Len() > 1 && currentWay.Way.Id() == way.Id()
	if sameCurrent {
		lastChange = currentWay.LastChangeTime
		if narrowErr == nil && narrow.OnWay && narrow.Distance.LinePosition.T != 0 && narrow.Distance.LinePosition.T != 1 {
			selection = custom.WaySelectionType_current
			confidence = currentWay.ConfidenceCounter + 1
			onWay = narrow
		}
	}
	if selection == custom.WaySelectionType_fail && narrowErr == nil && narrow.OnWay {
		for _, next := range nextWays {
			if next.Way.Nodes.Len() > 1 && next.Way.Id() == way.Id() {
				selection = custom.WaySelectionType_predicted
				onWay = narrow
				break
			}
		}
	}
	if selection == custom.WaySelectionType_fail && narrowErr == nil && narrow.OnWay {
		selection = custom.WaySelectionType_possible
		onWay = narrow
	}
	if selection == custom.WaySelectionType_fail && sameCurrent {
		selection = custom.WaySelectionType_extended
		confidence = currentWay.ConfidenceCounter
		stableDistance = currentWay.StableDistance
	}
	if selection == custom.WaySelectionType_fail {
		return CurrentWay{SelectionType: custom.WaySelectionType_fail}, errors.New("sole broad-envelope way failed selection threshold")
	}
	start, end := way.GetStartEnd(onWay.IsForward)
	return CurrentWay{Way: way, Distance: onWay.Distance, OnWay: onWay,
		StartPosition: start, EndPosition: end, ConfidenceCounter: confidence,
		LastChangeTime: lastChange, StableDistance: stableDistance,
		SelectionType: selection}, nil
}

func NextWays(location log.GpsLocationData, currentWay CurrentWay, offlineMaps *maps.Offline, isForward bool) ([]maps.NextWayResult, error) {
	nextWays := []maps.NextWayResult{}
	dist := float32(0.0)
	wayIdx := currentWay.Way
	forward := isForward
	startPos := m.NewPosition(location.Latitude(), location.Longitude())
	for dist < ms.MIN_WAY_DIST {
		d, err := wayIdx.DistanceToEnd(startPos, forward)
		if err != nil || d <= 0 {
			break
		}
		dist += d
		nw, err := wayIdx.NextWay(offlineMaps, forward)
		if err != nil {
			break
		}
		nextWays = append(nextWays, nw)
		wayIdx = nw.Way

		startPos = nw.StartPosition
		forward = nw.IsForward
	}

	if len(nextWays) == 0 {
		nextWay, err := currentWay.Way.NextWay(offlineMaps, isForward)
		if err != nil {
			return []maps.NextWayResult{}, err
		}
		nextWays = append(nextWays, nextWay)
	}

	return nextWays, nil
}
