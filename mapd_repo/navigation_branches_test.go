package main

import (
	"math"
	"testing"
	"time"

	"capnproto.org/go/capnp/v3"
	"pfeifer.dev/mapd/cereal/offline"

	"pfeifer.dev/mapd/cereal/custom"
	"pfeifer.dev/mapd/maps"
	m "pfeifer.dev/mapd/math"
)

func navigationState(t *testing.T, specs []syntheticWay, heading float32) *State {
	t.Helper()
	tile := packedRoadTile(t, specs)
	s := &State{ShadowOnly: true}
	s.Init()
	s.bootNow = func() uint64 { return 10_100_000_000 }
	sample := ambiguitySample(t, 10_000_000_000, true)
	sample.Location.SetBearingDeg(heading)
	s.ProcessGps(sample, true, func(m.Position) (maps.Offline, error) { return tile, nil }, time.Unix(0, 0))
	return s
}

func TestNavigationBranchesUseMatchedRoadAndTravelDirection(t *testing.T) {
	for _, reverse := range []bool{false, true} {
		road := syntheticWay{1, 8, 35.14, -97.9, 35.151, -97.9, false, 2}
		if reverse {
			road.lat1, road.lat2 = road.lat2, road.lat1
		}
		s := navigationState(t, []syntheticWay{road,
			{2, 8, 35.151, -97.9, 35.151, -97.902, false, 2},
			{3, 8, 35.151, -97.9, 35.151, -97.898, false, 2},
		}, 0)
		event, out := decodedOutput(t, s)
		branches, err := out.NavigationBranches()
		if err != nil || !event.Valid() || branches.Len() != 2 {
			t.Fatalf("reverse=%v valid=%v branches=%d err=%v", reverse, event.Valid(), branches.Len(), err)
		}
		for i, angle := range []float64{-90, 90} {
			branch := branches.At(i)
			if math.Abs(float64(branch.AngleDegrees())-angle) > 1 || math.Abs(float64(branch.DistanceMeters())-111.2) > 2 {
				t.Fatalf("reverse=%v branch=%d angle=%f distance=%f", reverse, i, branch.AngleDegrees(), branch.DistanceMeters())
			}
		}
		// Existing freshness gates must clear junction evidence too.
		s.bootNow = func() uint64 { return 11_000_000_000 }
		event, out = decodedOutput(t, s)
		branches, err = out.NavigationBranches()
		if err != nil || event.Valid() || branches.Len() != 0 || len(s.NavigationBranches) != 0 {
			t.Fatal("expired geometry survived")
		}
	}
}

func TestNavigationBranchesDoNotInventCrossingsOrOppositeTravel(t *testing.T) {
	road := syntheticWay{1, 8, 35.14, -97.9, 35.151, -97.9, false, 2}
	for _, other := range []syntheticWay{
		// A line crossing the way without shared stored endpoints is not a junction.
		{2, 8, 35.151, -97.902, 35.151, -97.898, false, 2},
		// Incoming-only road: the candidate cannot be entered from this junction.
		{2, 8, 35.151, -97.902, 35.151, -97.9, true, 2},
	} {
		s := navigationState(t, []syntheticWay{road, other}, 0)
		_, out := decodedOutput(t, s)
		branches, err := out.NavigationBranches()
		if err != nil || branches.Len() != 0 {
			t.Fatalf("invented branch: %v", err)
		}
	}
	s := navigationState(t, []syntheticWay{road, {2, 8, 35.151, -97.9, 35.151, -97.902, false, 2}}, 180)
	_, out := decodedOutput(t, s)
	branches, err := out.NavigationBranches()
	if err != nil || branches.Len() != 0 {
		t.Fatal("junction behind car published")
	}
}

func TestNavigationBranchesAreBoundedAndClearedWithRoadLoss(t *testing.T) {
	s := navigationState(t, []syntheticWay{{1, 8, 35.14, -97.9, 35.151, -97.9, false, 2},
		{2, 8, 35.151, -97.9, 35.151, -97.902, false, 2}}, 0)
	if len(s.NavigationBranches) == 0 {
		t.Fatal("fixture has no branches")
	}
	s.clearRoadDerived()
	s.RoadStatus = custom.MapdOut_SampleStatus_noMatch
	_, out := decodedOutput(t, s)
	branches, err := out.NavigationBranches()
	if err != nil || branches.Len() != 0 {
		t.Fatal("road loss retained geometry")
	}
}

func TestNavigationBranchesBoundOutputAtDenseJunction(t *testing.T) {
	specs := []syntheticWay{{1, 8, 35.14, -97.9, 35.151, -97.9, false, 2}}
	for i := 0; i < maxNavigationBranches+8; i++ {
		specs = append(specs, syntheticWay{int64(i + 2), 8, 35.151, -97.9, 35.152 + float64(i)*0.00001, -97.898, false, 2})
	}
	s := navigationState(t, specs, 0)
	_, out := decodedOutput(t, s)
	rows, err := out.NavigationBranches()
	if err != nil || rows.Len() != maxNavigationBranches {
		t.Fatalf("unbounded junction: %d %v", rows.Len(), err)
	}
}

func TestNavigationBranchesAtInteriorJunctionIncludeStraightContinuation(t *testing.T) {
	data := packedRoadTileBytes(t, []syntheticWay{{1, 8, 35.14, -97.9, 35.153, -97.9, false, 2},
		{2, 8, 35.151, -97.9, 35.151, -97.898, false, 2}})
	msg, err := capnp.UnmarshalPacked(data)
	if err != nil {
		t.Fatal(err)
	}
	root, err := offline.ReadRootOffline(msg)
	if err != nil {
		t.Fatal(err)
	}
	ways, err := root.Ways()
	if err != nil {
		t.Fatal(err)
	}
	// UnmarshalPacked uses a read-only arena; copy the fixture into a writable one.
	mutable, segment, err := capnp.NewMessage(capnp.SingleSegment(nil))
	if err != nil {
		t.Fatal(err)
	}
	copyRoot, err := offline.NewRootOffline(segment)
	if err != nil {
		t.Fatal(err)
	}
	copyRoot.SetMinLat(35)
	copyRoot.SetMinLon(-98)
	copyRoot.SetMaxLat(35.25)
	copyRoot.SetMaxLon(-97.75)
	if err := copyRoot.SetWays(ways); err != nil {
		t.Fatal(err)
	}
	ways, err = copyRoot.Ways()
	if err != nil {
		t.Fatal(err)
	}
	nodes, err := ways.At(0).NewNodes(3)
	if err != nil {
		t.Fatal(err)
	}
	for i, lat := range []float64{35.14, 35.151, 35.153} {
		nodes.At(i).SetLatitude(lat)
		nodes.At(i).SetLongitude(-97.9)
	}
	data, err = mutable.MarshalPacked()
	if err != nil {
		t.Fatal(err)
	}
	tile := maps.ReadOffline(data)
	s := &State{ShadowOnly: true}
	s.Init()
	s.bootNow = func() uint64 { return 10_100_000_000 }
	s.ProcessGps(ambiguitySample(t, 10_000_000_000, true), true, func(m.Position) (maps.Offline, error) { return tile, nil }, time.Unix(0, 0))
	_, out := decodedOutput(t, s)
	branches, err := out.NavigationBranches()
	if err != nil || branches.Len() != 2 {
		t.Fatalf("interior branches=%d err=%v", branches.Len(), err)
	}
	if branches.At(0).WayId() != 1 || math.Abs(float64(branches.At(0).AngleDegrees())) > 1 ||
		branches.At(1).WayId() != 2 || math.Abs(float64(branches.At(1).AngleDegrees())-90) > 1 {
		t.Fatal("interior continuation/turn missing")
	}
}
