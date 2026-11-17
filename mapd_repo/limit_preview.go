package main

import (
	"crypto/sha256"
	"fmt"
	"math"

	"pfeifer.dev/mapd/cereal/custom"
	"pfeifer.dev/mapd/maps"
)

const previewHorizonM = 500
const previewMaxWays = 64

type LimitPreview struct {
	Speed      float32
	Distance   float32
	BoundaryID string
	WayID      int64
}

// A preview follows only a unique legal continuation, never a guessed turn.
func (s *State) previewLimit() LimitPreview {
	if !s.ShadowOnly || s.SnapshotID == "" || s.CurrentWay.SelectionType != custom.WaySelectionType_current || s.MatchedLimitMps <= 0 {
		return LimitPreview{}
	}
	way, forward, position := s.CurrentWay.Way, s.CurrentWay.OnWay.IsForward, s.Position
	distance := float32(0)
	seen := map[int64]bool{way.Id(): true}
	for range previewMaxWays {
		_, end := way.GetStartEnd(forward)
		if distance+position.DistanceTo(end) > previewHorizonM {
			return LimitPreview{}
		}
		segment, found, err := way.DistanceToNode(position, forward, end)
		if err != nil || !found || segment < 0 || math.IsNaN(float64(segment)) || math.IsInf(float64(segment), 0) {
			return LimitPreview{}
		}
		distance += segment
		box := s.Data.OverlapBox()
		if distance <= 0 || distance > previewHorizonM || !box.PosInside(end) {
			return LimitPreview{}
		}
		matches, err := way.MatchingWays(&s.Data, end)
		if err != nil {
			return LimitPreview{}
		}
		legal := map[int64]maps.Way{}
		for _, next := range matches {
			first, last := next.GetStartEnd(true)
			if first.Equals(last) && !next.OneWay() {
				return LimitPreview{} // A two-way loop has two possible entry directions.
			}
			if next.Id() > 0 && (next.IsForwardFrom(end) || !next.OneWay()) {
				legal[next.Id()] = next
			}
		}
		if len(legal) != 1 {
			return LimitPreview{}
		}
		var next maps.Way
		for _, candidate := range legal {
			next = candidate
		}
		if seen[next.Id()] {
			return LimitPreview{}
		}
		nextForward := next.IsForwardFrom(end)
		road := CurrentWay{Way: next, OnWay: maps.OnWayResult{IsForward: nextForward}}
		speed := road.EffectiveMaxSpeed()
		if speed <= 0 || math.IsNaN(speed) || math.IsInf(speed, 0) || speed > math.MaxFloat32 {
			return LimitPreview{}
		}
		if math.Abs(speed-float64(s.MatchedLimitMps)) > .02 {
			key := fmt.Sprintf("%s:%d:%d:%t:%.7f:%.7f", s.SnapshotID, way.Id(), next.Id(), nextForward, end.Lat(), end.Lon())
			return LimitPreview{float32(speed), distance, fmt.Sprintf("%x", sha256.Sum256([]byte(key))), next.Id()}
		}
		seen[next.Id()] = true
		way, forward, position = next, nextForward, end
	}
	return LimitPreview{}
}
