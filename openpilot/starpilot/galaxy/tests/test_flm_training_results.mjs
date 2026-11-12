import assert from "node:assert/strict"
import { validTrainingResults, trainingSummary, detailText, learningReason, offroadView, tunerStatus, SURFACE_NAMES, FRICTION_NAMES, surfaceRows, offroadCoordinateEvidence } from "../web/js/flm-training-results.js"

const report = () => ({ version: 1, current: { fresh: true, ageSeconds: .1, enabled: true,
  status: "collecting", reason: "independent_evidence", progress: { samples: 140, blocks: 3, requiredBlocks: 16 },
  fit: null, application: { applied: false, reason: "No fit has passed validation" },
  details: { accepted_samples: 140, rejected_reasons: { steering_override: 4 }, coordinates: [0, 0] } }, history: [] })
assert(validTrainingResults(report()))
const counters = report(); counters.current.details.rejected_reasons = Object.fromEntries(Array.from({ length: 24 }, (_, i) => [`reason_${i}`, i]))
assert(validTrainingResults(counters))
assert(validTrainingResults({ version: 1, current: null, history: [] }))
const missing = report(); missing.current.progress.samples = null
assert(validTrainingResults(missing))
assert.equal(detailText(null), "Not reported")
assert.equal(detailText({ steering_override: 4 }), "steering override: 4")
assert.match(trainingSummary({ version: 1, current: null, history: [] }), /No training results/)
const stale = report(); stale.current.fresh = false
assert.match(trainingSummary(stale), /Saved report only/)
assert.match(trainingSummary(report(), "Disconnected"), /out of date/)
for (const mutate of [value => value.current.progress.blocks = -1,
  value => value.current.ageSeconds = Infinity, value => value.current.fit = { status: "rejected", reason: "x" },
  value => value.history = Array.from({ length: 21 }, () => ({ time: "now", status: "collecting", reason: "x", applied: false })),
  value => value.current.details = { invalid: { nested: { deeper: { too_deep: true } } } }]) {
  const value = report(); mutate(value); assert(!validTrainingResults(value))
}
console.log("Training wire validation, bounded details and stale/no-data labels passed")

// The live producer's full30 flat acknowledgement and nested refusal schema
// must not be rejected as generic oversized/deep diagnostic objects.
const receiver = Object.fromEntries(['deviceMotion','extrinsicsCalibration','carState','driverMonitoringState'].map(name => [name,
  {seen:true,alive:true,valid:true,freqOk:false,sourceEventMonoTime:1,receivedMonoTime:2}]))
const snapshot = {reason:'receiver_health',observedMonoTime:3,readyPairs:4,currentNative:true,
  controlMonoTime:1,applyMonoTime:2,originalNativeObservedMonoTime:1,originalNativeValidUntilMonoTime:3,receiver}
const sourceRefusals = {publisherRefusal:'unknown_not_transmitted',scope:'learner receiver only; not the Controls publisher admission reason',
  counts:{receiver_health:1},lastRefusal:{reason:'receiver_health',observedMonoTime:3},lastException:null,
  firstRefusal:snapshot,firstReadyPairRefusal:snapshot}
const actualShape=report();actualShape.current.details['GLOBAL source observation refusals']=sourceRefusals
actualShape.current.details['Applied correction coordinates']=Array(30).fill(0)
assert(validTrainingResults(actualShape))
for(const mutate of [v=>v.current.details['Applied correction coordinates'].pop(),
  v=>v.current.details['Applied correction coordinates'].push(0),
  v=>v.current.details['Applied correction coordinates'][0]=.251,
  v=>v.current.details['Applied correction coordinates'][0]=true,
  v=>v.current.details['GLOBAL source observation refusals'].firstRefusal.receiver.carState.freqOk=1,
  v=>v.current.details['GLOBAL source observation refusals'].counts.receiver_health=-1,
  v=>v.current.details['GLOBAL source observation refusals'].firstRefusal.receiver.unknown={},
  v=>v.current.details['GLOBAL source observation refusals'].firstRefusal.unexpected=0]) {
 const invalid=structuredClone(actualShape);mutate(invalid);assert(!validTrainingResults(invalid))
}
const alias=report();alias.current.details['GLOBAL source observation refusals ']=sourceRefusals
assert(!validTrainingResults(alias),'No generic nesting or alias relaxation')
console.log('Exact live full30 acknowledgement and bounded source-refusal telemetry accepted; malformed and unknown fields refused')

const completed = report()
completed.preference = { version: 1, valid: true, enabled: true, parked: true, vehicle: 'declared-torque-car' }
completed.current.reason = 'recorded_tracking_suggestion_not_yet_qualified_for_automatic_application'
completed.current.details = {
  'Completed drive analyzed': 'declared-drive', 'Recorded steering observations': 241573,
  'Analysis decision': completed.current.reason, 'Automatic configuration changed': false,
  'Recorded route suggestions': ['highway_center_taper_max', 'turn_in_boost_left', 'unwind_taper_left',
    'turn_in_boost_right', 'unwind_taper_right'].map(name => [name, .005, 9, 'controller_native_checks_not_passed']),
}
assert(validTrainingResults(completed))
assert.equal(offroadView(completed).suggestions.length, 5)
assert.equal(offroadView(completed).samples, 241573)
assert.match(learningReason("observed_unwind_too_slow_right"), /right curves.*stronger than requested/)
const history = structuredClone(completed)
history.current.details['Drives in steering evidence history'] = 4
assert.equal(offroadView(history).evidenceDrives, 4)
history.current.details['Drives in steering evidence history'] = 9
assert.equal(offroadView(history).evidenceDrives, null)
assert.equal(offroadView(completed).evidenceDrives, null, 'Older reports must not imply cross-drive evidence')
assert.equal(tunerStatus(completed, true, true).title, 'Completed drive analyzed', 'Parked status must not mask completed analysis')
assert.match(tunerStatus(completed, true, true).description, /left your tune unchanged/)
assert.equal(completed.current.application.applied, false)
const earlierDrive = structuredClone(completed)
Object.assign(earlierDrive.current.details, {
  'Latest completed recording reviewed': 'declared-newer-manual-drive',
  'Latest recording analysis decision': 'more_independent_eligible_tracking_required',
  'Findings retained from earlier drive': 'declared-drive',
  'Drives in steering evidence history': 2,
})
assert(validTrainingResults(earlierDrive))
const priorFindings = offroadView(earlierDrive)
assert.equal(priorFindings.suggestions.length, 5, 'A newer recording without active steering must not erase earlier ideas')
assert.equal(priorFindings.earlierDrive, true)
assert.equal(priorFindings.latestRecording, 'declared-newer-manual-drive')
assert.equal(priorFindings.retained, false, 'Fresh historical findings must not claim analysis is still running')
assert.equal(priorFindings.saved, false)
assert.equal(priorFindings.savedValues, null)
assert.equal(priorFindings.evidenceDrives, 2)
assert.match(offroadCoordinateEvidence(earlierDrive, 'highway_center_taper_max'), /Earlier analyzed drive.*not saved/)
assert.equal(offroadView(completed).earlierDrive, false, 'Old reports remain readable without inventing history')
for (const mutate of [d => d['Latest completed recording reviewed'] = 7,
  d => d['Latest completed recording reviewed'] = 'declared-drive',
  d => d['Findings retained from earlier drive'] = 'different-source']) {
  const bad = structuredClone(earlierDrive); mutate(bad.current.details); assert.equal(offroadView(bad), null)
}
const configured = structuredClone(completed)
configured.current.details['Saved route configuration state'] = 'ready_for_next_drive'
assert.equal(tunerStatus(configured, true, true).title, 'Tune saved for the next drive')
assert.equal(configured.current.application.applied, false, 'Saving is not current application')
assert.notEqual(tunerStatus(configured, false, true).title, 'Tune saved for the next drive', 'Old reports cannot assert current saved state')
const fitting = structuredClone(completed)
fitting.current.status = 'fitting'; fitting.current.reason = 'analyzing_last_completed_drive'
assert.equal(tunerStatus(fitting, true, true).title, 'Analyzing your completed drive')
const disabled = structuredClone(completed); disabled.preference.enabled = false
assert.equal(tunerStatus(disabled, true, true).title, 'Auto Learn is off')
for (const mutate of [v => v.current.details['Recorded route suggestions'][0][1] = .251,
  v => v.current.details['Recorded route suggestions'][0][3] = 'confirmed',
  v => v.current.details['Recorded route suggestions'][0][2] = -1]) {
  const malformed = structuredClone(completed); mutate(malformed); assert.equal(offroadView(malformed), null)
}
console.log('Completed-drive recommendations, parked analysis, saved-versus-applied state and malformed candidate refusal passed')

// Reproduce the user's observed combination: three saved curve changes, two
// UNSAVED scalar recommendations, no live application and empty event history.
const reconciled = structuredClone(completed)
Object.assign(reconciled.current.details, {
  'Saved route configuration state': 'ready_for_next_drive', 'Saved route adjustment count': 3,
  'Full surface coordinates': SURFACE_NAMES.map(name => [name, 0]),
  'Saved friction-curve adjustments': FRICTION_NAMES.map((name, index) => [name, [0, 0, .002775, .0037125, .0037125][index]]),
  'Controller and encoder check windows': [24, 24], 'Automatic tune refusal reasons': [],
  'Recorded route suggestions': [['turn_in_boost_left', .005, 13, 'controller_native_checks_not_passed'],
    ['unwind_taper_right', .005, 6, 'controller_native_checks_not_passed']],
})
assert(validTrainingResults(reconciled))
const observedState = offroadView(reconciled)
assert.equal(observedState.savedCount, 3)
assert.equal(Object.values(observedState.savedValues).filter(value => value !== 0).length, 3)
assert.equal(observedState.savedValues.friction_threshold_mid, .002775)
assert.equal(observedState.savedValues.friction_threshold_highway, .0037125)
assert.equal(observedState.savedValues.turn_in_boost_left, 0, 'A recommendation must not overwrite the saved map')
assert.equal(observedState.suggestions[0].delta, .005)
assert.equal(surfaceRows(reconciled, false).find(row => row.name === 'turn_in_boost_left').recommendations.length, 1)
assert(surfaceRows(reconciled, false).every(row => row.pending === null && row.applied === null))
assert.match(offroadCoordinateEvidence(reconciled, 'turn_in_boost_left'), /recommendation: change \+0.005; not saved/)
assert.doesNotMatch(offroadCoordinateEvidence(reconciled, 'turn_in_boost_left'), /applied now|Auto Learn saved/)
assert.match(offroadCoordinateEvidence(reconciled, 'friction_threshold_mid'), /Auto Learn saved: \+0.002775 for the next drive/)
assert.equal(reconciled.history.length, 0, 'The completed assessment must not fabricate history events')
assert.equal(reconciled.current.application.applied, false)
const noStore = structuredClone(reconciled)
delete noStore.current.details['Saved route configuration state']
assert.equal(offroadView(noStore).savedValues, null, 'An unqualified proposal must never be labeled a saved tune')
const badCurve = structuredClone(reconciled)
badCurve.current.details['Saved friction-curve adjustments'][0][1] = NaN
assert.equal(offroadView(badCurve).savedValues.friction_threshold_crawl, undefined)
const adjustedSlider = structuredClone(reconciled)
adjustedSlider.current.details['Full surface coordinates'][0][1] = .005
assert.match(offroadCoordinateEvidence(adjustedSlider, SURFACE_NAMES[0]), /Auto Learn saved: \+0.005 for the next drive/)
assert(surfaceRows(adjustedSlider, false).every(row => row.applied === null))
console.log('Saved curve and slider adjustments, unsaved recommendations, absent history and current application reconcile without writes')
