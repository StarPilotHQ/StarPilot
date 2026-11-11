package main

import (
	"cmp"
	"slices"

	"pfeifer.dev/mapd/cereal/log"
	"pfeifer.dev/mapd/maps"
)

// Navigation-only road choice for fixes the conservative matcher calls
// ambiguous. It selects the road whose junctions corroborate phone guidance;
// it never becomes CurrentWay, a speed limit, curve output or a valid event.
const (
	navigationMinSpeed       = 3                  // m/s; slower headings are noise
	navigationMaxHeadingSine = 0.5735764363510461 // sin(35 deg)
	navigationMinMargin      = 8                  // metres between nearest and next road
)

type navigationCandidate struct {
	way      maps.Way
	distance float32
}

// navigationWay returns the way the junction list should be built from, or
// false. held is the previous navigation way ID, kept while nearly stopped.
func navigationWay(offlineMaps *maps.Offline, location log.GpsLocationData, held int64) (CurrentWay, bool) {
	ways := plausibleLoadedWays(offlineMaps, location)
	if len(ways) == 0 {
		return CurrentWay{}, false
	}
	moving := location.Speed() >= navigationMinSpeed && location.BearingAccuracyDeg() < 180
	candidates := make([]navigationCandidate, 0, len(ways))
	for _, way := range ways {
		if moving {
			sine, err := way.BearingAlignment(location)
			if err != nil || sine > navigationMaxHeadingSine {
				continue
			}
		} else if way.Id() != held {
			continue
		}
		onWay, err := way.OnWay(location, 2)
		if err != nil || !onWay.OnWay {
			continue
		}
		candidates = append(candidates, navigationCandidate{way, onWay.Distance.Distance})
	}
	if len(candidates) == 0 {
		return CurrentWay{}, false
	}
	slices.SortStableFunc(candidates, func(a, b navigationCandidate) int { return cmp.Compare(a.distance, b.distance) })
	best := candidates[0].way
	if len(candidates) > 1 && !sameNamedRoad(candidates) {
		margin := max(float32(navigationMinMargin), location.HorizontalAccuracy())
		if candidates[1].distance-candidates[0].distance < margin {
			return CurrentWay{}, false
		}
	}
	// The chosen road still needs the matcher's own narrow selection envelope.
	onWay, err := best.OnWay(location, best.DistanceMultiplier())
	if err != nil || !onWay.OnWay {
		return CurrentWay{}, false
	}
	return CurrentWay{Way: best, Distance: onWay.Distance, OnWay: onWay}, true
}

// sameNamedRoad is true when every candidate is a piece of one named road,
// such as consecutive OSM ways of a freeway split at each bridge.
func sameNamedRoad(candidates []navigationCandidate) bool {
	key := func(w *maps.Way) string { return w.WayName() + "\x00" + w.WayRef() }
	first := key(&candidates[0].way)
	if first == "\x00" {
		return false
	}
	for i := range candidates[1:] {
		if key(&candidates[i+1].way) != first {
			return false
		}
	}
	return true
}
