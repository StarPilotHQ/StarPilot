package maps

import (
	"math"
	"strings"

	"github.com/pkg/errors"

	"pfeifer.dev/mapd/cereal/log"
	"pfeifer.dev/mapd/cereal/offline"
	m "pfeifer.dev/mapd/math"
	ms "pfeifer.dev/mapd/settings"
	u "pfeifer.dev/mapd/utils"
)

type RoadContext int

const (
	CONTEXT_FREEWAY RoadContext = iota
	CONTEXT_CITY
	CONTEXT_UNKNOWN
)

// Highway hierarchy ranking
var HIGHWAY_RANK = map[string]int{
	"motorway":       0,
	"motorway_link":  1,
	"trunk":          10,
	"trunk_link":     11,
	"primary":        20,
	"primary_link":   21,
	"secondary":      30,
	"secondary_link": 31,
	"tertiary":       40,
	"tertiary_link":  41,
	"unclassified":   50,
	"residential":    60,
	"living_street":  61,
}

// OSM highway tag value -> class stored in the offline data
var HIGHWAY_TAG_TO_CLASS = map[string]offline.HighwayClass{
	"motorway":       offline.HighwayClass_motorway,
	"motorway_link":  offline.HighwayClass_motorwayLink,
	"trunk":          offline.HighwayClass_trunk,
	"trunk_link":     offline.HighwayClass_trunkLink,
	"primary":        offline.HighwayClass_primary,
	"primary_link":   offline.HighwayClass_primaryLink,
	"secondary":      offline.HighwayClass_secondary,
	"secondary_link": offline.HighwayClass_secondaryLink,
	"tertiary":       offline.HighwayClass_tertiary,
	"tertiary_link":  offline.HighwayClass_tertiaryLink,
	"unclassified":   offline.HighwayClass_unclassified,
	"residential":    offline.HighwayClass_residential,
	"living_street":  offline.HighwayClass_livingStreet,
}

func HighwayClassFromTag(tag string) offline.HighwayClass {
	if class, ok := HIGHWAY_TAG_TO_CLASS[tag]; ok {
		return class
	}
	return offline.HighwayClass_unknown
}

// Road type detection and priorities
var LANE_COUNT_PRIORITY = map[uint8]int{
	8: 110, // Major freeway
	6: 100, // Freeway
	5: 95,
	4: 90, // Major arterial
	3: 70, // Arterial
	2: 50, // Collector/local
	1: 40, // Local street
	0: 30, // Unknown
}

type OnWayResult struct {
	OnWay     bool
	Distance  DistanceResult
	IsForward bool
}

type DistanceResult struct {
	LineStart    m.Position
	LineEnd      m.Position
	LinePosition m.LinePosition
	Distance     float32
}

type NextWayResult struct {
	Way           Way
	IsForward     bool
	StartPosition m.Position
	EndPosition   m.Position
}

type Way struct {
	Way offline.Way

	// calculated values
	width              u.Curry[float32]
	context            u.Curry[RoadContext]
	isFreeway          u.Curry[bool]
	name               u.Curry[string]
	distance           u.Curry[float32]
	rank               u.Curry[int]
	priority           u.Curry[int]
	distanceMultiplier u.Curry[float32]

	// values from offline file
	oneWay           u.Curry[bool]
	highwayClass     u.Curry[offline.HighwayClass]
	wayName          u.Curry[string]
	wayRef           u.Curry[string]
	maxSpeed         u.Curry[float64]
	box              u.Curry[m.Box]
	Nodes            u.CurryList[m.Position]
	nodesRaw         offline.Coordinates_List
	lanes            u.Curry[int]
	advisorySpeed    u.Curry[float64]
	hazard           u.Curry[string]
	maxSpeedForward  u.Curry[float64]
	maxSpeedBackward u.Curry[float64]

	maxSpeedConditional           u.Curry[string]
	maxSpeedForwardConditional    u.Curry[string]
	maxSpeedBackwardConditional   u.Curry[string]
	conditionalSpeedRulesForward  u.Curry[[]ConditionalSpeedRule]
	conditionalSpeedRulesBackward u.Curry[[]ConditionalSpeedRule]

	id u.Curry[int64]
}

func NewWay(way offline.Way) Way {
	nodes, _ := way.Nodes()
	w := Way{
		Way: way,
	}
	w.nodesRaw = nodes
	w.Nodes.Init(w._nodeAt, nodes.Len())
	return w
}

func (w *Way) IsForwardFrom(matchNode m.Position) bool {
	if w.Nodes.Len() == 0 {
		return true
	}
	if w.Nodes.Len() < 2 {
		return true
	}

	lastNode := w.Nodes.At(w.Nodes.Len() - 1)
	if lastNode.Equals(matchNode) {
		// A closed one-way can be entered at its repeated endpoint in stored order.
		return w.OneWay() && matchNode.Equals(w.Nodes.At(0))
	}
	return true
}

func IsForward(lineStart m.Position, lineEnd m.Position, bearing float64) bool {
	vec := lineStart.VectorTo(lineEnd)
	wayBearing := vec.Bearing()
	bearingDelta := math.Abs(bearing*ms.TO_RADIANS - wayBearing)
	return math.Cos(bearingDelta) >= 0
}

func (w *Way) _nodeAt(index int) m.Position {
	n := w.nodesRaw.At(index)
	return m.NewPosition(n.Latitude(), n.Longitude())
}

func (w *Way) _oneWay() bool {
	return w.Way.OneWay()
}

func (w *Way) OneWay() bool {
	return w.oneWay.Value(w._oneWay)
}

func (w *Way) _highwayClass() offline.HighwayClass {
	return w.Way.HighwayClass()
}

func (w *Way) HighwayClass() offline.HighwayClass {
	return w.highwayClass.Value(w._highwayClass)
}

func (w *Way) _id() int64 {
	return w.Way.Id()
}

func (w *Way) Id() int64 {
	return w.id.Value(w._id)
}

func (w *Way) _wayName() string {
	wn, err := w.Way.Name()
	if err != nil {
		wn = ""
	}
	return wn
}

func (w *Way) WayName() string {
	return w.wayName.Value(w._wayName)
}

func (w *Way) _wayRef() string {
	wr, err := w.Way.Ref()
	if err != nil {
		wr = ""
	}
	return wr
}

func (w *Way) WayRef() string {
	return w.wayRef.Value(w._wayRef)
}

func (w *Way) _maxSpeed() float64 {
	return w.Way.MaxSpeed()
}

func (w *Way) MaxSpeed() float64 {
	return w.maxSpeed.Value(w._maxSpeed)
}

func (w *Way) _maxSpeedForward() float64 {
	return w.Way.MaxSpeedForward()
}

func (w *Way) MaxSpeedForward() float64 {
	return w.maxSpeedForward.Value(w._maxSpeedForward)
}

func (w *Way) _maxSpeedBackward() float64 {
	return w.Way.MaxSpeedBackward()
}

func (w *Way) MaxSpeedBackward() float64 {
	return w.maxSpeedBackward.Value(w._maxSpeedBackward)
}

func (w *Way) _maxSpeedConditional() string {
	msc, err := w.Way.MaxSpeedConditional()
	if err != nil {
		msc = ""
	}
	return msc
}

func (w *Way) MaxSpeedConditional() string {
	return w.maxSpeedConditional.Value(w._maxSpeedConditional)
}

func (w *Way) _maxSpeedForwardConditional() string {
	msc, err := w.Way.MaxSpeedForwardConditional()
	if err != nil {
		msc = ""
	}
	return msc
}

func (w *Way) MaxSpeedForwardConditional() string {
	return w.maxSpeedForwardConditional.Value(w._maxSpeedForwardConditional)
}

func (w *Way) _maxSpeedBackwardConditional() string {
	msc, err := w.Way.MaxSpeedBackwardConditional()
	if err != nil {
		msc = ""
	}
	return msc
}

func (w *Way) MaxSpeedBackwardConditional() string {
	return w.maxSpeedBackwardConditional.Value(w._maxSpeedBackwardConditional)
}

// the raw conditional tag for the direction of travel, falling back to the
// undirected tag like the directional max speeds do
func (w *Way) ConditionalMaxSpeedRaw(isForward bool) string {
	if isForward && w.MaxSpeedForwardConditional() != "" {
		return w.MaxSpeedForwardConditional()
	}
	if !isForward && w.MaxSpeedBackwardConditional() != "" {
		return w.MaxSpeedBackwardConditional()
	}
	return w.MaxSpeedConditional()
}

func (w *Way) _conditionalSpeedRulesForward() []ConditionalSpeedRule {
	return ParseConditionalSpeeds(w.ConditionalMaxSpeedRaw(true))
}

func (w *Way) _conditionalSpeedRulesBackward() []ConditionalSpeedRule {
	return ParseConditionalSpeeds(w.ConditionalMaxSpeedRaw(false))
}

func (w *Way) ConditionalSpeedRules(isForward bool) []ConditionalSpeedRule {
	if isForward {
		return w.conditionalSpeedRulesForward.Value(w._conditionalSpeedRulesForward)
	}
	return w.conditionalSpeedRulesBackward.Value(w._conditionalSpeedRulesBackward)
}

func (w *Way) _box() m.Box {
	return m.Box{
		MinPos: m.NewPosition(w.Way.MinLat(), w.Way.MinLon()),
		MaxPos: m.NewPosition(w.Way.MaxLat(), w.Way.MaxLon()),
	}
}

func (w *Way) Box() m.Box {
	return w.box.Value(w._box)
}

func (w *Way) _lanes() int {
	return int(w.Way.Lanes())
}

func (w *Way) Lanes() int {
	return w.lanes.Value(w._lanes)
}

func (w *Way) _advisorySpeed() float64 {
	return w.Way.AdvisorySpeed()
}

func (w *Way) AdvisorySpeed() float64 {
	return w.advisorySpeed.Value(w._advisorySpeed)
}

func (w *Way) _hazard() string {
	hazard, err := w.Way.Hazard()
	if err != nil {
		hazard = ""
	}
	return hazard
}

func (w *Way) Hazard() string {
	return w.hazard.Value(w._hazard)
}

func (w *Way) OnWay(location log.GpsLocationData, distanceMultiplier float32) (OnWayResult, error) {
	res := OnWayResult{}
	pos := m.NewPosition(location.Latitude(), location.Longitude())
	box := w.Box()
	oversizedBox := box.Overlap(0.01)
	if !oversizedBox.PosInside(pos) {
		res.OnWay = false
		return res, nil
	}
	max_dist := MatchDistance(location, w.Width(), distanceMultiplier)
	if !MayBeWithin(box, pos, max_dist) {
		// No point of the way can be close enough; skip the per-segment
		// distance. The result is the same OnWay=false the scan would give.
		res.OnWay = false
		return res, nil
	}
	d, err := w.DistanceFrom(pos)
	res.Distance = d
	if err != nil {
		res.OnWay = false
		return res, errors.Wrap(err, "could not get distance to way")
	}

	if d.Distance < max_dist {
		res.OnWay = true
		res.IsForward = IsForward(d.LineStart, d.LineEnd, float64(location.BearingDeg()))
		if !res.IsForward && w.OneWay() {
			res.OnWay = false
		}
		return res, nil
	}
	res.OnWay = false
	return res, nil
}

// MatchDistance is the distance from a way, in metres, within which OnWay
// accepts a fix: the fix's accuracy (at least 5 m) plus the road width,
// scaled by the caller's multiplier.
func MatchDistance(location log.GpsLocationData, width float32, distanceMultiplier float32) float32 {
	return (max(location.HorizontalAccuracy(), 5) + width) * distanceMultiplier
}

// MayBeWithin is false only when every point of box is at least maxDist
// metres from pos by DistanceTo's haversine. Validated way nodes, and so
// every segment point DistanceFrom measures to, lie inside the way's box.
// The pads are twice the bound: the haversine distance is at least R*dLat
// and at least (2/pi)*R*cos(maxLat)*dLon.
func MayBeWithin(box m.Box, pos m.Position, maxDist float32) bool {
	dist := float64(maxDist)
	latPad := 2 * dist / ms.R * ms.TO_DEGREES
	maxLat := max(math.Abs(box.MinPos.Lat()), math.Abs(box.MaxPos.Lat()), math.Abs(pos.Lat()))
	if !(maxLat < 89) {
		return true // too close to a pole for the longitude bound; measure instead
	}
	lonPad := 2 * dist / (ms.R * math.Cos(maxLat*ms.TO_RADIANS)) * ms.TO_DEGREES
	padded := m.Box{
		MinPos: m.NewPosition(box.MinPos.Lat()-latPad, box.MinPos.Lon()-lonPad),
		MaxPos: m.NewPosition(box.MaxPos.Lat()+latPad, box.MaxPos.Lon()+lonPad),
	}
	return padded.PosInside(pos)
}

func (w *Way) BearingAlignment(location log.GpsLocationData) (float32, error) {
	pos := m.NewPosition(location.Latitude(), location.Longitude())
	d, err := w.DistanceFrom(pos)
	if err != nil {
		return 1.0, err
	}

	lineVec := d.LineStart.VectorTo(d.LineEnd)
	wayBearing := lineVec.Bearing()

	// Compare the undirected segment axis. GPS headings and atan2 bearings
	// use different wrap ranges; a single 2*pi subtraction can produce a
	// negative penalty and incorrectly reward a crossing road.
	delta := float64(location.BearingDeg())*ms.TO_RADIANS - wayBearing
	return float32(math.Abs(math.Sin(delta))), nil
}

func (w *Way) DistanceFrom(pos m.Position) (DistanceResult, error) {
	res := DistanceResult{}
	var minNodeStart m.Position
	var minNodeEnd m.Position
	minDistance := float32(math.MaxFloat32)
	if w.Nodes.Len() < 2 {
		return res, errors.New("not enough nodes to determine distance")
	}

	minLinePosition := m.LinePosition{}
	minIdx := 0
	for i := 0; i < w.Nodes.Len()-1; i++ {
		nodeStart := w.Nodes.At(i)
		nodeEnd := w.Nodes.At(i + 1)
		line := m.Line{Start: nodeStart, End: nodeEnd}
		linePosition := line.NearestPosition(pos)
		distance := pos.DistanceTo(linePosition.Pos)

		if distance < minDistance {
			minDistance = distance
			minNodeStart = nodeStart
			minNodeEnd = nodeEnd
			minLinePosition = linePosition
			minIdx = i
		}
	}
	onWayDistance := minNodeStart.DistanceTo(minLinePosition.Pos)
	for i := range minIdx {
		nodeStart := w.Nodes.At(i)
		nodeEnd := w.Nodes.At(i + 1)
		onWayDistance += nodeStart.DistanceTo(nodeEnd)
	}

	res.Distance = minDistance
	res.LineStart = minNodeStart
	res.LineEnd = minNodeEnd
	res.LinePosition = minLinePosition
	return res, nil
}

func (w *Way) GetStartEnd(isForward bool) (m.Position, m.Position) {
	if w.Nodes.Len() == 0 {
		return m.Position{}, m.Position{}
	}

	if w.Nodes.Len() == 1 {
		return w.Nodes.At(0), w.Nodes.At(0)
	}

	if isForward {
		return w.Nodes.At(0), w.Nodes.At(w.Nodes.Len() - 1)
	}
	return w.Nodes.At(w.Nodes.Len() - 1), w.Nodes.At(0)
}

func (w *Way) MatchingWays(offlineMaps *Offline, matchNode m.Position) ([]Way, error) {
	matchingWays := []Way{}

	candidates, indexed := offlineMaps.WaysWithEndpoint(matchNode)
	count := len(candidates)
	if !indexed {
		count = offlineMaps.Ways.Len()
	}
	for c := range count {
		i := c
		if indexed {
			i = int(candidates[c])
		}
		way := offlineMaps.Ways.At(i)
		if way.Nodes.Len() == 0 {
			continue
		}

		if way.Id() == w.Id() {
			continue
		}

		if way.Nodes.Len() < 2 {
			continue
		}

		fNode := way.Nodes.At(0)
		lNode := way.Nodes.At(way.Nodes.Len() - 1)
		if fNode.Equals(matchNode) || lNode.Equals(matchNode) {
			matchingWays = append(matchingWays, way)
		}
	}

	return matchingWays, nil
}

func (w *Way) isValidConnection(matchNode, bearingNode m.Position, maxCurvature float64) bool {
	if w.Nodes.Len() < 2 {
		return false
	}

	var nextBearingNode m.Position
	if matchNode.Equals(w.Nodes.At(0)) {
		nextBearingNode = w.Nodes.At(1)
	} else {
		nextBearingNode = w.Nodes.At(w.Nodes.Len() - 2)
	}

	curv := m.CalculateCurvature(bearingNode, matchNode, nextBearingNode)
	return math.Abs(curv.Curvature) <= maxCurvature
}

func (w *Way) DistanceToEnd(pos m.Position, isForward bool) (float32, error) {
	if w.Nodes.Len() == 0 {
		return 0, nil
	}
	dist, _, err := w.DistanceToNode(pos, isForward, w.Nodes.At(w.Nodes.Len()-1))
	return dist, err
}

func (w *Way) DistanceToNode(pos m.Position, isForward bool, finalNode m.Position) (float32, bool, error) {
	distanceResult, err := w.DistanceFrom(pos)
	if err != nil {
		return 0, false, err
	}
	dist := float32(0.0)
	stopFiltering := false
	lastPos := distanceResult.LinePosition.Pos
	found := false
	for i := range w.Nodes.Len() {
		index := i
		if !isForward {
			index = w.Nodes.Len() - 1 - i
		}
		node := w.Nodes.At(index)
		if (node.Equals(distanceResult.LineStart) || node.Equals(distanceResult.LineEnd)) && !stopFiltering {
			stopFiltering = true
			continue
		}
		if !stopFiltering {
			continue
		}

		dist += lastPos.DistanceTo(node)
		lastPos = node
		if lastPos.Equals(finalNode) {
			found = true
			break
		}
	}
	return dist, found, nil
}

func (w *Way) _distance() float32 {
	if w.Nodes.Len() < 2 {
		return 0
	}

	totalDistance := float32(0.0)
	for i := range w.Nodes.Len() - 1 {
		nodeStart := w.Nodes.At(i)
		nodeEnd := w.Nodes.At(i + 1)
		distance := nodeStart.DistanceTo(nodeEnd)
		totalDistance += distance
	}

	return totalDistance
}

func (w *Way) Distance() float32 {
	return w.distance.Value(w._distance)
}

func (w *Way) _name() string {
	name, err := w.Way.Name()
	if err == nil {
		if len(name) > 0 {
			return name
		}
	}
	ref, err := w.Way.Ref()
	if err == nil {
		if len(ref) > 0 {
			return ref
		}
	}
	return ""
}

func (w *Way) Name() string {
	return w.name.Value(w._name)
}

func (w *Way) _width() float32 {
	lanes := w.Way.Lanes()
	if lanes == 0 {
		lanes = 2
	}
	width := float32(lanes) * ms.Settings.DefaultLaneWidth
	return width
}

func (w *Way) Width() float32 {
	return w.width.Value(w._width)
}

func (w *Way) _context() RoadContext {
	lanes := w.Way.Lanes()
	name, _ := w.Way.Name()
	ref, _ := w.Way.Ref()

	if w.IsFreeway() || lanes >= 4 {
		return CONTEXT_FREEWAY
	}

	nameUpper := strings.ToUpper(name)
	if lanes <= 3 && (strings.Contains(nameUpper, "STREET") ||
		strings.Contains(nameUpper, "AVENUE") ||
		strings.Contains(nameUpper, "BOULEVARD") ||
		strings.Contains(nameUpper, "ROAD") ||
		len(ref) == 0) {
		return CONTEXT_CITY
	}
	return CONTEXT_UNKNOWN
}

func (w *Way) Context() RoadContext {
	return w.context.Value(w._context)
}

func (w *Way) _isFreeway() bool {
	lanes := w.Way.Lanes()
	name, _ := w.Way.Name()
	ref, _ := w.Way.Ref()

	if lanes >= 6 {
		return true
	}

	nameUpper := strings.ToUpper(name)
	refUpper := strings.ToUpper(ref)

	if strings.Contains(nameUpper, "INTERSTATE") ||
		strings.Contains(nameUpper, "FREEWAY") ||
		strings.Contains(nameUpper, "EXPRESSWAY") ||
		strings.Contains(nameUpper, "PARKWAY") ||
		strings.HasPrefix(refUpper, "I-") ||
		strings.HasPrefix(refUpper, "I ") ||
		(lanes >= 4 && len(ref) > 0 && !strings.Contains(nameUpper, "STREET")) {
		return true
	}

	return false
}

func (w *Way) IsFreeway() bool {
	return w.isFreeway.Value(w._isFreeway)
}

func (w *Way) _rank() int {
	name, _ := w.Way.Name()
	ref, _ := w.Way.Ref()
	lanes := w.Way.Lanes()

	// Infer highway type from characteristics
	if w.IsFreeway() {
		if lanes >= 6 {
			return HIGHWAY_RANK["motorway"]
		}
		return HIGHWAY_RANK["trunk"]
	}

	nameUpper := strings.ToUpper(name)
	refUpper := strings.ToUpper(ref)

	// Primary roads (usually have ref numbers)
	if len(ref) > 0 && !strings.Contains(nameUpper, "STREET") {
		if strings.HasPrefix(refUpper, "US-") || strings.HasPrefix(refUpper, "SR-") {
			return HIGHWAY_RANK["primary"]
		}

		return HIGHWAY_RANK["secondary"]
	}

	// Local roads
	if strings.Contains(nameUpper, "STREET") ||
		strings.Contains(nameUpper, "AVENUE") ||
		strings.Contains(nameUpper, "ROAD") {
		return HIGHWAY_RANK["residential"]
	}

	// Default to unclassified
	return HIGHWAY_RANK["unclassified"]
}

// Get highway hierarchy rank for a way
func (w *Way) Rank() int {
	return w.rank.Value(w._rank)
}

func (w *Way) _priority() int {
	lanes := w.Way.Lanes()
	name, _ := w.Way.Name()
	ref, _ := w.Way.Ref()

	priority := LANE_COUNT_PRIORITY[lanes]
	if priority == 0 {
		priority = 30
	}

	switch w.Context() {
	case CONTEXT_FREEWAY:
		if w.IsFreeway() {
			priority += 30
		}
		nameUpper := strings.ToUpper(name)
		if strings.Contains(nameUpper, "STREET") ||
			strings.Contains(nameUpper, "AVENUE") {
			priority -= 40
		}

	case CONTEXT_CITY:
		if w.IsFreeway() {
			priority += 10
		}
		nameUpper := strings.ToUpper(name)
		if strings.Contains(nameUpper, "SERVICE") {
			priority -= 5
		}
		// Boost local street names in city context
		if strings.Contains(nameUpper, "STREET") ||
			strings.Contains(nameUpper, "AVENUE") ||
			strings.Contains(nameUpper, "ROAD") {
			priority += 5
		}

	case CONTEXT_UNKNOWN:
		if w.IsFreeway() {
			priority += 20
		}
	}

	if len(ref) > 0 {
		priority += 10
	}

	return priority
}

func (w *Way) Priority() int {
	return w.priority.Value(w._priority)
}

func (w *Way) _distanceMultiplier() float32 {
	switch w.Context() {
	case CONTEXT_CITY:
		return 0.75
	case CONTEXT_FREEWAY:
		return 1.5
	default:
		return 1
	}
}

func (w *Way) DistanceMultiplier() float32 {
	return w.distanceMultiplier.Value(w._distanceMultiplier)
}

func (w *Way) NextWay(offlineMaps *Offline, isForward bool) (NextWayResult, error) {
	if w.Nodes.Len() == 0 {
		return NextWayResult{}, nil
	}

	var matchNode m.Position
	var matchBearingNode m.Position
	if isForward {
		matchNode = w.Nodes.At(w.Nodes.Len() - 1)
		if w.Nodes.Len() > 1 {
			matchBearingNode = w.Nodes.At(w.Nodes.Len() - 2)
		}
	} else {
		matchNode = w.Nodes.At(0)
		if w.Nodes.Len() > 1 {
			matchBearingNode = w.Nodes.At(1)
		}
	}

	box := offlineMaps.OverlapBox()
	if !box.PosInside(matchNode) {
		return NextWayResult{}, nil
	}

	matchingWays, err := w.MatchingWays(offlineMaps, matchNode)
	if err != nil {
		return NextWayResult{StartPosition: matchNode}, errors.Wrap(err, "could not check for next ways")
	}

	legalWays := []Way{}
	for _, mWay := range matchingWays {
		isForward := mWay.IsForwardFrom(matchNode)
		if !isForward && mWay.OneWay() {
			continue
		}
		legalWays = append(legalWays, mWay)
	}
	matchingWays = legalWays

	if len(matchingWays) == 0 {
		return NextWayResult{StartPosition: matchNode}, nil
	}

	if w.Context() == CONTEXT_FREEWAY {
		filteredWays := []Way{}
		for _, mWay := range matchingWays {
			name := mWay.WayName()
			nameUpper := strings.ToUpper(name)
			if !strings.Contains(nameUpper, "SERVICE") &&
				!strings.Contains(nameUpper, "ACCESS") &&
				!(strings.Contains(nameUpper, "RAMP") && mWay.Lanes() < 2) {
				filteredWays = append(filteredWays, mWay)
			}
		}
		if len(filteredWays) > 0 {
			matchingWays = filteredWays
		}
	}

	curvatureThreshold := 0.15
	if w.Context() == CONTEXT_CITY {
		curvatureThreshold = 0.3
	} else if w.Context() == CONTEXT_FREEWAY {
		curvatureThreshold = 0.1
	}

	name := w.WayName()
	if len(name) > 0 {
		candidates := []Way{}
		for _, mWay := range matchingWays {
			mName := mWay.WayName()
			if mName == name {
				if w.Nodes.Len() > 1 && mWay.isValidConnection(matchNode, matchBearingNode, curvatureThreshold) {
					candidates = append(candidates, mWay)
				}
			}
		}

		if len(candidates) > 0 {
			bestWay := selectBestCandidate(candidates, w.Context())
			isForward := bestWay.IsForwardFrom(matchNode)
			start, end := bestWay.GetStartEnd(isForward)
			return NextWayResult{
				Way:           bestWay,
				StartPosition: start,
				EndPosition:   end,
				IsForward:     isForward,
			}, nil
		}
	}

	ref := w.WayRef()
	if len(ref) > 0 {
		candidates := []Way{}
		for _, mWay := range matchingWays {
			mRef := mWay.WayRef()
			if mRef == ref {
				if w.Nodes.Len() > 1 && mWay.isValidConnection(matchNode, matchBearingNode, curvatureThreshold) {
					candidates = append(candidates, mWay)
				}
			}
		}

		if len(candidates) > 0 {
			bestWay := selectBestCandidate(candidates, w.Context())
			isForward := bestWay.IsForwardFrom(matchNode)
			start, end := bestWay.GetStartEnd(isForward)
			return NextWayResult{
				Way:           bestWay,
				StartPosition: start,
				EndPosition:   end,
				IsForward:     isForward,
			}, nil
		}
	}

	if len(ref) > 0 {
		refs := strings.Split(ref, ";")
		candidates := []Way{}
		for _, mWay := range matchingWays {
			mRef := mWay.WayRef()
			mRefs := strings.Split(mRef, ";")
			hasMatch := false
			for _, r := range refs {
				for _, mr := range mRefs {
					hasMatch = hasMatch || (strings.TrimSpace(r) == strings.TrimSpace(mr))
				}
			}
			if hasMatch {
				if w.Nodes.Len() > 1 && mWay.isValidConnection(matchNode, matchBearingNode, curvatureThreshold) {
					candidates = append(candidates, mWay)
				}
			}
		}

		if len(candidates) > 0 {
			bestWay := selectBestCandidate(candidates, w.Context())
			isForward := bestWay.IsForwardFrom(matchNode)
			start, end := bestWay.GetStartEnd(isForward)
			return NextWayResult{
				Way:           bestWay,
				StartPosition: start,
				EndPosition:   end,
				IsForward:     isForward,
			}, nil
		}
	}

	validWays := []Way{}
	for _, mWay := range matchingWays {
		if w.Nodes.Len() > 1 && mWay.isValidConnection(matchNode, matchBearingNode, curvatureThreshold) {
			validWays = append(validWays, mWay)
		}
	}

	if len(validWays) > 0 {
		bestWay := selectBestCandidate(validWays, w.Context())
		nextIsForward := bestWay.IsForwardFrom(matchNode)
		start, end := bestWay.GetStartEnd(nextIsForward)
		return NextWayResult{
			Way:           bestWay,
			StartPosition: start,
			EndPosition:   end,
			IsForward:     nextIsForward,
		}, nil
	}

	if len(matchingWays) > 0 {
		nextIsForward := matchingWays[0].IsForwardFrom(matchNode)
		start, end := matchingWays[0].GetStartEnd(nextIsForward)
		return NextWayResult{
			Way:           matchingWays[0],
			StartPosition: start,
			EndPosition:   end,
			IsForward:     nextIsForward,
		}, nil
	}

	return NextWayResult{StartPosition: matchNode}, nil
}

func selectBestCandidate(candidates []Way, context RoadContext) Way {
	if len(candidates) == 1 {
		return candidates[0]
	}

	bestWay := candidates[0]
	bestScore := float64(-1000)

	for _, way := range candidates {
		score := float64(way.Priority())

		lanes := way.Lanes()
		if lanes > 0 {
			laneWeight := 2.0
			if context == CONTEXT_FREEWAY {
				laneWeight = 4.0
			} else if context == CONTEXT_CITY {
				laneWeight = 1.0
			}
			score += float64(lanes) * laneWeight
		}

		if score > bestScore {
			bestScore = score
			bestWay = way
		}
	}

	return bestWay
}
