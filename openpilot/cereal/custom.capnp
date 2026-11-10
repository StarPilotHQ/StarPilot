using Cxx = import "/include/c++.capnp";
$Cxx.namespace("cereal");
using Car = import "/car.capnp";

@0xb526ba661d550a59;

# custom.capnp: a home for empty structs reserved for custom forks
# These structs are guaranteed to remain reserved and empty in mainline
# cereal, so use these if you want custom events in your fork.

# DO rename the structs
# DON'T change the identifier (e.g. @0x81c2f05a394cf4af)

struct StarPilotCarControl @0x81c2f05a394cf4af {
  hudControl @0 :HUDControl;
  steeringLimitInfo @1 :SteeringLimitInfo;

  struct HUDControl {
    audibleAlert @0 :AudibleAlert;

    enum AudibleAlert {
      none @0;

      engage @1;
      disengage @2;
      refuse @3;

      warningSoft @4;
      warningImmediate @5;

      prompt @6;
      promptRepeat @7;
      promptDistracted @8;

      # Random Events
      angry @9;
      continued @10;
      dejaVu @11;
      doc @12;
      fart @13;
      firefox @14;
      goat @15;
      hal9000 @16;
      mail @17;
      nessie @18;
      noice @19;
      startup @20;
      thisIsFine @21;
      uwu @22;
    }
  }

  struct SteeringLimitInfo {
    valid @0 :Bool;
    modelLimitErrorDeg @1 :Float32;
    resumeLimitErrorDeg @2 :Float32;
    cooperativeLimitErrorDeg @3 :Float32;
    cooperativeOffsetDeg @4 :Float32;
    monoTime @5 :UInt64;
    combinedLimitErrorDeg @6 :Float32;
  }

  navigation @2 :Navigation;

  struct Navigation {
  sessionId @0 :Text;
    frameMonoTime @1 :UInt64;
    startedMonoTime @2 :UInt64;
    revision @3 :Text;
    enabled @4 :Bool;
    status @5 :Text;
    destinationName @6 :Text;
    instruction @7 :Instruction;
    route @8 :List(Coordinate);
    nextManeuver @9 :Instruction;
    locationMonoTime @10 :UInt64;
    controlValid @11 :Bool;

    struct Coordinate {
      latitude @0 :Float64;
      longitude @1 :Float64;
    }
    struct Instruction {
      text @0 :Text;
      maneuverType @1 :Text;
      maneuverModifier @2 :Text;
      distanceMeters @3 :Float32;
      remainingDistanceMeters @4 :Float32;
      remainingDurationSeconds @5 :Float32;
      lanes @6 :List(Lane);
    }
    struct Lane {
      active @0 :Bool;
      directions @1 :List(Text);
      activeDirection @2 :Text;
    }
    version @12 :UInt16;
    source @13 :Text;
    observationMonoTime @14 :UInt64;
    distanceKnown @15 :Bool;
    totalsKnown @16 :Bool;
  }

  phoneGps @3 :StarPilotCarState.Gps;  # sourceMonoTime is comma receive time minus the phone's fix age


  struct PreapRadarStartup {
    version @0 :UInt16;
    kind @1 :UInt16;
    carParamsSha256 @2 :Data;
    safetyModel @3 :UInt16;
    safetyParam @4 :UInt16;
    lateralOffset @5 :Float32;
  }

  struct PreapPedalStartup {
    version @0 :UInt16;
    kind @1 :UInt16;
    carParamsSha256 @2 :Data;
    safetyModel @3 :UInt16;
    safetyParam @4 :UInt16;
    factor @5 :Float32;  # original DI units per physical pedal unit
    zero @6 :Float32;  # original physical pedal unit offset
    pedalBus @7 :UInt16;
    sourceMonoTime @8 :UInt64;  # CLOCK_MONOTONIC ns from actual Card startup
  }
}
struct StarPilotCarParams @0xaedffd8f31e7b55d {
  alternativeExperience @0 :Int16;
  canUsePedal @1 :Bool;
  canUseSDSU @2 :Bool;
  flags @3 :UInt32;
  isHDA2 @4 :Bool;
  openpilotLongitudinalControlDisabled @5 :Bool;
  safetyConfigs @6 :List(SafetyConfig);
  pcmCruiseSpeed @7 :Bool = true;
  redneckCruiseAvailable @8 :Bool;

  struct SafetyConfig {
    safetyParam @0 :UInt16;
  }

  learning @9 :AutoSteeringLearning;

  struct AutoSteeringLearning {
    version @0 :UInt16;
    status @1 :Status;
    reason @2 :Text;
    identity @3 :Identity;
    session @4 :Text;
    sequence @5 :UInt64;
    observedMonoTime @6 :UInt64;  # MONOTONIC, separate from Event BOOTTIME.
    validUntilMonoTime @7 :UInt64;
    sourceWindowStartMonoTime @8 :UInt64;
    sourceWindowEndMonoTime @9 :UInt64;
    family @10 :Family;
    latAccelFactor @11 :Float64;
    acceptancePolicyId @12 :Text;
    trainWindows @13 :UInt32;
    heldoutWindows @14 :UInt32;
    trainBaselineLoss @15 :Float64;
    trainCandidateLoss @16 :Float64;
    heldoutBaselineLoss @17 :Float64;
    heldoutCandidateLoss @18 :Float64;

    enum Status { unavailable @0; collecting @1; candidate @2; rejected @3; qualifiedFit @4; }
    enum Family { none @0; linearFactor @1; runtimeProfile @2; }
    runtimeProfile @19 :AutomaticRuntimeProfile;
    sourceReceipt @20 :AutomaticLearningSourceReceipt;
    centreApplicationWitness @21 :CentreApplicationWitness;
    struct Identity {
      vehicle @0 :Text;
      brand @1 :Text;
      controller @2 :Text;
      policy @3 :Text;
      profile @4 :Text;
      conversionVersion @5 :Text;
      firmwareSha256 @6 :Text;
      basis @7 :List(Float64);
      baselineSha256 @8 :Text;
      preferenceSha256 @9 :Text;
      resetGeneration @10 :Text;
      controllerRevision @11 :Text;
    }
  }
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
  lateralAuthorityUnavailable @32 :Bool;
  sourceCarStateMonoTime @33 :UInt64;
  appliedEvidence @34 :LearningAppliedEvidence;
  fordLearningEvidence @35 :FordLearningEvidence;
  learningApplication @36 :LearningApplication;
  feedbackPublication @37 :FeedbackPublication;
  steeringLimitInfo @39 :StarPilotCarControl.SteeringLimitInfo;
  toyotaCruise @38 :ToyotaCruise;

  struct ToyotaCruise {
    version @0 :UInt16;
    status @1 :Text;
    targetKph @2 :Float32;
    nativeKph @3 :Float32;
    nativeClusterKph @4 :Float32;
    sessionId @5 :Text;
    acceptedMonoTime @6 :UInt64;
    approvedTargetMps @7 :Float32;
  }

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

  struct FeedbackPublication {
    version @0 :UInt16;
    cardSession @1 :Data; # Exactly16 bytes, never default-empty proof.
    publicationSequence @2 :UInt64;
    publicationMonoTime @3 :UInt64;
    sourceCarStateMonoTime @4 :UInt64; # Held AppliedEvidence.state_ns.
    outputKnown @5 :Bool;
    outputControlMonoTime @6 :UInt64;
    outputApplyMonoTime @7 :UInt64;
    outputNativeIdentity @8 :Text; # Optional reference only; child verifies original native proof.
    outputCarrierSequence @9 :UInt64; # Held AppliedEvidence.carrier_frame, NOT a local apply serial.
    reportedTorque @10 :Float32;
    outputValid @11 :Bool; # Exact CO Event.valid, not held evidence validity.
    coBodySha256 @12 :Data; # Exactly32 bytes.
    applyEvidenceSha256 @13 :Data; # Exactly32 bytes when known; empty when unknown.
    sourceEpoch @14 :Text;
    cpSha256 @15 :Text;
    carrierObservedMonoTime @16 :UInt64; # Exact held carrier receipt; never CO/apply relabel.
  }
}

struct StarPilotLongitudinalPlan @0xda96579883444c35 {
  version @0 :UInt16;
  sourcePlanMonoTime @1 :UInt64;
  modelMonoTime @2 :UInt64;
  driveStartMonoTime @3 :UInt64;
  forceStopHolding @4 :Bool;
  forceStopBraking @5 :Bool;
  forceStopEpisodeMonoTime @6 :UInt64;
}

struct SlcState @0xa1680744031fdb2d {
  # Retain the historical model-status fields for log compatibility.
  slotId @0 :Text;
  slotName @1 :Text;
  variant @2 :Text;
  variantLabel @3 :Text;
  reason @4 :Text;
  wallTimeNanos @5 :UInt64;

  sessionId @6 :Text;
  frameMonoTime @7 :UInt64;
  enabled @8 :Bool;
  displayOnly @9 :Bool;
  observationKind @10 :Text;
  source @11 :Text;
  speedLimit @12 :Float32; # m/s, meaningful only for a valid observation
  acceptedSpeedLimit @13 :Float32; # m/s, meaningful only when hasAccepted is true
  hasAccepted @14 :Bool;
  offset @15 :Float32; # m/s
  effectiveCap @16 :Float32; # m/s, meaningful only when hasCeiling is true
  hasCeiling @17 :Bool;
  pendingSpeedLimit @18 :Float32; # m/s, meaningful only when hasPending is true
  hasPending @19 :Bool;
  decisionId @20 :UInt64;
  presentationId @21 :UInt64;
  status @22 :Text;
  actionSequenceId @23 :UInt64;
  actionStatus @24 :Text;
  sourceProducerSessionId @25 :Text;
  sourceEpisode @26 :UInt32;
  sourceObservedMonoTime @27 :UInt64;
  sourceValidUntilMonoTime @28 :UInt64;
  commandId @29 :UInt64;
  commandStatus @30 :Text;
  # Independently qualified diagnostics from the same plannerd publisher.
  # Existing SLC fields keep their meaning when the curve runtime is present.
  curve @31 :CurveState;
  # Saved conditional-mode proposal from this plannerd cycle. Consumers must
  # independently join it to live authority and the current saved revision.
  conditionalMode @32 :ConditionalModeState;
  # Exact Ioniq physical media intent, accepted by this planner only while
  # its original source and current longitudinal authority remain qualified.
  trafficMode @33 :TrafficModeState;
  switchbackMode @34 :SwitchbackModeState;
  # Presentation metadata only; planner/control consumers retain existing fields.
  acceptedSource @35 :Text;
  pendingSource @36 :Text;
  effectiveClusterTarget @37 :Float32; # m/s in the selected-cruise cluster coordinate
  hasEffectiveClusterTarget @38 :Bool;
  isLimitingMaxSet @39 :Bool; # qualified raw ceiling below selected raw cruise
  driverOverrideActive @40 :Bool;
  # Presentation only. Qualified source observations from this publisher cycle;
  # consumers use the outer session and frame freshness, never these rows for control.
  sourceReadings @41 :List(SourceReading);
  acceptedAdjustedLimit @42 :Float32;
  hasAcceptedAdjustedLimit @43 :Bool;
  pendingAdjustedLimit @44 :Float32;
  hasPendingAdjustedLimit @45 :Bool;
  presentationAdjustedLimit @46 :Float32;
  hasPresentationAdjustedLimit @47 :Bool;
  overrideBasis @48 :Text; # none/pedal/persistent/wheel; qualified contribution only
  retainedOverride @49 :Bool;
  commandActionSequenceId @50 :UInt64; # UI sequence that issued this command
  automaticAdoptionPending @51 :Bool;
  pendingSinceMonoTime @52 :UInt64;
  # Presentation only; zero means no limit received in this session.
  lastReceivedSpeedLimit @53 :Float32;
  lastReceivedSource @54 :Text;
  lastReceivedAdjustedLimit @55 :Float32;
  acceptedMonoTime @56 :UInt64;
  # Qualified adoption proposal; the current displayed observation stays above.
  upcomingSpeedLimit @57 :Float32;
  upcomingBoundaryId @58 :Text;
  acceptedIsPreview @59 :Bool; # a temporary ceiling, ineligible for initializing SET
  wheelOverrideSince @60 :UInt64; # nonzero while a wheel tap may cross the current adjusted limit
  wheelAdoptMonoTime @61 :UInt64; # Card's original wheel event timestamp, only on the approval frame
  wheelAdoptTargetMps @62 :Float32; # exact adjusted SET approved for that event
  struct SourceReading {
    source @0 :Text;
    enabled @1 :Bool;
    observationKind @2 :Text; # valid/absent/stale/unknown; numeric defaults are not readings
    speedLimit @3 :Float32; # raw m/s only when observationKind is valid
  }

  struct SwitchbackModeState {
    version @0 :UInt16;
    sessionId @1 :Text;
    sequence @2 :UInt64;
    observedMonoTime @3 :UInt64;
    validUntilMonoTime @4 :UInt64;
    driveStartMonoTime @5 :UInt64;
    requested @6 :Bool;
    effective @7 :Bool;
    sourceCarControlMonoTime @8 :UInt64;
  }

  struct TrafficModeState {
    version @0 :UInt16;
    sessionId @1 :Text;
    sequence @2 :UInt64;
    observedMonoTime @3 :UInt64;
    validUntilMonoTime @4 :UInt64;
    driveStartMonoTime @5 :UInt64;
    sourceEpoch @6 :UInt64;
    sourceBootTime @7 :UInt64;
    accepted @8 :Bool;
    effective @9 :Bool;
    reason @10 :Text;
    settingsFingerprint @11 :Text;
    buttonMapFingerprint @12 :Text;
    profileTargetReady @13 :Bool;
    profileReason @14 :Text;
    controllerSource @15 :Bool;
  }

  struct CurveState {
    version @0 :UInt16;
    sessionId @1 :Text;
    sequence @2 :UInt64;
    observedMonoTime @3 :UInt64;
    validUntilMonoTime @4 :UInt64;
    modelMonoTime @5 :UInt64;
    configured @6 :Bool;
    documentValid @7 :Bool;
    hasCandidate @8 :Bool;
    candidateMps @9 :Float32;
    hasCeiling @10 :Bool;
    ceilingMps @11 :Float32;
    applied @12 :Bool;
    controlling @13 :Bool;
    training @14 :Bool;
    calibrationProgress @15 :Float32; # percent, 0..100
    comfortAccel @16 :Float32;
    bindingDistance @17 :Float32;
    reason @18 :Text;
    plannerStatus @19 :Text;
    persistenceStatus @20 :Text;
    glow @21 :Bool;
    curveOnly @22 :Bool;
    hasRoadCurvature @23 :Bool;
    roadCurvature @24 :Float32; # 1/m, diagnostic prediction for display
  }

  struct ConditionalModeState {
    version @0 :UInt16;
    sessionId @1 :Text;
    sequence @2 :UInt64;
    observedMonoTime @3 :UInt64;
    validUntilMonoTime @4 :UInt64;
    driveStartMonoTime @5 :UInt64;
    modelMonoTime @6 :UInt64;
    carStateMonoTime @7 :UInt64;
    settingsRevision @8 :UInt64;
    settingsFingerprint @9 :Text;
    choice @10 :Choice;
    hasOverride @11 :Bool;
    experimental @12 :Bool;
    status @13 :Text;
    reason @14 :Text;
    statusCode @15 :UInt16;

    enum Choice {
      stock @0;
      conditionalExperimental @1;
      conditionalChill @2;
    }
  }
}

struct SlcAction @0x9ccdc8676701b412 {
  sessionId @0 :Text;
  sequenceId @1 :UInt64;
  decisionId @2 :UInt64;
  presentationId @3 :UInt64;
  kind @4 :Kind;
  conditionalManual @5 :ConditionalManualAction;
  controllerCruise @6 :ControllerCruiseAction;
  controllerMode @7 :ControllerCruiseAction;
  wheelAdoptMonoTime @8 :UInt64; # original timestamp of a qualified Adopt Speed Limit wheel event

  struct ControllerCruiseAction {
    version @0 :UInt16;
    sessionId @1 :Text;
    sequence @2 :UInt64;
    observedMonoTime @3 :UInt64;
    validUntilMonoTime @4 :UInt64;
    driveStartMonoTime @5 :UInt64;
    carFingerprint @6 :Text;
    sourceCarControlMonoTime @7 :UInt64;
    actionCode @8 :UInt8;
    buttonKey @9 :Text;
    sourceCarStateMonoTime @10 :UInt64;
    carParamsFingerprint @11 :Text;
    targetSpeedMps @12 :Float32;
  }

  struct ConditionalManualAction {
    version @0 :UInt16;
    sessionId @1 :Text;
    sequence @2 :UInt64;
    observedMonoTime @3 :UInt64;
    validUntilMonoTime @4 :UInt64;
    driveStartMonoTime @5 :UInt64;
    settingsFingerprint @6 :Text;
    choice @7 :SlcState.ConditionalModeState.Choice;
    plannerSessionId @8 :Text;
    sourceCarStateMonoTime @9 :UInt64;
    sourceSelfdriveStateMonoTime @10 :UInt64;
    expectedManualCode @11 :UInt8;
    expectedExperimental @12 :Bool;
  }

  enum Kind {
    unknown @0;
    accept @1;
    reject @2;
    adopt @3;
    conditionalModeCycle @4;
    cruiseIncrease @5;
    cruiseDecrease @6;
    trafficModeToggle @7;
    switchbackModeToggle @8;
    cruiseSet @9;
    forceCoastToggle @10;
    pulseGlideToggle @11;
    disengageRequest @12;
  }
}

struct SlcCruiseEvent @0xcd96dafb67a082d0 {
  eventId @0 :UInt64;
  observedMonoTime @1 :UInt64;
  previousMps @2 :Float32;
  selectedMps @3 :Float32;
  button @4 :Button;
  longPress @5 :Bool;
  producerSessionId @6 :Text;
  kind @7 :Kind;
  sessionId @8 :Text;
  decisionId @9 :UInt64;
  presentationId @10 :UInt64;
  commandId @11 :UInt64;
  manualMode @12 :ManualModeGesture;
  trafficMode @13 :TrafficModeGesture;
  wheelAction @14 :SlcAction.ControllerCruiseAction;
  teslaStockCruise @15 :TeslaStockCruise;
  wheelOverrideSince @16 :UInt64; # SLC eligibility captured by a physical + tap; zero for ordinary SET changes

  struct TeslaStockCruise {
    version @0 :UInt16;
    carStateMonoTime @1 :UInt64;
    cpFingerprint @2 :Text;
    engaged @3 :Bool;
    notArmed @4 :Bool;
  }

  enum Kind {
    unknown @0;
    driverChange @1;
    confirmationAccept @2;
    confirmationReject @3;
    commandApplied @4;
    commandRejected @5;
    curveAccelPress @6;
    conditionalMode @7;
    trafficMode @8;
    switchbackMode @9;
    wheelAction @10;
    teslaStockCruise @11;
    engagementAdopt @12;
  }

  struct TrafficModeGesture {
    version @0 :UInt16;
    sessionId @1 :Text;
    sequence @2 :UInt64;
    observedMonoTime @3 :UInt64;
    driveStartMonoTime @4 :UInt64;
    settingsFingerprint @5 :Text;
    buttonMapFingerprint @6 :Text;
    sourceEpoch @7 :UInt64;
    sourceBootTime @8 :UInt64;
    sourceCarStateMonoTime @9 :UInt64;
    validUntilMonoTime @10 :UInt64;
    toggle @11 :Bool;
    button @12 :Button;
    press @13 :Press;

    enum Button { unknown @0; mode @1; custom @2; distance @3; }
    enum Press { unknown @0; short @1; long @2; veryLong @3; }
  }

  enum Button {
    unknown @0;
    accel @1;
    decel @2;
  }

  struct ManualModeGesture {
    version @0 :UInt16;
    sessionId @1 :Text;
    sequence @2 :UInt64;
    observedMonoTime @3 :UInt64;
    driveStartMonoTime @4 :UInt64;
    settingsFingerprint @5 :Text;
    choice @6 :Choice;
    button @7 :Button;
    press @8 :Press;
    sourceCarStateMonoTime @9 :UInt64;
    validUntilMonoTime @10 :UInt64;

    enum Choice {
      stock @0;
      conditionalExperimental @1;
      conditionalChill @2;
    }

    enum Button {
      unknown @0;
      lkas @1;
      distance @2;
      mode @3;
      custom @4;
    }

    enum Press {
      unknown @0;
      short @1;
      long @2;
      veryLong @3;
    }
  }
}

struct SlcDashboardObservation @0xb057204d7deadf3f {
  producerSessionId @0 :Text;
  carStateLogMonoTime @1 :UInt64;
  status @2 :Status;
  speedMps @3 :Float32; # SI, only meaningful when valid
  observedMonoTime @4 :UInt64;
  validUntilMonoTime @5 :UInt64;
  episode @6 :UInt32;

  enum Status {
    unknown @0;
    valid @1;
    absent @2;
    stale @3;
  }
}

struct StarPilotModelDataV2 @0x80ae746ee2596b11 {
  turnDirection @0 :TurnDirection;
  vision @1 :VisionObservation;

  enum TurnDirection @0xab5928774e6e64fc {
    none @0;
    turnLeft @1;
    turnRight @2;
  }

  struct VisionObservation @0x9e04a179bcb4ab72 {
    producerSessionId @0 :Text;
    status @1 :Status;
    speedMps @2 :Float32; # meaningful only for valid
    confidence @3 :Float32; # 0..1, meaningful only for valid
    supportCount @4 :UInt16;
    observedMonoTime @5 :UInt64; # Python CLOCK_MONOTONIC at inference completion
    validUntilMonoTime @6 :UInt64; # short producer support TTL in CLOCK_MONOTONIC
    cameraFrameEofBootTime @7 :UInt64; # VisionIPC/camerad CLOCK_BOOTTIME
    episode @8 :UInt32;
    modelId @9 :Text; # exact bundled detector/classifier identity
    frameId @10 :UInt32;
    stream @11 :Stream;
    stopSigns @12 :StopObservation;

    struct StopObservation {
      status @0 :Status;
      observedMonoTime @1 :UInt64;
      validUntilMonoTime @2 :UInt64;
      cameraFrameEofBootTime @3 :UInt64;
      frameId @4 :UInt32;
      imageWidth @5 :UInt16;
      imageHeight @6 :UInt16;
      detections @7 :List(StopDetection);
    }

    struct StopDetection {
      confidence @0 :Float32;
      x1Pixels @1 :UInt16;
      y1Pixels @2 :UInt16;
      x2Pixels @3 :UInt16;
      y2Pixels @4 :UInt16;
    }


    enum Status @0xc97a1c56dc1427ae {
      unknown @0;
      valid @1;
      stale @2;
      unavailable @3;
    }

    enum Stream @0xb45fd9e3634d228f {
      unknown @0;
      road @1;
      wideRoad @2;
    }
  }
}

struct StarPilotSteeringTracking @0xa5cd762cd951a455 {
  version @0 :UInt16;
  sourceReceipt @1 :AutomaticLearningSourceReceipt;
  trial @2 :LiveTrackingTrial;
  restore @3 :LiveTrackingRestore;
}

struct CustomReserved6 @0xf98d843bfd7004a3 {
  # Temporary old StarPilot route replay compatibility: historical starpilotPlan at Event @113.
  # Adapter and retirement instructions: openpilot/starpilot/ui/old_starpilot_route_shim.py.
  # Keep field ordinals/types frozen. When old-route replay support is removed,
  # empty this struct but retain its type ID.
  # Historical prefix fields preserve the wire layout needed to decode the SLC fields below.
  accelerationJerk @0 :Float32; cscControllingSpeed @1 :Bool; cscSpeed @2 :Float32;
  cscTraining @3 :Bool; dangerFactor @4 :Float32; dangerJerk @5 :Float32;
  desiredFollowDistance @6 :Int64; experimentalMode @7 :Bool; forcingStop @8 :Bool;
  forcingStopLength @9 :Float32; starpilotEvents @10 :AnyPointer; starpilotToggles @11 :Text;
  increasedStoppedDistance @12 :Float32; lateralCheck @13 :Bool; laneWidthLeft @14 :Float32;
  laneWidthRight @15 :Float32; maxAcceleration @16 :Float32; minAcceleration @17 :Float32;
  redLight @18 :Bool; roadCurvature @19 :Float32;
  # Historical SLC values (speeds/offsets in m/s); replay display only.
  slcMapSpeedLimit @20 :Float32;
  slcMapboxSpeedLimit @21 :Float32; slcNextSpeedLimit @22 :Float32; slcOverriddenSpeed @23 :Float32;
  slcSpeedLimit @24 :Float32; slcSpeedLimitOffset @25 :Float32; slcSpeedLimitSource @26 :Text;
}

struct StarPilotRadarState @0xb86e6369214c01c8 {
  # Frozen 678af783 fields and nested layouts are retained for old log readers.
  # These historical fields are not populated by the new qualified producer.
  leadLeft @0 :LeadData;
  leadRight @1 :LeadData;
  adjacentStopped @2 :AdjacentStopped;

  struct AdjacentStopped {
    status @0 :Bool;
    dRel @1 :Float32;
    yRel @2 :Float32;
    radarTrackId @3 :Int32 = -1;
  }

  struct LeadData {
    dRel @0 :Float32;
    yRel @1 :Float32;
    vRel @2 :Float32;
    aRel @3 :Float32;
    vLead @4 :Float32;
    dPath @6 :Float32;
    vLat @7 :Float32;
    vLeadK @8 :Float32;
    aLeadK @9 :Float32;
    fcw @10 :Bool;
    status @11 :Bool;
    aLeadTau @12 :Float32;
    modelProb @13 :Float32;
    radar @14 :Bool;
    radarTrackId @15 :Int32 = -1;

    aLeadDEPRECATED @5 :Float32;
  }

  qualifiedAdjacent @3 :QualifiedAdjacent;

  struct QualifiedAdjacent {
    version @0 :UInt16;
    status @1 :Status;
    producerSessionId @2 :Text;
    sequence @3 :UInt64;
    radarTracksMonoTime @4 :UInt64;
    modelMonoTime @5 :UInt64;
    carStateMonoTime @6 :UInt64;
    cameraEofBootTime @7 :UInt64;
    observedMonoTime @8 :UInt64;
    validUntilMonoTime @9 :UInt64;
    left @10 :Candidate;
    right @11 :Candidate;

    enum Status {
      unknown @0;
      clear @1;
      ambiguous @2;
    }

    struct Candidate {
      present @0 :Bool;
      trackId @1 :UInt32;
      distanceM @2 :Float32;
      lateralM @3 :Float32;
      speedMps @4 :Float32;
    }
  }
}

struct StarPilotSelfdriveState @0xf416ec09499d9d19 {
  # Exact historical fields @0..6. Old route readers retain their meanings.
  alertText1 @0 :Text;
  alertText2 @1 :Text;
  alertStatus @2 :AlertStatus;
  alertSize @3 :AlertSize;
  alertType @4 :Text;
  alertSound @5 :Car.CarControl.HUDControl.AudibleAlert;
  vEgo @6 :Float32;
  conditionalModeAck @7 :ConditionalModeAck;

  enum AlertStatus @0xc0f486ad93ed68c9 {
    normal @0;
    userPrompt @1;
    critical @2;
    starpilot @3;
  }

  enum AlertSize @0xe22723d973fc2afb {
    none @0;
    small @1;
    mid @2;
    full @3;
  }

  struct ConditionalModeAck @0xb42d77eaa5b93711 {
    version @0 :UInt16;
    sessionId @1 :Text;
    sequence @2 :UInt64;
    observedMonoTime @3 :UInt64;
    validUntilMonoTime @4 :UInt64;
    sourceSelfdriveStateMonoTime @5 :UInt64;
    driveStartMonoTime @6 :UInt64;
    choice @7 :SlcState.ConditionalModeState.Choice;
    accepted @8 :Bool;
    effectiveExperimental @9 :Bool;
    plannerSessionId @10 :Text;
    plannerSequence @11 :UInt64;
    settingsFingerprint @12 :Text;
    settingsRevision @13 :UInt64;
    modelMonoTime @14 :UInt64;
    reason @15 :Text;
    statusCode @16 :UInt16;
  }
}

struct SpotMonitorState @0xcb9fd56c7057593a {
  # Frozen StarPilotLateralManeuverPlanDEPRECATED @136 carried this field.
  # It retains the original 1/m meaning; V-ASM never populates it.
  desiredCurvature @0 :Float32;
  observation @1 :Observation;

  struct Observation {
    version @0 :UInt16;
    producerSessionId @1 :Text;
    sequence @2 :UInt64;
    modelSha256 @3 :Text;
    settingsFingerprint @4 :Text;
    observedMonoTime @5 :UInt64;
    observedBootTime @6 :UInt64;
    sourceFrameId @7 :UInt64;
    sourceFrameEofBootTime @8 :UInt64;
    validUntilBootTime @9 :UInt64;
    # Display sides: left derives from camera-right, right from camera-left.
    left @10 :Side;
    right @11 :Side;

    struct Side {
      status @0 :Status;
      confidence @1 :Float32;
      warning @2 :Bool;
      sourceFrameId @3 :UInt64;
      sourceFrameEofBootTime @4 :UInt64;
      sourceObservedMonoTime @5 :UInt64;
      validUntilBootTime @6 :UInt64;

      enum Status {
        unknown @0;
        clear @1;
        warning @2;
      }
    }
  }
}

struct StarPilotLateralState @0xc2243c65e0340384 {
  active @0 :Bool;
  frictionThreshold @1 :Float32;
  frictionScale @2 :Float32;
  feedforward @3 :Float32;
  frictionJerk @4 :Float32;
  frictionJerkDeadzone @5 :Float32;
  lowSpeedFactor @6 :Float32;
  unwindDetected @7 :Bool;
  laneCentering @8 :LaneCentering;
  learningEvidence @9 :LearningControlEvidence;
  learningApplication @10 :LearningApplication;
  feedbackConsumption @11 :FeedbackConsumption;
  trackingApplication @12 :LiveTrackingApplication;

  struct LaneCentering {
    version @0 :UInt16;
    modelMonoTime @1 :UInt64;
    carControlMonoTime @2 :UInt64;
    lateralActive @3 :Bool;
    requestedCorrection @4 :Float32;
    appliedCorrection @5 :Float32;
    reason @6 :Text;
  }

  struct FeedbackConsumption {
    version @0 :UInt16;
    controlMonoTime @1 :UInt64;
    consumedPublicationMonoTime @2 :UInt64;
    consumedRecvFrame @3 :UInt64;
    consumedReceiptMonoTime @4 :UInt64;
    consumedSeen @5 :Bool;
    consumedValid @6 :Bool;
    coBodySha256 @7 :Data; # Exactly32 bytes.
    reportedTorque @8 :Float32;
    requestTorque @9 :Float32;
    latActive @10 :Bool;
    inputSafetyLimited @11 :Bool;
    publishedSafetyLimited @12 :Bool;
    priorPublishControlMonoTime @13 :UInt64;
    sourceEpoch @14 :Text;
    cpSha256 @15 :Text;
  }
}

struct SlcCruiseCommand @0xbd443b539493bc68 {
  kind @0 :Kind;
  sessionId @1 :Text;
  commandId @2 :UInt64;
  actionId @3 :UInt64;
  decisionId @4 :UInt64;
  presentationId @5 :UInt64;
  sourceProducerSessionId @6 :Text;
  sourceEpisode @7 :UInt32;
  sourceObservedMonoTime @8 :UInt64;
  sourceValidUntilMonoTime @9 :UInt64;
  issuedMonoTime @10 :UInt64;
  expiresMonoTime @11 :UInt64;
  expectedSelectedMps @12 :Float32;
  targetMps @13 :Float32;
  source @14 :Text;
  upcomingBoundaryId @15 :Text;

  enum Kind {
    unknown @0;
    adoptAcceptedHigherLimit @1;
    adoptAcceptedLimit @2;
  }
}

struct AolAxisState @0xfc6241ed8877b611 {
  sessionId @0 :Text;
  sequence @1 :UInt64;
  sourceCarStateMonoTime @2 :UInt64;
  observedMonoTime @3 :UInt64;
  validUntilMonoTime @4 :UInt64;
  mode @5 :Mode;
  lateralActive @6 :Bool;
  longitudinalActive @7 :Bool;
  qualified @8 :Bool;
  desiredLateral @9 :Bool;
  desiredLongitudinal @10 :Bool;
  # Fresh negotiated native status exists; each active axis separately checks
  # its requested bit and permission against AolSafetyState.
  nativeAcknowledged @11 :Bool;
  optionalSetRelease @12 :Bool;
  # Intent retention provenance; default/old senders cannot classify a pause.
  faultReason @13 :FaultReason;
  faultEventMonoTime @14 :UInt64;
  faultSessionId @15 :Text;
  intentProducerSessionId @16 :Text;
  intentIntegrityEpoch @17 :UInt64;

  enum FaultReason {
    unknown @0;
    none @1;
    transportPause @2;
    critical @3;
    availabilityPause @4;
  }

  enum Mode {
    off @0;
    lateralOnly @1;
    longitudinalOnly @2;
    combined @3;
  }

  # Flat Cap'n Proto payloads carried by reserved Event Data slots 124/125.
  # Keeping these nested under the owned axis type gives them fresh generated
  # type IDs without repurposing historic Mapd reserved types 17/18.
  struct SafetyWire {
    kind @0 :UInt8;
    version @1 :UInt16;
    protocolVersion @2 :UInt16;
    compatible @3 :Bool;
    observedMonoTime @4 :UInt64;
    validUntilMonoTime @5 :UInt64;
    safetyModel @6 :UInt16;
    safetyParam @7 :UInt16;
    lateralAllowed @8 :Bool;
    longitudinalAllowed @9 :Bool;
    requestedLateral @10 :Bool;
    requestedLongitudinal @11 :Bool;
    pandaSerial @12 :Text;
    axisSessionId @13 :Text;
    # Exact source Event stamp uses pandad BOOTTIME, for provenance only.
    # Inventory shares the wire's CLOCK_MONOTONIC observed/expiry lease.
    sourcePandaStatesMonoTime @14 :UInt64;
    pandaInventory @15 :List(PandaSlot);

    struct PandaSlot {
      slotIndex @0 :UInt16;
      hardwareSerial @1 :Text;
      safetyModel @2 :UInt16;
      safetyParam @3 :UInt16;
      alternativeExperience @4 :UInt16;
      controlsAllowed @5 :Bool;
      safetyRxChecksInvalid @6 :Bool;
      heartbeatLost @7 :Bool;
      faults @8 :UInt32;
    }
  }

  struct IntentWire {
    kind @0 :UInt8;
    version @1 :UInt16;
    producerSessionId @2 :Text;
    sequence @3 :UInt64;
    carStateLogMonoTime @4 :UInt64;
    observedMonoTime @5 :UInt64;
    validUntilMonoTime @6 :UInt64;
    allowedLatch @7 :Bool;
    pauseLateral @8 :Bool;
    pauseLongitudinal @9 :Bool;
    settingsQualified @10 :Bool;
    # Selection feedback only; allowedLatch retains its driving gates.
    lateralArmed @11 :Bool;
    optionalSetRelease @12 :Bool;
  }

  # A separate modeld-owned payload carried by reserved Event Data @126.
  # Nesting keeps its new type inside an existing fork-owned schema root.
  struct LaneChangeStatusWire {
    kind @0 :UInt8;
    version @1 :UInt16;
    producerSessionId @2 :Text;
    sequence @3 :UInt64;
    modelFrameId @4 :UInt32;
    modelTimestampEofNs @5 :UInt64;
    observedMonoTimeNs @6 :UInt64;
    validUntilMonoTimeNs @7 :UInt64;
    phase @8 :Phase;
    direction @9 :Direction;
    autoConfigured @10 :Bool;
    engaged @11 :Bool;

    enum Phase {
      manualRequired @0;
      waitingForDelay @1;
      laneUnavailable @2;
      blindspotBlocked @3;
    }

    enum Direction {
      none @0;
      left @1;
      right @2;
    }
  }
}

struct MapdExtendedOut @0xa30662f84033036c {
  downloadProgress @0 :MapdDownloadProgress;
  settings @1 :Text;
  path @2 :List(MapdPathPoint);
  position @3 :MapdPosition;
  loopRateAverage @4 :Float32;
  loopRateMin @5 :Float32;

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
}

struct MapdIn @0xc86a3d38d13eb3ef {
  type @0 :MapdInputType;
  float @1 :Float32;
  str @2 :Text;
  bool @3 :Bool;
  jsonPath @4 :Text;

  enum MapdInputType @0x859f26628fc24358 {
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
}

struct MapdOut @0xa4f1eb3323f5f582 {
  # Historical map output, append-compatible with the pinned provider.
  # These nested enums retain their original type IDs and ordinal meanings.
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

  # Optional local junction geometry from the matched road and the same GPS fix.
  # Corroboration only: not a route, turn legality, or control authority.
  navigationBranches @38 :List(NavigationBranch);

  struct NavigationBranch {
    wayId @0 :Int64;
    junctionLatitude @1 :Float64;
    junctionLongitude @2 :Float64;
    distanceMeters @3 :Float32;
    angleDegrees @4 :Float32;  # Negative left, positive right, zero straight.
    roadName @5 :Text;
    highwayClass @6 :HighwayClass;
  }

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

  enum RoadContext @0xabefa88b9563dbae {
    freeway @0;
    city @1;
    unknown @2;
  }

  enum WaySelectionType @0xfa3e5ce74e25e82a {
    current @0;
    predicted @1;
    possible @2;
    extended @3;
    fail @4;
  }

  enum HighwayClass @0xd18274dcdc63c41d {
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
}

# Automatic FLM adds children only to existing reserved fork carriers.
# All evidence clocks are explicitly MONOTONIC; default/version0 is invalid.
struct LearningControlEvidence @0xf2447170bb68f358 {
  version @0 :UInt16;
  identity @1 :Text;
  generation @2 :UInt64;
  controlMonoTime @3 :UInt64;
  sourceStateMonoTime @4 :UInt64;
  domain @5 :Text;
  stageRevision @6 :Text;
  responseRevision @7 :Text;
  requested @8 :Float64;
  features @9 :List(Float64);
  baselinePrediction @10 :Float64;
  enabled @11 :Bool;
  manual @12 :Bool;
  active @13 :Bool;
  valid @14 :Bool;
  override @15 :Bool;
  fault @16 :Bool;
  saturated @17 :Bool;
  limited @18 :Bool;
  modelWobble @19 :Bool;
  controllerChatter @20 :Bool;
  stageSchemaRevision @21 :Text;
  stageValues @22 :List(StageValue);
  appliedCoordinates @23 :List(Float64);
  sourceEpoch @24 :Text;
  sourceReceiptMonoTime @25 :UInt64;
  controllerMode @26 :Text;
  policy @27 :Text;
  baselineRevision @28 :Text;
  baselineValues @29 :List(Float64);
  controllerRevision @30 :Text;
  replaySnapshot @31 :List(NamedValue);
  replayInputs @32 :List(NamedValue);
  snapshotSequence @33 :UInt64;
  turnAssist @34 :Bool;
  appliedParameterNames @35 :List(Text);
  nativeRequired @36 :Bool;
  nativeObservedMonoTime @37 :UInt64;
  nativeValidUntilMonoTime @38 :UInt64;
  nativeAllowed @39 :Bool;
  nativeIdentity @40 :Text;
  appliedProfileSequence @41 :UInt64;
  appliedProfileObservedMonoTime @42 :UInt64;
  appliedProfileValidUntilMonoTime @43 :UInt64;
  effectTrace @44 :FullEffectTrace;
  effectPrepareMonoTime @45 :UInt64; # Exact MONOTONIC owner tick, not carControl publication.
  vehicleModelAnchor @46 :List(Float64); # Actual owner stable (sR,cF,cR), empty on old senders.
  liveAppliedCoordinates @47 :List(Float64); # Actual same-frame live full30 hooks, never offered target.
  struct FullEffectTrace {
    version @0 :UInt16;
    envelope @1 :AutomaticRuntimeProfile.FullSurfaceEnvelope;
    controlMonoTime @2 :UInt64;
    previousMonoTime @3 :UInt64;
    qualified @4 :Bool;
    before @5 :List(StageValue);
    target @6 :List(StageValue);
    applied @7 :List(StageValue);
    appliedFullProfileSha256 @8 :Text;
  }
  struct NamedValue {
    name @0 :Text;
    union { number @1 :Float64; text @2 :Text; boolean @3 :Bool; }
  }
  struct StageValue { name @0 :Text; value @1 :Float64; }
}
struct LearningAppliedEvidence @0xbab0f51a74ffee6e {
  version @0 :UInt16;
  controlMonoTime @1 :UInt64;
  sourceStateMonoTime @2 :UInt64;
  appliedMonoTime @3 :UInt64;
  domain @4 :Text;
  applied @5 :Float64;
  valid @6 :Bool;
  active @7 :Bool;
  override @8 :Bool;
  fault @9 :Bool;
  limited @10 :Bool;
  nativeAllowed @11 :Bool;
  projection @12 :LearningTorqueProjection;
  # Carrier continuity only; never a command/physical/native lease timestamp.
  carrierFrame @13 :UInt64;
  carrierObservedMonoTime @14 :UInt64;
}
struct AutomaticRuntimeProfile @0xef38d302825641e3 {
  version @0 :UInt16;
  identity @1 :Text;
  sequence @2 :UInt64;
  observedMonoTime @3 :UInt64;
  validUntilMonoTime @4 :UInt64;
  policy @5 :Text;
  parameters @6 :List(Parameter);
  coefficients @7 :List(Float64);
  trainIds @8 :List(Text);
  heldoutIds @9 :List(Text);
  baselineLoss @10 :Float64;
  candidateLoss @11 :Float64;
  cohortImprovements @12 :List(CohortImprovement);
  replay @13 :ReplayValidation;
  fullSurface @14 :FullSurfaceEnvelope;
  struct FullSurfaceEnvelope {
    version @0 :UInt16;
    manifestSha256 @1 :Text;
    fullOriginalAnchor @2 :Text;
    trialAnchor @3 :Text;
    contextKey @4 :Text;
    cpSha256 @5 :Text;
    controllerRevision @6 :Text;
    sourceEpoch @7 :Text;
    resetGeneration @8 :Text;
    selectedBaselineRevision @9 :Text;
    priorFullProfileSha256 @10 :Text;
    fullProfileSha256 @11 :Text;
    priorCoordinates @12 :List(Coordinate);
    coordinates @13 :List(Coordinate);
    trialNames @14 :List(Text);
    persistentSourceEpoch @15 :Text;
    operation @16 :Text;
    historyFullProfileSha256 @17 :Text;
    restoreTransactionSha256 @18 :Text;
    struct Coordinate {
      name @0 :Text;
      value @1 :Float64;
    }
  }
  struct ReplayValidation {
    baselineRevision @0 :Text;
    physicalModelRevision @1 :Text;
    noSaturationRegression @2 :Bool;
    noSlewRegression @3 :Bool;
    worstCohortImprovement @4 :Float64;
    nativeBaselineValues @5 :List(Float64);
  }
  struct Parameter {
    name @0 :Text;
    stageRevision @1 :Text;
    low @2 :Float64;
    high @3 :Float64;
    maxStep @4 :Float64;
    slewPerS @5 :Float64;
    excitationFloor @6 :Float64 = 0.01;
  }
  struct CohortImprovement {
    name @0 :Text;
    lowerBound @1 :Float64;
  }
}

struct AutomaticLearningSourceReceipt @0x9fe2d23e75805149 {
  version @0 :UInt16;
  enabled @1 :Bool;
  manual @2 :Bool;
  resetGeneration @3 :Text;
  sourceEpoch @4 :Text;
  observedMonoTime @5 :UInt64;
  validUntilMonoTime @6 :UInt64;
  cpSha256 @7 :Text;
  calibrationBasis @8 :List(Float64);
  calibrationGeneration @9 :Text;
  persistentSourceEpoch @10 :Text;
}

struct LearningApplication @0x82b6c8ea6a6efabd {
  version @0 :UInt16;
  identity @1 :Text;
  profileSequence @2 :UInt64;
  observedMonoTime @3 :UInt64;
  status @4 :Text;
  actualCoordinates @5 :List(Float64);
  reason @6 :Text;
  baselineRevision @7 :Text;
  physicalModelRevision @8 :Text;
  effectManifestSha256 @9 :Text;
  effectOriginalAnchor @10 :Text;
  effectAppliedProfileSha256 @11 :Text;
}


struct FordLearningEvidence @0xd1dfbadb6a811fee {
  version @0 :UInt16;
  identity @1 :Text;
  generation @2 :UInt64;
  sourceEpoch @3 :Text;
  baselineRevision @4 :Text;
  baselineValues @5 :List(Float64);
  controllerRevision @6 :Text;
  cpSha256 @7 :Text;
  controllerMode @8 :Text;
  policy @9 :Text;
  conversionRevision @10 :Text;
  stageRevision @11 :Text;
  sourceReceiptMonoTime @12 :UInt64;
  sourceValidUntilMonoTime @13 :UInt64;
  enabled @14 :Bool;
  manual @15 :Bool;
  rows @16 :List(FordLearningEvent);
  dropped @17 :UInt64;
  appliedProfileSequence @18 :UInt64;
  appliedProfileObservedMonoTime @19 :UInt64;
  appliedProfileValidUntilMonoTime @20 :UInt64;
}

struct FordLearningEvent @0xb276dede9e6b2cc4 {
  eventMonoTime @0 :UInt64;
  sequence @1 :UInt64;
  kind @2 :Text;
  inputs @3 :List(LearningControlEvidence.NamedValue);
  appliedCoefficient @4 :Float64;
  actualPending @5 :List(Float64);
  actualCommand @6 :List(Float64);
  controlMonoTime @7 :UInt64;
  pendingControlMonoTime @8 :UInt64;
  announcedControlMonoTime @9 :UInt64;
  carStateMonoTime @10 :UInt64;
  nativeAllowed @11 :Bool;
  fault @12 :Bool;
  override @13 :Bool;
  limited @14 :Bool;
  hasWirePath @15 :Bool;
  actualWirePath @16 :Float64;
  checkpoint @17 :List(LearningControlEvidence.NamedValue);
  checkpointSequence @18 :UInt64;
  sourceReceiptMonoTime @19 :UInt64;
  sourceValidUntilMonoTime @20 :UInt64;
  appliedProfileSequence @21 :UInt64;
  appliedProfileObservedMonoTime @22 :UInt64;
  appliedProfileValidUntilMonoTime @23 :UInt64;
  nativeIdentity @24 :Text;
  nativeObservedMonoTime @25 :UInt64;
  nativeValidUntilMonoTime @26 :UInt64;
  nativeValidatedMonoTime @27 :UInt64;
  nativeCommandMonoTime @28 :UInt64;
  announcedCoefficient @29 :Float64;
}


struct CentreApplicationWitness @0xab0b33cac682523e {
  version @0 :UInt16;
  identity @1 :Text;
  sourceEpoch @2 :Text;
  controlMonoTime @3 :UInt64;
  profileSequence @4 :UInt64;
  actualCoordinates @5 :List(Float64);
  baselineRevision @6 :Text;
  physicalModelRevision @7 :Text;
  originalObservedMonoTime @8 :UInt64;
  originalValidUntilMonoTime @9 :UInt64;
  baselineFriction @10 :Float64;
  actualFriction @11 :Float64;
}

struct LearningTorqueProjection @0xf078f562cbce49ad {
  version @0 :UInt16;
  family @1 :Text;
  fingerprint @2 :Text;
  flags @3 :UInt64;
  codecId @4 :Text;
  mappingKind @5 :Text;
  frame @6 :UInt64;
  requestedTorque @7 :Float64;
  codecInputTorque @8 :Float64;
  unlimitedRaw @9 :Int32;
  finalRaw @10 :Int32;
  effectiveMax @11 :Float64;
  lookupX @12 :List(Float64);
  lookupY @13 :List(Float64);
  limiterKind @14 :Text;
  previousTorque @15 :Float64;
  measuredTorque @16 :Float64;
  limits @17 :List(LearningControlEvidence.StageValue);
  shape @18 :List(LearningControlEvidence.NamedValue);
  steerRequested @19 :Bool;
  canAddress @20 :UInt32;
  canBus @21 :UInt16;
  canData @22 :Data;
  reportedTorque @23 :Float64;
  unlimitedRawPresent @24 :Bool;
}

# Live tracking trials are a separate protocol, never ReplayValidation.
struct LiveTrackingBinding @0x9f853f7eb4110c72 {
 identity @0 :Text; cpSha256 @1 :Text; controllerRevision @2 :Text;
 baselineRevision @3 :Text; sourceEpoch @4 :Text; resetGeneration @5 :Text;
 calibrationGeneration @6 :Text; vehicleModelGeneration @7 :Text;
 nativeIdentity @8 :Text; physicalModelRevision @9 :Text; persistentSourceEpoch @10 :Text;
 nativeRequired @11 :Bool;
}
struct LiveTrackingTrial @0xe6f67224bc0ee1a3 {
 version @0 :UInt16; protocol @1 :Text; binding @2 :LiveTrackingBinding;
 trialId @3 :Text; sequence @4 :UInt64; phase @5 :Text; group @6 :Text;
 names @7 :List(Text); baseline @8 :List(Coordinate); target @9 :List(Coordinate);
 baselineSha256 @10 :Text; targetSha256 @11 :Text;
 startedMonoTime @12 :UInt64; observedMonoTime @13 :UInt64; validUntilMonoTime @14 :UInt64;
 responseWitness @15 :LiveTrackingResponseWitness;
 targetSequence @16 :UInt64; deadlineMonoTime @17 :UInt64;
 struct Coordinate { name @0 :Text; value @1 :Float64; }
}
struct LiveTrackingApplication @0xdb85b982067f1bc1 {
 version @0 :UInt16; binding @1 :LiveTrackingBinding; trialId @2 :Text;
 sequence @3 :UInt64; controlMonoTime @4 :UInt64; status @5 :Text;
 applied @6 :List(LiveTrackingTrial.Coordinate); appliedSha256 @7 :Text;
 confirmedTargetSha256 @8 :Text; reason @9 :Text; targetSequence @10 :UInt64;
}

struct LiveTrackingRestore @0xef6257bed9318012 {
 version @0 :UInt16; protocol @1 :Text; binding @2 :LiveTrackingBinding;
 historyScope @3 :Text; historyTrialId @4 :Text; target @5 :List(LiveTrackingTrial.Coordinate);
 targetSha256 @6 :Text; sequence @7 :UInt64; observedMonoTime @8 :UInt64;
 validUntilMonoTime @9 :UInt64; responseWitness @10 :LiveTrackingResponseWitness;
}

struct LiveTrackingResponseCertificate @0xbbb8f17940692312 {
 version @0 :Text; modelRevision @1 :Text; lagNs @2 :UInt64;
 slope @3 :Float64; intercept @4 :Float64; commandLow @5 :Float64;
 commandHigh @6 :Float64; heldoutRmse @7 :Float64;
 trainBlockIds @8 :List(Text); heldoutBlockIds @9 :List(Text);
 sourceInventorySha256 @10 :Text; identificationSha256 @11 :Text; identifiedMonoTime @12 :UInt64;
 provenance @13 :Text; parentModelRevision @14 :Text; requalificationSha256 @15 :Text;
}
struct LiveTrackingResponseWitness @0xdaa1a9f0d8968e31 {
 certificate @0 :LiveTrackingResponseCertificate;
 lastControlMonoTime @1 :UInt64; lastApplyMonoTime @2 :UInt64; lastPoseMonoTime @3 :UInt64;
 windowStartMonoTime @4 :UInt64; independentPoses @5 :UInt16;
 residualRmse @6 :Float64; validUntilMonoTime @7 :UInt64;
}

