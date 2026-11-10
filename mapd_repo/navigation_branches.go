package main

import (
	"math"

	"pfeifer.dev/mapd/cereal/custom"
	"pfeifer.dev/mapd/maps"
	m "pfeifer.dev/mapd/math"
)

const maxNavigationBranches = 32
const navigationLookaheadMeters = 1500

type navigationBranch struct {
	way      maps.Way
	junction m.Position
	distance float32
	angle    float32
}

// localNavigationBranches enumerates connections from the already matched road.
// It never chooses a continuation or constructs a route. Endpoint-only local
// topology is incomplete; absent candidates must not invalidate phone guidance.
func (s *State) localNavigationBranches() []navigationBranch {
	w := &s.CurrentWay.Way
	if !s.Data.Loaded || w.Nodes.Len() < 2 {
		return nil
	}
	forward := s.CurrentWay.OnWay.IsForward
	step := 1
	if !forward {
		step = -1
	}
	d := s.CurrentWay.OnWay.Distance
	next := -1
	for i := 0; i < w.Nodes.Len()-1; i++ {
		a, b := w.Nodes.At(i), w.Nodes.At(i+1)
		if a.Equals(d.LineStart) && b.Equals(d.LineEnd) {
			next = i + 1
			if !forward {
				next = i
			}
			break
		}
	}
	if next < 0 {
		return nil
	}
	last := d.LinePosition.Pos
	along := float32(0)
	result := make([]navigationBranch, 0, 4)
	for index, visited := next, 0; index >= 0 && index < w.Nodes.Len() && visited < 64; index, visited = index+step, visited+1 {
		node := w.Nodes.At(index)
		along += last.DistanceTo(node)
		last = node
		if along > navigationLookaheadMeters {
			break
		}
		previous := index - step
		if previous < 0 || previous >= w.Nodes.Len() {
			continue
		}
		incoming := w.Nodes.At(previous)
		// Very short geometry segments do not establish a useful turn angle.
		for n := 0; incoming.DistanceTo(node) < 15 && n < 8; n++ {
			previous -= step
			if previous < 0 || previous >= w.Nodes.Len() {
				break
			}
			incoming = w.Nodes.At(previous)
		}
		if incoming.DistanceTo(node) < 3 {
			continue
		}
		ways, err := w.MatchingWays(&s.Data, node)
		if err != nil || len(ways) == 0 {
			continue
		}
		incomingVector := incoming.VectorTo(node)
		heading := incomingVector.Bearing()
		seen := map[int64]bool{}
		appendBranch := func(candidate maps.Way, direction bool, start int) {
			if len(result) >= maxNavigationBranches || seen[candidate.Id()] {
				return
			}
			stride := 1
			if !direction {
				stride = -1
			}
			endpoint := node
			for j, n := start+stride, 0; j >= 0 && j < candidate.Nodes.Len() && n < 16; j, n = j+stride, n+1 {
				endpoint = candidate.Nodes.At(j)
				if node.DistanceTo(endpoint) >= 15 {
					break
				}
			}
			if node.DistanceTo(endpoint) < 3 {
				return
			}
			outgoing := node.VectorTo(endpoint)
			angle := math.Mod((outgoing.Bearing()-heading)*180/math.Pi+540, 360) - 180
			if math.IsNaN(angle) || math.IsInf(angle, 0) {
				return
			}
			seen[candidate.Id()] = true
			result = append(result, navigationBranch{candidate, node, along, float32(angle)})
		}
		// At an interior junction, continuing on the current road is also an option.
		if index+step >= 0 && index+step < w.Nodes.Len() {
			appendBranch(*w, forward, index)
		}
		for _, candidate := range ways {
			direction := candidate.IsForwardFrom(node)
			if candidate.OneWay() && !direction {
				continue
			}
			start := 0
			if !direction {
				start = candidate.Nodes.Len() - 1
			}
			appendBranch(candidate, direction, start)
		}
		if len(result) >= maxNavigationBranches {
			break
		}
	}
	return result
}

func (s *State) setNavigationBranches(out custom.MapdOut) error {
	branches, err := out.NewNavigationBranches(int32(len(s.NavigationBranches)))
	if err != nil {
		return err
	}
	for i, branch := range s.NavigationBranches {
		item := branches.At(i)
		item.SetWayId(branch.way.Id())
		item.SetJunctionLatitude(branch.junction.Lat())
		item.SetJunctionLongitude(branch.junction.Lon())
		item.SetDistanceMeters(branch.distance)
		item.SetAngleDegrees(branch.angle)
		item.SetHighwayClass(custom.HighwayClass(branch.way.HighwayClass()))
		if err := item.SetRoadName(branch.way.Name()); err != nil {
			return err
		}
	}
	return nil
}
