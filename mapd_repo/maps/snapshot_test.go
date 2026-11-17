package maps

import (
	"archive/tar"
	"bytes"
	"compress/gzip"
	"context"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
	"time"

	"capnproto.org/go/capnp/v3"
	"google.golang.org/protobuf/encoding/protowire"
	"pfeifer.dev/mapd/cereal/offline"
	m "pfeifer.dev/mapd/math"
)

func pbfBytesField(dst []byte, field protowire.Number, data []byte) []byte {
	dst = protowire.AppendTag(dst, field, protowire.BytesType)
	return protowire.AppendBytes(dst, data)
}

func archiveTiles(t *testing.T, count int) []byte {
	t.Helper()
	var out bytes.Buffer
	gz := gzip.NewWriter(&out)
	tw := tar.NewWriter(gz)
	expected, err := expectedSnapshotTiles([4]float64{0, 0, 2, 2})
	if err != nil {
		t.Fatal(err)
	}
	for _, rel := range expected[:count] {
		var a, b, c, d float64
		parts := strings.Split(filepath.Base(rel), "_")
		if len(parts) != 4 {
			t.Fatal(rel)
		}
		if _, err := fmt.Sscanf(strings.Join(parts, " "), "%f %f %f %f", &a, &b, &c, &d); err != nil {
			t.Fatal(err)
		}
		msg, seg, err := capnp.NewMessage(capnp.SingleSegment(nil))
		if err != nil {
			t.Fatal(err)
		}
		tile, err := offline.NewRootOffline(seg)
		if err != nil {
			t.Fatal(err)
		}
		tile.SetMinLat(a)
		tile.SetMinLon(b)
		tile.SetMaxLat(c)
		tile.SetMaxLon(d)
		tile.SetOverlap(.001)
		if _, err := tile.NewWays(0); err != nil {
			t.Fatal(err)
		}
		data, err := msg.MarshalPacked()
		if err != nil {
			t.Fatal(err)
		}
		name := "offline/" + rel
		if err := tw.WriteHeader(&tar.Header{Name: name, Typeflag: tar.TypeReg, Mode: 0644, Size: int64(len(data))}); err != nil {
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
	return out.Bytes()
}

func TestSnapshotArchiveAtomicSelectionAndMalformedInputs(t *testing.T) {
	root := t.TempDir()
	pbf := filepath.Join("..", "testdata", "synthetic_snapshot.osm.pbf")
	oldID, err := AdmitSnapshot(context.Background(), AdmitOptions{Root: root, Bounds: [4]float64{35, -98, 35.25, -97.75}, Overlap: .001, PBF: pbf, SourceRevision: "test", UpstreamRevision: "test", SourceDigest: "test"})
	if err != nil {
		t.Fatal(err)
	}
	oldDir, oldReceipt, err := ResolveSnapshot(root)
	if err != nil {
		t.Fatal(err)
	}
	oldLoader := SnapshotLoader(oldDir, oldReceipt)
	archiveDir := filepath.Join(t.TempDir(), "archives")
	if err := os.MkdirAll(filepath.Join(archiveDir, "0"), 0700); err != nil {
		t.Fatal(err)
	}
	archive := filepath.Join(archiveDir, "0", "0.tar.gz")
	options := AdmitOptions{Root: root, Bounds: [4]float64{0, 0, 2, 2}, Overlap: .001, ArchiveDir: archiveDir, SourceRevision: "test", UpstreamRevision: "test", SourceDigest: "test"}
	if err := os.WriteFile(archive, archiveTiles(t, 63), 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := AdmitSnapshot(context.Background(), options); err == nil {
		t.Fatal("missing cell admitted")
	}
	selected, _, err := ResolveSnapshot(root)
	if err != nil || filepath.Base(selected) != oldID {
		t.Fatalf("partial archive changed selector: %v", err)
	}
	if err := os.WriteFile(archive, archiveTiles(t, 64), 0600); err != nil {
		t.Fatal(err)
	}
	canceled, stop := context.WithCancel(context.Background())
	stop()
	if _, err := AdmitSnapshot(canceled, options); err == nil {
		t.Fatal("canceled admission selected a generation")
	}
	newID, err := AdmitSnapshot(context.Background(), options)
	if err != nil || newID == oldID {
		t.Fatalf("complete archive rejected: %v", err)
	}
	selected, receipt, err := ResolveSnapshot(root)
	if err != nil || filepath.Base(selected) != newID || len(receipt.Tiles) != 64 {
		t.Fatalf("archive receipt wrong: %v", err)
	}
	if _, err := SnapshotLoader(selected, receipt)(m.NewPosition(.125, .125)); err != nil {
		t.Fatal(err)
	}
	if oldWay, err := oldLoader(m.NewPosition(35.125, -97.875)); err != nil || !oldWay.Loaded || oldWay.Ways.Len() != 1 {
		t.Fatalf("selector changed an already pinned process generation: %v", err)
	}
	if _, err := SnapshotLoader(filepath.Join(root, "generations", oldID), receipt)(m.NewPosition(.125, .125)); err == nil {
		t.Fatal("different generation accepted as cache identity")
	}
}

func TestSnapshotSpecialFilesAndOversizeAreRejected(t *testing.T) {
	root := t.TempDir()
	pbf := filepath.Join("..", "testdata", "synthetic_snapshot.osm.pbf")
	id, err := AdmitSnapshot(context.Background(), AdmitOptions{Root: root, Bounds: [4]float64{35, -98, 35.25, -97.75}, Overlap: .001, PBF: pbf, SourceRevision: "test", UpstreamRevision: "test", SourceDigest: "test"})
	if err != nil {
		t.Fatal(err)
	}
	selected, receipt, err := ResolveSnapshot(root)
	if err != nil {
		t.Fatal(err)
	}
	tile := filepath.Join(selected, receipt.Tiles[0].Path)
	original, err := os.ReadFile(tile)
	if err != nil {
		t.Fatal(err)
	}
	for _, kind := range []string{"fifo", "symlink", "oversize"} {
		t.Run(kind, func(t *testing.T) {
			if err := os.Remove(tile); err != nil {
				t.Fatal(err)
			}
			switch kind {
			case "fifo":
				if err := syscall.Mkfifo(tile, 0600); err != nil {
					t.Fatal(err)
				}
			case "symlink":
				if err := os.Symlink(filepath.Join(root, "current.json"), tile); err != nil {
					t.Fatal(err)
				}
			case "oversize":
				f, err := os.Create(tile)
				if err != nil {
					t.Fatal(err)
				}
				if err := f.Truncate(maxOfflinePackedBytes + 1); err != nil {
					t.Fatal(err)
				}
				f.Close()
			}
			if _, err := SnapshotLoader(selected, receipt)(m.NewPosition(35.125, -97.875)); err == nil {
				t.Fatal("malformed requested tile accepted")
			}
			if err := os.Remove(tile); err != nil {
				t.Fatal(err)
			}
			if err := os.WriteFile(tile, original, 0600); err != nil {
				t.Fatal(err)
			}
		})
	}
	selector := filepath.Join(root, "current.json")
	savedSelector, err := os.ReadFile(selector)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.Remove(selector); err != nil {
		t.Fatal(err)
	}
	if err := syscall.Mkfifo(selector, 0600); err != nil {
		t.Fatal(err)
	}
	if _, _, err := ResolveSnapshot(root); err == nil {
		t.Fatal("FIFO selector accepted")
	}
	os.Remove(selector)
	os.WriteFile(selector, savedSelector, 0600)
	os.Remove(selector)
	if err := os.Symlink(filepath.Join(root, "generations", id, "receipt.json"), selector); err != nil {
		t.Fatal(err)
	}
	if _, _, err := ResolveSnapshot(root); err == nil {
		t.Fatal("symlink selector accepted")
	}
	os.Remove(selector)
	f, err := os.Create(selector)
	if err != nil {
		t.Fatal(err)
	}
	if err := f.Truncate(maxSelectorBytes + 1); err != nil {
		t.Fatal(err)
	}
	f.Close()
	if _, _, err := ResolveSnapshot(root); err == nil {
		t.Fatal("oversize selector accepted")
	}
	os.Remove(selector)
	os.WriteFile(selector, savedSelector, 0600)
	receiptPath := filepath.Join(root, "generations", id, "receipt.json")
	savedReceipt, err := os.ReadFile(receiptPath)
	if err != nil {
		t.Fatal(err)
	}
	os.Remove(receiptPath)
	syscall.Mkfifo(receiptPath, 0600)
	if _, _, err := ResolveSnapshot(root); err == nil {
		t.Fatal("FIFO receipt accepted")
	}
	os.Remove(receiptPath)
	os.WriteFile(receiptPath, savedReceipt, 0600)
	os.Remove(receiptPath)
	if err := os.Symlink(selector, receiptPath); err != nil {
		t.Fatal(err)
	}
	if _, _, err := ResolveSnapshot(root); err == nil {
		t.Fatal("symlink receipt accepted")
	}
	os.Remove(receiptPath)
	f, err = os.Create(receiptPath)
	if err != nil {
		t.Fatal(err)
	}
	if err := f.Truncate(maxReceiptBytes + 1); err != nil {
		t.Fatal(err)
	}
	f.Close()
	if _, _, err := ResolveSnapshot(root); err == nil {
		t.Fatal("oversize receipt accepted")
	}
	os.Remove(receiptPath)
	os.WriteFile(receiptPath, savedReceipt, 0600)
}

func TestMalformedCanonicalReceiptBoundsRejectBeforeArchiveLoops(t *testing.T) {
	root := t.TempDir()
	receipt := SnapshotReceipt{Version: 1, SourceKind: "provided-group-archives", GeneratedUnix: 1800000000,
		Bounds: [4]float64{-1e300, 0, 1e300, 0}, Overlap: .001, SourceRevision: "test", UpstreamRevision: "test", SourceDigest: "test",
		TileSchema: "offline.capnp:0xda3a0d9284ca402f", Attribution: attribution,
		Inputs: []SnapshotInput{{Name: "0/0.tar.gz", Size: 1, SHA256: strings.Repeat("a", 64)}},
		Tiles:  []SnapshotTile{{Path: "0/0/tile", Size: 1, SHA256: strings.Repeat("b", 64)}}}
	raw, err := json.Marshal(receipt)
	if err != nil {
		t.Fatal(err)
	}
	hash := sha256.Sum256(raw)
	id := hex.EncodeToString(hash[:])
	dir := filepath.Join(root, "generations", id)
	if err := os.MkdirAll(dir, 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "receipt.json"), raw, 0600); err != nil {
		t.Fatal(err)
	}
	selector, _ := json.Marshal(snapshotSelector{Version: 1, Generation: id})
	if err := os.WriteFile(filepath.Join(root, "current.json"), selector, 0600); err != nil {
		t.Fatal(err)
	}
	if _, _, err := ResolveSnapshot(root); err == nil {
		t.Fatal("out-of-range canonical receipt accepted")
	}
}
func pbfVarintField(dst []byte, field protowire.Number, value uint64) []byte {
	dst = protowire.AppendTag(dst, field, protowire.VarintType)
	return protowire.AppendVarint(dst, value)
}
func pbfPacked(values ...uint64) []byte {
	var data []byte
	for _, value := range values {
		data = protowire.AppendVarint(data, value)
	}
	return data
}
func pbfBlock(kind string, raw []byte) []byte {
	blob := pbfBytesField(nil, 1, raw)
	header := pbfBytesField(nil, 1, []byte(kind))
	header = pbfVarintField(header, 3, uint64(len(blob)))
	data := make([]byte, 4)
	binary.BigEndian.PutUint32(data, uint32(len(header)))
	data = append(data, header...)
	return append(data, blob...)
}
func syntheticPBF(tags ...string) []byte {
	header := pbfBytesField(nil, 4, []byte("OsmSchema-V0.6"))
	header = pbfBytesField(header, 5, []byte("LocationsOnWays"))
	if len(tags) == 0 {
		tags = []string{"highway", "residential", "maxspeed", "30 mph"}
	}
	var table []byte
	for _, value := range append([]string{""}, tags...) {
		table = pbfBytesField(table, 1, []byte(value))
	}
	var keys, values []uint64
	for i := 0; i < len(tags); i += 2 {
		keys = append(keys, uint64(i+1))
		values = append(values, uint64(i+2))
	}
	var way []byte
	way = pbfVarintField(way, 1, 42)
	way = pbfBytesField(way, 2, pbfPacked(keys...))
	way = pbfBytesField(way, 3, pbfPacked(values...))
	way = pbfBytesField(way, 8, pbfPacked(protowire.EncodeZigZag(1), protowire.EncodeZigZag(1)))
	way = pbfBytesField(way, 9, pbfPacked(protowire.EncodeZigZag(351000000), protowire.EncodeZigZag(1000000)))
	way = pbfBytesField(way, 10, pbfPacked(protowire.EncodeZigZag(-979000000), protowire.EncodeZigZag(0)))
	group := pbfBytesField(nil, 3, way)
	block := pbfBytesField(nil, 1, table)
	block = pbfBytesField(block, 2, group)
	block = pbfVarintField(block, 17, 100)
	return append(pbfBlock("OSMHeader", header), pbfBlock("OSMData", block)...)
}

func TestActualGeneratorAndSnapshotAdmission(t *testing.T) {
	root := t.TempDir()
	checkedIn, err := os.ReadFile(filepath.Join("..", "testdata", "synthetic_snapshot.osm.pbf"))
	if err != nil || !bytes.Equal(checkedIn, syntheticPBF()) {
		t.Fatalf("synthetic PBF fixture drift: %v", err)
	}
	pbf := filepath.Join(t.TempDir(), "synthetic.osm.pbf")
	if err := os.WriteFile(pbf, syntheticPBF(), 0600); err != nil {
		t.Fatal(err)
	}
	bounds := [4]float64{35, -98, 35.25, -97.5}
	generated := filepath.Join(t.TempDir(), "generated")
	s := OfflineSettings{Box: m.Box{MinPos: m.NewPosition(35, -98), MaxPos: m.NewPosition(35.25, -97.5)},
		Overlap: .001, InputFile: pbf, OutputDirectory: generated, GenerateEmptyFiles: true}
	if err := GenerateOffline(s); err != nil {
		t.Fatal(err)
	}
	one, err := FindWaysAroundPositionIn(m.NewPosition(35.125, -97.875), generated)
	if err != nil || !one.Loaded || one.Ways.Len() != 1 {
		t.Fatalf("actual PBF way not loaded: %v %v", err, one.Ways.Len())
	}
	empty, err := FindWaysAroundPositionIn(m.NewPosition(35.125, -97.625), generated)
	if err != nil || !empty.Loaded || empty.Ways.Len() != 0 {
		t.Fatalf("actual empty coverage not loaded: %v %v", err, empty.Ways.Len())
	}
	noEmpty := filepath.Join(t.TempDir(), "no-empty")
	s.OutputDirectory, s.GenerateEmptyFiles = noEmpty, false
	if err := GenerateOffline(s); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(GenerateBoundsFileName(Area{Box: m.Box{MinPos: m.NewPosition(35, -97.75), MaxPos: m.NewPosition(35.25, -97.5)}}, OfflineSettings{OutputDirectory: noEmpty})); !os.IsNotExist(err) {
		t.Fatalf("generator emitted empty cell with flag off: %v", err)
	}
	options := AdmitOptions{Root: root, Bounds: bounds, Overlap: .001, PBF: pbf,
		SourceRevision: "test", UpstreamRevision: "test", SourceDigest: "test", Now: func() time.Time { return time.Unix(1800000000, 0) }}
	invalidOverlap := options
	invalidOverlap.Overlap = .25
	if _, err := AdmitSnapshot(context.Background(), invalidOverlap); err == nil {
		t.Fatal("one-cell overlap accepted")
	}
	id, err := AdmitSnapshot(context.Background(), options)
	if err != nil {
		t.Fatal(err)
	}
	selected, receipt, err := ResolveSnapshot(root)
	if err != nil || filepath.Base(selected) != id || len(receipt.Tiles) != 2 || receipt.Tiles[0].Empty || !receipt.Tiles[1].Empty {
		t.Fatalf("bad selected generation: %q %+v %v", selected, receipt.Tiles, err)
	}
	selector, err := os.ReadFile(filepath.Join(root, "current.json"))
	if err != nil {
		t.Fatal(err)
	}
	if len(selector) == 0 {
		t.Fatal("empty selector")
	}
	receiptBytes, err := os.ReadFile(filepath.Join(selected, "receipt.json"))
	if err != nil {
		t.Fatal(err)
	}
	digest := sha256.Sum256(receiptBytes)
	if hex.EncodeToString(digest[:]) != id {
		t.Fatal("receipt id differs from exact bytes")
	}
	tile := filepath.Join(selected, receipt.Tiles[0].Path)
	if err := os.WriteFile(tile, []byte("changed"), 0600); err != nil {
		t.Fatal(err)
	}
	selected, receipt, err = ResolveSnapshot(root)
	if err != nil {
		t.Fatalf("metadata should remain readable: %v", err)
	}
	if _, err := SnapshotLoader(selected, receipt)(m.NewPosition(35.125, -97.875)); err == nil {
		t.Fatal("changed requested tile accepted by pinned loader")
	}
	if _, err := AdmitSnapshot(context.Background(), options); err == nil {
		t.Fatal("existing corrupted generation reselected")
	}
}
