package main

import (
	"math"
	"testing"
	"time"

	"capnproto.org/go/capnp/v3"

	"pfeifer.dev/mapd/cereal/custom"
	"pfeifer.dev/mapd/cereal/offline"
	"pfeifer.dev/mapd/maps"
	m "pfeifer.dev/mapd/math"
)

type namedWay struct {
	syntheticWay
	name string
}

func namedRoadTile(t *testing.T, specs []namedWay) maps.Offline {
	t.Helper()
	msg, seg, err := capnp.NewMessage(capnp.SingleSegment(nil))
	if err != nil {
		t.Fatal(err)
	}
	root, err := offline.NewRootOffline(seg)
	if err != nil {
		t.Fatal(err)
	}
	root.SetMinLat(35)
	root.SetMaxLat(35.25)
	root.SetMinLon(-98)
	root.SetMaxLon(-97.75)
	ways, err := root.NewWays(int32(len(specs)))
	if err != nil {
		t.Fatal(err)
	}
	for i, spec := range specs {
		way := ways.At(i)
		way.SetId(spec.id)
		way.SetMaxSpeed(spec.speed)
		way.SetOneWay(spec.oneWay)
		way.SetLanes(spec.lanes)
		way.SetMinLat(min(spec.lat1, spec.lat2) - 0.001)
		way.SetMaxLat(max(spec.lat1, spec.lat2) + 0.001)
		way.SetMinLon(min(spec.lon1, spec.lon2) - 0.001)
		way.SetMaxLon(max(spec.lon1, spec.lon2) + 0.001)
		if err := way.SetName(spec.name); err != nil {
			t.Fatal(err)
		}
		nodes, err := way.NewNodes(2)
		if err != nil {
			t.Fatal(err)
		}
		nodes.At(0).SetLatitude(spec.lat1)
		nodes.At(0).SetLongitude(spec.lon1)
		nodes.At(1).SetLatitude(spec.lat2)
		nodes.At(1).SetLongitude(spec.lon2)
	}
	data, err := msg.MarshalPacked()
	if err != nil {
		t.Fatal(err)
	}
	tile := maps.ReadOffline(data)
	if !tile.Loaded {
		t.Fatal("packed tile rejected")
	}
	return tile
}

// navigationFix feeds one new external fix at 35.15,-97.9 heading north.
func navigationFix(t *testing.T, s *State, tile maps.Offline, lat float64, speed float32, mono uint64) {
	t.Helper()
	s.bootNow = func() uint64 { return mono + 100_000_000 }
	sample := ambiguitySample(t, mono, s.ProducerSession != 0 && s.GpsMonoTime == 0)
	sample.NewFix = true
	sample.Location.SetLatitude(lat)
	sample.Location.SetSpeed(speed)
	s.ProcessGps(sample, true, func(m.Position) (maps.Offline, error) { return tile, nil }, time.Unix(0, 0))
}

func navigationOutput(t *testing.T, s *State) (custom.MapdOut, []float64) {
	t.Helper()
	event, out := decodedOutput(t, s)
	if event.Valid() || out.RoadStatus() != custom.MapdOut_SampleStatus_unknown || out.WayId() != 0 ||
		out.SpeedLimit() != 0 || s.RoadMatched() || s.CurrentWay.Way.Nodes.Len() != 0 {
		t.Fatalf("ambiguous fix leaked a road match: valid=%v status=%v way=%d", event.Valid(), out.RoadStatus(), out.WayId())
	}
	branches, err := out.NavigationBranches()
	if err != nil {
		t.Fatal(err)
	}
	angles := []float64{}
	for i := 0; i < branches.Len(); i++ {
		angles = append(angles, math.Round(float64(branches.At(i).AngleDegrees())))
	}
	return out, angles
}

func TestNavigationJunctionsFollowSameNamedRoadThroughWaySplit(t *testing.T) {
	for _, tc := range []struct {
		next     string
		branches int
	}{{"Main", 2}, {"Other", 1}} {
		tile := namedRoadTile(t, []namedWay{
			{syntheticWay{1, 20, 35.14, -97.9, 35.15, -97.9, false, 2}, "Main"},
			{syntheticWay{2, 20, 35.15, -97.9, 35.16, -97.9, false, 2}, tc.next},
			{syntheticWay{3, 20, 35.16, -97.9, 35.16, -97.91, false, 2}, "West"},
			{syntheticWay{4, 20, 35.16, -97.9, 35.16, -97.89, false, 2}, "East"},
		})
		s := &State{ShadowOnly: true}
		s.Init()
		navigationFix(t, s, tile, 35.14992, 20, 10_000_000_000)
		out, angles := navigationOutput(t, s)
		branches, _ := out.NavigationBranches()
		if len(angles) != tc.branches {
			t.Fatalf("next=%s angles=%v", tc.next, angles)
		}
		if tc.next == "Other" && (angles[0] != 0 || branches.At(0).WayId() != 2) {
			t.Fatalf("renamed continuation angles=%v", angles)
		}
		// Through the split (not itself a junction) the walk reaches the far junction, about 1.1 km ahead.
		if tc.next == "Main" && (angles[0] != -90 || angles[1] != 90 || math.Abs(float64(branches.At(0).DistanceMeters())-1121) > 5) {
			t.Fatalf("far junction angles=%v distance=%f", angles, branches.At(0).DistanceMeters())
		}
	}
}

func TestNavigationJunctionsIgnoreCrossingRoadByHeading(t *testing.T) {
	tile := namedRoadTile(t, []namedWay{
		{syntheticWay{1, 20, 35.14, -97.9, 35.16, -97.9, false, 2}, "Main"},
		{syntheticWay{2, 20, 35.15005, -97.91, 35.15005, -97.89, false, 2}, "Cross"},
		{syntheticWay{3, 20, 35.16, -97.9, 35.16, -97.89, false, 2}, "East"},
	})
	s := &State{ShadowOnly: true}
	s.Init()
	navigationFix(t, s, tile, 35.15, 20, 10_000_000_000)
	if _, angles := navigationOutput(t, s); len(angles) != 1 || angles[0] != 90 {
		t.Fatalf("angles=%v", angles)
	}
	// Nearly stopped, heading is noise: keep the road chosen while moving.
	navigationFix(t, s, tile, 35.15, 0.5, 10_200_000_000)
	if _, angles := navigationOutput(t, s); len(angles) != 1 {
		t.Fatalf("held angles=%v", angles)
	}
	// Without a prior choice a stopped fix publishes nothing.
	fresh := &State{ShadowOnly: true}
	fresh.Init()
	navigationFix(t, fresh, tile, 35.15, 0.5, 10_000_000_000)
	if _, angles := navigationOutput(t, fresh); len(angles) != 0 {
		t.Fatalf("stopped fix chose a road: %v", angles)
	}
}

func TestNavigationJunctionsStayEmptyForCloseParallelRoads(t *testing.T) {
	tile := namedRoadTile(t, []namedWay{
		{syntheticWay{1, 20, 35.14, -97.9, 35.16, -97.9, false, 2}, ""},
		{syntheticWay{2, 20, 35.14, -97.90005, 35.16, -97.90005, false, 2}, ""},
		{syntheticWay{3, 20, 35.16, -97.9, 35.16, -97.89, false, 2}, ""},
	})
	s := &State{ShadowOnly: true}
	s.Init()
	navigationFix(t, s, tile, 35.15, 20, 10_000_000_000)
	if _, angles := navigationOutput(t, s); len(angles) != 0 {
		t.Fatalf("parallel roads 4.5 m apart resolved: %v", angles)
	}
}
