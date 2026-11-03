import assert from "node:assert/strict"
import { validTrainingResults, trainingSummary, detailText } from "../web/js/flm-training-results.js"

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
