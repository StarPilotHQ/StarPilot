package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"io"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"syscall"

	"pfeifer.dev/mapd/maps"
)

func snapshotSignalContext() (context.Context, context.CancelFunc) {
	return signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
}

// Managed requests use an operator-owned offline root. A checked leaf alone
// is insufficient: a symlinked ancestor could redirect the entire tree.
func ownedManagedRoot(root string) error {
	if !filepath.IsAbs(root) {
		return errors.New("managed snapshot root must be absolute")
	}
	for path := filepath.Clean(root); ; path = filepath.Dir(path) {
		info, err := os.Lstat(path)
		if err != nil {
			return err
		}
		if !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
			return errors.New("managed snapshot root has unsafe component")
		}
		if path == filepath.Dir(path) {
			return nil
		}
	}
}

func runSnapshotCatalog(args []string) error {
	if len(args) != 0 {
		return errors.New("snapshot catalog takes no arguments")
	}
	regions, err := maps.BundledManagedRegions()
	if err != nil {
		return err
	}
	return json.NewEncoder(os.Stdout).Encode(regions)
}

func runSnapshotManagedPrepare(args []string) error {
	ctx, stop := snapshotSignalContext()
	defer stop()
	return runSnapshotManagedPrepareContext(ctx, args, http.DefaultClient, os.Stdout)
}

// The client/writer seam is limited to tests. The production command always
// resolves the fixed bundled region and fixed HTTPS origin.
func runSnapshotManagedPrepareContext(ctx context.Context, args []string, client *http.Client, output io.Writer) error {
	flags := flag.NewFlagSet("snapshot-managed-prepare", flag.ContinueOnError)
	root := flags.String("offline-root", "", "existing owned snapshot root")
	regionToken := flags.String("region", "", "bundled named region token")
	maxTransfer := flags.Int64("max-transfer-bytes", 0, "global HTTP transfer cap")
	maxDisk := flags.Int64("max-new-disk-bytes", 0, "global new staging byte cap")
	if err := flags.Parse(args); err != nil || flags.NArg() != 0 || !filepath.IsAbs(*root) || *regionToken == "" ||
		*maxTransfer <= 0 || *maxDisk <= 0 {
		return errors.New("invalid managed snapshot request")
	}
	if err := ownedManagedRoot(*root); err != nil {
		return err
	}
	region, err := maps.ResolveManagedRegion(*regionToken)
	if err != nil {
		return err
	}
	encoder := json.NewEncoder(output)
	id, err := maps.FetchAndAdmitSnapshot(ctx, maps.FetchOptions{
		AdmitOptions: maps.AdmitOptions{Root: filepath.Clean(*root), Bounds: region.Bounds, Overlap: .001,
			SourceRevision: sourceRevision, UpstreamRevision: upstreamRevision, SourceDigest: sourceDigest, AddToCurrent: true},
		MaxBytes: *maxTransfer, MaxNewDiskBytes: *maxDisk, PrepareOnly: true,
		Progress: func(p maps.FetchProgress) error { return encoder.Encode(p) },
	}, client)
	if err != nil {
		return err
	}
	return encoder.Encode(struct {
		Phase      string `json:"phase"`
		Generation string `json:"generation"`
	}{"prepared", id})
}

func runSnapshotSelect(args []string) error {
	ctx, stop := snapshotSignalContext()
	defer stop()
	return runSnapshotSelectContext(ctx, args, os.Stdout)
}

func runSnapshotSelectContext(ctx context.Context, args []string, output io.Writer) error {
	flags := flag.NewFlagSet("snapshot-select", flag.ContinueOnError)
	root := flags.String("offline-root", "", "existing owned snapshot root")
	id := flags.String("generation", "", "prepared generation identity")
	expected := flags.String("expected-current", "", "previous selector identity, empty for first selection")
	if err := flags.Parse(args); err != nil || flags.NArg() != 0 || !filepath.IsAbs(*root) {
		return errors.New("invalid snapshot selection request")
	}
	if err := ownedManagedRoot(*root); err != nil {
		return err
	}
	if err := maps.SelectPreparedSnapshot(ctx, filepath.Clean(*root), *id, *expected); err != nil {
		return err
	}
	return json.NewEncoder(output).Encode(struct {
		Phase      string `json:"phase"`
		Generation string `json:"generation"`
	}{"selected", *id})
}
