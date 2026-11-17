package maps

import (
	"bytes"
	"context"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	m "pfeifer.dev/mapd/math"
)

func TestManagedDownloadsAccumulateCoverage(t *testing.T) {
	root := t.TempDir()
	archives := map[string][]byte{
		"/offline/0/0.tar.gz":   snapshotGroupArchive(t, 0, 0),
		"/offline/0/8.tar.gz":   snapshotGroupArchive(t, 0, 8),
		"/offline/-4/-8.tar.gz": snapshotGroupArchive(t, -4, -8),
	}
	var requests []string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requests = append(requests, r.URL.Path)
		body, ok := archives[r.URL.Path]
		if !ok {
			w.WriteHeader(http.StatusNotFound)
			return
		}
		_, _ = w.Write(body)
	}))
	defer server.Close()
	client := &http.Client{Transport: localArchiveTransport{server}}
	prepare := func(bounds [4]float64) string {
		t.Helper()
		o := fetchOptions(root, bounds, 1<<20)
		o.AddToCurrent, o.PrepareOnly, o.MaxNewDiskBytes = true, true, 16<<20
		id, err := FetchAndAdmitSnapshot(context.Background(), o, client)
		if err != nil {
			t.Fatal(err)
		}
		return id
	}
	first := prepare([4]float64{0, 0, 2, 2})
	if err := SelectPreparedSnapshot(context.Background(), root, first, ""); err != nil {
		t.Fatal(err)
	}
	oldDir, oldReceipt, err := ResolveSnapshot(root)
	if err != nil || oldReceipt.SourceKind != "provided-group-archives" || len(oldReceipt.Tiles) != 64 {
		t.Fatalf("legacy first snapshot: %+v %v", oldReceipt, err)
	}
	second := prepare([4]float64{0, 8, 2, 10})
	selected, _, err := ResolveSnapshot(root)
	if err != nil || filepath.Base(selected) != first {
		t.Fatalf("preparation replaced the active map: %s %v", selected, err)
	}
	if err := SelectPreparedSnapshot(context.Background(), root, second, ""); err == nil {
		t.Fatal("combined snapshot bypassed selection conflict")
	}
	if err := SelectPreparedSnapshot(context.Background(), root, second, first); err != nil {
		t.Fatal(err)
	}
	dir, receipt, err := ResolveSnapshot(root)
	if err != nil || len(receipt.Tiles) != 128 || len(receipt.Inputs) != 2 {
		t.Fatalf("second region did not preserve first: %+v %v", receipt, err)
	}
	loader := SnapshotLoader(dir, receipt)
	for _, lon := range []float64{.1, 8.1} {
		if _, err := loader(m.NewPosition(.1, lon)); err != nil {
			t.Fatalf("downloaded region missing at %f: %v", lon, err)
		}
	}
	if _, err := loader(m.NewPosition(.1, 4.1)); err == nil {
		t.Fatal("gap between downloads incorrectly claimed as covered")
	}
	a, err := os.Stat(filepath.Join(oldDir, oldReceipt.Tiles[0].Path))
	if err != nil {
		t.Fatal(err)
	}
	b, err := os.Stat(filepath.Join(dir, oldReceipt.Tiles[0].Path))
	if err != nil || !os.SameFile(a, b) {
		t.Fatalf("saved tile was duplicated instead of reused: %v", err)
	}
	third := prepare([4]float64{-4, -8, -2, -6})
	if err := SelectPreparedSnapshot(context.Background(), root, third, second); err != nil {
		t.Fatal(err)
	}
	// Refresh an overlapping group without duplicating its cells or losing others.
	archives["/offline/0/8.tar.gz"][4] = 1
	refreshed := prepare([4]float64{0, 8, 2, 10})
	if err := SelectPreparedSnapshot(context.Background(), root, refreshed, third); err != nil {
		t.Fatal(err)
	}
	dir, receipt, err = ResolveSnapshot(root)
	if err != nil || len(receipt.Tiles) != 192 || len(receipt.Inputs) != 3 {
		t.Fatalf("overlapping refresh changed union size: %+v %v", receipt, err)
	}
	loader = SnapshotLoader(dir, receipt)
	for _, pos := range [][2]float64{{.1, .1}, {.1, 8.1}, {-3.9, -7.9}} {
		if _, err := loader(m.NewPosition(pos[0], pos[1])); err != nil {
			t.Fatal(err)
		}
	}
	if strings.Join(requests, ",") != "/offline/0/0.tar.gz,/offline/0/8.tar.gz,/offline/-4/-8.tar.gz,/offline/0/8.tar.gz" {
		t.Fatalf("old coverage was downloaded again: %v", requests)
	}
	before, err := os.ReadFile(filepath.Join(root, "current.json"))
	if err != nil {
		t.Fatal(err)
	}
	for _, failure := range []string{"http", "canceled", "budget", "corrupt", "symlink"} {
		t.Run(failure, func(t *testing.T) {
			o := fetchOptions(root, [4]float64{0, 0, 2, 2}, 1<<20)
			o.AddToCurrent, o.PrepareOnly, o.MaxNewDiskBytes = true, true, 16<<20
			ctx := context.Background()
			switch failure {
			case "http":
				o.Bounds = [4]float64{0, 20, 2, 22}
			case "canceled":
				var cancel context.CancelFunc
				ctx, cancel = context.WithCancel(ctx)
				cancel()
			case "budget":
				o.MaxNewDiskBytes = 1
			case "corrupt", "symlink":
				path := filepath.Join(dir, receipt.Tiles[0].Path)
				original, err := os.ReadFile(path)
				if err != nil {
					t.Fatal(err)
				}
				if failure == "corrupt" {
					err = os.WriteFile(path, []byte("corrupt"), 0o600)
				} else {
					err = os.Rename(path, path+".original")
					if err == nil {
						err = os.Symlink(path+".original", path)
					}
				}
				if err != nil {
					t.Fatal(err)
				}
				defer func() {
					if failure == "symlink" {
						_ = os.Remove(path)
						_ = os.Rename(path+".original", path)
					} else {
						_ = os.WriteFile(path, original, 0o600)
					}
				}()
			}
			if _, err := FetchAndAdmitSnapshot(ctx, o, client); err == nil {
				t.Fatal("invalid addition succeeded")
			}
			after, err := os.ReadFile(filepath.Join(root, "current.json"))
			if err != nil || !bytes.Equal(before, after) {
				t.Fatalf("failed addition changed selection: %v", err)
			}
		})
	}
}

func TestCombinedSnapshotRejectsMalformedCoverage(t *testing.T) {
	for _, name := range []string{"../0.tar.gz", "0/0/0.tar.gz", "0/01.tar.gz", "1/0.tar.gz", "90/0.tar.gz", "0/180.tar.gz"} {
		_, err := receiptSnapshotTiles(SnapshotReceipt{SourceKind: "combined-group-archives", Inputs: []SnapshotInput{{Name: name}}})
		if err == nil {
			t.Fatalf("invalid combined input accepted: %s", name)
		}
	}
	for _, inputs := range [][]SnapshotInput{{{Name: "0/0.tar.gz"}, {Name: "0/0.tar.gz"}},
		{{Name: "0/8.tar.gz"}, {Name: "0/0.tar.gz"}}, make([]SnapshotInput, MaxManagedGroups+1)} {
		if _, err := receiptSnapshotTiles(SnapshotReceipt{SourceKind: "combined-group-archives", Inputs: inputs}); err == nil {
			t.Fatal("duplicate, unsorted or oversized combined coverage accepted")
		}
	}
	if _, err := receiptSnapshotTiles(SnapshotReceipt{SourceKind: "combined-group-archives", Bounds: [4]float64{0, 0, 2, 10},
		Inputs: []SnapshotInput{{Name: "0/0.tar.gz"}}}); err == nil {
		t.Fatal("incorrect coverage extent accepted")
	}
}
