using Go = import "/go.capnp";
@0xb526ba661d550a59;
$Go.package("custom");
$Go.import("pfeifer.dev/mapd/cereal/custom");

# custom.capnp: a home for empty structs reserved for custom forks
# These structs are guaranteed to remain reserved and empty in mainline
# cereal, so use these if you want custom events in your fork.

# DO rename the structs
# DON'T change the identifier (e.g. @0x81c2f05a394cf4af)

struct StarPilotCarControl @0x81c2f05a394cf4af {
  # The first three fields are pointer slots mapd does not read.
  hudControl @0 :AnyPointer;
  steeringLimitInfo @1 :AnyPointer;
  navigation @2 :AnyPointer;
  phoneGps @3 :StarPilotCarState.Gps;  # sourceMonoTime is comma receive time minus the phone's fix age
}

struct CustomReserved1 @0xaedffd8f31e7b55d {
}

struct StarPilotCarState @0xf35cc4560bbf6ec2 {
  accelPressed @0 :Bool;
  alwaysOnLateralEnabled @1 :Bool;
  brakeLights @2 :Bool;
  dashboardSpeedLimit @3 :Float32;
  decelPressed @4 :Bool;
  distancePressed @5 :Bool;
  distanceLongPressed @6 :Bool;
  distanceVeryLongPressed @7 :Bool;
  ecoGear @8 :Bool;
  forceCoast @9 :Bool;
  isParked @10 :Bool;
  pauseLateral @11 :Bool;
  pauseLongitudinal @12 :Bool;
  sportGear @13 :Bool;
  trafficModeEnabled @14 :Bool;
  gasStack @15 :Bool;  # Compatibility with older StarPilot payloads
  modePressed @16 :Bool;
  customPressed @17 :Bool;
  alwaysOnLateralAllowed @18 :Bool;
  dashboardStopSign @19 :UInt8;  # 0 = no signal / platform doesn't publish
  cancelPressed @20 :Bool;
  cancelLongPressed @21 :Bool;
  cancelVeryLongPressed @22 :Bool;
  pedalMaxRegen @23 :Bool;  # pedal at max regen, driver should use brake for more decel
  pedalLongActive @24 :Bool;  # Pre-AP pedal longitudinal mode is active (enableLongControl)
  teslaCCEngaged @25 :Bool;  # rising edge of stock Tesla CC engaging (no-pedal mode)
  teslaCCDisengaged @26 :Bool;  # falling edge of stock Tesla CC
  teslaCCNotArmed @27 :Bool;  # lateral engaged but DI_cruiseState != STANDBY/ENABLED
  accelHardCruise @28 :Bool;  # current/releasing accel cruise button came from GM hard-press signal
  decelHardCruise @29 :Bool;  # current/releasing decel cruise button came from GM hard-press signal
  pulseAndGlide @30 :Bool;  # developer-only wheel-button pulse-and-glide mode is enabled
  gps @31 :Gps;

  struct Gps {
    sourceMonoTime @0 :UInt64;  # CAN observation converted to CLOCK_MONOTONIC
    latitude @1 :Float64;
    longitude @2 :Float64;
    altitude @3 :Float64;
    speed @4 :Float32;
    bearingDeg @5 :Float32;
    horizontalAccuracy @6 :Float32;
    verticalAccuracy @7 :Float32;
    bearingAccuracyDeg @8 :Float32;
    speedAccuracy @9 :Float32;
    unixTimestampMillis @10 :Int64;
    hasFix @11 :Bool;
    vNED @12 :List(Float32);
  }
}

struct CustomReserved3 @0xda96579883444c35 {
}

struct CustomReserved4 @0x80ae746ee2596b11 {
}

struct CustomReserved5 @0xa5cd762cd951a455 {
}

struct CustomReserved6 @0xf98d843bfd7004a3 {
}

struct CustomReserved7 @0xb86e6369214c01c8 {
}

struct CustomReserved8 @0xf416ec09499d9d19 {
}

struct CustomReserved9 @0xa1680744031fdb2d {
}

struct CustomReserved10 @0xcb9fd56c7057593a {
}


struct CustomReserved11 @0xc2243c65e0340384 {
}

struct CustomReserved12 @0x9ccdc8676701b412 {
}

struct CustomReserved13 @0xcd96dafb67a082d0 {
}

struct CustomReserved14 @0xb057204d7deadf3f {
}

struct CustomReserved15 @0xbd443b539493bc68 {
}

struct CustomReserved16 @0xfc6241ed8877b611 {
}

struct MapdDownloadLocationDetails @0xff889853e7b0987f {
  location @0 :Text;
  totalFiles @1 :UInt32;
  downloadedFiles @2 :UInt32;
}

struct MapdDownloadProgress @0xfaa35dcac85073a2 {
  active @0 :Bool;
  cancelled @1 :Bool;
  totalFiles @2 :UInt32;
  downloadedFiles @3 :UInt32;
  locations @4 :List(Text);
  locationDetails @5 :List(MapdDownloadLocationDetails);
}

struct MapdPathPoint @0xd6f78acca1bc3939 {
  latitude @0 :Float64;
  longitude @1 :Float64;
  curvature @2 :Float32;
  targetVelocity @3 :Float32;
}

struct MapdPosition @0xde9705979aca8339 {
  latitude @0 :Float64;
  longitude @1 :Float64;
}

struct MapdExtendedOut @0xa30662f84033036c {
  downloadProgress @0 :MapdDownloadProgress;
  settings @1 :Text;
  path @2 :List(MapdPathPoint);
  position @3 :MapdPosition;
  loopRateAverage @4 :Float32;
  loopRateMin @5 :Float32;
}

enum MapdInputType {
  download @0;
  reloadSettings @9;
  saveSettings @10;
  loadDefaultSettings @21;
  loadRecommendedSettings @22;
  loadPersistentSettings @26;
  cancelDownload @27;
  setJsonPathFloat @43;
  setJsonPathText @44;
  setJsonPathBool @45;
  acceptSpeedLimit @34;

  # DEPRECATED settings inputs
  setLogLevel @6;
  setLogSource @29;
  setLogJson @28;
  setTargetLateralAccel @1;
  setSpeedLimitOffset @2;
  setSpeedLimitControl @3;
  setMapCurveSpeedControl @4;
  setVisionCurveSpeedControl @5;
  setVisionCurveTargetLatA @7;
  setVisionCurveMinTargetV @8;
  setEnableSpeed @11;
  setVisionCurveUseEnableSpeed @12;
  setMapCurveUseEnableSpeed @13;
  setSpeedLimitUseEnableSpeed @14;
  setHoldLastSeenSpeedLimit @15;
  setTargetSpeedJerk @16;
  setTargetSpeedAccel @17;
  setTargetSpeedTimeOffset @18;
  setDefaultLaneWidth @19;
  setMapCurveTargetLatA @20;
  setSlowDownForNextSpeedLimit @23;
  setSpeedUpForNextSpeedLimit @24;
  setHoldSpeedLimitWhileChangingSetSpeed @25;
  setExternalSpeedLimitControl @30;
  setExternalSpeedLimit @31;
  setSpeedLimitPriority @32;
  setSpeedLimitChangeRequiresAccept @33;
  setPressGasToAcceptSpeedLimit @35;
  setAdjustSetSpeedToAcceptSpeedLimit @36;
  setAcceptSpeedLimitTimeout @37;
  setPressGasToOverrideSpeedLimit @38;
  setConditionalSpeedLimitControl @39;
  setShadowCarState @40;
  setShadowModelV2 @41;
  setShadowGpsLocation @42;
  setShadowGpsLocationExternal @46;
}

enum WaySelectionType {
  current @0;
  predicted @1;
  possible @2;
  extended @3;
  fail @4;
}

enum SpeedLimitOffsetType {
  static @0;
  percent @1;
}

struct MapdIn @0xc86a3d38d13eb3ef {
  type @0 :MapdInputType;
  float @1 :Float32;
  str @2 :Text;
  bool @3 :Bool;
  jsonPath @4 :Text;
}

enum RoadContext {
  freeway @0;
  city @1;
  unknown @2;
}

# WARNING: must be kept in perfect sync (names and values) with the
# HighwayClass enum in cereal/offline/offline.capnp — state.go casts directly
# between the two generated enum types.
# unknown either means the way's highway tag was not one of the listed values
# or the loaded map tiles predate this field.
enum HighwayClass {
  unknown @0;
  motorway @1;
  motorwayLink @2;
  trunk @3;
  trunkLink @4;
  primary @5;
  primaryLink @6;
  secondary @7;
  secondaryLink @8;
  tertiary @9;
  tertiaryLink @10;
  unclassified @11;
  residential @12;
  livingStreet @13;
}

struct MapdOut @0xa4f1eb3323f5f582 {
  wayName @0 :Text;
  wayRef @1 :Text;
  roadName @2 :Text;
  speedLimit @3 :Float32;
  nextSpeedLimit @4 :Float32;
  nextSpeedLimitDistance @5 :Float32;
  hazard @6 :Text;
  nextHazard @7 :Text;
  nextHazardDistance @8 :Float32;
  advisorySpeed @9 :Float32;
  nextAdvisorySpeed @10 :Float32;
  nextAdvisorySpeedDistance @11 :Float32;
  oneWay @12 :Bool;
  lanes @13 :UInt8;
  tileLoaded @14 :Bool;
  speedLimitSuggestedSpeed @15 :Float32;
  suggestedSpeed @16 :Float32;
  estimatedRoadWidth @17 :Float32;
  roadContext @18 :RoadContext;
  distanceFromWayCenter @19 :Float32;
  visionCurveSpeed @20 :Float32;
  mapCurveSpeed @21 :Float32;
  waySelectionType @22 :WaySelectionType;
  speedLimitAccepted @23 :Bool;
  highwayClass @24 :HighwayClass;
  wayId @25 :Int64;
  conditionalSpeedLimit @26 :Text;

  # Producer v2 sample evidence; older versions are unqualified.
  sampleVersion @27 :UInt16;
  roadStatus @28 :SampleStatus;
  gpsSource @29 :GpsSource;
  # V2: Python GPS MONOTONIC observation converted to BOOTTIME after a startup/resume fence.
  # V1 raw-time evidence is unqualified.
  gpsMonoTime @30 :UInt64;
  computedMonoTime @31 :UInt64; # Computation boot-time nanoseconds, not send time.
  sourceGeneration @32 :UInt64;
  producerSession @33 :UInt64;  # Random nonzero process session.
  # Data-only preview from the same qualified GPS fix; no controller smoothing.
  previewSpeedLimit @34 :Float32;
  previewDistance @35 :Float32;
  previewBoundaryId @36 :Text;
  previewWayId @37 :Int64;

  enum SampleStatus {
    unknown @0;
    noGps @1;
    noCoverage @2;
    noMatch @3;
    matchedNoLimit @4;
    matchedLimit @5;
  }

  enum GpsSource {
    none @0;
    internal @1;
    external @2;
    car @3;
  }
}
