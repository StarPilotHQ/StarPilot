import assert from 'node:assert/strict'
import { compile } from '../web/vendor/vue/vue.esm-browser.js'
import { FlmPage } from '../web/js/flm.js'
import { SURFACE_NAMES, PID_UNSUPPORTED, validTrainingResults, validTrackingResults, trackingView, surfaceRows, measurementText, validTrackingValues, trackingCoordinates, validTrackingPlot, trackingPlotView, sourceSeconds, tunerStatus } from '../web/js/flm-training-results.js'

const coordinates = amount => SURFACE_NAMES.map((name, i) => [name, i === 0 ? amount : 0])
const tracking = () => ({ version: 1, status: 'trial_active', reason: 'scoring_future_tracking', group: 'feedforward',
  names: ['ff_gain_left'], trialId: 'a'.repeat(64), phase: 'trial', requiredEpisodes: 3,
  beforeEpisodes: 3, futureEpisodes: 1, modelStatus: 'valid', lagMs: 120,
  baselineErrorRms: .2, futureErrorRms: .18, baselineReversalHz: 1.2, futureReversalHz: 1.1,
  applicationAcknowledged: true, applicationSettledMonoTime: 1_000_000, lastCorrectionMonoTime: null,
  bindingCurrent: true })
const report = () => ({ version: 1, history: [], current: { fresh: true, ageSeconds: .1, enabled: true,
  status: 'collecting', reason: 'independent_evidence', progress: { samples: 120, blocks: 3, requiredBlocks: 16 },
  fit: null, application: { applied: true, reason: 'current_controller_application' },
  details: { 'Full surface coordinates': coordinates(.004), 'Actual full surface coordinates': coordinates(.001) },
  tracking: tracking() } })
assert(validTrackingResults(tracking()))
for (const requiredEpisodes of [3, 8, 9, 12, 24]) {
  const frozenQuota = report(); frozenQuota.current.tracking.requiredEpisodes = requiredEpisodes
  assert(validTrainingResults(frozenQuota))
}
const excessiveQuota = report(); excessiveQuota.current.tracking.requiredEpisodes = 25
assert(!validTrainingResults(excessiveQuota))
assert(validTrainingResults(report()))
assert.equal(trackingView(report(), true).label, 'Evaluating correction')
assert.equal(trackingView(report(), true).changedParameters, 'ff gain left')
assert.equal(trackingView(report(), true).measurements[0].future, .18)
assert.equal(surfaceRows(report(), true)[0].learned, .004, 'Do not relabel or overwrite replay learned proposals')
assert.equal(surfaceRows(report(), true)[0].applied, .001, 'Current values come from actual controller map')
assert.equal(surfaceRows(report(), true).filter(row => row.pidSupported).length, 26)
assert.equal(PID_UNSUPPORTED.length, 4)
const old = report(); delete old.current.tracking
assert(validTrainingResults(old)); assert.equal(trackingView(old, true), null)
old.current.tracking = null
assert(validTrainingResults(old)); assert.equal(trackingView(old, true), null)
for (const mutate of [value => value.current.tracking.applicationAcknowledged = false,
  value => value.current.tracking.applicationSettledMonoTime = null,
  value => value.current.tracking.trialId = null,
  value => value.current.tracking.bindingCurrent = false,
  value => value.current.application.applied = false,
  value => delete value.current.details['Actual full surface coordinates']]) {
  const value = report(); mutate(value)
  assert(validTrainingResults(value))
  assert.equal(trackingView(value, true).acknowledged, false)
  assert.notEqual(trackingView(value, true).label, 'Evaluating correction')
}
const pending = report(); pending.current.tracking.status = 'trial_pending'; pending.current.tracking.applicationAcknowledged = false
assert.match(trackingView(pending, true).label, /awaiting controller/)
const confirmed = report(); confirmed.current.tracking.status = 'confirmed'; confirmed.current.tracking.phase = 'confirmed'
confirmed.current.tracking.applicationAcknowledged = false
assert.equal(trackingView(confirmed, true).label, 'Future checks passed')
assert(!/applied|saved|persist/i.test(trackingView(confirmed, true).label))
const rollback = report(); rollback.current.tracking.status = 'rollback'; rollback.current.tracking.phase = 'rollback'
rollback.current.tracking.applicationAcknowledged = false
assert.match(trackingView(rollback, true).label, /awaiting controller/)
rollback.current.tracking.applicationAcknowledged = true
assert.equal(trackingView(rollback, true).label, 'Correction rollback acknowledged')
const neutralRollback = report(); neutralRollback.current.tracking.status = 'rollback'; neutralRollback.current.tracking.phase = 'rollback'
neutralRollback.current.application.applied = false
neutralRollback.current.details['Actual full surface coordinates'] = coordinates(0)
assert.equal(trackingView(neutralRollback, true).label, 'Correction rollback acknowledged')
const held = structuredClone(neutralRollback)
held.current.tracking.reason = 'original_input_regime_after_baseline_ack_required'
held.current.tracking.futureEpisodes = 2
assert.equal(trackingView(held, true).label, 'Baseline restored; waiting for matching road')
assert.equal(tunerStatus(held, true).title, 'Waiting for matching road')
assert.equal(trackingView(held, true).futureEpisodes, 2)
assert.equal(trackingView(held, true).requiredEpisodes, 3)
assert(!/confirmed|saved|correction applied/i.test(tunerStatus(held, true).title))
for (const mutate of [value => value.current.tracking.applicationAcknowledged = false,
  value => value.current.tracking.bindingCurrent = false,
  value => value.current.fresh = false,
  value => value.current.tracking.phase = 'trial',
  value => delete value.current.details['Actual full surface coordinates']]) {
  const noProof = structuredClone(held); mutate(noProof)
  assert.equal(trackingView(noProof, true).acknowledged, false)
  assert.notEqual(tunerStatus(noProof, true).title, 'Waiting for matching road')
}
assert.notEqual(tunerStatus(held, false).title, 'Waiting for matching road')
for (const mutate of [value => value.current.tracking.applicationAcknowledged = false,
  value => value.current.tracking.bindingCurrent = false,
  value => value.current.tracking.applicationSettledMonoTime = null,
  value => value.current.details['Actual full surface coordinates'] = coordinates(.001),
  value => delete value.current.details['Actual full surface coordinates']]) {
  const noProof = structuredClone(neutralRollback); mutate(noProof)
  assert.equal(trackingView(noProof, true).acknowledged, false)
}
const neutralTrial = structuredClone(neutralRollback); neutralTrial.current.tracking.status = 'trial_active'; neutralTrial.current.tracking.phase = 'trial'
assert.equal(trackingView(neutralTrial, true).acknowledged, false)
for (const value of [report(), pending, confirmed, rollback]) {
  const stale = trackingView(value, false)
  assert.equal(stale.acknowledged, false)
  assert.equal(stale.label, 'Earlier learning report; current application unknown')
}
for (const [status, phase] of [['trial_active', 'rollback'], ['trial_active', 'confirmed'], ['confirmed', 'trial'], ['confirmed', 'rollback'], ['rollback', 'confirmed'], ['trial_active', null]]) {
  const mismatch = report(); mismatch.current.tracking.status = status; mismatch.current.tracking.phase = phase
  assert(validTrainingResults(mismatch))
  assert.equal(trackingView(mismatch, true).acknowledged, false)
  assert.equal(trackingView(mismatch, true).label, 'Learning state reported; trial application unknown')
}
const pausedRollback = report(); pausedRollback.current.tracking.status = 'paused'; pausedRollback.current.tracking.phase = 'rollback'
assert.equal(trackingView(pausedRollback, true).label, 'Learning paused')
assert.equal(trackingView(pausedRollback, true).acknowledged, false)
const unknownPhase = report(); unknownPhase.current.tracking.phase = 'restore'
assert(validTrainingResults(unknownPhase))
assert.equal(trackingView(unknownPhase, true).acknowledged, false)
assert.equal(trackingView(unknownPhase, true).label, 'Learning state reported; trial application unknown')
const staleProducer = report(); staleProducer.current.fresh = false
assert.equal(trackingView(staleProducer, true).acknowledged, false)
assert.equal(trackingView(staleProducer, true).label, 'Earlier learning report; current application unknown')
const unmeasured = report()
for (const key of ['lagMs', 'baselineErrorRms', 'futureErrorRms', 'baselineReversalHz', 'futureReversalHz']) unmeasured.current.tracking[key] = null
assert(validTrainingResults(unmeasured))
assert.equal(measurementText(trackingView(unmeasured, true).measurements[0].future), 'Not measured')
assert.equal(measurementText(0), '0')
assert.equal(measurementText(.1234), '0.123')
assert.equal(measurementText(.000001), '1.00e-6')
for (const mutate of [value => value.version = 2, value => value.status = 'done',
  value => value.names = ['unknown'], value => value.names = ['ff_gain_left', 'ff_gain_left'],
  value => value.names = SURFACE_NAMES.slice(0, 5), value => value.trialId = 'bad', value => value.phase = 123,
  value => value.requiredEpisodes = 2, value => value.beforeEpisodes = -1, value => value.futureEpisodes = 25,
  value => value.modelStatus = 'assumed', value => value.lagMs = 501, value => value.lagMs = true,
  value => value.baselineErrorRms = -1, value => value.futureErrorRms = Infinity, value => value.futureReversalHz = NaN,
  value => value.applicationAcknowledged = 1, value => value.bindingCurrent = 'yes',
  value => value.applicationSettledMonoTime = 0, value => value.lastCorrectionMonoTime = 1.5]) {
  const value = report(); mutate(value.current.tracking)
  assert(!validTrackingResults(value.current.tracking))
  assert(!validTrainingResults(value))
  assert.equal(trackingView(value, true), null)
}
const namedCoordinates = amount => SURFACE_NAMES.map((name, i) => ({ name, value: i === 0 ? amount : 0 }))
const valuesReport = report()
valuesReport.current.trackingValues = { version: 1, accepted: namedCoordinates(.003), pending: namedCoordinates(.004),
  acceptedSha256: 'b'.repeat(64), pendingSha256: 'c'.repeat(64) }
assert(validTrainingResults(valuesReport))
assert.equal(trackingCoordinates(valuesReport.current.trackingValues.accepted).ff_gain_left, .003)
assert.equal(surfaceRows(valuesReport, true)[0].accepted, .003)
assert.equal(surfaceRows(valuesReport, true)[0].pending, .004)
assert.equal(surfaceRows(valuesReport, true)[0].learned, .004, 'Old replay proposal is preserved separately')
assert.equal(surfaceRows(valuesReport, false)[0].accepted, .003, 'Accepted baseline is historical learning, not current authority')
assert.equal(surfaceRows(valuesReport, false)[0].pending, null, 'An old offer must not be presented as pending now')
const noPending = structuredClone(valuesReport); noPending.current.trackingValues.pending = null; noPending.current.trackingValues.pendingSha256 = null
assert(validTrainingResults(noPending)); assert.equal(surfaceRows(noPending, true)[0].pending, null)
for (const mutate of [value => value.version = 2, value => value.accepted[0].value = .251,
  value => value.accepted[0].name = 'unknown', value => value.accepted = value.accepted.slice(1),
  value => value.accepted = value.accepted.reverse(), value => value.pending[0].value = NaN,
  value => value.pendingSha256 = null, value => value.acceptedSha256 = 'not-a-hash']) {
  const invalid = structuredClone(valuesReport); mutate(invalid.current.trackingValues)
  assert(!validTrackingValues(invalid.current.trackingValues)); assert(!validTrainingResults(invalid))
}
const plot = () => ({ version: 'live-tracking-paired-plot-v1', bindingCurrent: true, ageSeconds: 1,
  episodeId: 'd'.repeat(64), profileSha256: 'e'.repeat(64), lagNs: 100_000_000, units: 'm/s²', sampled: false, totalEndpoints: 4,
  points: Array.from({ length: 4 }, (_, i) => ({ controlMonoTime: 1_000_000_000 + i * 50_000_000,
    applyMonoTime: 1_002_000_000 + i * 50_000_000, poseMonoTime: 1_102_000_000 + i * 50_000_000,
    desiredForceAccel: [.2, .3, .4, .5][i], measuredForceAccel: [.18, .28, .38, .48][i], errorForceAccel: .02 })) })
const plotted = report(); plotted.current.trackingPlot = plot()
assert(validTrainingResults(plotted)); assert(validTrackingPlot(plot()))
const displayed = trackingPlotView(plotted, 500)
assert.equal(displayed.age, 1.6, 'Episode age adds actual report age and browser elapsed time without comparing clock domains')
assert.deepEqual(displayed.points.map(point => point.at), [0, 50, 100, 150])
assert.equal(displayed.points.length, plotted.current.trackingPlot.points.length, 'No extra interpolated or upsampled measurements')
for (const [i, point] of displayed.points.entries()) {
  for (const key of ['controlMonoTime', 'applyMonoTime', 'poseMonoTime', 'desiredForceAccel', 'measuredForceAccel', 'errorForceAccel'])
    assert.equal(point[key], plotted.current.trackingPlot.points[i][key])
}
assert.equal(sourceSeconds(1_002_000_000), '1.002000')
for (const boundary of [0, 30_000_000]) {
  const edge = plot()
  for (const point of edge.points) point.poseMonoTime = point.applyMonoTime + edge.lagNs + boundary
  assert(validTrackingPlot(edge), 'Preserve outer bounds of the genuine original-source lag-pair interval')
}
const sampled = plot(); sampled.sampled = true; sampled.totalEndpoints = 100
assert(validTrackingPlot(sampled))
const earlier = report(); earlier.current.fresh = false; earlier.current.trackingPlot = plot(); earlier.current.trackingPlot.bindingCurrent = false
assert(validTrainingResults(earlier)); assert(trackingPlotView(earlier), 'An earlier episode remains explicitly historical data')
for (const mutate of [value => value.version = 'unknown', value => value.bindingCurrent = 1,
  value => value.ageSeconds = -1, value => value.ageSeconds = Infinity, value => value.lagNs = 500_000_001,
  value => value.units = 'degrees', value => value.sampled = true, value => value.totalEndpoints = 3,
  value => value.points = value.points.slice(0, 1), value => value.points = Array(65).fill(value.points[0]),
  value => value.points[1].poseMonoTime = value.points[0].poseMonoTime,
  value => value.points[1].controlMonoTime = value.points[0].controlMonoTime,
  value => value.points[0].applyMonoTime = value.points[0].controlMonoTime - 1,
  value => value.points[0].poseMonoTime = '1102000000',
  value => value.points[0].poseMonoTime = value.points[0].applyMonoTime - 1,
  value => value.points[0].poseMonoTime = value.points[0].applyMonoTime + value.lagNs + 30_000_001,
  value => value.points[0].poseMonoTime = value.points[0].applyMonoTime + value.lagNs - 1,
  value => value.lagNs = 0,
  value => value.totalEndpoints = 241,
  value => value.points[0].measuredForceAccel = NaN, value => value.points[0].errorForceAccel = 1]) {
  const invalid = report(); invalid.current.trackingPlot = plot(); mutate(invalid.current.trackingPlot)
  assert(!validTrackingPlot(invalid.current.trackingPlot)); assert(!validTrainingResults(invalid)); assert.equal(trackingPlotView(invalid), null)
}
assert.match(FlmPage.template, /measured delay/)
assert.match(FlmPage.template, /Completed window/)
assert.match(FlmPage.template, /Applied now/)
assert.match(FlmPage.template, /saving is separate/)
assert.match(FlmPage.template, /No interpolation/)
compile(FlmPage.template, { onError: error => { throw error }, decodeEntities: value => value.replaceAll('&amp;', '&') })
assert.match(FlmPage.template, /Comparable trial windows/)
assert.match(FlmPage.template, /not a confirmed improvement/)
assert.doesNotMatch(FlmPage.template, /v-html/)
console.log('Optional versioned FLM tracking report, paired observed metrics, honest trial acknowledgments and old report compatibility passed')

const render = compile(FlmPage.template, { onError: e => { throw e }, decodeEntities: v => v.replaceAll('&amp;', '&') })
const ctx = { ...FlmPage.data(), ...FlmPage.methods, mode:'local', embedded:true, unauthorized() {},
 report:{version:1,history:[],current:{fresh:true,ageSeconds:0,enabled:true,status:'collecting',reason:'invalid_applied_output',progress:{samples:0,blocks:0,requiredBlocks:16},fit:null,application:{applied:false,reason:'no_current_controller_application'},details:{},tracking:{version:1,status:'collecting',reason:'uncensored_independent_comparison_baseline_required',group:null,names:[],trialId:null,phase:null,requiredEpisodes:null,beforeEpisodes:0,futureEpisodes:0,modelStatus:'valid',lagMs:170,baselineErrorRms:null,futureErrorRms:null,baselineReversalHz:null,futureReversalHz:null,applicationAcknowledged:false,applicationSettledMonoTime:null,lastCorrectionMonoTime:null,bindingCurrent:true}}}}
for (const [name,get] of Object.entries(FlmPage.computed)) Object.defineProperty(ctx,name,{get:()=>get.call(ctx)})
const text = vnode => typeof vnode === 'string' ? vnode : Array.isArray(vnode) ? vnode.map(text).join(' ') : vnode && typeof vnode === 'object' ? text(vnode.children) : ''
const liveText = text(render.call(ctx,ctx,[]))
assert(!liveText.toLowerCase().includes('invalid applied output'), 'Live learner must not display the independent legacy collector failure as its current state')
assert(!liveText.includes('Collection & fitting'), 'Legacy fitter cards must not describe the live empirical learner')
delete ctx.report.current.tracking
const legacyText = text(render.call(ctx,ctx,[]))
assert(legacyText.includes('The reported steering output could not be verified'))
assert(legacyText.includes('Collecting driving samples'))
console.log('Actual Vue render keeps live learner progress separate and preserves legacy report fallback')

const countsReport=report();countsReport.current.details['GLOBAL original source capture']={poses:1731,readyPairs:128}
const progressContext={...FlmPage.data(),report:countsReport}
for(const [name,get] of Object.entries(FlmPage.computed))Object.defineProperty(progressContext,name,{get:()=>get.call(progressContext)})
assert.equal(progressContext.measurements,1731,'Live source observations are independent of legacy fitter counts')
countsReport.current.details['GLOBAL original source capture'].poses=-1
assert.equal(progressContext.trackingProgress,null,'Invalid original counts stay unknown')
assert.equal(progressContext.measurements,null,'Missing live counts cannot be replaced with legacy zero')
console.log('Live pose count uses existing producer data, without extra telemetry or an eligibility claim')
