package main

import (
	"crypto/sha256"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"capnproto.org/go/capnp/v3"
	"pfeifer.dev/mapd/cereal"
	"pfeifer.dev/mapd/cereal/custom"
	"pfeifer.dev/mapd/cereal/offline"
	"pfeifer.dev/mapd/maps"
	m "pfeifer.dev/mapd/math"
)

func previewState(t *testing.T, specs []syntheticWay, index int, forward bool, latitude float64) State {
	t.Helper()
	tile := packedRoadTile(t, specs)
	return State{ShadowOnly: true, SnapshotID: "verified-snapshot", Data: tile,
		Position: m.NewPosition(latitude, -97.9), MatchedLimitMps: float32(specs[index].speed),
		CurrentWay: CurrentWay{Way: tile.Ways.At(index), OnWay: maps.OnWayResult{IsForward: forward}, SelectionType: custom.WaySelectionType_current}}
}

func previewRoads() []syntheticWay {
	return []syntheticWay{
		{1, 20, 35.1, -97.9, 35.101, -97.9, false, 2},
		{2, 20, 35.101, -97.9, 35.102, -97.9, false, 2},
		{3, 10, 35.102, -97.9, 35.103, -97.9, false, 2},
	}
}

func TestPreviewRawDistanceAndStableBoundaryAcrossApproachWays(t *testing.T) {
	s := previewState(t, previewRoads(), 0, true, 35.1005)
	a := s.previewLimit()
	s = previewState(t, previewRoads(), 1, true, 35.1015)
	b := s.previewLimit()
	if a.Speed != 10 || a.WayID != 3 || a.BoundaryID == "" || a.BoundaryID != b.BoundaryID || math.Abs(float64(a.Distance-b.Distance)-111.2) > 1 {
		t.Fatalf("unexpected road preview: %+v %+v", a, b)
	}
	s.Position = m.NewPosition(35.1014, -97.9)
	if s.previewLimit().Distance <= b.Distance {
		t.Fatal("raw distance was held at a previous minimum")
	}
	s.SnapshotID = "changed-snapshot"
	if s.previewLimit().BoundaryID == b.BoundaryID {
		t.Fatal("boundary identity survived a different snapshot")
	}
}

func TestPreviewDirectionUnknownLimitsBranchesAndHorizon(t *testing.T) {
	s := previewState(t, previewRoads(), 2, false, 35.1025)
	if p := s.previewLimit(); p.Speed != 20 || p.WayID != 2 || p.Distance <= 0 {
		t.Fatalf("reverse traversal failed: %+v", p)
	}
	for _, mode := range []string{"branch", "unknown", "oneway", "horizon", "unqualified", "unverified"} {
		t.Run(mode, func(t *testing.T) {
			roads := previewRoads()
			index, forward, latitude := 0, true, 35.1005
			switch mode {
			case "branch":
				roads = append(roads, syntheticWay{4, 30, 35.101, -97.9, 35.102, -97.899, false, 2})
			case "unknown":
				roads[1].speed = 0
			case "oneway":
				roads[1].oneWay = true
				index, forward, latitude = 2, false, 35.1025
			case "horizon":
				roads[0].lat1 = 35.09
				latitude = 35.091
			}
			s := previewState(t, roads, index, forward, latitude)
			if mode == "unqualified" {
				s.CurrentWay.SelectionType = custom.WaySelectionType_possible
			}
			if mode == "unverified" {
				s.SnapshotID = ""
			}
			if p := s.previewLimit(); p != (LimitPreview{}) {
				t.Fatalf("uncertain preview escaped: %+v", p)
			}
		})
	}
}

func TestPreviewClosedWayRequiresOneLegalDirection(t *testing.T) {
	for _, tc := range []struct {
		name                        string
		oneWay, branch, wantPreview bool
	}{
		{"one-way loop", true, false, true},
		{"two-way loop", false, false, false},
		{"two-way loop plus continuation", false, true, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			roads := []syntheticWay{
				{1, 20, 35.1, -97.9, 35.101, -97.9, true, 2},
				{2, 10, 35.101, -97.9, 35.102, -97.9, tc.oneWay, 2},
			}
			if tc.branch {
				roads = append(roads, syntheticWay{3, 15, 35.101, -97.9, 35.102, -97.899, false, 2})
			}
			decoded, err := capnp.UnmarshalPacked(packedRoadTileBytes(t, roads))
			if err != nil {
				t.Fatal(err)
			}
			root, err := decoded.Root()
			if err != nil {
				t.Fatal(err)
			}
			msg, _, err := capnp.NewMessage(capnp.SingleSegment(nil))
			if err != nil {
				t.Fatal(err)
			}
			if err := msg.SetRoot(root); err != nil {
				t.Fatal(err)
			}
			raw, err := offline.ReadRootOffline(msg)
			if err != nil {
				t.Fatal(err)
			}
			ways, err := raw.Ways()
			if err != nil {
				t.Fatal(err)
			}
			loop := ways.At(1)
			loop.SetMaxLon(-97.898)
			nodes, err := loop.NewNodes(4)
			if err != nil {
				t.Fatal(err)
			}
			for i, p := range [][2]float64{{35.101, -97.9}, {35.102, -97.9}, {35.102, -97.899}, {35.101, -97.9}} {
				nodes.At(i).SetLatitude(p[0])
				nodes.At(i).SetLongitude(p[1])
			}
			data, err := msg.MarshalPacked()
			if err != nil {
				t.Fatal(err)
			}
			tile := maps.ReadOffline(data)
			if !tile.Loaded {
				t.Fatal("closed road tile rejected")
			}
			s := State{ShadowOnly: true, SnapshotID: "verified-snapshot", Data: tile,
				Position: m.NewPosition(35.1005, -97.9), MatchedLimitMps: 20,
				CurrentWay: CurrentWay{Way: tile.Ways.At(0), OnWay: maps.OnWayResult{IsForward: true}, SelectionType: custom.WaySelectionType_current}}
			p := s.previewLimit()
			if tc.wantPreview {
				if p.Speed != 10 || p.WayID != 2 || p.Distance <= 0 || p.BoundaryID == "" {
					t.Fatalf("legal loop preview missing: %+v", p)
				}
			} else if p != (LimitPreview{}) {
				t.Fatalf("ambiguous loop produced a preview: %+v", p)
			}
		})
	}
}

func TestShadowPreviewSharesFixFreshnessWithoutControlOutputs(t *testing.T) {
	roads := []syntheticWay{{1, 20, 35.149, -97.9, 35.151, -97.9, false, 2}, {2, 10, 35.151, -97.9, 35.153, -97.9, false, 2}}
	data := packedRoadTileBytes(t, roads)
	id := strings.Repeat("a", 64)
	dir := filepath.Join(t.TempDir(), id)
	area := maps.Area{Box: m.Box{MinPos: m.NewPosition(35, -98), MaxPos: m.NewPosition(35.25, -97.75)}}
	path := maps.GenerateBoundsFileName(area, maps.OfflineSettings{OutputDirectory: dir})
	if err := os.MkdirAll(filepath.Dir(path), 0755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, data, 0644); err != nil {
		t.Fatal(err)
	}
	rel, err := filepath.Rel(dir, path)
	if err != nil {
		t.Fatal(err)
	}
	load := maps.SnapshotLoader(dir, maps.SnapshotReceipt{Tiles: []maps.SnapshotTile{{Path: rel, Size: int64(len(data)), SHA256: fmt.Sprintf("%x", sha256.Sum256(data))}}})
	s := State{ShadowOnly: true, SnapshotID: id}
	s.Init()
	boot := uint64(10_100_000_000)
	s.bootNow = func() uint64 { return boot }
	sample := syntheticSample(t, cereal.GpsSourceExternal, 10_000_000_000, 1, true, true)
	s.ProcessGps(sample, true, load, time.Unix(0, 0))
	sample.SourceChanged, sample.FixMonoTime = false, 10_050_000_000
	s.ProcessGps(sample, true, load, time.Unix(0, 1))
	event, output := decodedOutput(t, &s)
	boundary, _ := output.PreviewBoundaryId()
	if !event.Valid() || output.SpeedLimit() != 20 || output.PreviewSpeedLimit() != 10 || boundary == "" || output.PreviewDistance() <= 0 || output.NextSpeedLimit() != 0 || output.SuggestedSpeed() != 0 {
		t.Fatalf("shadow preview: valid=%v status=%v way=%v limit=%v preview=%+v", event.Valid(), output.RoadStatus(), output.WaySelectionType(), output.SpeedLimit(), s.LimitPreview)
	}
	boot = 10_600_000_000
	event, output = decodedOutput(t, &s)
	boundary, _ = output.PreviewBoundaryId()
	if event.Valid() || output.PreviewSpeedLimit() != 0 || boundary != "" {
		t.Fatal("expired GPS retained an upcoming limit")
	}
}
