package main

import (
	"fmt"
	"testing"
	"time"

	"capnproto.org/go/capnp/v3"
	"pfeifer.dev/mapd/cereal"
	"pfeifer.dev/mapd/cereal/custom"
	"pfeifer.dev/mapd/cereal/log"
	"pfeifer.dev/mapd/cereal/offline"
	"pfeifer.dev/mapd/maps"
	m "pfeifer.dev/mapd/math"
)

type syntheticWay struct {
	id                     int64
	speed                  float64
	lat1, lon1, lat2, lon2 float64
	oneWay                 bool
	lanes                  uint8
}

// The actual offline Capnp representation is packed, decoded, and passed to
// State; tests do not mock GetCurrentWay or BuildMessage.
func packedRoadTileBytes(tb testing.TB, specs []syntheticWay) []byte {
	tb.Helper()
	msg, seg, err := capnp.NewMessage(capnp.SingleSegment(nil))
	if err != nil {
		tb.Fatal(err)
	}
	tile, err := offline.NewRootOffline(seg)
	if err != nil {
		tb.Fatal(err)
	}
	tile.SetMinLat(35)
	tile.SetMaxLat(35.25)
	tile.SetMinLon(-98)
	tile.SetMaxLon(-97.75)
	ways, err := tile.NewWays(int32(len(specs)))
	if err != nil {
		tb.Fatal(err)
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
		nodes, nodeErr := way.NewNodes(2)
		if nodeErr != nil {
			tb.Fatal(nodeErr)
		}
		nodes.At(0).SetLatitude(spec.lat1)
		nodes.At(0).SetLongitude(spec.lon1)
		nodes.At(1).SetLatitude(spec.lat2)
		nodes.At(1).SetLongitude(spec.lon2)
	}
	data, err := msg.MarshalPacked()
	if err != nil {
		tb.Fatal(err)
	}
	return data
}

func packedRoadTile(tb testing.TB, specs []syntheticWay) maps.Offline {
	tb.Helper()
	data := packedRoadTileBytes(tb, specs)
	parsed := maps.ReadOffline(data)
	if !parsed.Loaded {
		tb.Fatal("packed tile rejected")
	}
	return parsed
}

func ambiguitySample(t *testing.T, mono uint64, changed bool) cereal.GpsSample {
	t.Helper()
	return syntheticSample(t, cereal.GpsSourceExternal, mono, 1, changed, true)
}

func TestPackedParallelAndCrossingRoadsPublishNeutralUnknown(t *testing.T) {
	local := syntheticWay{1, 8, 35.1, -97.9, 35.2, -97.9, false, 2}
	parallel := syntheticWay{2, 30, 35.1, -97.8999, 35.2, -97.8999, false, 6}
	crossing := syntheticWay{2, 30, 35.15, -97.95, 35.15, -97.85, false, 6}
	for _, tc := range []struct {
		name  string
		other syntheticWay
	}{{"parallel", parallel}, {"junction crossing", crossing}} {
		t.Run(tc.name, func(t *testing.T) {
			s := State{}
			s.Init()
			s.bootNow = func() uint64 { return 10_100_000_000 }
			tile := packedRoadTile(t, []syntheticWay{local, tc.other})
			s.ProcessGps(ambiguitySample(t, 10_000_000_000, true), true, func(m.Position) (maps.Offline, error) { return tile, nil }, time.Unix(0, 0))
			event, out := decodedOutput(t, &s)
			if event.Valid() || out.RoadStatus() != custom.MapdOut_SampleStatus_unknown || out.WayId() != 0 || out.SpeedLimit() != 0 || s.RoadMatched() || s.CurrentWay.Way.Nodes.Len() != 0 || len(s.NextWays) != 0 {
				t.Fatalf("competing roads leaked match: valid=%v status=%v way=%d limit=%f", event.Valid(), out.RoadStatus(), out.WayId(), out.SpeedLimit())
			}
			_, seg, err := capnp.NewMessage(capnp.SingleSegment(nil))
			if err != nil {
				t.Fatal(err)
			}
			extEvent, err := log.NewRootEvent(seg)
			if err != nil {
				t.Fatal(err)
			}
			extOut, err := extEvent.NewMapdExtendedOut()
			if err != nil {
				t.Fatal(err)
			}
			(&ExtendedState{state: &s}).setPath(extOut)
			path, err := extOut.Path()
			if err != nil || path.Len() != 0 {
				t.Fatalf("competing roads leaked extended path: %v %v", path.Len(), err)
			}
		})
	}
}

func TestPackedStickyRoadAmbiguityAndEmptyNewTileClearState(t *testing.T) {
	local := syntheticWay{1, 8, 35.1, -97.9, 35.2, -97.9, false, 2}
	rival := syntheticWay{2, 30, 35.1, -97.8999, 35.2, -97.8999, false, 6}
	first := packedRoadTile(t, []syntheticWay{local})
	competing := packedRoadTile(t, []syntheticWay{local, rival})
	empty := packedRoadTile(t, nil)
	s := State{}
	s.Init()
	boot := uint64(10_100_000_000)
	s.bootNow = func() uint64 { return boot }
	now := time.Unix(0, 0)
	s.ProcessGps(ambiguitySample(t, 10_000_000_000, true), true, func(m.Position) (maps.Offline, error) { return first, nil }, now)
	if !s.RoadMatched() {
		t.Fatal("initial road did not match")
	}
	s.SpeedLimit.AcceptedLimit = 8
	s.NextHazard.Value = "old"
	s.MapCurveSpeed = 7
	// Swap the loaded tile while preserving the old sticky current way.
	s.Data = competing
	boot += 100_000_000
	s.ProcessGps(ambiguitySample(t, 10_100_000_000, false), true, nil, now.Add(100*time.Millisecond))
	event, out := decodedOutput(t, &s)
	if event.Valid() || out.RoadStatus() != custom.MapdOut_SampleStatus_unknown || out.SpeedLimit() != 0 || s.SpeedLimit.AcceptedLimit != 0 || s.NextHazard.Value != "" || s.MapCurveSpeed != 0 {
		t.Fatalf("sticky match survived competing tile: status=%v limit=%f", out.RoadStatus(), out.SpeedLimit())
	}
	// No candidate in the next loaded tile cannot revive the earlier road.
	s.Data = empty
	boot += 100_000_000
	s.ProcessGps(ambiguitySample(t, 10_200_000_000, false), true, nil, now.Add(200*time.Millisecond))
	event, out = decodedOutput(t, &s)
	if event.Valid() || out.RoadStatus() != custom.MapdOut_SampleStatus_noMatch || out.WayId() != 0 || out.SpeedLimit() != 0 {
		t.Fatalf("empty tile revived road: status=%v way=%d", out.RoadStatus(), out.WayId())
	}
	s.Data = first
	boot += 100_000_000
	s.ProcessGps(ambiguitySample(t, 10_300_000_000, false), true, nil, now.Add(300*time.Millisecond))
	event, out = decodedOutput(t, &s)
	if !event.Valid() || out.RoadStatus() != custom.MapdOut_SampleStatus_matchedLimit || out.WayId() != 1 || out.SpeedLimit() != 8 || s.SpeedLimit.AcceptedLimit != 0 {
		t.Fatalf("new fix did not recover sole road cleanly: status=%v way=%d limit=%f accepted=%f", out.RoadStatus(), out.WayId(), out.SpeedLimit(), s.SpeedLimit.AcceptedLimit)
	}
}

func TestPackedPredictedWayCannotBypassCompetingRoad(t *testing.T) {
	local := syntheticWay{1, 8, 35.1, -97.9, 35.2, -97.9, false, 2}
	rival := syntheticWay{2, 30, 35.1, -97.8999, 35.2, -97.8999, false, 6}
	tile := packedRoadTile(t, []syntheticWay{local, rival})
	s := State{}
	s.Init()
	s.bootNow = func() uint64 { return 10_100_000_000 }
	s.Data = tile
	s.NextWays = []maps.NextWayResult{{Way: tile.Ways.At(1)}}
	s.ProcessGps(ambiguitySample(t, 10_000_000_000, false), true, nil, time.Unix(0, 0))
	event, out := decodedOutput(t, &s)
	if event.Valid() || out.RoadStatus() != custom.MapdOut_SampleStatus_unknown || out.SpeedLimit() != 0 {
		t.Fatalf("predicted way bypassed competing loaded road: status=%v limit=%f", out.RoadStatus(), out.SpeedLimit())
	}
}

func TestPackedUniqueDirectionalAndDuplicateWayID(t *testing.T) {
	north := syntheticWay{1, 8, 35.1, -97.9, 35.2, -97.9, true, 2}
	south := syntheticWay{2, 30, 35.2, -97.9, 35.1, -97.9, true, 6}
	for _, tc := range []struct {
		name string
		ways []syntheticWay
		want float32
	}{
		{"opposite one way excluded", []syntheticWay{north, south}, 8},
		{"duplicate same ID", []syntheticWay{north, north}, 8},
		{"unique no posted limit", []syntheticWay{{1, 0, 35.1, -97.9, 35.2, -97.9, false, 2}}, 0},
	} {
		t.Run(tc.name, func(t *testing.T) {
			s := State{}
			s.Init()
			s.bootNow = func() uint64 { return 10_100_000_000 }
			tile := packedRoadTile(t, tc.ways)
			s.ProcessGps(ambiguitySample(t, 10_000_000_000, true), true, func(m.Position) (maps.Offline, error) { return tile, nil }, time.Unix(0, 0))
			event, out := decodedOutput(t, &s)
			status := custom.MapdOut_SampleStatus_matchedLimit
			if tc.want == 0 {
				status = custom.MapdOut_SampleStatus_matchedNoLimit
			}
			if !event.Valid() || out.RoadStatus() != status || out.WayId() != 1 || out.SpeedLimit() != tc.want {
				t.Fatalf("unique directional road lost: status=%v way=%d limit=%f", out.RoadStatus(), out.WayId(), out.SpeedLimit())
			}
		})
	}
}

func TestPackedSoleBroadEnvelopeWayDoesNotWidenMatch(t *testing.T) {
	// About 18 m east of the fix: plausible under the conservative 2x
	// ambiguity envelope, but outside this new road's normal match threshold.
	road := syntheticWay{1, 8, 35.1, -97.8998, 35.2, -97.8998, false, 2}
	tile := packedRoadTile(t, []syntheticWay{road})
	s := State{}
	s.Init()
	s.bootNow = func() uint64 { return 10_100_000_000 }
	s.ProcessGps(ambiguitySample(t, 10_000_000_000, true), true,
		func(m.Position) (maps.Offline, error) { return tile, nil }, time.Unix(0, 0))
	event, out := decodedOutput(t, &s)
	if event.Valid() || out.RoadStatus() != custom.MapdOut_SampleStatus_noMatch || out.WayId() != 0 || out.SpeedLimit() != 0 {
		t.Fatalf("broad-only candidate widened match: status=%v way=%d limit=%f", out.RoadStatus(), out.WayId(), out.SpeedLimit())
	}
}

func BenchmarkPlausibleLoadedWays(b *testing.B) {
	for _, count := range []int{64, 256, 1024} {
		b.Run(fmt.Sprintf("ways_%d", count), func(b *testing.B) {
			specs := make([]syntheticWay, count)
			specs[0] = syntheticWay{1, 8, 35.1, -97.9, 35.2, -97.9, false, 2}
			for i := 1; i < count; i++ {
				specs[i] = syntheticWay{int64(i + 1), 13, 35.1, -97.7 + float64(i)*0.00001, 35.2, -97.7 + float64(i)*0.00001, false, 2}
			}
			tile := packedRoadTile(b, specs)
			_, seg, err := capnp.NewMessage(capnp.SingleSegment(nil))
			if err != nil {
				b.Fatal(err)
			}
			location, err := log.NewRootGpsLocationData(seg)
			if err != nil {
				b.Fatal(err)
			}
			location.SetLatitude(35.15)
			location.SetLongitude(-97.9)
			location.SetBearingDeg(0)
			location.SetHorizontalAccuracy(5)
			b.ResetTimer()
			for i := 0; i < b.N; i++ {
				if got := plausibleLoadedWays(&tile, location); len(got) != 1 {
					b.Fatalf("unexpected candidates %d", len(got))
				}
			}
		})
	}
}
