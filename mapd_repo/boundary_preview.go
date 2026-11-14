package main

import (
	"math"
	"pfeifer.dev/mapd/cereal/custom"
	"pfeifer.dev/mapd/maps"
)

// BoundaryPreview reports GPS/map observations only: no acceptance, offsets or
// target ownership. The first boundary may be found along the predicted road
// continuation (NextWay) when a junction has more than one legal way out.
// Short-zone suppression still requires an entirely unambiguous two-boundary path.
// Unknown limits, illegal directions, loops and tile edges terminate prediction.
type BoundaryPreview struct {
	Limit, Distance, EndDistance, FollowingLimit float32
	NextWay, FollowingWay                        int64
}

func (s *State) boundaryPreview() (float32, float32) {
	p := s.boundaryPreviewDetails()
	return p.Limit, p.Distance
}

func (s *State) boundaryPreviewDetails() BoundaryPreview {
	preview := BoundaryPreview{}
	if s.RoadStatus != custom.MapdOut_SampleStatus_matchedLimit || s.CurrentWay.SelectionType != custom.WaySelectionType_current {
		return preview
	}
	way, forward, position := s.CurrentWay.Way, s.CurrentWay.OnWay.IsForward, s.Position
	distance := float32(0)
	predictedPath := false
	seen := map[int64]bool{way.Id(): true}
	for range 64 {
		d, err := way.DistanceToEnd(position, forward)
		if err != nil || d < 0 || math.IsNaN(float64(d)) || math.IsInf(float64(d), 0) {
			return preview
		}
		distance += d
		if distance > 2000 {
			return preview
		}
		_, end := way.GetStartEnd(forward)
		box := s.Data.OverlapBox()
		if !box.PosInside(end) {
			return preview
		}
		candidates, err := way.MatchingWays(&s.Data, end)
		if err != nil {
			return preview
		}
		legal := []maps.Way{}
		for _, candidate := range candidates {
			if candidate.Id() == way.Id() || candidate.Nodes.Len() < 2 {
				continue
			}
			dir := candidate.IsForwardFrom(end)
			if !dir && candidate.OneWay() {
				continue
			}
			legal = append(legal, candidate)
		}
		if len(legal) == 0 {
			return preview
		}
		chosen := legal[0]
		if len(legal) != 1 {
			// The original map lookahead predicts the continuation using road
			// name/ref, connection geometry and road priority. Use that same
			// selector, without giving the provider speed-control authority.
			if preview.Limit != 0 {
				return preview
			}
			next, err := way.NextWay(&s.Data, forward)
			if err != nil || next.Way.Nodes.Len() < 2 {
				return preview
			}
			found := false
			for _, candidate := range legal {
				if candidate.Id() == next.Way.Id() {
					chosen = candidate
					found = true
					break
				}
			}
			if !found {
				return preview
			}
			predictedPath = true
		}
		if seen[chosen.Id()] {
			return preview
		}
		way = chosen
		forward = way.IsForwardFrom(end)
		position = end
		seen[way.Id()] = true
		current := CurrentWay{Way: way, OnWay: maps.OnWayResult{IsForward: forward}}
		limit := current.EffectiveMaxSpeed()
		if math.IsNaN(limit) || math.IsInf(limit, 0) || limit < 1 || limit > 80 {
			return preview
		}
		if preview.Limit == 0 {
			if math.Abs(limit-float64(s.MatchedLimitMps)) > .1 {
				preview.Limit, preview.Distance, preview.NextWay = float32(limit), distance, way.Id()
				if predictedPath {
					return preview
				}
			}
		} else if math.Abs(limit-float64(preview.Limit)) > .1 {
			preview.EndDistance, preview.FollowingLimit, preview.FollowingWay = distance, float32(limit), way.Id()
			return preview
		}
	}
	return preview
}
