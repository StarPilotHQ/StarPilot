package maps

import (
	"testing"

	"pfeifer.dev/mapd/settings"
)

func TestBundledManagedRegionCoverage(t *testing.T) {
	regions, err := BundledManagedRegions()
	if err != nil {
		t.Fatal(err)
	}
	if len(regions) != 242 {
		t.Fatalf("bundled catalog changed: %d", len(regions))
	}
	available := 0
	for _, region := range regions {
		if region.Available {
			available++
			if region.Groups < 1 || region.Groups > MaxManagedGroups || region.Unavailable != "" {
				t.Fatalf("invalid available region: %+v", region)
			}
		}
	}
	if available != 238 {
		t.Fatalf("review supported-region count changed: %d", available)
	}
	for token, groups := range map[string]int{"us_state.CA": 36, "us_state.TX": 56, "us_state.IL": 12, "nation.CA": 765, "nation.JP": 72, "nation.US": 390} {
		region, err := ResolveManagedRegion(token)
		if err != nil || region.Groups != groups {
			t.Fatalf("%s: %+v %v", token, region, err)
		}
	}
	for _, token := range []string{"us_state.AK", "nation.FJ", "nation.RU", "nation.AQ", "us_state.CA.extra", "nation."} {
		if _, err := ResolveManagedRegion(token); err == nil {
			t.Fatalf("unsupported region %s accepted", token)
		}
	}
}

// Province coverage comes from the pinned Geofabrik extract envelopes. Every
// envelope must survive outward grid alignment without losing any edge.
func TestCanadianProvinceCoverage(t *testing.T) {
	menu, err := settings.BundledDownloadMenu()
	if err != nil {
		t.Fatal(err)
	}
	if len(menu["ca_province"]) != 13 {
		t.Fatal("expected ten provinces and three territories")
	}
	for key, data := range menu["ca_province"] {
		region, err := ResolveManagedRegion("ca_province." + key)
		if err != nil || !region.Available {
			t.Fatalf("%s unavailable: %v", key, err)
		}
		b := data.BoundingBox
		if region.Bounds[0] > b.MinLat || region.Bounds[1] > b.MinLon || region.Bounds[2] < b.MaxLat || region.Bounds[3] < b.MaxLon {
			t.Fatalf("coverage clipped: %s", key)
		}
		if region.Groups >= 765 {
			t.Fatalf("province not smaller than Canada: %s", key)
		}
	}
	for _, token := range []string{"ca_province.CA", "ca_province.ON.extra", "ca_province."} {
		if _, err := ResolveManagedRegion(token); err == nil {
			t.Fatalf("unknown token accepted: %s", token)
		}
	}
}
