package maps

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"syscall"
	"time"

	m "pfeifer.dev/mapd/math"
	"pfeifer.dev/mapd/settings"
)

const maxReceiptBytes = 16 << 20
const maxSnapshotTiles = 65536
const maxSelectorBytes = 256
const attribution = "© OpenStreetMap contributors; https://www.openstreetmap.org/copyright (ODbL)"

var snapshotID = regexp.MustCompile(`^[0-9a-f]{64}$`)

type SnapshotInput struct {
	Name   string `json:"name"`
	Size   int64  `json:"size"`
	SHA256 string `json:"sha256"`
}
type SnapshotTile struct {
	Path   string `json:"path"`
	Size   int64  `json:"size"`
	SHA256 string `json:"sha256"`
	Empty  bool   `json:"empty"`
}
type SnapshotReceipt struct {
	Version          int             `json:"version"`
	SourceKind       string          `json:"sourceKind"`
	SourceURL        string          `json:"sourceUrl"`
	SourceDate       string          `json:"sourceDate"`
	SourceVerified   bool            `json:"sourceVerified"`
	GeneratedUnix    int64           `json:"generatedUnix"`
	Bounds           [4]float64      `json:"bounds"`
	Overlap          float64         `json:"overlap"`
	SourceRevision   string          `json:"sourceRevision"`
	UpstreamRevision string          `json:"upstreamRevision"`
	SourceDigest     string          `json:"sourceDigest"`
	TileSchema       string          `json:"tileSchema"`
	Attribution      string          `json:"attribution"`
	Inputs           []SnapshotInput `json:"inputs"`
	Tiles            []SnapshotTile  `json:"tiles"`
}
type snapshotSelector struct {
	Version    int    `json:"version"`
	Generation string `json:"generation"`
}
type AdmitOptions struct {
	Root             string
	Bounds           [4]float64
	Overlap          float64
	PBF              string
	ArchiveDir       string
	SourceURL        string
	SourceDate       string
	SourceRevision   string
	UpstreamRevision string
	SourceDigest     string
	Now              func() time.Time
	Budget           *SnapshotDiskBudget
	AddToCurrent     bool
}

func expectedSnapshotTiles(bounds [4]float64) ([]string, error) {
	for i, v := range bounds {
		if math.IsNaN(v) || math.IsInf(v, 0) || v*4 != math.Round(v*4) {
			return nil, errors.New("snapshot bounds must align to quarter-degree cells")
		}
		if (i%2 == 0 && (v < -90 || v > 90)) || (i%2 == 1 && (v < -180 || v > 180)) {
			return nil, errors.New("snapshot bounds outside globe")
		}
	}
	if bounds[0] >= bounds[2] || bounds[1] >= bounds[3] {
		return nil, errors.New("empty snapshot bounds")
	}
	count := int((bounds[2]-bounds[0])*4) * int((bounds[3]-bounds[1])*4)
	if count <= 0 || count > maxSnapshotTiles {
		return nil, errors.New("snapshot region exceeds finite cell budget")
	}
	result := make([]string, 0, count)
	for lat := int(math.Round(bounds[0] * 4)); lat < int(math.Round(bounds[2]*4)); lat++ {
		for lon := int(math.Round(bounds[1] * 4)); lon < int(math.Round(bounds[3]*4)); lon++ {
			area := Area{Box: m.Box{MinPos: m.NewPosition(float64(lat)/4, float64(lon)/4), MaxPos: m.NewPosition(float64(lat+1)/4, float64(lon+1)/4)}}
			result = append(result, strings.TrimPrefix(GenerateBoundsFileName(area, OfflineSettings{OutputDirectory: "."}), "./"))
		}
	}
	return result, nil
}

type snapshotContextReader struct {
	ctx    context.Context
	reader io.Reader
}

func (r snapshotContextReader) Read(data []byte) (int, error) {
	if err := r.ctx.Err(); err != nil {
		return 0, err
	}
	return r.reader.Read(data)
}

func stageSnapshotInput(ctx context.Context, input, stage, display string, budget *SnapshotDiskBudget) (string, SnapshotInput, error) {
	var empty SnapshotInput
	source, before, err := openSnapshotRegular(input, 0)
	if err != nil {
		return "", empty, err
	}
	defer source.Close()
	target, err := os.CreateTemp(stage, ".snapshot-input-")
	if err != nil {
		return "", empty, err
	}
	keep := false
	defer func() {
		target.Close()
		if !keep {
			os.Remove(target.Name())
		}
	}()
	hash := sha256.New()
	if before.Size() == math.MaxInt64 {
		return "", empty, errors.New("snapshot input size cannot be bounded")
	}
	if err := budget.charge(before.Size()); err != nil {
		return "", empty, err
	}
	n, err := io.Copy(io.MultiWriter(target, hash), io.LimitReader(snapshotContextReader{ctx, source}, before.Size()+1))
	if err != nil || n != before.Size() {
		return "", empty, errors.New("snapshot input changed or copy failed")
	}
	after, err := source.Stat()
	pathInfo, pathErr := os.Lstat(input)
	if err != nil || pathErr != nil || !sameOfflineFile(before, after) || !sameOfflineFile(after, pathInfo) {
		return "", empty, errors.New("snapshot input changed during staging")
	}
	if err := errors.Join(target.Sync(), target.Close()); err != nil {
		return "", empty, err
	}
	// Transfer ownership of the staged regular file to the caller.
	staged := target.Name()
	keep = true
	return staged, SnapshotInput{Name: display, Size: n, SHA256: hex.EncodeToString(hash.Sum(nil))}, nil
}

func openSnapshotRegular(path string, limit int64) (*os.File, os.FileInfo, error) {
	fd, err := syscall.Open(path, syscall.O_RDONLY|syscall.O_NOFOLLOW|syscall.O_NONBLOCK, 0)
	if err != nil {
		return nil, nil, err
	}
	file := os.NewFile(uintptr(fd), path)
	info, err := file.Stat()
	if err != nil || !info.Mode().IsRegular() || (limit > 0 && info.Size() > limit) {
		file.Close()
		return nil, nil, fmt.Errorf("unsafe or oversized regular file: %s", path)
	}
	return file, info, nil
}

func readSnapshotRegular(path string, limit int64) ([]byte, error) {
	file, info, err := openSnapshotRegular(path, limit)
	if err != nil {
		return nil, err
	}
	defer file.Close()
	data, err := io.ReadAll(io.LimitReader(file, limit+1))
	if err != nil || int64(len(data)) > limit || int64(len(data)) != info.Size() {
		return nil, errors.New("bounded snapshot read failed")
	}
	after, err := file.Stat()
	pathInfo, pathErr := os.Lstat(path)
	if err != nil || pathErr != nil || !sameOfflineFile(info, after) || !sameOfflineFile(after, pathInfo) || after.Size() != info.Size() {
		return nil, errors.New("snapshot file changed during read")
	}
	return data, nil
}

func readCanonicalReceipt(path string) (SnapshotReceipt, []byte, error) {
	var receipt SnapshotReceipt
	data, err := readSnapshotRegular(path, maxReceiptBytes)
	if err != nil {
		return receipt, nil, err
	}
	if len(data) == 0 || len(data) > maxReceiptBytes {
		return receipt, nil, errors.New("receipt size invalid")
	}
	if err = json.Unmarshal(data, &receipt); err != nil {
		return receipt, nil, err
	}
	canonical, err := json.Marshal(receipt)
	if err != nil || !bytes.Equal(canonical, data) {
		return receipt, nil, errors.New("noncanonical receipt")
	}
	return receipt, data, nil
}

func ownedSnapshotDir(path string) error {
	info, err := os.Lstat(path)
	if err != nil {
		return err
	}
	if !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
		return fmt.Errorf("unsafe snapshot directory: %s", path)
	}
	return nil
}

func lockSnapshotAdmission(ctx context.Context, root string) (*os.File, error) {
	path := filepath.Join(root, ".admission.lock")
	fd, err := syscall.Open(path, syscall.O_CREAT|syscall.O_RDWR|syscall.O_NOFOLLOW|syscall.O_NONBLOCK, 0o600)
	if err != nil {
		return nil, err
	}
	file := os.NewFile(uintptr(fd), path)
	info, err := file.Stat()
	if err != nil || !info.Mode().IsRegular() {
		file.Close()
		return nil, errors.New("unsafe snapshot admission lock")
	}
	for {
		err = syscall.Flock(fd, syscall.LOCK_EX|syscall.LOCK_NB)
		if err == nil {
			return file, nil
		}
		if err != syscall.EWOULDBLOCK {
			file.Close()
			return nil, err
		}
		select {
		case <-ctx.Done():
			file.Close()
			return nil, ctx.Err()
		case <-time.After(50 * time.Millisecond):
		}
	}
}

func snapshotTilePath(root, rel string) (string, error) {
	if rel == "" || filepath.IsAbs(rel) || filepath.Clean(rel) != rel || strings.Contains(rel, "\\") {
		return "", errors.New("unsafe tile path")
	}
	parts := strings.Split(rel, string(os.PathSeparator))
	if len(parts) != 3 || parts[0] == ".." || parts[1] == ".." || parts[2] == ".." {
		return "", errors.New("unexpected tile path")
	}
	for _, d := range []string{filepath.Join(root, parts[0]), filepath.Join(root, parts[0], parts[1])} {
		if err := ownedSnapshotDir(d); err != nil {
			return "", err
		}
	}
	return filepath.Join(root, rel), nil
}

func inspectSnapshotTiles(root string, overlap float64, expected []string) ([]SnapshotTile, error) {
	tiles := make([]SnapshotTile, 0, len(expected))
	for _, rel := range expected {
		path, err := snapshotTilePath(root, rel)
		if err != nil {
			return nil, err
		}
		data, err := readSnapshotRegular(path, maxOfflinePackedBytes)
		if err != nil {
			return nil, err
		}
		digestBytes := sha256.Sum256(data)
		digest := hex.EncodeToString(digestBytes[:])
		size := int64(len(data))
		tile := ReadOffline(data)
		if !tile.Loaded {
			return nil, fmt.Errorf("invalid tile: %s", rel)
		}
		parts := strings.Split(filepath.Base(rel), "_")
		if len(parts) != 4 {
			return nil, fmt.Errorf("bad cell name: %s", rel)
		}
		// The existing loader compares exact cell bounds; use its path-bound check.
		// The filename is generated from aligned bounds, so cell center selects it.
		var coords [4]float64
		for i, part := range parts {
			coords[i], err = strconv.ParseFloat(part, 64)
			if err != nil {
				return nil, err
			}
		}
		a, b, c, d := coords[0], coords[1], coords[2], coords[3]
		actual := tile.Box()
		if actual.MinPos.Lat() != a || actual.MinPos.Lon() != b || actual.MaxPos.Lat() != c || actual.MaxPos.Lon() != d || tile.Overlap() != overlap {
			return nil, fmt.Errorf("tile path/bounds mismatch: %s", rel)
		}
		tiles = append(tiles, SnapshotTile{Path: rel, Size: size, SHA256: digest, Empty: tile.Ways.Len() == 0})
	}
	// Reject an extra tile, not merely a missing declared tile.
	seen := make(map[string]bool, len(tiles))
	seenDirs := make(map[string]bool, len(tiles)*2)
	for _, t := range tiles {
		seen[t.Path] = true
		seenDirs[filepath.Dir(t.Path)] = true
		seenDirs[filepath.Dir(filepath.Dir(t.Path))] = true
	}
	err := filepath.WalkDir(root, func(path string, entry os.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if path == root {
			return nil
		}
		if entry.Type()&os.ModeSymlink != 0 {
			return fmt.Errorf("symlink in generation: %s", path)
		}
		rel, _ := filepath.Rel(root, path)
		if entry.IsDir() {
			if !seenDirs[rel] {
				return fmt.Errorf("unexpected generation directory: %s", rel)
			}
			return nil
		}
		if rel == "receipt.json" {
			return nil
		}
		if !entry.Type().IsRegular() || !seen[rel] {
			return fmt.Errorf("unexpected generation member: %s", rel)
		}
		return nil
	})
	return tiles, err
}

func verifySnapshot(root, id string) (string, SnapshotReceipt, error) {
	var empty SnapshotReceipt
	if !snapshotID.MatchString(id) {
		return "", empty, errors.New("invalid generation id")
	}
	if err := ownedSnapshotDir(root); err != nil {
		return "", empty, err
	}
	genParent := filepath.Join(root, "generations")
	if err := ownedSnapshotDir(genParent); err != nil {
		return "", empty, err
	}
	dir := filepath.Join(genParent, id)
	if err := ownedSnapshotDir(dir); err != nil {
		return "", empty, err
	}
	receipt, data, err := readCanonicalReceipt(filepath.Join(dir, "receipt.json"))
	if err != nil {
		return "", empty, err
	}
	digest := sha256.Sum256(data)
	if hex.EncodeToString(digest[:]) != id || receipt.Version != 1 || receipt.TileSchema != "offline.capnp:0xda3a0d9284ca402f" ||
		receipt.Attribution != attribution || receipt.SourceVerified || len(receipt.Inputs) == 0 {
		return "", empty, errors.New("receipt identity or contract invalid")
	}
	if (receipt.SourceKind != "local-pbf" && receipt.SourceKind != "provided-group-archives" && receipt.SourceKind != "combined-group-archives") ||
		math.IsNaN(receipt.Overlap) || math.IsInf(receipt.Overlap, 0) || receipt.Overlap < 0 || receipt.Overlap >= 0.25 ||
		receipt.GeneratedUnix <= 0 || len(receipt.SourceRevision) == 0 || len(receipt.SourceRevision) > 128 ||
		len(receipt.UpstreamRevision) == 0 || len(receipt.UpstreamRevision) > 128 ||
		len(receipt.SourceDigest) == 0 || len(receipt.SourceDigest) > 128 {
		return "", empty, errors.New("receipt metadata invalid")
	}
	expected, err := receiptSnapshotTiles(receipt)
	if err != nil || len(receipt.Tiles) != len(expected) {
		return "", empty, errors.New("receipt cell coverage invalid")
	}
	for _, input := range receipt.Inputs {
		if input.Name == "" || filepath.IsAbs(input.Name) || filepath.Clean(input.Name) != input.Name || strings.HasPrefix(input.Name, "..") ||
			input.Size <= 0 || !snapshotID.MatchString(input.SHA256) {
			return "", empty, errors.New("receipt input invalid")
		}
	}
	if receipt.SourceKind == "local-pbf" && len(receipt.Inputs) != 1 {
		return "", empty, errors.New("PBF receipt has multiple inputs")
	}
	if receipt.SourceKind == "provided-group-archives" {
		if receipt.Bounds[0]/2 != math.Round(receipt.Bounds[0]/2) || receipt.Bounds[1]/2 != math.Round(receipt.Bounds[1]/2) ||
			receipt.Bounds[2]/2 != math.Round(receipt.Bounds[2]/2) || receipt.Bounds[3]/2 != math.Round(receipt.Bounds[3]/2) {
			return "", empty, errors.New("archive receipt bounds are unaligned")
		}
		index := 0
		for lat := int(receipt.Bounds[0]); lat < int(receipt.Bounds[2]); lat += 2 {
			for lon := int(receipt.Bounds[1]); lon < int(receipt.Bounds[3]); lon += 2 {
				if index >= len(receipt.Inputs) || receipt.Inputs[index].Name != filepath.Join(fmt.Sprintf("%d", lat), fmt.Sprintf("%d.tar.gz", lon)) {
					return "", empty, errors.New("archive receipt input coverage invalid")
				}
				index++
			}
		}
		if index != len(receipt.Inputs) {
			return "", empty, errors.New("extra archive input")
		}
	}
	for i, tile := range receipt.Tiles {
		if tile.Path != expected[i] || tile.Size <= 0 || tile.Size > maxOfflinePackedBytes || !snapshotID.MatchString(tile.SHA256) {
			return "", empty, fmt.Errorf("invalid declared tile: %d", i)
		}
	}
	return dir, receipt, nil
}

// SnapshotLoader checks the requested tile against the pinned receipt using
// the existing bounded path/Cap'n Proto loader. Other cells are not opened.
func SnapshotLoader(dir string, receipt SnapshotReceipt) func(m.Position) (Offline, error) {
	known := make(map[string]SnapshotTile, len(receipt.Tiles))
	for _, tile := range receipt.Tiles {
		known[tile.Path] = tile
	}
	return func(pos m.Position) (Offline, error) {
		area, ok := areaForPosition(pos)
		if !ok {
			return Offline{}, errors.New("invalid snapshot position")
		}
		rel := strings.TrimPrefix(GenerateBoundsFileName(area, OfflineSettings{OutputDirectory: "."}), "./")
		declared, ok := known[rel]
		if !ok {
			return Offline{}, errors.New("position outside admitted snapshot")
		}
		if _, err := snapshotTilePath(dir, rel); err != nil {
			return Offline{}, err
		}
		loaded, err := findWaysAroundPositionIn(pos, dir, declared.SHA256)
		if err == nil && (!loaded.Loaded || (loaded.Ways.Len() == 0) != declared.Empty) {
			return Offline{}, errors.New("snapshot tile differs from declared coverage")
		}
		if err == nil {
			loaded.snapshotID = filepath.Base(dir)
		}
		return loaded, err
	}
}

// ResolveSnapshot pins one selected, validated generation for a process session.
func ResolveSnapshot(root string) (string, SnapshotReceipt, error) {
	var empty SnapshotReceipt
	if err := ownedSnapshotDir(root); err != nil {
		return "", empty, err
	}
	path := filepath.Join(root, "current.json")
	info, err := os.Lstat(path)
	if err != nil || !info.Mode().IsRegular() || info.Size() > maxSelectorBytes {
		return "", empty, errors.New("missing or unsafe snapshot selector")
	}
	data, err := readSnapshotRegular(path, maxSelectorBytes)
	if err != nil {
		return "", empty, err
	}
	var selector snapshotSelector
	if err := json.Unmarshal(data, &selector); err != nil {
		return "", empty, err
	}
	canonical, _ := json.Marshal(selector)
	if !bytes.Equal(data, canonical) || selector.Version != 1 {
		return "", empty, errors.New("invalid snapshot selector")
	}
	return verifySnapshot(root, selector.Generation)
}

func syncSnapshotTree(root string) error {
	var dirs []string
	err := filepath.WalkDir(root, func(path string, entry os.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if entry.IsDir() {
			dirs = append(dirs, path)
			return nil
		}
		file, err := os.Open(path)
		if err != nil {
			return err
		}
		return errors.Join(file.Sync(), file.Close())
	})
	if err != nil {
		return err
	}
	for i := len(dirs) - 1; i >= 0; i-- {
		file, err := os.Open(dirs[i])
		if err != nil {
			return err
		}
		if err := errors.Join(file.Sync(), file.Close()); err != nil {
			return err
		}
	}
	return nil
}

func writeSelector(ctx context.Context, root, id string) error {
	data, _ := json.Marshal(snapshotSelector{Version: 1, Generation: id})
	tmp, err := os.CreateTemp(root, ".current-")
	if err != nil {
		return err
	}
	defer os.Remove(tmp.Name())
	if _, err = tmp.Write(data); err != nil {
		tmp.Close()
		return err
	}
	if err = errors.Join(tmp.Sync(), tmp.Close()); err != nil {
		return err
	}
	if err := ctx.Err(); err != nil {
		return err
	}
	if err = os.Rename(tmp.Name(), filepath.Join(root, "current.json")); err != nil {
		return err
	}
	dir, err := os.Open(root)
	if err != nil {
		return err
	}
	return errors.Join(dir.Sync(), dir.Close())
}

// AdmitSnapshot creates and selects one complete immutable generation. It does
// not download data, remove prior generations, or restart a running provider.
func AdmitSnapshot(ctx context.Context, o AdmitOptions) (string, error) {
	return admitSnapshot(ctx, o, true)
}

// PrepareSnapshot fully admits an immutable generation without changing the
// current selector. A separate, checked SelectPreparedSnapshot is required.
func PrepareSnapshot(ctx context.Context, o AdmitOptions) (string, error) {
	return admitSnapshot(ctx, o, false)
}

func admitSnapshot(ctx context.Context, o AdmitOptions, selectNow bool) (string, error) {
	if (o.PBF == "") == (o.ArchiveDir == "") {
		return "", errors.New("choose one explicit input kind")
	}
	expected, err := expectedSnapshotTiles(o.Bounds)
	if err != nil {
		return "", err
	}
	if math.IsNaN(o.Overlap) || math.IsInf(o.Overlap, 0) || o.Overlap < 0 || o.Overlap >= 0.25 {
		return "", errors.New("invalid overlap")
	}
	if err := ownedSnapshotDir(o.Root); err != nil {
		return "", err
	}
	lock, err := lockSnapshotAdmission(ctx, o.Root)
	if err != nil {
		return "", err
	}
	defer func() { syscall.Flock(int(lock.Fd()), syscall.LOCK_UN); lock.Close() }()
	parent := filepath.Join(o.Root, "generations")
	if err := os.Mkdir(parent, 0o700); err != nil && !os.IsExist(err) {
		return "", err
	}
	if err := ownedSnapshotDir(parent); err != nil {
		return "", err
	}
	stage, err := os.MkdirTemp(parent, ".stage-")
	if err != nil {
		return "", err
	}
	defer os.RemoveAll(stage)
	now := time.Now
	if o.Now != nil {
		now = o.Now
	}
	receipt := SnapshotReceipt{Version: 1, SourceURL: o.SourceURL, SourceDate: o.SourceDate,
		GeneratedUnix: now().Unix(), Bounds: o.Bounds, Overlap: o.Overlap,
		SourceRevision: o.SourceRevision, UpstreamRevision: o.UpstreamRevision, SourceDigest: o.SourceDigest,
		TileSchema: "offline.capnp:0xda3a0d9284ca402f", Attribution: attribution}
	if o.PBF != "" {
		receipt.SourceKind = "local-pbf"
		input, record, err := stageSnapshotInput(ctx, o.PBF, stage, filepath.Base(o.PBF), o.Budget)
		if err != nil {
			return "", err
		}
		receipt.Inputs = []SnapshotInput{record}
		s := OfflineSettings{Context: ctx, Box: m.Box{MinPos: m.NewPosition(o.Bounds[0], o.Bounds[1]), MaxPos: m.NewPosition(o.Bounds[2], o.Bounds[3])},
			Overlap: o.Overlap, InputFile: input, OutputDirectory: stage, GenerateEmptyFiles: true}
		if err := GenerateOffline(s); err != nil {
			return "", err
		}
		if err := os.Remove(input); err != nil {
			return "", err
		}
	} else {
		receipt.SourceKind = "provided-group-archives"
		if o.Bounds[0]/2 != math.Round(o.Bounds[0]/2) || o.Bounds[1]/2 != math.Round(o.Bounds[1]/2) ||
			o.Bounds[2]/2 != math.Round(o.Bounds[2]/2) || o.Bounds[3]/2 != math.Round(o.Bounds[3]/2) {
			return "", errors.New("archive region must align to two-degree groups")
		}
		for lat := int(o.Bounds[0]); lat < int(o.Bounds[2]); lat += 2 {
			for lon := int(o.Bounds[1]); lon < int(o.Bounds[3]); lon += 2 {
				if err := ctx.Err(); err != nil {
					return "", err
				}
				rel := filepath.Join(fmt.Sprintf("%d", lat), fmt.Sprintf("%d.tar.gz", lon))
				path := filepath.Join(o.ArchiveDir, rel)
				input, record, err := stageSnapshotInput(ctx, path, stage, rel, o.Budget)
				if err != nil {
					return "", err
				}
				receipt.Inputs = append(receipt.Inputs, record)
				if err := settings.ExtractGroupArchiveFileLimited(ctx, input, stage, lat, lon, o.Budget.charge); err != nil {
					return "", err
				}
				if err := os.Remove(input); err != nil {
					return "", err
				}
			}
		}
	}
	if err := ctx.Err(); err != nil {
		return "", err
	}
	receipt.Tiles, err = inspectSnapshotTiles(stage, o.Overlap, expected)
	if err != nil {
		return "", err
	}
	if o.AddToCurrent {
		if err := addSelectedSnapshot(ctx, o.Root, stage, &receipt); err != nil {
			return "", err
		}
		expected, err = receiptSnapshotTiles(receipt)
		if err != nil {
			return "", err
		}
	}
	data, err := json.Marshal(receipt)
	if err != nil || len(data) > maxReceiptBytes {
		return "", errors.New("receipt exceeds finite limit")
	}
	if err := o.Budget.charge(int64(len(data))); err != nil {
		return "", err
	}
	idBytes := sha256.Sum256(data)
	id := hex.EncodeToString(idBytes[:])
	if err := os.WriteFile(filepath.Join(stage, "receipt.json"), data, 0o444); err != nil {
		return "", err
	}
	if err := syncSnapshotTree(stage); err != nil {
		return "", err
	}
	final := filepath.Join(parent, id)
	existed := false
	if _, err := os.Lstat(final); os.IsNotExist(err) {
		if err := os.Rename(stage, final); err != nil {
			return "", err
		}
		p, err := os.Open(parent)
		if err != nil {
			return "", err
		}
		if err := errors.Join(p.Sync(), p.Close()); err != nil {
			return "", err
		}
	} else if err != nil {
		return "", err
	} else {
		existed = true
	}
	if _, _, err := verifySnapshot(o.Root, id); err != nil {
		return "", err
	}
	if existed {
		tiles, err := inspectSnapshotTiles(final, o.Overlap, expected)
		if err != nil {
			return "", err
		}
		for i := range tiles {
			if tiles[i] != receipt.Tiles[i] {
				return "", errors.New("existing generation is not the validated snapshot")
			}
		}
	}
	if err := ctx.Err(); err != nil {
		return "", err
	}
	if selectNow {
		if err := writeSelector(ctx, o.Root, id); err != nil {
			return "", err
		}
	}
	return id, nil
}

// SelectPreparedSnapshot is the managed operation's commit point. The
// admission lock serializes it with other admission/selection commands; a
// stale expected selector or canceled context cannot initiate the rename.
func SelectPreparedSnapshot(ctx context.Context, root, id, expectedCurrent string) error {
	if !snapshotID.MatchString(id) || (expectedCurrent != "" && !snapshotID.MatchString(expectedCurrent)) {
		return errors.New("invalid snapshot identity")
	}
	if err := ownedSnapshotDir(root); err != nil {
		return err
	}
	lock, err := lockSnapshotAdmission(ctx, root)
	if err != nil {
		return err
	}
	defer func() { syscall.Flock(int(lock.Fd()), syscall.LOCK_UN); lock.Close() }()
	selected := ""
	if _, statErr := os.Lstat(filepath.Join(root, "current.json")); statErr == nil {
		currentDir, _, resolveErr := ResolveSnapshot(root)
		if resolveErr != nil {
			return resolveErr
		}
		selected = filepath.Base(currentDir)
	} else if !os.IsNotExist(statErr) {
		return statErr
	}
	if selected != expectedCurrent {
		return errors.New("snapshot selector changed after preparation")
	}
	dir, receipt, err := verifySnapshot(root, id)
	if err != nil {
		return err
	}
	expected, err := receiptSnapshotTiles(receipt)
	if err != nil {
		return err
	}
	tiles, err := inspectSnapshotTiles(dir, receipt.Overlap, expected)
	if err != nil {
		return err
	}
	for i := range tiles {
		if tiles[i] != receipt.Tiles[i] {
			return errors.New("prepared generation content changed")
		}
	}
	if err := ctx.Err(); err != nil {
		return err
	}
	return writeSelector(ctx, root, id)
}
