package maps

import (
	"math"
	"os"
	"path/filepath"
	"testing"

	m "pfeifer.dev/mapd/math"
)

func TestGeneratorPreservesFixedOneWayDirectionAndSpeedTags(t *testing.T) {
	for _, tc := range []struct {
		name, oneWay, highway, junction string
		wantOneWay, reverse             bool
	}{
		{"ordinary", "", "residential", "", false, false},
		{"yes", "yes", "residential", "", true, false},
		{"numeric", "1", "residential", "", true, false},
		{"reverse", "-1", "residential", "", true, true},
		{"roundabout", "", "residential", "roundabout", true, false},
		{"motorway", "", "motorway", "", true, false},
		{"roundabout explicit no", "no", "residential", "roundabout", false, false},
		{"roundabout explicit zero", "0", "residential", "roundabout", false, false},
		{"motorway explicit no", "no", "motorway", "", false, false},
		{"motorway explicit zero", "0", "motorway", "", false, false},
		{"motorway link", "", "motorway_link", "", false, false},
		{"reversible", "reversible", "residential", "roundabout", false, false},
		{"alternating", "alternating", "motorway", "", false, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			forwardRule, backwardRule := "15 @ (Mo-Fr 08:00-09:00)", "25 @ (Sa 10:00-11:00)"
			tags := []string{"highway", tc.highway, "maxspeed", "30 mph",
				"maxspeed:forward", "10", "maxspeed:backward", "20",
				"maxspeed:forward:conditional", forwardRule, "maxspeed:backward:conditional", backwardRule}
			if tc.oneWay != "" {
				tags = append(tags, "oneway", tc.oneWay)
			}
			if tc.junction != "" {
				tags = append(tags, "junction", tc.junction)
			}
			root := t.TempDir()
			pbf := filepath.Join(root, "road.osm.pbf")
			if err := os.WriteFile(pbf, syntheticPBF(tags...), 0600); err != nil {
				t.Fatal(err)
			}
			output := filepath.Join(root, "tiles")
			if err := GenerateOffline(OfflineSettings{InputFile: pbf, OutputDirectory: output, Overlap: .001,
				Box: m.Box{MinPos: m.NewPosition(35, -98), MaxPos: m.NewPosition(35.25, -97.75)}}); err != nil {
				t.Fatal(err)
			}
			tile, err := FindWaysAroundPositionIn(m.NewPosition(35.15, -97.9), output)
			if err != nil || !tile.Loaded || tile.Ways.Len() != 1 {
				t.Fatalf("generated road not loaded: %v", err)
			}
			way := tile.Ways.At(0)
			if way.Nodes.Len() != 2 {
				t.Fatalf("generated road has %d nodes", way.Nodes.Len())
			}
			first, last := 35.1, 35.2
			forwardSpeed, backwardSpeed := 10./3.6, 20./3.6
			if tc.reverse {
				first, last = last, first
				forwardSpeed, backwardSpeed = backwardSpeed, forwardSpeed
				forwardRule, backwardRule = backwardRule, forwardRule
			}
			firstNode, lastNode := way.Nodes.At(0), way.Nodes.At(1)
			if way.Id() != 42 || way.OneWay() != tc.wantOneWay ||
				math.Abs(firstNode.Lat()-first) > 1e-9 || math.Abs(lastNode.Lat()-last) > 1e-9 ||
				math.Abs(way.MaxSpeed()-13.4112) > 1e-6 ||
				math.Abs(way.MaxSpeedForward()-forwardSpeed) > 1e-5 || math.Abs(way.MaxSpeedBackward()-backwardSpeed) > 1e-5 ||
				way.ConditionalMaxSpeedRaw(true) != forwardRule || way.ConditionalMaxSpeedRaw(false) != backwardRule {
				t.Fatalf("generated road lost direction or speed metadata: oneWay=%v forward=%v backward=%v",
					way.OneWay(), way.MaxSpeedForward(), way.MaxSpeedBackward())
			}
		})
	}
}
