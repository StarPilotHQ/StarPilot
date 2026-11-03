const text = (value, limit = 240) => typeof value === "string" && value.length <= limit
const count = value => value === null || Number.isSafeInteger(value) && value >= 0
const record = value => value !== null && typeof value === 'object' && !Array.isArray(value)
const keysAre = (value, keys) => record(value) && Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key))
const stamp = value => Number.isSafeInteger(value) && value >= 0
const REFUSAL_SERVICES = ['deviceMotion', 'extrinsicsCalibration', 'carState', 'driverMonitoringState']
function refusalSnapshot(value) {
  return value === null || keysAre(value, ['reason', 'observedMonoTime', 'readyPairs', 'currentNative', 'controlMonoTime',
    'applyMonoTime', 'originalNativeObservedMonoTime', 'originalNativeValidUntilMonoTime', 'receiver']) &&
    text(value.reason) && stamp(value.observedMonoTime) && stamp(value.readyPairs) && typeof value.currentNative === 'boolean' &&
    ['controlMonoTime', 'applyMonoTime', 'originalNativeObservedMonoTime', 'originalNativeValidUntilMonoTime'].every(key => value[key] === null || stamp(value[key])) &&
    keysAre(value.receiver, REFUSAL_SERVICES) && REFUSAL_SERVICES.every(service => {
      const row = value.receiver[service]
      return keysAre(row, ['seen', 'alive', 'valid', 'freqOk', 'sourceEventMonoTime', 'receivedMonoTime']) &&
        ['seen', 'alive', 'valid', 'freqOk'].every(key => typeof row[key] === 'boolean') &&
        stamp(row.sourceEventMonoTime) && stamp(row.receivedMonoTime)
    })
}
export function validSourceRefusals(value) {
  return keysAre(value, ['publisherRefusal', 'scope', 'counts', 'lastRefusal', 'lastException', 'firstRefusal', 'firstReadyPairRefusal']) &&
    value.publisherRefusal === 'unknown_not_transmitted' &&
    value.scope === 'learner receiver only; not the Controls publisher admission reason' &&
    record(value.counts) && Object.keys(value.counts).length <= 24 &&
    Object.entries(value.counts).every(([key, amount]) => text(key, 96) && stamp(amount)) &&
    (value.lastRefusal === null || keysAre(value.lastRefusal, ['reason', 'observedMonoTime']) && text(value.lastRefusal.reason) && stamp(value.lastRefusal.observedMonoTime)) &&
    (value.lastException === null || keysAre(value.lastException, ['observedMonoTime', 'type', 'message']) &&
      stamp(value.lastException.observedMonoTime) && text(value.lastException.type, 96) && text(value.lastException.message, 192)) &&
    refusalSnapshot(value.firstRefusal) && refusalSnapshot(value.firstReadyPairRefusal)
}
const detailValue = (value, depth = 0) => value === null || typeof value === "boolean" ||
  typeof value === "number" && Number.isFinite(value) || text(value, 500) ||
  depth < 2 && (Array.isArray(value) ? value.length <= 16 && value.every(item => detailValue(item, depth + 1)) :
    value && typeof value === "object" && Object.keys(value).length <= 64 &&
    Object.entries(value).every(([key, item]) => text(key, 80) && detailValue(item, depth + 1)))
export function validTrainingResults(value) {
  if (!value || value.version !== 1 || !Array.isArray(value.history) || value.history.length > 20) return false
  if (value.preference !== undefined && !validLearningPreference(value.preference)) return false
  if (!value.history.every(row => row && text(row.time, 80) && text(row.status, 64) && text(row.reason) && typeof row.applied === "boolean")) return false
  const row = value.current
  if (row === null) return true
  if (!row || typeof row.fresh !== "boolean" || !Number.isFinite(row.ageSeconds) || row.ageSeconds < 0 ||
      ![true, false, null].includes(row.enabled) || !text(row.status, 64) || !text(row.reason) || !row.progress ||
      ![row.progress.samples, row.progress.blocks, row.progress.requiredBlocks].every(count)) return false
  if (row.fit !== null && (!row.fit || !text(row.fit.status, 64) || !text(row.fit.reason) ||
      ![row.fit.trainWindows, row.fit.heldoutWindows].every(count))) return false
  if (row.application !== null && (!row.application || typeof row.application.applied !== "boolean" || !text(row.application.reason))) return false
  if (row.tracking !== undefined && row.tracking !== null && !validTrackingResults(row.tracking)) return false
  if (row.trackingValues !== undefined && row.trackingValues !== null && !validTrackingValues(row.trackingValues)) return false
  if (row.trackingPlot !== undefined && row.trackingPlot !== null && !validTrackingPlot(row.trackingPlot)) return false
  return row.details === undefined || row.details && typeof row.details === "object" && !Array.isArray(row.details) &&
    Object.keys(row.details).length <= 64 && Object.entries(row.details).every(([key, item]) => text(key, 80) &&
      (["Full surface coordinates", "Actual full surface coordinates"].includes(key) ? fullCoordinates(item) !== null :
        key === 'Applied correction coordinates' && Array.isArray(item) && item.length === 30 ?
          item.every(number => typeof number === 'number' && Number.isFinite(number) && Math.abs(number) <= .25) :
        key === 'GLOBAL source observation refusals' ? validSourceRefusals(item) : detailValue(item)))
}
const labels = { collecting: "Gathering driving examples", unavailable: "Waiting for usable driving data",
  qualifiedFit: "Fit passed validation", qualified: "Fit passed validation", rejected: "Fit did not pass validation",
  fitting: "Evaluating a possible adjustment", applied: "Adjustment applied",
  independent_evidence: "Gathering independently measured driving examples",
  settings_receipt_expired: "Learning paused because its settings confirmation expired",
  no_current_controller_evidence: "Waiting for current steering measurements",
  capturing_steering_evidence: "Recording eligible steering measurements",
  awaiting_different_maneuver: "Current maneuver captured; waiting for a different driving example",
  evaluating_collected_maneuvers: "Checking the collected driving examples",
  whole_physical_event_support: "Too few complete independent event bundles for fitting",
  whole_event_export_support: "Too few complete bundles fit the replay export budget",
  event_storage_packet_support: "Waiting for enough retained storage packets",
  snapshot_context_changed: "Collection context or objective changed; waiting for its next snapshot selection",
  snapshot_preparing: "Complete bundles selected; export is not complete",
  snapshot_not_attempted: "No snapshot selection has run for this collection context",
  snapshot_ready: "Snapshot selected; child qualification is still required",
  no_current_collection_context: "Waiting for a current collection context",
  no_current_physical_context: "Waiting for a current physical context",
  unchanged_storage_since_submission: "Waiting for new storage after the submitted fit",
  event_snapshot_unavailable: "The snapshot could not be prepared",
  report_stale: "Current results are unavailable; this is an earlier report" }
export const readable = value => labels[value] || String(value ?? "Not reported").replaceAll("_", " ")
export const detailText = value => value === null ? "Not reported" : Array.isArray(value) ? value.map(detailText).join(", ") :
  typeof value === "boolean" ? value ? "Yes" : "No" : typeof value === "object" ?
    Object.entries(value).map(([key, item]) => `${readable(key)}: ${detailText(item)}`).join("; ") : String(value)
export const trainingSummary = (report, error = "") => error ? "Live update unavailable. Previous results may be out of date." :
  !report?.current ? "No training results have been reported yet." : !report.current.fresh ?
    "Saved report only. Current learning and application are unknown." : readable(report.current.status)

// Exact canonical order from canonical_surface_adapter.NAMES; no controller imports.
export const SURFACE_NAMES = Object.freeze([
  "ff_gain_left",
  "ff_gain_right",
  "turn_in_boost_left",
  "turn_in_boost_right",
  "unwind_taper_left",
  "unwind_taper_right",
  "center_taper_max",
  "highway_center_taper_max",
  "center_deadband_crawl_deg",
  "center_deadband_low_deg",
  "center_deadband_mid_deg",
  "center_deadband_fast_deg",
  "center_deadband_highway_deg",
  "turn_in_threshold_reduction_left",
  "turn_in_threshold_reduction_right",
  "unwind_threshold_increase_left",
  "unwind_threshold_increase_right",
  "crawl_turn_in_ff_boost_left",
  "crawl_turn_in_ff_boost_right",
  "low_speed_angle_assist_max_torque",
  "curvy_speed_min",
  "curvy_speed_max",
  "curvy_turn_in_trim_speed_min",
  "curvy_turn_in_trim_speed_max",
  "curvy_turn_in_trim_left",
  "curvy_turn_in_trim_right",
  "curvy_unwind_floor_relief_left",
  "curvy_unwind_floor_relief_right",
  "curvy_unwind_extra_reduction_left",
  "curvy_unwind_extra_reduction_right"
])
export const PID_UNSUPPORTED = Object.freeze([
  "turn_in_threshold_reduction_left", "turn_in_threshold_reduction_right",
  "unwind_threshold_increase_left", "unwind_threshold_increase_right",
])
export function fullCoordinates(rows) {
  if (!Array.isArray(rows) || rows.length !== SURFACE_NAMES.length || rows.some((row, i) =>
    !Array.isArray(row) || row.length !== 2 || row[0] !== SURFACE_NAMES[i] ||
    typeof row[1] !== "number" || !Number.isFinite(row[1]) || Math.abs(row[1]) > .25)) return null
  return Object.fromEntries(rows)
}
export function surfaceRows(report, live = false) {
  const details = report?.current?.details || {}
  const learned = fullCoordinates(details["Full surface coordinates"])
  const applied = live && report.current?.application?.applied === true ?
    fullCoordinates(details["Actual full surface coordinates"]) : null
  const values = validTrackingValues(report?.current?.trackingValues) ? report.current.trackingValues : null
  const accepted = values ? trackingCoordinates(values.accepted) : null
  const pending = values && live && report.current?.tracking?.bindingCurrent === true ? trackingCoordinates(values.pending) : null
  return SURFACE_NAMES.map(name => ({ name, baseline: 0, accepted: accepted?.[name] ?? null, pending: pending?.[name] ?? null, learned: learned?.[name] ?? null,
    applied: applied?.[name] ?? null, pidSupported: !PID_UNSUPPORTED.includes(name) }))
}
export const coordinateText = value => value === null ? "Not reported" :
  value === 0 ? "0" : Math.abs(value) < 1e-6 ? value.toExponential(3) : (value > 0 ? "+" : "") + value.toFixed(6).replace(/0+$/, "").replace(/\.$/, "")

export function validLearningPreference(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value) && value.version === 1 &&
    typeof value.valid === "boolean" && [true, false, null].includes(value.enabled) &&
    [true, false, null].includes(value.parked) && (value.vehicle === null || text(value.vehicle, 256)) &&
    (value.valid ? typeof value.enabled === "boolean" && typeof value.parked === "boolean" && value.vehicle !== null : value.enabled === null)
}

export const SURFACE_GROUPS = Object.freeze([
  { id: "balance", label: "Steering balance", icon: "bi-arrow-left-right", description: "Response to left and right steering requests.", names: ["ff_gain_left", "ff_gain_right", "turn_in_boost_left", "turn_in_boost_right", "unwind_taper_left", "unwind_taper_right"] },
  { id: "center", label: "Straight & center", icon: "bi-signpost-split", description: "Small corrections and straight-road steadiness.", names: ["center_taper_max", "highway_center_taper_max", ...SURFACE_NAMES.filter(name => name.startsWith("center_deadband_"))] },
  { id: "low", label: "Low speed", icon: "bi-speedometer", description: "Crawl-speed turn-in and angle assistance.", names: ["crawl_turn_in_ff_boost_left", "crawl_turn_in_ff_boost_right", "low_speed_angle_assist_max_torque"] },
  { id: "entry", label: "Corner entry", icon: "bi-arrow-up-right", description: "Friction thresholds and turn-in trimming.", names: ["turn_in_threshold_reduction_left", "turn_in_threshold_reduction_right", "curvy_turn_in_trim_left", "curvy_turn_in_trim_right"] },
  { id: "exit", label: "Corner exit", icon: "bi-arrow-return-right", description: "Friction and relief as steering unwinds.", names: ["unwind_threshold_increase_left", "unwind_threshold_increase_right", "curvy_unwind_floor_relief_left", "curvy_unwind_floor_relief_right", "curvy_unwind_extra_reduction_left", "curvy_unwind_extra_reduction_right"] },
  { id: "speed", label: "Speed ranges", icon: "bi-sliders", description: "Where turn-in and unwind shaping take effect.", names: ["curvy_speed_min", "curvy_speed_max", "curvy_turn_in_trim_speed_min", "curvy_turn_in_trim_speed_max"] },
])

const REASONS = {
  controller_collection_required: "Waiting for active steering before collecting samples.",
  fresh_physical_residual_window_required: "Checking the car's current steering response.",
  qualified_tracking_episodes_required: "Collecting complete, comparable steering windows.",
  uncensored_independent_comparison_baseline_required: "Measuring comparable driving before testing a correction.",
  qualified_consistent_hook_sensitivity_required: "Collecting repeated examples before choosing an adjustment.",
  stale_control: "Steering samples arrived late. Fresh data is required before learning can continue.",
  stale_applied_output: "Applied steering data arrived late. Those samples cannot be used.",
  invalid_applied_output: "The reported steering output could not be verified.",
  independent_baseline_trial_power_insufficient: "More comparable driving is needed to distinguish a correction from normal variation.",
  fresh_selected_native_probe_required: "Checking the proposed adjustment against the current steering commands.",
  genuine_native_sensitivity_unavailable: "The effect of this adjustment has not been verified yet.",
  consistent_later_tracking_improved: "Later comparable driving showed lower steering error.",
  independent_future_tracking_improved: "Independent trial windows showed lower steering error.",
  matched_tracking_not_consistently_improved: "The trial did not show a consistent improvement.",
  trial_tracking_or_oscillation_worse: "The trial increased steering error or oscillation, so it was rolled back.",
  trial_oscillation_batch_worse: "Comparable trial windows showed more oscillation, so the correction was rolled back.",
  predeclared_independent_future_look_required: "Collecting the next planned set of comparison windows.",
  prior_accepted_baseline_restored: "The controller returned to its prior baseline.",
  original_input_regime_after_baseline_ack_required: "The baseline is restored. Completed comparison samples are kept while waiting for the same driving conditions.",
  scoring_later_matched_whole_episodes: "Comparing new steering windows with the baseline.",
  accepted_target_currently_acknowledged: "The controller acknowledged the accepted correction.",
}
export const learningReason = reason => Object.hasOwn(REASONS, reason) ? REASONS[reason] : "The device's detailed report is available in Activity."
export function sourceCaptureCounts(report) {
  const value = report?.current?.details?.['GLOBAL original source capture']
  return validTrackingResults(report?.current?.tracking) && value &&
    ['poses', 'readyPairs'].every(key => Number.isSafeInteger(value[key]) && value[key] >= 0) ?
    { capturedPoses: value.poses, readyPairs: value.readyPairs } : null
}
const HISTORY_LABELS = { collecting: 'Collecting samples', calibrating: 'Measuring response',
  not_identifiable: 'More evidence needed', ready: 'Adjustment selected', trial_pending: 'Trial offered',
  trial_active: 'Evaluating a trial', confirmed: 'Correction confirmed', rollback: 'Returning to baseline', paused: 'Learning paused' }
export const historyTitle = status => Object.hasOwn(HISTORY_LABELS, status) ? HISTORY_LABELS[status] : readable(status)

export function tunerStatus(report, live = false, choiceLive = false, error = "") {
  const preference = choiceLive && validLearningPreference(report?.preference) ? report.preference : null
  const current = report?.current
  if (error) return { key: "offline", tone: "muted", title: "Connection interrupted", description: "Earlier results are available below. Current steering activity is unknown." }
  if (preference?.valid && preference.enabled === false || !preference && live && current?.enabled === false)
    return { key: "off", tone: "muted", title: "Auto Learn is off", description: "No driving samples are being collected by the learner. Turn it on in steering settings to begin." }
  if (preference?.valid && preference.enabled && preference.parked)
    return { key: "parked", tone: "ready", title: "Auto Learn is on", description: "Collection starts while driving. No new correction is being applied while parked." }
  if (!current) return { key: "waiting", tone: "muted", title: "Waiting for the learner", description: preference?.enabled ? "Auto Learn is on, but no learner report has arrived yet." : preference ? "The device has not identified a current vehicle and Auto Learn setting." : "Connect to your device to see its learning activity and steering tune." }
  if (!live) return { key: "stale", tone: "muted", title: "No current learner report", description: "These are earlier observations. They do not establish a correction being applied now." }
  const tracking = trackingView(report, live)
  if ((tracking?.reason || current.reason) === "controller_collection_required")
    return { key: "waiting", tone: "muted", title: "Waiting for active steering", description: learningReason("controller_collection_required") }
  if (tracking && !tracking.bindingCurrent)
    return { key: "paused", tone: "warning", title: "Learning paused", description: "Current steering evidence is unavailable. A fresh qualified window is required." }
  if (tracking?.acknowledged && tracking.status === "confirmed") return { key: "confirmed", tone: "success", title: "Correction applied", description: "The comparison passed and the controller acknowledged this correction. Disk saving is a separate result." }
  if (tracking?.acknowledged && tracking.status === "trial_active") return { key: "testing", tone: "active", title: "Testing a small correction", description: "The controller acknowledged the trial. Comparable new driving windows decide whether to keep it." }
  if (tracking?.status === "trial_pending") return { key: "pending", tone: "active", title: "Waiting for the controller", description: "A trial was offered. It has not yet been acknowledged as applied." }
  if (tracking?.status === "rollback" && tracking.acknowledged && tracking.reason === "original_input_regime_after_baseline_ack_required")
    return { key: "waiting", tone: "ready", title: "Waiting for matching road", description: learningReason(tracking.reason) }
  if (tracking?.status === "rollback") return { key: "rollback", tone: "warning", title: "Returning to the baseline", description: tracking.acknowledged ? "The controller acknowledged the rollback." : "A rollback was requested. Current application is not yet acknowledged." }
  if (tracking?.status === "paused" || current.status === "unavailable") return { key: "paused", tone: "warning", title: "Learning paused", description: learningReason(tracking?.reason || current.reason) }
  return { key: "collecting", tone: "active", title: tracking?.status === "calibrating" ? "Measuring steering response" : "Collecting driving samples", description: learningReason(tracking?.reason || current.reason) }
}


const TRACKING_STATES = ['calibrating', 'collecting', 'not_identifiable', 'ready', 'trial_pending', 'trial_active', 'confirmed', 'rollback', 'paused']
const optionalMeasurement = value => value === null || typeof value === 'number' && Number.isFinite(value) && value >= 0
const episodeCount = value => Number.isSafeInteger(value) && value >= 0 && value <= 24
const optionalStamp = value => value === null || Number.isSafeInteger(value) && value > 0
export function validTrackingResults(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value) && value.version === 1 &&
    TRACKING_STATES.includes(value.status) && text(value.reason) &&
    (value.group === null || text(value.group, 80)) && Array.isArray(value.names) && value.names.length <= 4 &&
    value.names.every(name => SURFACE_NAMES.includes(name)) && new Set(value.names).size === value.names.length &&
    (value.trialId === null || typeof value.trialId === 'string' && /^[0-9a-f]{64}$/.test(value.trialId)) &&
    (value.phase === null || text(value.phase, 64)) &&
    (value.requiredEpisodes === null || Number.isSafeInteger(value.requiredEpisodes) && value.requiredEpisodes >= 3 && value.requiredEpisodes <= 24) && episodeCount(value.beforeEpisodes) && episodeCount(value.futureEpisodes) &&
    ['valid', 'stale', 'drift', 'unavailable'].includes(value.modelStatus) &&
    optionalMeasurement(value.lagMs) && (value.lagMs === null || value.lagMs <= 500) &&
    [value.baselineErrorRms, value.futureErrorRms, value.baselineReversalHz, value.futureReversalHz].every(optionalMeasurement) &&
    typeof value.applicationAcknowledged === 'boolean' && typeof value.bindingCurrent === 'boolean' &&
    optionalStamp(value.applicationSettledMonoTime) && optionalStamp(value.lastCorrectionMonoTime)
}
const trackingLabels = {
  calibrating: 'Measuring steering response', collecting: 'Collecting comparable driving examples',
  not_identifiable: 'Waiting for identifiable steering response', ready: 'Ready to evaluate a small correction',
  trial_pending: 'Correction offered; awaiting controller acknowledgement',
  trial_active: 'Trial awaiting current application evidence', confirmed: 'Future checks passed',
  rollback: 'Rollback awaiting controller acknowledgement', paused: 'Learning paused',
}
export function trackingView(report, live = false) {
  const value = report?.current?.tracking
  if (!validTrackingResults(value)) return null
  live = live && report.current.fresh && report.current.ageSeconds <= 2
  const expectedPhase = { trial_pending: 'trial', trial_active: 'trial', confirmed: 'confirmed', rollback: 'rollback' }[value.status]
  const matchingPhase = expectedPhase !== undefined && value.phase === expectedPhase
  const actual = fullCoordinates(report.current.details?.['Actual full surface coordinates'])
  // The producer acknowledgement binds the actual owner target; a zero offer alone is never evidence.
  const neutralRollback = value.status === 'rollback' && matchingPhase && actual !== null &&
    Object.values(actual).every(coordinate => coordinate === 0)
  const acknowledged = live && matchingPhase && value.status !== 'trial_pending' && value.bindingCurrent && value.applicationAcknowledged &&
    value.applicationSettledMonoTime !== null && value.trialId !== null && actual !== null &&
    (report.current.application?.applied === true || neutralRollback)
  let label = trackingLabels[value.status]
  if (value.status === 'trial_active' && acknowledged) label = 'Evaluating correction'
  if (value.status === 'confirmed' && acknowledged) label = 'Confirmed correction acknowledged'
  if (value.status === 'rollback' && acknowledged) label = 'Correction rollback acknowledged'
  if (value.status === 'rollback' && acknowledged && value.reason === 'original_input_regime_after_baseline_ack_required') label = 'Baseline restored; waiting for matching road'
  if (value.status !== 'paused' && (value.phase !== null && !matchingPhase || expectedPhase !== undefined && !matchingPhase)) label = 'Learning state reported; trial application unknown'
  if (live && !value.bindingCurrent) label = 'Learning paused; current steering evidence unavailable'
  if (!live) label = 'Earlier learning report; current application unknown'
  return { ...value, label, acknowledged, changedParameters: value.names.map(readable).join(', '),
    measurements: [
      { label: 'Tracking error RMS · m/s²', before: value.baselineErrorRms, future: value.futureErrorRms },
      { label: 'Steering reversals · Hz', before: value.baselineReversalHz, future: value.futureReversalHz },
    ] }
}
export const measurementText = value => value === null ? 'Not measured' :
  value !== 0 && value < .001 ? value.toExponential(2) : value.toFixed(3).replace(/0+$/, '').replace(/\.$/, '')


const sha256 = value => typeof value === 'string' && /^[0-9a-f]{64}$/.test(value)
export function trackingCoordinates(rows) {
  if (!Array.isArray(rows) || rows.length !== SURFACE_NAMES.length || rows.some((row, i) =>
    !row || typeof row !== 'object' || Array.isArray(row) || row.name !== SURFACE_NAMES[i] ||
    typeof row.value !== 'number' || !Number.isFinite(row.value) || Math.abs(row.value) > .25)) return null
  return Object.fromEntries(rows.map(row => [row.name, row.value]))
}
export function validTrackingValues(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value) && value.version === 1 &&
    trackingCoordinates(value.accepted) !== null && sha256(value.acceptedSha256) &&
    (value.pending === null && value.pendingSha256 === null || trackingCoordinates(value.pending) !== null && sha256(value.pendingSha256))
}
export function validTrackingPlot(value) {
  if (!value || value.version !== 'live-tracking-paired-plot-v1' || typeof value.bindingCurrent !== 'boolean' ||
      typeof value.ageSeconds !== 'number' || !Number.isFinite(value.ageSeconds) || value.ageSeconds < 0 ||
      !sha256(value.episodeId) || !sha256(value.profileSha256) || value.units !== 'm/s²' ||
      !Number.isSafeInteger(value.lagNs) || value.lagNs < 0 || value.lagNs > 500_000_000 ||
      !Array.isArray(value.points) || value.points.length < 2 || value.points.length > 64 ||
      !Number.isSafeInteger(value.totalEndpoints) || value.totalEndpoints < value.points.length || value.totalEndpoints < 4 || value.totalEndpoints > 240 ||
      value.sampled !== (value.totalEndpoints > value.points.length)) return false
  return value.points.every((point, i) => point &&
    ['controlMonoTime', 'applyMonoTime', 'poseMonoTime'].every(key => Number.isSafeInteger(point[key]) && point[key] > 0) &&
    point.applyMonoTime >= point.controlMonoTime && point.poseMonoTime >= point.applyMonoTime + value.lagNs &&
    point.poseMonoTime <= point.applyMonoTime + value.lagNs + 30_000_000 &&
    ['desiredForceAccel', 'measuredForceAccel', 'errorForceAccel'].every(key => typeof point[key] === 'number' && Number.isFinite(point[key])) &&
    Math.abs(point.desiredForceAccel - point.measuredForceAccel - point.errorForceAccel) <=
      1e-9 * Math.max(1, Math.abs(point.desiredForceAccel), Math.abs(point.measuredForceAccel)) &&
    (i === 0 || point.controlMonoTime > value.points[i - 1].controlMonoTime && point.poseMonoTime > value.points[i - 1].poseMonoTime))
}
export function trackingPlotView(report, elapsedMs = 0) {
  const value = report?.current?.trackingPlot
  if (!validTrackingPlot(value)) return null
  const first = value.points[0].poseMonoTime
  return { ...value, age: value.ageSeconds + report.current.ageSeconds + Math.max(0, elapsedMs) / 1000,
    points: value.points.map(point => ({ ...point, at: (point.poseMonoTime - first) / 1e6 })) }
}
export const sourceSeconds = value => (value / 1e9).toFixed(6)
