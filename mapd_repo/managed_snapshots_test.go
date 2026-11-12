package main

import (
	"archive/tar"
	"bytes"
	"compress/gzip"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strconv"
	"testing"

	"pfeifer.dev/mapd/maps"
)

func managedFixtureArchive(t *testing.T, bounds [4]float64) []byte {
	t.Helper()
	root := t.TempDir()
	_, err := maps.AdmitSnapshot(context.Background(), maps.AdmitOptions{Root: root, Bounds: bounds, Overlap: .001,
		PBF:            filepath.Join("testdata", "synthetic_snapshot.osm.pbf"),
		SourceRevision: "fixture", UpstreamRevision: "fixture", SourceDigest: "fixture"})
	if err != nil {
		t.Fatal(err)
	}
	selected, receipt, err := maps.ResolveSnapshot(root)
	if err != nil || len(receipt.Tiles) != 64 {
		t.Fatalf("fixture generation invalid: %v %+v", err, receipt)
	}
	var buffer bytes.Buffer
	gz := gzip.NewWriter(&buffer)
	tw := tar.NewWriter(gz)
	for _, tile := range receipt.Tiles {
		data, err := os.ReadFile(filepath.Join(selected, tile.Path))
		if err != nil {
			t.Fatal(err)
		}
		if err := tw.WriteHeader(&tar.Header{Name: "offline/" + tile.Path, Typeflag: tar.TypeReg, Mode: 0o644,
			Size: int64(len(data))}); err != nil {
			t.Fatal(err)
		}
		if _, err := tw.Write(data); err != nil {
			t.Fatal(err)
		}
	}
	if err := tw.Close(); err != nil {
		t.Fatal(err)
	}
	if err := gz.Close(); err != nil {
		t.Fatal(err)
	}
	return buffer.Bytes()
}

func TestManagedSnapshotCatalogPrepareSelect(t *testing.T) {
	region, err := maps.ResolveManagedRegion("nation.CY")
	if err != nil || region.Bounds != [4]float64{34, 32, 36, 34} {
		t.Fatalf("reviewed one-group token changed: %+v %v", region, err)
	}
	archive := managedFixtureArchive(t, region.Bounds)
	requests := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requests++
		if r.URL.Path != "/offline/34/32.tar.gz" {
			t.Errorf("wrong managed region URL: %s", r.URL.Path)
		}
		_, _ = w.Write(archive)
	}))
	defer server.Close()
	client := &http.Client{Transport: fixedOriginTestTransport{server}}
	root := t.TempDir()
	var output bytes.Buffer
	args := []string{"--offline-root", root, "--region", "nation.CY", "--max-transfer-bytes", strconv.Itoa(len(archive)),
		"--max-new-disk-bytes", strconv.Itoa(16 << 20)}
	if err := runSnapshotManagedPrepareContext(context.Background(), args, client, &output); err != nil {
		t.Fatal(err)
	}
	if requests != 1 {
		t.Fatalf("unexpected request count: %d", requests)
	}
	if _, err := os.Stat(filepath.Join(root, "current.json")); !os.IsNotExist(err) {
		t.Fatalf("prepare selected a generation: %v", err)
	}
	dec := json.NewDecoder(&output)
	var records []struct {
		Phase      string `json:"phase"`
		Generation string `json:"generation"`
		Completed  int    `json:"completedGroups"`
	}
	for dec.More() {
		var record struct {
			Phase      string `json:"phase"`
			Generation string `json:"generation"`
			Completed  int    `json:"completedGroups"`
		}
		if err := dec.Decode(&record); err != nil {
			t.Fatal(err)
		}
		records = append(records, record)
	}
	if len(records) != 4 || records[0].Phase != "transferring" || records[1].Completed != 1 ||
		records[2].Phase != "validating" || records[3].Phase != "prepared" || len(records[3].Generation) != 64 {
		t.Fatalf("wrong managed progress: %+v", records)
	}
	output.Reset()
	if err := runSnapshotSelectContext(context.Background(), []string{"--offline-root", root, "--generation", records[3].Generation}, &output); err != nil {
		t.Fatal(err)
	}
	selected, _, err := maps.ResolveSnapshot(root)
	if err != nil || filepath.Base(selected) != records[3].Generation {
		t.Fatalf("selection failed: %v %s", err, selected)
	}
}

func TestManagedSnapshotCLIAddsSecondRegion(t *testing.T) {
	root := t.TempDir()
	bodies := make(map[string][]byte)
	for _, token := range []string{"nation.CY", "nation.BN"} {
		region, err := maps.ResolveManagedRegion(token)
		if err != nil || region.Groups != 1 {
			t.Fatalf("one-group fixture: %+v %v", region, err)
		}
		path := "/offline/" + strconv.Itoa(int(region.Bounds[0])) + "/" + strconv.Itoa(int(region.Bounds[1])) + ".tar.gz"
		bodies[path] = managedFixtureArchive(t, region.Bounds)
	}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, ok := bodies[r.URL.Path]
		if !ok {
			w.WriteHeader(http.StatusNotFound)
			return
		}
		_, _ = w.Write(body)
	}))
	defer server.Close()
	client := &http.Client{Transport: fixedOriginTestTransport{server}}
	previous := ""
	for i, token := range []string{"nation.CY", "nation.BN"} {
		var output bytes.Buffer
		args := []string{"--offline-root", root, "--region", token, "--max-transfer-bytes", "1048576", "--max-new-disk-bytes", "16777216"}
		if err := runSnapshotManagedPrepareContext(context.Background(), args, client, &output); err != nil {
			t.Fatal(err)
		}
		decoder := json.NewDecoder(&output)
		var last struct{ Generation string }
		for decoder.More() {
			if err := decoder.Decode(&last); err != nil {
				t.Fatal(err)
			}
		}
		if err := runSnapshotSelectContext(context.Background(), []string{"--offline-root", root, "--generation", last.Generation,
			"--expected-current", previous}, &bytes.Buffer{}); err != nil {
			t.Fatal(err)
		}
		_, receipt, err := maps.ResolveSnapshot(root)
		if err != nil || len(receipt.Tiles) != 64*(i+1) || len(receipt.Inputs) != i+1 {
			t.Fatalf("CLI replaced existing region: %+v %v", receipt, err)
		}
		previous = last.Generation
	}
}

func TestManagedSnapshotRejectsSymlinkedAncestorAndInvalidToken(t *testing.T) {
	base := t.TempDir()
	real := filepath.Join(base, "real")
	if err := os.MkdirAll(filepath.Join(real, "offline"), 0o700); err != nil {
		t.Fatal(err)
	}
	link := filepath.Join(base, "linked")
	if err := os.Symlink(real, link); err != nil {
		t.Fatal(err)
	}
	root := filepath.Join(link, "offline")
	requests := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { requests++ }))
	defer server.Close()
	client := &http.Client{Transport: fixedOriginTestTransport{server}}
	args := []string{"--offline-root", root, "--region", "nation.CY", "--max-transfer-bytes", "1000", "--max-new-disk-bytes", "1000"}
	if err := runSnapshotManagedPrepareContext(context.Background(), args, client, &bytes.Buffer{}); err == nil {
		t.Fatal("symlinked parent accepted for prepare")
	}
	if err := runSnapshotSelectContext(context.Background(), []string{"--offline-root", root, "--generation", "invalid"}, &bytes.Buffer{}); err == nil {
		t.Fatal("symlinked parent accepted for selection")
	}
	args[1] = filepath.Join(real, "offline")
	args[3] = "us_state.CA.extra"
	if err := runSnapshotManagedPrepareContext(context.Background(), args, client, &bytes.Buffer{}); err == nil {
		t.Fatal("unreviewed region token accepted")
	}
	if requests != 0 {
		t.Fatalf("invalid request reached network: %d", requests)
	}
}
