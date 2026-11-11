import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { compile } from '../web/vendor/vue/vue.esm-browser.js'
import { FlmPage } from '../web/js/flm.js'
import { GalaxySettingRow } from '../web/js/galaxy-setting-row.js'
import { SURFACE_NAMES, FRICTION_NAMES, offroadView, offroadCoordinateEvidence, recommendationText, PID_UNSUPPORTED, validTrainingResults, validTrackingResults, trackingView, surfaceRows, measurementText, validTrackingValues, trackingCoordinates, validTrackingPlot, trackingPlotView, sourceSeconds, tunerStatus } from '../web/js/flm-training-results.js'

if (process.env.CI && process.platform === 'linux') execFileSync('python3', ['tools/ci/check_prebuilt_receipt.py', '--revision', 'HEAD'], { stdio: 'inherit' })

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
assert.match(FlmPage.template, /saving not confirmed/)
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
ctx.report.current.trackingValues = structuredClone(valuesReport.current.trackingValues)
const acceptedOnlyText = text(render.call(ctx,ctx,[]))
assert.match(acceptedOnlyText, /accepted.*saving not confirmed/s)
assert(!acceptedOnlyText.includes('Saved adjustments'), 'A live accepted map is not proof of persistence')
delete ctx.report.current.trackingValues

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

// Parked analysis has a fresh report but deliberately has NO live application.
ctx.report = {version:1,history:[],preference:{version:1,valid:true,enabled:true,parked:true,vehicle:'declared-torque-car'},
 current:{fresh:true,ageSeconds:.1,enabled:null,status:'collecting',reason:'recorded_tracking_suggestion_not_yet_qualified_for_automatic_application',
 progress:{samples:241573,blocks:251,requiredBlocks:6},fit:null,application:{applied:false,reason:'saved_configuration_is_not_current_application'},
 details:{'Completed drive analyzed':'declared-drive','Recorded steering observations':241573,
 'Analysis decision':'recorded_tracking_suggestion_not_yet_qualified_for_automatic_application','Automatic configuration changed':false,
 'Recorded route suggestions':['highway_center_taper_max','turn_in_boost_left','unwind_taper_left','turn_in_boost_right','unwind_taper_right']
 .map(name=>[name,.005,9,'controller_native_checks_not_passed'])}}}
assert.equal(ctx.live,false,'Parked report is never a live application acknowledgement')
assert.equal(ctx.reportFresh,true)
assert.equal(ctx.state.title,'Completed drive analyzed')
ctx.view = 'tune'
const offroadText=text(render.call(ctx,ctx,[]))
for(const label of ['Highway center taper','Left turn-in boost','Left unwind taper','Right turn-in boost','Right unwind taper'])assert(offroadText.includes(label))
assert(!offroadText.includes('Waiting for driving samples'))
assert(!offroadText.includes('Real measurements go here'))
assert.equal(ctx.currentApplied,false)
ctx.view = 'overview'
const measuredWindow={version:1,domain:'lateral_acceleration',samples:2000,windows:10,error_rms:.12,route:'declared-drive',segment:0,
 source_start_ns:1e9,source_end_ns:2.9e9,points:Array.from({length:20},(_,i)=>[1e9+i*1e8,.2,.08]),
 sampling:'input_ordered_original_endpoints_no_interpolation'}
ctx.report.current.details['Recorded steering response']=measuredWindow
ctx.report.current.details['Saved route adjustment count']=0
assert(validTrainingResults(ctx.report),'The whole wire report must accept its bounded recorded graph before rendering')
const completeGraph=structuredClone(ctx.report)
completeGraph.current.details['Recorded steering response']={...measuredWindow,source_end_ns:7.3e9,
 points:Array.from({length:64},(_,i)=>[1e9+i*1e8,.2,.08])}
assert(validTrainingResults(completeGraph),'The production 64-point recorded graph fits the wire contract')
completeGraph.current.details['Recorded steering response'].points.push([7.4e9,.2,.08])
assert(!validTrainingResults(completeGraph),'The recorded graph retains its 64-point bound')
const unboundedDetails=structuredClone(ctx.report)
unboundedDetails.current.details['Unknown nested diagnostic']={points:measuredWindow.points}
assert(!validTrainingResults(unboundedDetails),'Unrelated nested details retain their original bounds')
ctx.report.current.details['Controller and encoder check windows']=[22,24]
ctx.report.current.details['Automatic tune refusal reasons']=[['original_scored_frame_not_eligible',1],['candidate changed an actual limiter/support',1]]
assert.equal(ctx.offroad.observed.errorRms,.12)
assert.equal(ctx.recordedTrackingChart.valid,true)
assert(ctx.recordedTrackingChart.paths.every(path=>path.length===1),'Both original series render')
const measuredText=text(render.call(ctx,ctx,[]))
assert(measuredText.includes('Recorded baseline') && measuredText.includes(measurementText(.12)))
assert.match(measuredText, /0\s+saved/)
assert(measuredText.includes('No pending trial reported'))
assert(!measuredText.includes('Awaiting next-drive measurement'), 'Recorded analysis without a saved trial must not invent a pending trial')
assert(measuredText.includes('No interpolation or delay alignment'))
assert(measuredText.includes('22 / 24 original controller and encoder windows passed'))
assert(measuredText.includes('candidate triggered an encoder limiter'))
ctx.report.current.details['Controller and encoder check windows']=[35,35]
ctx.report.current.details['Automatic tune refusal reasons']=[]
assert(text(render.call(ctx,ctx,[])).includes('35 / 35 original controller and encoder windows passed'))
ctx.report.current.details['Controller and encoder check windows']=[36,37]
assert.equal(ctx.offroad.checks,null,'The independent-drive count remains bounded')
ctx.report.current.details['Controller and encoder check windows']=[22,24]
assert.equal(ctx.currentApplied,false)
for(const changed of [{points:measuredWindow.points.toReversed()},{error_rms:NaN},{domain:'torque'},
 {source_end_ns:measuredWindow.source_end_ns+1}]){
 ctx.report.current.details['Recorded steering response']={...measuredWindow,...changed}
 assert.equal(ctx.offroad.observed,null,'Malformed recorded data cannot populate the graph')
 assert(!validTrainingResults(ctx.report),'Malformed recorded graphs must be refused at the wire boundary')
}
ctx.report.current.details['Recorded steering response']={...measuredWindow,domain:'steering_angle'}
assert.equal(ctx.offroad.observed.units,'degrees','PID observations retain their angle units')
ctx.report.current.details['Recorded steering response']=measuredWindow
ctx.report.current.details['Saved route configuration state']='ready_for_next_drive'
assert.equal(ctx.state.title,'Tune saved for the next drive')
assert(text(render.call(ctx,ctx,[])).includes('Awaiting next-drive measurement'), 'A genuinely reported saved trial awaits the next drive')
assert.equal(ctx.currentApplied,false,'Next-drive saving never fabricates live application')
ctx.now=3000
assert.equal(ctx.reportFresh,false)
assert.notEqual(ctx.state.title,'Tune saved for the next drive','Expired report cannot assert the current saved tune')
console.log('Actual parked Vue render exposes all five route candidates, distinguishes saving/application and expires stale status')

// Declared UI states only: never enable driving edits or contact a device.
// Render BOTH the parent and the actual setting-row template so a parent prop
// override cannot silently remove help while the slider remains editable.
const settingRender = compile(GalaxySettingRow.template, { onError: e => { throw e }, decodeEntities: v => v.replaceAll('&amp;', '&') })
const vnodeRows = vnode => Array.isArray(vnode) ? vnode.flatMap(vnodeRows)
  : vnode && typeof vnode === 'object' ? [vnode, ...vnodeRows(vnode.children)] : []
const manualContext = { ...FlmPage.data(), mode: 'local', view: 'tune', group: 'balance', unauthorized() {} }
for (const [name, fn] of Object.entries(FlmPage.methods)) manualContext[name] = fn.bind(manualContext)
for (const [name, get] of Object.entries(FlmPage.computed)) Object.defineProperty(manualContext, name, { get: () => get.call(manualContext) })
const manualHelp = 'Sets the extra planned steering effort in left curves, before tracking-error corrections. Higher adds more effort; lower adds less.'
for (const state of [{ parked: true }, { parked: false }, { parked: true }, { parked: true, error: 'Connection lost' }]) {
  const calls = []
  manualContext.manualFeed = { active: true, async previewValue(index, value) { calls.push([index, value]) } }
  manualContext.manualState = { status: 'ready', pending: null, error: state.error || '', data: { rows: [{
    coordinate: 'ff_gain_left', label: 'Steering gain', reason: state.parked ? manualHelp : manualHelp + ' Parked only.',
    available: state.parked, action: true, value: 0.5, defaultValue: 0, resetAvailable: state.parked,
    minimum: -25, maximum: 25, step: 0.25, unit: '%',
  }] } }
  const parentRows = vnodeRows(render.call(manualContext, manualContext, [])).filter(node => node.props?.row?.coordinate === 'ff_gain_left')
  assert.equal(parentRows.length, 1)
  const props = parentRows[0].props
  assert.equal(props.row.reason, manualContext.manualState.data.rows[0].reason, 'Available manual sliders must retain their descriptions')
  const defaults = Object.fromEntries(Object.entries(GalaxySettingRow.props)
    .filter(([, prop]) => Object.hasOwn(prop, 'default')).map(([name, prop]) => [name, prop.default]))
  const settingContext = { ...defaults, ...GalaxySettingRow.data(), ...props,
    saveValue: props.saveValue || props['save-value'] }
  assert.equal(typeof settingContext.saveValue, 'function')
  for (const [name, fn] of Object.entries(GalaxySettingRow.methods)) settingContext[name] = fn.bind(settingContext)
  for (const [name, get] of Object.entries(GalaxySettingRow.computed)) Object.defineProperty(settingContext, name, { get: () => get.call(settingContext) })
  const rowTree = settingRender.call(settingContext, settingContext, [])
  assert(text(rowTree).includes(manualHelp), 'Help must render when parked, driving, and after returning to park')
  if (!state.parked) assert(text(rowTree).includes('Parked only.'), 'The driving restriction must remain visible')
  const input = vnodeRows(rowTree).find(node => node.type === 'input' && node.props?.type === 'range')
  const reset = vnodeRows(rowTree).find(node => node.type === 'button' && node.props?.['aria-label']?.startsWith('Reset '))
  assert(input && reset)
  const locked = !state.parked || !!state.error
  assert.equal(!!input.props.disabled, locked, 'Descriptions must not change the driving/error slider lockout')
  assert.equal(!!reset.props.disabled, locked, 'Descriptions must not change the reset lockout')
  await settingContext.commit(1)
  assert.equal(calls.length, locked ? 0 : 1, 'Locked slider events must not reach settings preview/save')
  if (locked) {
    assert.equal(await manualContext.saveManual(0, 1), false, 'The parent must also reject direct locked saves')
    assert.equal(calls.length, 0)
  } else assert.deepEqual(calls, [[0, 1]])
}
console.log('Actual FLM and setting-row Vue renders preserve parked/driving descriptions and reject locked saves')


// A retry remains provisional; neither its saved state nor its text asserts application.
ctx.now = 1000
ctx.report.observedMonoTime = ctx.now * 1_000_000
ctx.report.current.details['Saved route configuration state'] = 'ready_for_next_drive'
for (const reason of ['trial_started_waiting_for_comparable_drive', 'trial_measurement_inconclusive']) {
  ctx.report.current.reason = reason
  ctx.report.current.details['Analysis decision'] = reason
  const state = tunerStatus(ctx.report, false, false, '', true)
  assert.equal(state.key, 'next-drive')
  assert(state.description.includes('trial'))
  assert.notEqual(state.description, tunerStatus({ ...ctx.report, current: { ...ctx.report.current,
    details: { ...ctx.report.current.details, 'Analysis decision': 'bounded_trial_configured_for_next_drive' } } }, false, false, '', true).description)
  assert.equal(ctx.currentApplied, false, 'A pending retry cannot fabricate applied or accepted steering')
}
console.log('Inconclusive next-drive trials remain visibly provisional without fabricated application')

// Complete, read-only dial advice is independent of automatic-tune approval.
const advisoryNames = [...SURFACE_NAMES, ...FRICTION_NAMES]
const advisoryReport = { version: 1, vehicle: 'DECLARED_CAR', history: [], current: { fresh: true, ageSeconds: 0,
  enabled: null, status: 'collecting', reason: 'no_new_native_feasible_training_step', progress: { samples: 100, blocks: 6, requiredBlocks: 6 },
  fit: null, application: { applied: false, reason: 'not_applied' }, details: {
    'Completed drive analyzed': 'declared-last-drive', 'Recorded steering observations': 100,
    'Analysis decision': 'no_new_native_feasible_training_step', 'Recorded route suggestions': [],
    'Recorded coordinate recommendations': advisoryNames.map(name => [name, 0, .005, 6, 'training_candidate_not_independently_confirmed']),
  } } }
assert(validTrainingResults(advisoryReport))
assert.equal(offroadView(advisoryReport).suggestions.length, 35, 'All thirty dials and five curve points survive the public wire')
assert.equal(surfaceRows(advisoryReport).filter(row => row.recommendations.length).length, 30)
assert(offroadCoordinateEvidence(advisoryReport, 'ff_gain_left', false, '%').includes('suggested +0.5% (from 0%; change +0.5%)'))
assert.equal(advisoryReport.current.application.applied, false)
manualContext.report = advisoryReport
manualContext.now = 0
manualContext.receivedAt = 0
manualContext.manualState = { status: 'ready', pending: null, error: '', data: { rows: [{
  coordinate: 'ff_gain_left', label: 'Steering gain', reason: manualHelp, available: true, action: true,
  value: 0, defaultValue: 0, resetAvailable: true, minimum: -25, maximum: 25, step: .25, unit: '%',
}] } }
const advisoryTree = render.call(manualContext, manualContext, [])
assert(text(advisoryTree).includes('suggested +0.5%'), 'The suggested slider-scale value must appear beside the actual manual dial')
assert(text(advisoryTree).includes('not saved'))
const untouchedManual = vnodeRows(advisoryTree).find(node => node.props?.row?.coordinate === 'ff_gain_left')
assert.equal(untouchedManual.props.row.value, 0, 'Showing advice cannot move a manual slider or enable an override')
const singleAdvisory = structuredClone(advisoryReport)
singleAdvisory.current.details['Recorded coordinate recommendations'] = singleAdvisory.current.details['Recorded coordinate recommendations'].slice(0, 1)
assert(offroadCoordinateEvidence(singleAdvisory, 'unwind_taper_right').includes('No recommendation from this analyzed drive'))
const symptomAdvice = structuredClone(singleAdvisory)
symptomAdvice.current.details['Recorded coordinate recommendations'] = [
  ['unwind_taper_right', 0, .006, 5, 'observed_unwind_too_slow_right'],
]
manualContext.report = symptomAdvice
assert(text(render.call(manualContext, manualContext, [])).includes('While easing out of right curves, steering stayed stronger than requested.'),
  'Show the observed behavior on the finding itself, not only its technical details')
manualContext.report = advisoryReport

const conflictingAdvisory = structuredClone(singleAdvisory)
conflictingAdvisory.current.details['Recorded coordinate recommendations'][0][2] = null
conflictingAdvisory.current.details['Recorded coordinate recommendations'][0][4] = 'conflicting_recorded_candidates'
assert(validTrainingResults(conflictingAdvisory))
assert(recommendationText(offroadView(conflictingAdvisory).suggestions[0]).includes('no single suggested setting'))
for (const row of [ ['unknown', 0, .005, 6, 'unconfirmed'], ['ff_gain_left', 0, NaN, 6, 'unconfirmed'],
  ['ff_gain_left', 0, .02, 6, 'unconfirmed'], ['ff_gain_left', true, .005, 6, 'unconfirmed'],
  ['ff_gain_left', 0, .005, -1, 'unconfirmed'], ['ff_gain_left', 0, null, 6, 'unconfirmed'] ]) {
  const invalid = structuredClone(singleAdvisory)
  invalid.current.details['Recorded coordinate recommendations'] = [row]
  assert(!validTrainingResults(invalid), 'Invalid advice cannot pass the public report boundary')
  assert.equal(offroadView(invalid), null)
}
const duplicateAdvisory = structuredClone(singleAdvisory)
duplicateAdvisory.current.details['Recorded coordinate recommendations'].push(...duplicateAdvisory.current.details['Recorded coordinate recommendations'])
assert(!validTrainingResults(duplicateAdvisory))
const arbitraryList = structuredClone(advisoryReport)
arbitraryList.current.details['Unknown detail'] = Array(17).fill(1)
assert(!validTrainingResults(arbitraryList), 'The dedicated recommendation wire must not loosen unrelated telemetry bounds')
const historicalAdvisory = structuredClone(singleAdvisory)
historicalAdvisory.current.status = 'fitting'
historicalAdvisory.current.reason = 'analyzing_last_completed_drive'
historicalAdvisory.current.details['Earlier analysis shown while updating'] = true
assert(tunerStatus(historicalAdvisory, false, false, '', true).description.includes('Earlier completed-drive findings'))
assert(offroadCoordinateEvidence(historicalAdvisory, 'ff_gain_left').startsWith('Earlier analyzed drive'))
historicalAdvisory.preference = { version: 1, valid: true, enabled: true, parked: true, vehicle: 'OTHER_CAR' }
assert.equal(offroadView(historicalAdvisory), null, 'Earlier advice must not be relabeled for another reported vehicle')
console.log('All thirty dial targets, five curve points, honest conflicts and retained findings render without automatic approval or manual writes')

for (const name of SURFACE_NAMES) assert(recommendationText({ parameter: name, baseline: 0, target: .005, delta: .005 }).includes('+0.5%'), 'Advice must use the same percent scale as TorqueFeature.manual_rows for every dial')
const nowSavedAdvisory = structuredClone(advisoryReport)
nowSavedAdvisory.current.details['Saved route configuration state'] = 'ready_for_next_drive'
nowSavedAdvisory.current.details['Full surface coordinates'] = coordinates(.005)
assert.equal(offroadView(nowSavedAdvisory).suggestions.filter(row => row.parameter === 'ff_gain_left').length, 0)
assert(offroadCoordinateEvidence(nowSavedAdvisory, 'ff_gain_left', false, '%').includes('Auto Learn saved: +0.5% for the next drive'))
assert(!offroadCoordinateEvidence(nowSavedAdvisory, 'ff_gain_left', false, '%').includes('Previous-drive recommendation'))
assert.equal(nowSavedAdvisory.current.application.applied, false, 'A saved recommendation is still not current application')

await import('./test_plots.mjs')
await import('./test_settings_controls.mjs')
await import('./test_flm_review.mjs')
if (process.env.CI && process.platform === 'linux') await import('./test_flm_ux_browser.mjs')
