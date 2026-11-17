package maps

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
)

// Combined snapshots declare only the archive groups actually downloaded,
// rather than treating the rectangle between separate regions as covered.
func receiptSnapshotTiles(receipt SnapshotReceipt) ([]string, error) {
	if receipt.SourceKind != "combined-group-archives" {
		return expectedSnapshotTiles(receipt.Bounds)
	}
	if len(receipt.Inputs) == 0 || len(receipt.Inputs) > MaxManagedGroups {
		return nil, errors.New("combined snapshot exceeds finite group budget")
	}
	var extent [4]float64
	expected := make([]string, 0, len(receipt.Inputs)*64)
	previous := ""
	for i, input := range receipt.Inputs {
		parts := strings.Split(input.Name, "/")
		if len(parts) != 2 || !strings.HasSuffix(parts[1], ".tar.gz") || i > 0 && input.Name <= previous {
			return nil, errors.New("combined archive inputs are not unique and ordered")
		}
		lat, latErr := strconv.Atoi(parts[0])
		lon, lonErr := strconv.Atoi(strings.TrimSuffix(parts[1], ".tar.gz"))
		if latErr != nil || lonErr != nil || lat < -90 || lat > 88 || lon < -180 || lon > 178 ||
			lat%2 != 0 || lon%2 != 0 || input.Name != fmt.Sprintf("%d/%d.tar.gz", lat, lon) {
			return nil, errors.New("combined archive group is invalid")
		}
		bounds := [4]float64{float64(lat), float64(lon), float64(lat + 2), float64(lon + 2)}
		tiles, err := expectedSnapshotTiles(bounds)
		if err != nil {
			return nil, err
		}
		expected = append(expected, tiles...)
		if i == 0 {
			extent = bounds
		} else {
			extent = unionSnapshotBounds(extent, bounds)
		}
		previous = input.Name
	}
	if extent != receipt.Bounds {
		return nil, errors.New("combined snapshot extent does not match its groups")
	}
	sort.Strings(expected)
	return expected, nil
}

func unionSnapshotBounds(a, b [4]float64) [4]float64 {
	for i := 0; i < 2; i++ {
		if b[i] < a[i] {
			a[i] = b[i]
		}
		if b[i+2] > a[i+2] {
			a[i+2] = b[i+2]
		}
	}
	return a
}

func addSelectedSnapshot(ctx context.Context, root, stage string, receipt *SnapshotReceipt) error {
	if _, err := os.Lstat(filepath.Join(root, "current.json")); os.IsNotExist(err) {
		return nil
	} else if err != nil {
		return err
	}
	dir, saved, err := ResolveSnapshot(root)
	if err != nil {
		return err
	}
	if receipt.SourceKind != "provided-group-archives" ||
		(saved.SourceKind != "provided-group-archives" && saved.SourceKind != "combined-group-archives") ||
		saved.Overlap != receipt.Overlap {
		return errors.New("saved snapshot cannot be combined with this download")
	}
	inputs := make(map[string]SnapshotInput, len(saved.Inputs)+len(receipt.Inputs))
	for _, input := range saved.Inputs {
		inputs[input.Name] = input
	}
	for _, input := range receipt.Inputs {
		inputs[input.Name] = input
	}
	receipt.Inputs = make([]SnapshotInput, 0, len(inputs))
	for _, input := range inputs {
		receipt.Inputs = append(receipt.Inputs, input)
	}
	sort.Slice(receipt.Inputs, func(i, j int) bool { return receipt.Inputs[i].Name < receipt.Inputs[j].Name })
	receipt.SourceKind = "combined-group-archives"
	receipt.Bounds = unionSnapshotBounds(saved.Bounds, receipt.Bounds)
	expected, err := receiptSnapshotTiles(*receipt)
	if err != nil {
		return err
	}
	declared := make(map[string]SnapshotTile, len(saved.Tiles)+len(receipt.Tiles))
	for _, tile := range receipt.Tiles {
		declared[tile.Path] = tile
	}
	for _, tile := range saved.Tiles {
		if err := ctx.Err(); err != nil {
			return err
		}
		if _, updated := declared[tile.Path]; updated {
			continue
		}
		source, err := snapshotTilePath(dir, tile.Path)
		if err != nil {
			return err
		}
		target := filepath.Join(stage, tile.Path)
		if err := os.MkdirAll(filepath.Dir(target), 0o700); err != nil {
			return err
		}
		if err := os.Link(source, target); err != nil {
			return err
		}
		declared[tile.Path] = tile
	}
	tiles, err := inspectSnapshotTiles(stage, receipt.Overlap, expected)
	if err != nil {
		return err
	}
	for _, tile := range tiles {
		if tile != declared[tile.Path] {
			return errors.New("saved snapshot tile changed while adding coverage")
		}
	}
	receipt.Tiles = tiles
	return ctx.Err()
}
