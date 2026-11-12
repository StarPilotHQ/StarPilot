package maps

import (
	"errors"
	"math"
	"sort"
	"strings"

	"pfeifer.dev/mapd/settings"
)

const MaxManagedGroups = maxSnapshotTiles / 64

type ManagedRegion struct {
	Token       string     `json:"token"`
	Name        string     `json:"name"`
	Bounds      [4]float64 `json:"bounds"`
	Groups      int        `json:"groups"`
	Available   bool       `json:"available"`
	Unavailable string     `json:"unavailable,omitempty"`
}

func managedRegion(group, key string, data settings.LocationData) ManagedRegion {
	r := ManagedRegion{Token: group + "." + key, Name: data.FullName}
	b := data.BoundingBox
	values := [4]float64{b.MinLat, b.MinLon, b.MaxLat, b.MaxLon}
	for _, value := range values {
		if math.IsNaN(value) || math.IsInf(value, 0) {
			r.Unavailable = "invalid_bounds"
			return r
		}
	}
	if b.MinLat < -90 || b.MaxLat > 90 || b.MinLon < -180 || b.MaxLon > 180 ||
		b.MinLat >= b.MaxLat || b.MinLon >= b.MaxLon {
		r.Unavailable = "invalid_bounds"
		return r
	}
	if b.MaxLon-b.MinLon > 180 {
		r.Unavailable = "date_line_bounds"
		return r
	}
	r.Bounds = [4]float64{2 * math.Floor(b.MinLat/2), 2 * math.Floor(b.MinLon/2),
		2 * math.Ceil(b.MaxLat/2), 2 * math.Ceil(b.MaxLon/2)}
	if r.Bounds[0] < -90 || r.Bounds[1] < -180 || r.Bounds[2] > 90 || r.Bounds[3] > 180 {
		r.Unavailable = "invalid_bounds"
		return r
	}
	r.Groups = int((r.Bounds[2] - r.Bounds[0]) / 2 * (r.Bounds[3] - r.Bounds[1]) / 2)
	if r.Groups <= 0 || r.Groups > MaxManagedGroups {
		r.Unavailable = "too_large"
		return r
	}
	if _, err := expectedSnapshotTiles(r.Bounds); err != nil {
		r.Unavailable = "invalid_bounds"
		return r
	}
	r.Available = true
	return r
}

// BundledManagedRegions resolves names to approximate rectangular coverage.
// The list is copied from pinned source bytes, never from /data overrides.
func BundledManagedRegions() ([]ManagedRegion, error) {
	menu, err := settings.BundledDownloadMenu()
	if err != nil {
		return nil, err
	}
	result := make([]ManagedRegion, 0, 229)
	for _, group := range []string{"nation", "us_state"} {
		for key, data := range menu[group] {
			if key == "" || strings.Contains(key, ".") || data.FullName == "" {
				return nil, errors.New("invalid bundled region")
			}
			result = append(result, managedRegion(group, key, data))
		}
	}
	sort.Slice(result, func(i, j int) bool { return result[i].Token < result[j].Token })
	return result, nil
}

func ResolveManagedRegion(token string) (ManagedRegion, error) {
	regions, err := BundledManagedRegions()
	if err != nil {
		return ManagedRegion{}, err
	}
	i := sort.Search(len(regions), func(i int) bool { return regions[i].Token >= token })
	if i == len(regions) || regions[i].Token != token || !regions[i].Available {
		return ManagedRegion{}, errors.New("region unavailable")
	}
	return regions[i], nil
}
