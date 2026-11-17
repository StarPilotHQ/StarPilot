package maps

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
	"log/slog"
	"math"
	"os"
	"reflect"
	"runtime"
	"slices"
	"strconv"
	"strings"
	"syscall"

	"capnproto.org/go/capnp/v3"
	"github.com/paulmach/osm"
	"github.com/paulmach/osm/osmpbf"
	"github.com/pkg/errors"
	"pfeifer.dev/mapd/cereal/offline"
	m "pfeifer.dev/mapd/math"
	"pfeifer.dev/mapd/params"
	ms "pfeifer.dev/mapd/settings"
	"pfeifer.dev/mapd/utils"
)

type TmpNode struct {
	Latitude  float64
	Longitude float64
}
type TmpWay struct {
	Name             string
	Ref              string
	Hazard           string
	MaxSpeed         float64
	MaxSpeedForward  float64
	MaxSpeedBackward float64
	MaxSpeedAdvisory float64
	Lanes            uint8
	Box              m.Box
	OneWay           bool
	HighwayClass     offline.HighwayClass
	Nodes            []TmpNode
	Id               int64

	MaxSpeedConditional         string
	MaxSpeedForwardConditional  string
	MaxSpeedBackwardConditional string
}

type Area struct {
	Box  m.Box
	Ways []TmpWay
}

func (a *Area) OverlapBox(overlap float64) m.Box {
	return m.Box{
		MinPos: m.NewPosition(a.Box.MinPos.Lat()-overlap, a.Box.MinPos.Lon()-overlap),
		MaxPos: m.NewPosition(a.Box.MaxPos.Lat()+overlap, a.Box.MaxPos.Lon()+overlap),
	}
}

type OfflineSettings struct {
	Context            context.Context
	Box                m.Box
	OutputDirectory    string
	InputFile          string
	GenerateEmptyFiles bool
	Overlap            float64
}

var DEFAULT_SETTINGS = OfflineSettings{
	OutputDirectory: fmt.Sprintf("%s/offline", params.GetBaseOpPath()),
}

func EnsureOfflineMapsDirectories(s OfflineSettings) {
	if err := os.MkdirAll(s.OutputDirectory, 0o775); err != nil {
		slog.Warn("could not make offline maps directory", "error", err, "directory", s.OutputDirectory)
	}
}

// Creates a file for a specific bounding box
func GenerateBoundsFileName(a Area, s OfflineSettings) string {
	p := a.Box.GroupPos()
	dir := fmt.Sprintf("%s/%d/%d", s.OutputDirectory, int(p.Lat()), int(p.Lon()))
	return fmt.Sprintf("%s/%f_%f_%f_%f", dir, a.Box.MinPos.Lat(), a.Box.MinPos.Lon(), a.Box.MaxPos.Lat(), a.Box.MaxPos.Lon())
}

// Creates a file for a specific bounding box
func CreateBoundsDir(a Area, s OfflineSettings) error {
	p := a.Box.GroupPos()
	dir := fmt.Sprintf("%s/%d/%d", s.OutputDirectory, int(p.Lat()), int(p.Lon()))
	err := os.MkdirAll(dir, 0o775)
	return errors.Wrap(err, "could not create bounds directory")
}

const (
	latitudeAreas  = int(180 / ms.AREA_BOX_DEGREES)
	longitudeAreas = int(360 / ms.AREA_BOX_DEGREES)
)

func areaBox(latitudeIndex int, longitudeIndex int) m.Box {
	minLatitude := -90 + float64(latitudeIndex)*ms.AREA_BOX_DEGREES
	minLongitude := -180 + float64(longitudeIndex)*ms.AREA_BOX_DEGREES
	return m.Box{
		MinPos: m.NewPosition(minLatitude, minLongitude),
		MaxPos: m.NewPosition(minLatitude+ms.AREA_BOX_DEGREES, minLongitude+ms.AREA_BOX_DEGREES),
	}
}

func GenerateOffline(s OfflineSettings) error {
	slog.Info("Generating Offline Map")
	if !validCoordinate(s.Box.MinPos.Lat(), s.Box.MinPos.Lon()) || !validCoordinate(s.Box.MaxPos.Lat(), s.Box.MaxPos.Lon()) ||
		s.Box.MinPos.Lat() >= s.Box.MaxPos.Lat() || s.Box.MinPos.Lon() >= s.Box.MaxPos.Lon() ||
		math.IsNaN(s.Overlap) || math.IsInf(s.Overlap, 0) || s.Overlap < 0 {
		return errors.New("invalid generation bounds or overlap")
	}
	if err := os.MkdirAll(s.OutputDirectory, 0o755); err != nil {
		return err
	}
	file, err := os.Open(s.InputFile)
	if err != nil {
		return errors.Wrap(err, "could not open map pbf file")
	}
	defer file.Close()

	// The third parameter is the number of parallel decoders to use.
	ctx := s.Context
	if ctx == nil {
		ctx = context.Background()
	}
	scanner := osmpbf.New(ctx, file, runtime.GOMAXPROCS(-1))
	scanner.SkipRelations = true
	defer scanner.Close()

	scannedWays := []TmpWay{}

	slog.Info("Scanning Ways")
	for scanner.Scan() {
		if err := ctx.Err(); err != nil {
			return err
		}
		var way *osm.Way
		switch o := scanner.Object(); o.(type) {
		case *osm.Way:
			way = o.(*osm.Way)
		default:
			way = nil
		}
		if way != nil && len(way.Nodes) > 1 {
			tags := way.TagMap()
			lanes, _ := strconv.ParseUint(tags["lanes"], 10, 8)
			tmpWay := TmpWay{
				Nodes:            make([]TmpNode, len(way.Nodes)),
				Name:             tags["name"],
				Ref:              tags["ref"],
				Hazard:           tags["hazard"],
				MaxSpeed:         ParseMaxSpeed(tags["maxspeed"]),
				MaxSpeedForward:  ParseMaxSpeed(tags["maxspeed:forward"]),
				MaxSpeedBackward: ParseMaxSpeed(tags["maxspeed:backward"]),
				MaxSpeedAdvisory: ParseMaxSpeed(tags["maxspeed:advisory"]),
				Lanes:            uint8(lanes),
				Id:               int64(way.ID),
				HighwayClass:     HighwayClassFromTag(tags["highway"]),

				MaxSpeedConditional:         tags["maxspeed:conditional"],
				MaxSpeedForwardConditional:  tags["maxspeed:forward:conditional"],
				MaxSpeedBackwardConditional: tags["maxspeed:backward:conditional"],
			}

			minLat := float64(90)
			minLon := float64(180)
			maxLat := float64(-90)
			maxLon := float64(-180)
			for i, n := range way.Nodes {
				if n.Lat < minLat {
					minLat = n.Lat
				}
				if n.Lon < minLon {
					minLon = n.Lon
				}
				if n.Lat > maxLat {
					maxLat = n.Lat
				}
				if n.Lon > maxLon {
					maxLon = n.Lon
				}
				tmpWay.Nodes[i].Latitude = n.Lat
				tmpWay.Nodes[i].Longitude = n.Lon
			}
			switch tags["oneway"] {
			case "yes", "1":
				tmpWay.OneWay = true
			case "-1":
				tmpWay.OneWay = true
				slices.Reverse(tmpWay.Nodes)
				tmpWay.MaxSpeedForward, tmpWay.MaxSpeedBackward = tmpWay.MaxSpeedBackward, tmpWay.MaxSpeedForward
				tmpWay.MaxSpeedForwardConditional, tmpWay.MaxSpeedBackwardConditional = tmpWay.MaxSpeedBackwardConditional, tmpWay.MaxSpeedForwardConditional
			case "":
				tmpWay.OneWay = tags["junction"] == "roundabout" || tags["highway"] == "motorway"
			}
			tmpWay.Box.MinPos = m.NewPosition(minLat, minLon)
			tmpWay.Box.MaxPos = m.NewPosition(maxLat, maxLon)
			scannedWays = append(scannedWays, tmpWay)
		}
	}
	if err := scanner.Err(); err != nil {
		return errors.Wrap(err, "could not scan map pbf file")
	}

	slog.Info("Finding Bounds")
	overlapBox := s.Box.Overlap(s.Overlap)
	latStart := max(0, int(math.Floor((overlapBox.MinPos.Lat()+90)/ms.AREA_BOX_DEGREES))-1)
	latEnd := min(latitudeAreas, int(math.Ceil((overlapBox.MaxPos.Lat()+90)/ms.AREA_BOX_DEGREES))+1)
	lonStart := max(0, int(math.Floor((overlapBox.MinPos.Lon()+180)/ms.AREA_BOX_DEGREES))-1)
	lonEnd := min(longitudeAreas, int(math.Ceil((overlapBox.MaxPos.Lon()+180)/ms.AREA_BOX_DEGREES))+1)
	for latIndex := latStart; latIndex < latEnd; latIndex++ {
		for lonIndex := lonStart; lonIndex < lonEnd; lonIndex++ {
			if err := ctx.Err(); err != nil {
				return err
			}
			area := Area{Box: areaBox(latIndex, lonIndex)}
			if !overlapBox.Contains(area.Box) {
				continue
			}

			arena := capnp.MultiSegment(nil)
			msg, seg, err := capnp.NewMessage(arena)
			if err != nil {
				return err
			}
			rootOffline, err := offline.NewRootOffline(seg)
			if err != nil {
				return err
			}

			for _, way := range scannedWays {

				overlaps := way.Box.Overlapping(area.OverlapBox(s.Overlap))
				if overlaps {
					area.Ways = append(area.Ways, way)
				}
			}
			if len(area.Ways) == 0 && !s.GenerateEmptyFiles {
				continue
			}

			slog.Info("Writing Area")
			ways, err := rootOffline.NewWays(int32(len(area.Ways)))
			if err != nil {
				return err
			}
			rootOffline.SetMinLat(area.Box.MinPos.Lat())
			rootOffline.SetMinLon(area.Box.MinPos.Lon())
			rootOffline.SetMaxLat(area.Box.MaxPos.Lat())
			rootOffline.SetMaxLon(area.Box.MaxPos.Lon())
			rootOffline.SetOverlap(s.Overlap)
			for i, way := range area.Ways {
				w := ways.At(i)
				w.SetId(way.Id)
				w.SetMinLat(way.Box.MinPos.Lat())
				w.SetMinLon(way.Box.MinPos.Lon())
				w.SetMaxLat(way.Box.MaxPos.Lat())
				w.SetMaxLon(way.Box.MaxPos.Lon())
				err := w.SetName(way.Name)
				if err != nil {
					return err
				}
				err = w.SetRef(way.Ref)
				if err != nil {
					return err
				}
				err = w.SetHazard(way.Hazard)
				if err != nil {
					return err
				}
				w.SetMaxSpeed(way.MaxSpeed)
				w.SetMaxSpeedForward(way.MaxSpeedForward)
				w.SetMaxSpeedBackward(way.MaxSpeedBackward)
				err = w.SetMaxSpeedConditional(way.MaxSpeedConditional)
				if err != nil {
					return err
				}
				err = w.SetMaxSpeedForwardConditional(way.MaxSpeedForwardConditional)
				if err != nil {
					return err
				}
				err = w.SetMaxSpeedBackwardConditional(way.MaxSpeedBackwardConditional)
				if err != nil {
					return err
				}
				w.SetAdvisorySpeed(way.MaxSpeedAdvisory)
				w.SetLanes(way.Lanes)
				w.SetOneWay(way.OneWay)
				w.SetHighwayClass(way.HighwayClass)
				nodes, err := w.NewNodes(int32(len(way.Nodes)))
				if err != nil {
					return err
				}
				for j, node := range way.Nodes {
					n := nodes.At(j)
					n.SetLatitude(node.Latitude)
					n.SetLongitude(node.Longitude)
				}
			}

			data, err := msg.MarshalPacked()
			if err != nil {
				return err
			}
			err = CreateBoundsDir(area, s)
			if err != nil {
				return err
			}
			err = os.WriteFile(GenerateBoundsFileName(area, s), data, 0o644)
			if err != nil {
				return err
			}
		}
	}
	f, err := os.Open(s.OutputDirectory)
	if err != nil {
		return err
	}
	syncErr := f.Sync()
	closeErr := f.Close()
	if syncErr != nil {
		return syncErr
	}
	if closeErr != nil {
		return closeErr
	}

	slog.Info("Done Generating Offline Map")
	return nil
}

func areaForPosition(pos m.Position) (Area, bool) {
	latitude := pos.Lat()
	longitude := pos.Lon()
	if math.IsNaN(latitude) || math.IsNaN(longitude) ||
		latitude < -90 || latitude > 90 || longitude < -180 || longitude > 180 {
		return Area{}, false
	}

	latitudeIndex := int(math.Ceil(latitude/ms.AREA_BOX_DEGREES)) + latitudeAreas/2 - 1
	longitudeIndex := int(math.Ceil(longitude/ms.AREA_BOX_DEGREES)) + longitudeAreas/2 - 1
	if latitudeIndex < 0 {
		latitudeIndex = 0
	}
	if longitudeIndex < 0 {
		longitudeIndex = 0
	}

	return Area{Box: areaBox(latitudeIndex, longitudeIndex)}, true
}

func FindWaysAroundPosition(pos m.Position) (Offline, error) {
	return FindWaysAroundPositionIn(pos, DEFAULT_SETTINGS.OutputDirectory)
}

// FindWaysAroundPositionIn keeps the tile root explicit for diagnostic runs.
// It never changes the normal provider's persisted/default settings.
func FindWaysAroundPositionIn(pos m.Position, outputDirectory string) (Offline, error) {
	return findWaysAroundPositionIn(pos, outputDirectory, "")
}

func findWaysAroundPositionIn(pos m.Position, outputDirectory, expectedDigest string) (Offline, error) {
	area, found := areaForPosition(pos)
	if !found {
		cBox := utils.Curry[m.Box]{}
		cBox.Set(area.Box)
		return Offline{Loaded: false, box: cBox}, nil
	}

	boundsName := GenerateBoundsFileName(area, OfflineSettings{OutputDirectory: outputDirectory})
	slog.Info("Loading bounds file", "filename", boundsName)
	// O_NONBLOCK prevents a FIFO at the expected basename from hanging before
	// fstat can reject it as a non-regular tile.
	fd, err := syscall.Open(boundsName, syscall.O_RDONLY|syscall.O_NOFOLLOW|syscall.O_NONBLOCK, 0)
	if err != nil {
		return Offline{}, errors.Wrap(err, "could not open current offline data file")
	}
	file := os.NewFile(uintptr(fd), boundsName)
	defer file.Close()
	before, err := file.Stat()
	if err != nil || !before.Mode().IsRegular() || before.Size() > maxOfflinePackedBytes {
		return Offline{}, errors.New("offline tile is not a regular file")
	}
	data, err := io.ReadAll(io.LimitReader(file, maxOfflinePackedBytes+1))
	if err != nil || len(data) > maxOfflinePackedBytes {
		return Offline{}, errors.New("could not read bounded offline tile")
	}
	if expectedDigest != "" {
		actual := sha256.Sum256(data)
		if hex.EncodeToString(actual[:]) != expectedDigest {
			return Offline{}, errors.New("offline tile differs from pinned snapshot receipt")
		}
	}
	after, err := file.Stat()
	if err != nil || !sameOfflineFile(before, after) {
		return Offline{}, errors.New("offline tile changed during read")
	}
	pathInfo, err := os.Lstat(boundsName)
	if err != nil || !pathInfo.Mode().IsRegular() || !sameOfflineFile(after, pathInfo) {
		return Offline{}, errors.New("offline tile path changed during read")
	}
	o := ReadOffline(data)
	if o.Loaded && !sameAreaBox(o.Box(), area.Box) {
		o = Offline{}
	}
	if !o.Loaded {
		o.box.Set(area.Box)
	} else {
		o.sourcePath = boundsName
		o.sourceInfo = pathInfo
	}
	return o, nil
}

func sameAreaBox(a, b m.Box) bool {
	return a.MinPos.Lat() == b.MinPos.Lat() && a.MinPos.Lon() == b.MinPos.Lon() &&
		a.MaxPos.Lat() == b.MaxPos.Lat() && a.MaxPos.Lon() == b.MaxPos.Lon()
}

func sameOfflineFile(a, b os.FileInfo) bool {
	if !os.SameFile(a, b) || a.Size() != b.Size() || !a.ModTime().Equal(b.ModTime()) {
		return false
	}
	// Modification time alone can miss an in-place rewrite. Unix stat exposes
	// change time as Ctim (Linux) or Ctimespec (Darwin).
	aStat, bStat := reflect.ValueOf(a.Sys()), reflect.ValueOf(b.Sys())
	if aStat.Kind() != reflect.Pointer || bStat.Kind() != reflect.Pointer {
		return false
	}
	for _, field := range []string{"Ctim", "Ctimespec"} {
		aTime, bTime := aStat.Elem().FieldByName(field), bStat.Elem().FieldByName(field)
		if aTime.IsValid() && bTime.IsValid() {
			return reflect.DeepEqual(aTime.Interface(), bTime.Interface())
		}
	}
	return false
}

func ParseMaxSpeed(maxspeed string) float64 {
	splitSpeed := strings.Split(maxspeed, " ")
	if len(splitSpeed) == 0 {
		return 0
	}

	numeric, err := strconv.ParseUint(splitSpeed[0], 10, 64)
	if err != nil {
		return 0
	}

	if len(splitSpeed) == 1 {
		return 0.277778 * float64(numeric)
	}

	if splitSpeed[1] == "kph" || splitSpeed[1] == "km/h" || splitSpeed[1] == "kmh" {
		return 0.277778 * float64(numeric)
	} else if splitSpeed[1] == "mph" {
		return 0.44704 * float64(numeric)
	} else if splitSpeed[1] == "knots" {
		return 0.514444 * float64(numeric)
	}

	return 0
}
