package maps

import (
	"math"
	"testing"

	"capnproto.org/go/capnp/v3"
	"pfeifer.dev/mapd/cereal/log"
	"pfeifer.dev/mapd/cereal/offline"
	m "pfeifer.dev/mapd/math"
)

func TestBearingAlignmentIsAnUndirectedNonnegativePenalty(t *testing.T) {
	// Roads are represented in either node order. Reversing the same segment
	// or wrapping a GPS heading must not turn a crossing into a score bonus.
	start := m.NewPosition(35.15, -97.9)
	for _, delta := range [][2]float64{{0.01, 0}, {0, 0.01}, {-0.01, 0}, {0, -0.01}, {-0.01, -0.01}} {
		end := m.NewPosition(start.Lat()+delta[0], start.Lon()+delta[1])
		_, seg, err := capnp.NewMessage(capnp.SingleSegment(nil))
		if err != nil {
			t.Fatal(err)
		}
		raw, err := offline.NewRootWay(seg)
		if err != nil {
			t.Fatal(err)
		}
		nodes, err := raw.NewNodes(2)
		if err != nil {
			t.Fatal(err)
		}
		nodes.At(0).SetLatitude(start.Lat())
		nodes.At(0).SetLongitude(start.Lon())
		nodes.At(1).SetLatitude(end.Lat())
		nodes.At(1).SetLongitude(end.Lon())
		way := NewWay(raw)
		location, err := log.NewGpsLocationData(seg)
		if err != nil {
			t.Fatal(err)
		}
		location.SetLatitude((start.Lat() + end.Lat()) / 2)
		location.SetLongitude((start.Lon() + end.Lon()) / 2)
		vec := start.VectorTo(end)
		bearing := vec.Bearing()
		for _, relative := range []float64{0, 30, 90, 150, 180, 270} {
			heading := math.Mod(bearing*180/math.Pi+relative+360, 360)
			location.SetBearingDeg(float32(heading))
			got, err := way.BearingAlignment(location)
			if err != nil {
				t.Fatal(err)
			}
			want := math.Abs(math.Sin(relative * math.Pi / 180))
			if got < 0 || got > 1 || math.Abs(float64(got)-want) > 1e-6 {
				t.Errorf("segment delta=%v heading=%f: alignment=%f want=%f", delta, heading, got, want)
			}
		}
	}
}

func TestWayEntryDirectionForOpenAndClosedWays(t *testing.T) {
	start, end := m.NewPosition(35.15, -97.9), m.NewPosition(35.151, -97.9)
	for _, tc := range []struct {
		name                                 string
		closed, oneWay, fromEnd, wantForward bool
	}{
		{"open start", false, true, false, true},
		{"open end", false, true, true, false},
		{"closed one-way", true, true, false, true},
		{"closed two-way", true, false, false, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			_, seg, err := capnp.NewMessage(capnp.SingleSegment(nil))
			if err != nil {
				t.Fatal(err)
			}
			raw, err := offline.NewRootWay(seg)
			if err != nil {
				t.Fatal(err)
			}
			raw.SetOneWay(tc.oneWay)
			positions := []m.Position{start, end}
			if tc.closed {
				positions = append(positions, start)
			}
			nodes, err := raw.NewNodes(int32(len(positions)))
			if err != nil {
				t.Fatal(err)
			}
			for i, p := range positions {
				nodes.At(i).SetLatitude(p.Lat())
				nodes.At(i).SetLongitude(p.Lon())
			}
			from := start
			if tc.fromEnd {
				from = end
			}
			way := NewWay(raw)
			if got := way.IsForwardFrom(from); got != tc.wantForward {
				t.Fatalf("entry direction: got %v want %v", got, tc.wantForward)
			}
		})
	}
}
