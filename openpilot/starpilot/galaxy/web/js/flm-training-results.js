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
        key === 'GLOBAL source observation refusals' ? validSourceRefusals(item) :
        key === 'Recorded steering response' ? item === null || recordedSteeringView(item) !== null :
        key === 'Recorded coordinate recommendations' ? validCoordinateRecommendations(item) : detailValue(item)))
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
  const route = offroadView(report)
  return SURFACE_NAMES.map(name => ({ name, baseline: 0, accepted: accepted?.[name] ?? null, pending: pending?.[name] ?? null, learned: learned?.[name] ?? null,
    saved: route?.savedValues?.[name] ?? null, recommendations: route?.suggestions.filter(row => row.parameter === name) || [],
    applied: applied?.[name] ?? null, pidSupported: !PID_UNSUPPORTED.includes(name) }))
}
export const coordinateText = value => value === null ? "Not reported" :
  value === 0 ? "0" : Math.abs(value) < 1e-6 ? value.toExponential(3) : (value > 0 ? "+" : "") + value.toFixed(6).replace(/0+$/, "").replace(/\.$/, "")

export const FRICTION_NAMES = Object.freeze(['friction_threshold_crawl', 'friction_threshold_low', 'friction_threshold_mid',
  'friction_threshold_fast', 'friction_threshold_highway'])
const frictionCoordinates = rows => Array.isArray(rows) && rows.length === 5 && rows.every((row, i) =>
  Array.isArray(row) && row.length === 2 && row[0] === FRICTION_NAMES[i] && typeof row[1] === 'number' &&
  Number.isFinite(row[1]) && Math.abs(row[1]) <= .25) ? Object.fromEntries(rows) : null

export function recommendationText(row, unit = null) {
  if (row.target === null) return 'Conflicting recorded candidates; no single suggested setting'
  if (row.target === undefined) return `change ${coordinateText(row.delta)}`
  // The setting owner displays every canonical coordinate as percent, including window edges.
  const suffix = unit ?? '%'
  const display = value => `${coordinateText(suffix === '%' ? value * 100 : value)}${suffix === 'm/s' ? ' ' : ''}${suffix}`
  return `suggested ${display(row.target)} (from ${display(row.baseline)}; change ${display(row.delta)})`
}

export function offroadCoordinateEvidence(report, name, live = false, unit = null) {
  const route = offroadView(report), row = surfaceRows(report, live).find(row => row.name === name)
  const statements = []
  const display = value => unit === '%' ? `${coordinateText(value * 100)}%` : coordinateText(value) + (unit || '')
  if (row?.applied !== null && row?.applied !== undefined && row.applied !== 0)
    statements.push(`Auto Learn applied now: ${display(row.applied)}.`)
  const saved = route?.savedValues?.[name]
  if (saved !== undefined && saved !== 0)
    statements.push(`Auto Learn saved: ${display(saved)}${route.configurationState === 'ready_for_next_drive' ? ' for the next drive' : ''}.`)
  for (const suggestion of route?.suggestions || []) if (suggestion.parameter === name)
    statements.push(`${route.retained ? 'Earlier analyzed drive' : 'Previous-drive recommendation'}: ${recommendationText(suggestion, unit)}; not saved.${suggestion.reason ? ` ${learningReason(suggestion.reason)}` : ''}`)
  if (route && !statements.length) statements.push('No recommendation from this analyzed drive for this dial.')
  return statements.join(' ')
}

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
  heldout_profile_does_not_support_training_step: "Part of the drive suggested this setting; separate windows did not confirm it.",
  independent_support_for_frozen_training_profile: "Separate recorded windows support this starting point. Automatic approval is separate.",
  training_candidate_not_independently_confirmed: "Training windows suggested this setting; it is not independently confirmed.",
  recorded_candidate_requires_automatic_checks: "A recorded-drive starting point, not approved for automatic saving.",
  conflicting_recorded_candidates: "Different windows suggested different settings. No single setting is recommended.",
  trial_started_waiting_for_comparable_drive: "The trial ran, but this drive lacked matching comparison windows. The same bounded trial will be checked on another drive.",
  trial_measurement_inconclusive: "The comparison did not establish improvement or worsening. The same bounded trial will be checked on another drive.",
  trial_inconclusive_drive_limit: "Three trial drives did not establish an improvement. The prior tune was restored.",
  next_drive_observed_tracking_worse: "Comparable driving showed a steering regression, so the prior tune was restored.",
  analyzing_last_completed_drive: "Analyzing the last completed drive while parked.",
  checking_recorded_steering_recommendations: "Checking the recorded-drive recommendations against the original controller and steering outputs.",
  recorded_tracking_suggestion_not_yet_qualified_for_automatic_application: "Recommendations were found, but none has passed the controller and steering-output checks required for automatic saving.",
  original_controller_or_registered_encoder_check_refused: "A tune was suggested, but at least one original controller or encoder check refused it. Your saved tune was kept.",
  original_scored_frame_not_eligible: "An original frame was inactive, limited or otherwise ineligible.",
  "candidate changed an actual limiter/support": "The candidate triggered an encoder limiter that the recorded request did not.",
  original_controller_or_registered_encoder_check_refused: "The recommendation failed an original controller or steering-output check. The saved tune was kept.",
  independent_drive_tracking_does_not_support_change: "The separate drive did not support the same adjustment. Your saved tune was kept.",
  no_new_native_feasible_training_step: "No bounded training step passed the original steering-output checks. The saved tune was kept.",
  no_consistent_adjustment_in_completed_drive: "The independent parts of this drive did not support the same adjustment. The saved tune was kept.",
  more_independent_eligible_tracking_required: "The drive was analyzed. More complete independent steering windows are needed before saving a tune.",
  bounded_trial_configured_for_next_drive: "A bounded, reversible tune was saved automatically for the next drive. Its road improvement is not yet confirmed.",
  recorded_steering_window_inputs_unavailable: "The recording is missing inputs needed to verify its recommendations. The saved tune was kept.",
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
export function learningReason(reason) {
  if (Object.hasOwn(REASONS, reason)) return REASONS[reason]
  const pattern = /^observed_(understeer|oversteer|late_turn_in|early_turn_in|unwind_too_slow|unwind_too_fast|low_speed_unwillingness|center_chatter|notchy_mid_curve)_(left|right|center)$/.exec(reason)
  if (pattern) {
    const [, behavior, side] = pattern
    return {
      understeer: `On ${side} curves, the car turned less sharply than requested.`,
      oversteer: `On ${side} curves, the car turned more sharply than requested.`,
      late_turn_in: `Steering was slow to build as ${side} turns started.`,
      early_turn_in: `Steering built too early as ${side} turns started.`,
      unwind_too_slow: `While easing out of ${side} curves, steering stayed stronger than requested.`,
      unwind_too_fast: `While easing out of ${side} curves, steering eased off faster than requested.`,
      low_speed_unwillingness: `At low speed, steering was slow to build into ${side} turns.`,
      center_chatter: 'Small steering corrections kept reversing on straights.',
      notchy_mid_curve: `Steering made repeated corrections while holding ${side} curves.`,
    }[behavior]
  }
  return "The device's detailed report is available in Activity."
}
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

export function validCoordinateRecommendations(rows) {
  const names = [...SURFACE_NAMES, ...FRICTION_NAMES]
  return Array.isArray(rows) && rows.length <= names.length && new Set(rows.map(row => row?.[0])).size === rows.length &&
    rows.every(row => Array.isArray(row) && row.length === 5 && names.includes(row[0]) &&
      typeof row[1] === 'number' && Number.isFinite(row[1]) && Math.abs(row[1]) <= 1000 &&
      (row[2] === null && row[4] === 'conflicting_recorded_candidates' ||
        typeof row[2] === 'number' && Number.isFinite(row[2]) && Math.abs(row[2]) <= 1000 &&
        Math.abs(row[2] - row[1]) > 1e-12 && Math.abs(row[2] - row[1]) <= .01 + 1e-12) &&
      stamp(row[3]) && row[3] > 0 && text(row[4], 240))
}

export function offroadView(report) {
  const details = report?.current?.details
  if (!record(details) || !text(details['Completed drive analyzed'], 180) || !details['Completed drive analyzed'] ||
      !stamp(details['Recorded steering observations']) || !text(details['Analysis decision'])) return null
  if (report.preference?.valid && report.vehicle && report.preference.vehicle !== report.vehicle) return null
  const rows = details['Recorded route suggestions'] ?? []
  if (!Array.isArray(rows) || rows.length > 16 || rows.some(row => !Array.isArray(row) || row.length !== 4 ||
      !text(row[0], 500) || typeof row[1] !== 'number' || !Number.isFinite(row[1]) || Math.abs(row[1]) > .25 ||
      !stamp(row[2]) || row[3] !== 'controller_native_checks_not_passed')) return null
  const observed = recordedSteeringView(details['Recorded steering response'])
  const recommendations = details['Recorded coordinate recommendations']
  if (recommendations !== undefined && !validCoordinateRecommendations(recommendations)) return null
  const savedCount = details['Saved route adjustment count']
  const checked = details['Controller and encoder check windows'], refused = details['Automatic tune refusal reasons']
  const checks = Array.isArray(checked) && checked.length === 2 && checked.every(stamp) && checked[0] <= checked[1] && checked[1] <= 36 &&
    Array.isArray(refused) && refused.length <= 36 && refused.every(r => Array.isArray(r) && r.length === 2 && text(r[0]) && stamp(r[1])) ?
    { passed: checked[0], required: checked[1], refusals: refused.map(([reason, count]) => ({ reason, count })) } : null
  const configurationState = details['Saved route configuration state'] ?? null
  const stored = ['ready_for_next_drive', 'accepted', 'accepted_after_drive', 'rolled_back'].includes(configurationState)
  const savedSurface = stored ? fullCoordinates(details['Full surface coordinates']) : null
  const savedFriction = stored ? frictionCoordinates(details['Saved friction-curve adjustments']) : null
  return { route: details['Completed drive analyzed'], samples: details['Recorded steering observations'],
    evidenceDrives: stamp(details['Drives in steering evidence history']) && details['Drives in steering evidence history'] <= 8 ?
      details['Drives in steering evidence history'] : null,
    reason: details['Analysis decision'], saved: details['Automatic configuration changed'] === true,
    configurationState, savedValues: savedSurface ? { ...savedSurface, ...(savedFriction || {}) } : null,
    observed, checks, savedCount: stamp(savedCount) && savedCount <= 35 ? savedCount : null,
    retained: details['Earlier analysis shown while updating'] === true,
    suggestions: recommendations === undefined ? rows.map(([parameter, delta, windows, qualification]) => ({ parameter, delta, windows, qualification })) :
      recommendations.map(([parameter, baseline, target, windows, reason]) => ({ parameter, baseline, target,
        delta: target === null ? null : target - baseline, windows, reason, qualification: 'controller_native_checks_not_passed' }))
        .filter(row => row.target === null || row.target !== { ...savedSurface, ...savedFriction }[row.parameter]),
    moreSuggestions: details['More recorded suggestions omitted'] === true }
}

export function recordedSteeringView(value) {
  if (!record(value) || value.version !== 1 || !['lateral_acceleration', 'steering_angle'].includes(value.domain) ||
      !stamp(value.samples) || value.samples < 20 || !stamp(value.windows) || value.windows < 1 ||
      !Number.isFinite(value.error_rms) || value.error_rms < 0 || !text(value.route, 180) ||
      !stamp(value.segment) || !stamp(value.source_start_ns) || !stamp(value.source_end_ns) ||
      value.source_start_ns <= 0 || value.source_end_ns <= value.source_start_ns ||
      value.sampling !== 'input_ordered_original_endpoints_no_interpolation' ||
      !Array.isArray(value.points) || value.points.length < 20 || value.points.length > 64 ||
      value.points.some((r, i) => !Array.isArray(r) || r.length !== 3 || !stamp(r[0]) ||
        r[0] < value.source_start_ns || r[0] > value.source_end_ns || i > 0 && r[0] <= value.points[i - 1][0] ||
        !r.slice(1).every(v => Number.isFinite(v) && Math.abs(v) <= 10000)) ||
      value.points[0][0] !== value.source_start_ns || value.points.at(-1)[0] !== value.source_end_ns) return null
  return { errorRms: value.error_rms, samples: value.samples, windows: value.windows, route: value.route,
    segment: value.segment, units: value.domain === 'lateral_acceleration' ? 'm/s²' : 'degrees',
    points: value.points.map(([ns, desired, actual]) => ({ at: (ns - value.source_start_ns) / 1e6, desired, actual })) }
}

export function tunerStatus(report, live = false, choiceLive = false, error = "", reportFresh = live) {
  const preference = choiceLive && validLearningPreference(report?.preference) ? report.preference : null
  const current = report?.current
  if (error) return { key: "offline", tone: "muted", title: "Connection interrupted", description: "Earlier results are available below. Current steering activity is unknown." }
  if (preference?.valid && preference.enabled === false || !preference && live && current?.enabled === false)
    return { key: "off", tone: "muted", title: "Auto Learn is off", description: "No driving samples are being collected by the learner. Turn it on in steering settings to begin." }
  if (reportFresh && current?.status === 'fitting' && ['analyzing_last_completed_drive', 'checking_recorded_steering_recommendations'].includes(current.reason))
    return { key: 'fitting', tone: 'active', title: 'Analyzing your completed drive', description: learningReason(current.reason) + (offroadView(report)?.retained ? ' Earlier completed-drive findings remain below while this analysis runs.' : '') }
  const route = reportFresh && (!preference || preference.parked) ? offroadView(report) : null
  if (route) {
    if (route.configurationState === 'ready_for_next_drive')
      return { key: 'next-drive', tone: 'ready', title: 'Tune saved for the next drive', description: learningReason(
        ['trial_started_waiting_for_comparable_drive', 'trial_measurement_inconclusive'].includes(route.reason)
          ? route.reason : 'bounded_trial_configured_for_next_drive') }
    return { key: 'analyzed', tone: route.suggestions.length ? 'warning' : 'ready', title: 'Completed drive analyzed', description: learningReason(route.reason) }
  }
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
