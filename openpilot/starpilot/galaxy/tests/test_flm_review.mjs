import assert from 'node:assert/strict'
import { compile } from '../web/vendor/vue/vue.esm-browser.js'
import { FlmPage } from '../web/js/flm.js'
import { FlmBackup, parseTune } from '../web/js/flm-backup.js'
import { GalaxySettingRow } from '../web/js/galaxy-setting-row.js'
import { PlotGraph } from '../web/js/plots.js'
import { reviewModel, dialPercent, ideaValue, ideaStatus, ideaExplanation, ideaDetails, coordinateStatus } from '../web/js/flm-review.js'
import { SURFACE_NAMES, FRICTION_NAMES, offroadView, validTrainingResults, validSavedHistory } from '../web/js/flm-training-results.js'

const idea = (parameter, target = .005) => ({ parameter, baseline: 0, target, delta: target, windows: 14,
  reason: 'recorded_candidate_requires_automatic_checks' })
const route = { suggestions: [idea('highway_center_taper_max'), ...FRICTION_NAMES.slice(1).map(name => idea(name))],
  savedValues: null, saved: false, configurationState: null }
const review = reviewModel(route)
assert.match(ideaExplanation({ ...idea('turn_in_boost_right'), earlierDrive: true }), /earlier drive with the same setup/)
assert.match(ideaExplanation({ ...idea('turn_in_boost_right'), earlierDrive: true, reason: 'partial_drive_provisional_only' }),
  /earlier drive.*incomplete recording.*will not save it automatically/)
assert.equal(review.count, 5)
assert.equal(review.manual.length, 1)
assert.equal(review.curve.length, 4)
assert.equal(review.findings.length, 5)
assert.equal(review.manualGroups.length, 1)
assert.equal(new Set(review.findings.map(row => row.destination)).size, 2)
const earlierGroup = reviewModel({ ...route, suggestions: [{ parameter: 'older_grouped_name', delta: .005, windows: 14 }] })
assert.equal(earlierGroup.manual.length, 0)
assert.equal(earlierGroup.other.length, 1)
assert.equal(earlierGroup.findings[0].destination, 'tuner-earlier-findings')
assert.deepEqual(review.areas, ['Straight & center', 'Small corrections by speed'])
assert.equal(review.tone, 'review')
assert(!/applied|improved|saved for/.test(review.title))
assert.equal(dialPercent(.005), '0.5%')
assert.equal(dialPercent(0), '0%')
assert.equal(dialPercent(-.005), '-0.5%')
assert.equal(ideaValue(idea('highway_center_taper_max')), '0% → 0.5%')
assert.equal(ideaValue({ delta: .005 }), 'Change +0.5%')
assert.match(ideaValue({ target: null }), /No single/)
assert.equal(ideaStatus({ target: null }), 'Conflicting findings')
assert.match(ideaExplanation({ target: null }), /did not support the same change/)
assert.match(ideaExplanation(route.suggestions[1]), /more evidence/)
assert.equal(reviewModel({ ...route, suggestions: [] }).count, 0)
assert.equal(reviewModel(null).count, 0)
const failedReview = FlmPage.computed.review.call({ offroad: route, current: { status: 'unavailable', reason: 'Assessment cache full' } })
assert.equal(failedReview.title, 'Drive review could not finish')
assert.match(failedReview.description, /latest analysis could not be saved/)
assert.doesNotMatch(failedReview.description, /No new change was saved/)
assert.match(FlmPage.template, /Latest review incomplete/)
assert.match(FlmPage.template, /v-if="!bluetooth"[^>]*href="\.\/api\/flm\/logs\/download"/)
assert.equal(reviewModel({ ...route, configurationState: 'ready_for_next_drive' }).tone, 'saved')
assert.equal(reviewModel({ ...route, configurationState: 'rolled_back' }).title, 'Your previous tune was restored')
assert.equal(reviewModel({ ...route, saved: true }).title, 'A configuration change was reported')
const historical = reviewModel({ ...route, configurationState: 'ready_for_next_drive' }, true)
assert.equal(historical.tone, 'neutral')
assert.equal(historical.title, 'Previously saved tune')
assert.match(historical.description, /earlier report/)
assert.doesNotMatch(historical.description, /Refresh|Reconnect/)
assert.match(historical.next, /latest saved state/)
const updating = reviewModel(route, true, true)
assert.equal(updating.description, review.description, 'Background progress must not alternate the drive explanation')
assert.doesNotMatch(updating.description, /Refresh|Reconnect/)
assert.equal(updating.next, review.next)
for (const state of ['ready_for_next_drive', 'accepted', 'accepted_after_drive', 'retained_after_drive', 'rolled_back']) {
  const savedRoute = { ...route, configurationState: state, savedValues: { highway_center_taper_max: .005 } }
  const completed = reviewModel(savedRoute)
  const inProgress = reviewModel(savedRoute, true, true)
  for (const key of ['title', 'description', 'tone', 'next', 'saved']) assert.deepEqual(inProgress[key], completed[key])
  assert.equal(inProgress.historical, true, 'The retained report remains explicitly historical')
}
assert.deepEqual(ideaDetails(idea('highway_center_taper_max')), [{ label: 'Starting value', value: '0%' }, { label: 'Suggested value', value: '0.5%' }])
assert.deepEqual(ideaDetails({ delta: .005 }), [{ label: 'Suggested change', value: '+0.5%' }])
assert.equal(ideaDetails({ baseline: 0, target: null })[1].value, 'No single suggested value')
assert.doesNotMatch(FlmPage.template, /gx-tuner__idea-value/)

for (const component of [FlmPage, GalaxySettingRow, PlotGraph, FlmBackup])
  compile(component.template, { onError: error => { throw error }, decodeEntities: value => value.replaceAll('&amp;', '&') })
assert.equal(PlotGraph.props.endLabel.default, 'Latest', 'Existing live plots retain their labels')
assert.match(FlmPage.template, /end-label=.*End/)
assert.match(FlmPage.template, /not a full-drive score/)
assert.match(FlmPage.template, /Auto Learn only/)
assert.match(FlmPage.template, /These suggestions are not saved/)
assert.doesNotMatch(FlmPage.template, /@click=".*(?:applyIdea|saveSuggestion)/)

const context = { ...FlmPage.data(), report: null, mode: 'local', unauthorized() {} }
for (const [name, fn] of Object.entries(FlmPage.methods)) context[name] = fn.bind(context)
for (const [name, fn] of Object.entries(FlmPage.computed)) Object.defineProperty(context, name, { get: () => fn.call(context) })
const renderEmpty = compile(FlmPage.template, { decodeEntities: value => value.replaceAll('&amp;', '&') })
const textOf = node => Array.isArray(node) ? node.map(textOf).join(' ') : node && typeof node === 'object' ? textOf(node.children) : typeof node === 'string' ? node : ''
const emptyText = textOf(renderEmpty.call(context, context, []))
assert(emptyText.includes('Saved tune not reported'))
assert(!emptyText.includes('Earlier report'), 'No-report state must not pretend an earlier report exists')
const tuneFile = { format: 'starpilot-flm-tune', version: 1, vehicle: 'DECLARED_UI_CAR', manual: [], learned: {} }
assert.deepEqual(parseTune(JSON.stringify(tuneFile)), tuneFile)
for (const invalid of ['{', 'x'.repeat(65537), JSON.stringify({ ...tuneFile, version: 3 }), JSON.stringify({ ...tuneFile, learned: [] })])
  assert.throws(() => parseTune(invalid))
const transfer = { ...FlmBackup.data(), parked: true, bluetooth: false, unauthorized() {}, $emit() {} }
for (const [name, fn] of Object.entries(FlmBackup.methods)) transfer[name] = fn.bind(transfer)
const operations = []
transfer.request = async (path, body) => {
  operations.push({ path, body })
  return path.endsWith('/preview') ? { intent: 'declared-import', question: 'Restore this tune?', count: 0, changes: [] } :
    { complete: true, restored: 0, unchanged: 1, skipped: [] }
}
await transfer.chooseFile({ target: { files: [{ size: 100, text: async () => JSON.stringify(tuneFile) }], value: 'file' } })
assert.equal(operations.length, 0, 'Choosing a file must not upload or restore it')
await transfer.review()
assert.equal(operations[0].path, './api/settings/flm-backup/preview')
assert.equal(operations.length, 1, 'Preview must not send a confirmation')
await transfer.restore()
assert.equal(operations[1].path, './api/settings/flm-backup/confirm')
assert.equal(operations[1].body.confirmed, true)
assert.match(transfer.notice, /already match/)
transfer.pending = { intent: 'blocked' };transfer.parked = false
await transfer.restore()
assert.equal(operations.length, 2, 'Driving blocks restore before any request')
assert.equal(context.navigationGroups.length, 7)
const savedHistory = { status: 'readable', historicalOnly: true, profiles: [{ completedAt: 1794777785171,
  state: 'ready_for_next_drive', values: [['friction_threshold_mid', .0036], ['friction_threshold_fast', .0048], ['friction_threshold_highway', .0048]] }] }
assert(validSavedHistory(savedHistory))
assert(!validSavedHistory({ ...savedHistory, historicalOnly: false }))
assert(!validSavedHistory({ ...savedHistory, status: 'unavailable' }))
for (const pair of [['invented_dial', .0048], ['friction_threshold_mid', Infinity], ['friction_threshold_mid', .251]])
  assert(!validSavedHistory({ ...savedHistory, profiles: [{ ...savedHistory.profiles[0], values: [pair] }] }))
context.view = 'tune'
context.report = { version: 1, history: [], current: null, savedHistory }
assert(validTrainingResults(context.report))
const earlierText = textOf(renderEmpty.call(context, context, []))
assert(earlierText.includes('Earlier saved setup') && earlierText.includes('Mid-speed friction threshold'))
assert(!earlierText.includes('No saved adjustments reported'))
assert.equal(context.currentApplied, false, 'History cannot acknowledge current application')
assert.equal(context.review.saved.length, 0, 'An earlier setup does not become the current tune')
context.view = 'overview';context.report = null
assert.deepEqual(context.navigationGroups.slice(0, 6).flatMap(group => group.names).sort(), [...SURFACE_NAMES].sort())
assert.deepEqual(context.navigationGroups[6].names, FRICTION_NAMES)
context.group = 'friction'
assert.equal(context.selectedGroup.id, 'friction')
assert.equal(context.manualRows.length, 0)
assert.equal(context.frictionRows.length, 5)
// A write failure is uncertain until NEW saved-setting data resolves the request.
const oldPage = { rows: [{ coordinate: 'highway_center_taper_max', value: '0.0000', unit: '%' }] }
context.manualState.data = oldPage
context.beginManualWrite([['highway_center_taper_max', .5, '%']])
context.publishManual({ status: 'ready', data: oldPage, error: 'Connection refused' })
assert.equal(context.manualWrite.status, 'unconfirmed')
assert.match(context.manualErrorText, /Could not confirm this change was saved/)
context.publishManual({ status: 'ready', data: oldPage, error: '' })
assert.equal(context.manualWrite.status, 'unconfirmed', 'Cached data must not resolve an uncertain save')
context.publishManual({ status: 'ready', data: { rows: [{ coordinate: 'highway_center_taper_max', value: null, unit: '%' }] }, error: '' })
assert.equal(context.manualWrite.status, 'unconfirmed', 'Missing values must not be treated as zero')
context.publishManual({ status: 'ready', data: structuredClone(oldPage), error: '' })
assert.equal(context.manualWrite.status, 'checked')
assert.match(context.manualReadbackText, /saved value is 0%/)
assert.match(context.manualReadbackText, /requested 0.5% is not the current saved value/)
context.publishManual({ status: 'ready', data: context.manualState.data, error: 'Later read failed' })
assert.equal(context.manualErrorText, 'Later read failed', 'A later read error must not invent a new failed write')
context.beginManualWrite([['highway_center_taper_max', .5, '%']])
context.publishManual({ status: 'ready', data: { rows: [{ coordinate: 'highway_center_taper_max', value: '0.5000', unit: '%' }] }, error: '' })
assert.match(context.manualReadbackText, /saved value confirmed at 0.5%/)
context.publishManual({ status: 'ready', data: structuredClone(oldPage), error: '' })
assert.match(context.manualReadbackText, /requested 0.5% is not the current saved value/, 'A later fresh read must update the notice, not retain a stale confirmation')
context.manualState = FlmPage.data().manualState;context.manualWrite = null


// Same validated wire feeds the summary, group badges and detail destinations.
const data = { version: 1, history: [], current: { fresh: true, ageSeconds: 0, enabled: null,
  status: 'collecting', reason: 'no_new_native_feasible_training_step', progress: { samples: 71357, blocks: 1, requiredBlocks: 1 },
  fit: null, application: { applied: false, reason: 'saved_configuration_is_not_current_application' },
  details: { 'Completed drive analyzed': 'declared-drive', 'Recorded steering observations': 71357,
    'Analysis decision': 'no_new_native_feasible_training_step', 'Automatic configuration changed': false,
    'Recorded coordinate recommendations': [...SURFACE_NAMES, ...FRICTION_NAMES].map(name => [name, 0, .005, 14, 'recorded_candidate_requires_automatic_checks']) } } }
assert(validTrainingResults(data))
context.report = data
assert.equal(context.review.count, 35)
assert.equal(context.review.manual.length, 30)
assert.equal(context.review.curve.length, 5)
for (const group of context.navigationGroups) assert(context.groupHasIdeas(group))
assert.equal(context.navigationGroups.reduce((sum, group) => sum + offroadView(data).suggestions.filter(row => group.names.includes(row.parameter)).length, 0), 35)
assert.equal(context.currentApplied, false)
const mixed = structuredClone(data)
mixed.current.details['Recorded coordinate recommendations'] = [
  ['turn_in_boost_left', 0, .005, 6, 'independent_support_for_frozen_training_profile'],
  ['friction_threshold_low', 0, .002, 6, 'training_candidate_not_independently_confirmed'],
  ['unwind_taper_right', 0, null, 6, 'conflicting_recorded_candidates'],
  ['ff_gain_right', 0, .005, 6, 'controller_checks_did_not_support_change'],
]
assert(validTrainingResults(mixed))
const mixedReview = reviewModel(offroadView(mixed))
assert.equal(mixedReview.count, 2, 'Supported and provisional neighbors survive a rejected bundled profile')
assert.equal(mixedReview.manual.length, 1)
assert.equal(mixedReview.curve.length, 1)
assert.equal(ideaStatus(mixedReview.manual[0]), 'Repeated finding')
assert.equal(ideaStatus(mixedReview.curve[0]), 'Possible change')
assert.equal(offroadView(mixed).saved, false)
const rendered = compile(FlmPage.template, { decodeEntities: value => value.replaceAll('&amp;', '&') })
const walk = node => Array.isArray(node) ? node.flatMap(walk) : node && typeof node === 'object' ? [node, ...walk(node.children)] : []
context.view = 'tune'
const original = structuredClone(data)
const tree = walk(rendered.call(context, context, []))
assert.equal(tree.filter(node => node.props?.['data-coordinate'] && SURFACE_NAMES.includes(node.props['data-coordinate'])).length, 30)
assert.deepEqual(data, original, 'Rendering never changes the report or saved state')
console.log('Drive review keeps all 35 findings reachable, seven destinations, readable values, historical truth and unapproved status')

const confirmedRow = { coordinate: 'unwind_taper_left', value: '0.5000', acceptedFindingValue: .005,
  coordinateLocked: false, learningEligible: true, available: true, unit: '%', minimum: 0, maximum: 25, step: .25 }
assert.deepEqual(coordinateStatus(confirmedRow), { label: 'Change confirmed', tone: 'saved', learning: 'Eligible for Auto Learn' })
assert.equal(coordinateStatus({ ...confirmedRow, value: '0', acceptedFindingValue: 0 }).label, 'Change confirmed',
  'An accepted reset is not a manual override or an unknown value')
assert.equal(coordinateStatus({ ...confirmedRow, available: false }).label, 'Change confirmed',
  'Driving-time editing lockout does not establish a learning lock')
assert.equal(coordinateStatus({ ...confirmedRow, coordinate: 'friction_threshold_mid' }).label, 'Change confirmed')
assert.equal(coordinateStatus({ ...confirmedRow, coordinateLocked: true }).label, 'Manual override', 'An actual lock takes priority')
assert.equal(coordinateStatus({ ...confirmedRow, learningEligible: false }).label, 'Saved change')
assert.doesNotMatch(coordinateStatus({ ...confirmedRow, learningEligible: false }).learning, /Eligible for/)
for (const row of [{ coordinate: 'unwind_taper_left', value: '0.5000' },
  { ...confirmedRow, acceptedFindingValue: undefined }, { ...confirmedRow, acceptedFindingValue: Infinity }])
  assert.equal(coordinateStatus(row), null, 'A number or display state cannot invent acceptance or a lock')
const beforeOwnership = { view: context.view, group: context.group, report: context.report,
  manualWrite: context.manualWrite, manualState: context.manualState }
context.view = 'tune';context.group = 'balance';context.report = null;context.manualWrite = null
for (const [row, expected, forbidden] of [[confirmedRow, 'Change confirmed', 'Manual override'],
  [{ ...confirmedRow, available: false }, 'Change confirmed', 'Manual override'],
  [{ ...confirmedRow, value: '0', acceptedFindingValue: 0 }, 'Change confirmed', 'Manual override'],
  [{ ...confirmedRow, acceptedFindingValue: undefined, coordinateLocked: true, learningEligible: false }, 'Manual override', 'Change confirmed'],
  [{ ...confirmedRow, acceptedFindingValue: undefined, coordinateLocked: false }, 'Manual setting', 'Manual override'],
  [{ ...confirmedRow, learningEligible: false }, 'Saved change', 'Change confirmed']]) {
  context.manualState = { status: 'ready', data: { rows: [row] }, error: '' }
  const before = JSON.stringify(context.manualState)
  const dial = walk(rendered.call(context, context, [])).find(node => node.props?.id === 'tuner-dial-unwind_taper_left')
  assert(dial, 'The actual tuning template must render the setting')
  assert(textOf(dial).includes(expected), expected)
  assert(!textOf(dial).includes(forbidden), forbidden)
  assert.equal(JSON.stringify(context.manualState), before, 'Rendering labels never changes the saved value, lock or reset state')
}
const savedReport = structuredClone(data)
Object.assign(savedReport.current.details, {
  'Saved route configuration state': 'ready_for_next_drive',
  'Full surface coordinates': SURFACE_NAMES.map(name => [name, { center_taper_max: .005, turn_in_boost_right: .0025 }[name] ?? 0]),
})
context.report = savedReport
context.manualState = { status: 'ready', data: { parked: false, rows: [
  { ...confirmedRow, coordinate: 'center_taper_max' }, // Existing auto value copied into the manual document.
  { ...confirmedRow, coordinate: 'turn_in_boost_right', value: '0.7500', acceptedFindingValue: undefined, coordinateLocked: true },
  { ...confirmedRow, coordinate: 'friction_threshold_mid', value: '0', acceptedFindingValue: 0, available: false },
  { ...confirmedRow, coordinate: 'unwind_taper_right', acceptedFindingValue: undefined },
] }, error: '' }
const savedCard = () => walk(rendered.call(context, context, [])).find(node => node.props?.['aria-labelledby'] === 'tuner-saved-title')
const summaryRows = () => Object.fromEntries(walk(savedCard()).filter(node => node.props?.['data-saved-coordinate'])
  .map(node => [node.props['data-saved-coordinate'], textOf(node)]))
assert.match(textOf(savedCard()), /Auto-learned settings.*Your changes/)
const savedRows = summaryRows()
assert.deepEqual(Object.keys(savedRows), ['center_taper_max', 'turn_in_boost_right', 'friction_threshold_mid'])
assert.match(savedRows.center_taper_max, /0.5%.*Default 0%/)
assert.doesNotMatch(savedRows.center_taper_max, /Change confirmed/)
assert.match(savedRows.turn_in_boost_right, /Manual override.*0.75%.*Default 0%/)
assert.doesNotMatch(savedRows.turn_in_boost_right, /0.25%/)
assert.match(savedRows.friction_threshold_mid, /Change confirmed.*Eligible for Auto Learn.*0%.*Default 0%/)
context.report = null
assert.equal(Object.keys(summaryRows()).length, 3, 'Saved manual settings do not require a completed-drive report')
assert.match(textOf(savedCard()), /Auto-learned settings.*No automatic save.*Your changes/)
Object.assign(context, beforeOwnership)
assert.doesNotMatch(FlmPage.template, /Manual override saved|Number\(row.value\) !== 0/)
console.log('Actual tune cards distinguish confirmed eligible changes from manual locks, defaults, context mismatch and read-only editing')


const contradictory = { ...route, suggestions: [route.suggestions[0],
  { ...route.suggestions[1], reason: 'heldout_profile_does_not_support_training_step' },
  { ...route.suggestions[2], target: null, reason: 'conflicting_recorded_candidates' },
  { ...route.suggestions[3], reason: 'controller_checks_did_not_support_change' }] }
assert.equal(reviewModel(contradictory).count, 1, 'Contradicted values do not inflate suggestions or category badges')
assert.equal(reviewModel(contradictory).curve.length, 0)
assert.match(ideaExplanation(route.suggestions[0]), /manually while parked/)
assert.doesNotMatch(ideaExplanation(route.suggestions[1]), /manually/, 'Auto Learn-only curve points cannot invent manual sliders')
assert.doesNotMatch(ideaStatus(route.suggestions[0]), /approved/i)
assert.doesNotMatch(ideaDetails(route.suggestions[0]).map(row => row.label).join(' '), /candidate/i)
context.receivedAt = context.now = 1_000
context.report.current.ageSeconds = 1.2
const beforeExpiry = JSON.stringify(context.review)
context.now = 2_100
assert.equal(context.reportFresh, false)
assert.equal(JSON.stringify(context.review), beforeExpiry, 'Live-clock expiry must not rewrite completed-drive findings')
context.report.current.ageSeconds = 0
context.now = context.receivedAt
assert.equal(JSON.stringify(context.review), beforeExpiry)
assert.equal(context.currentApplied, false)

const partialReview = reviewModel({ ...route, partial: true, partialSegments: [9,10],
  configurationState: 'ready_for_next_drive', savedValues: { friction_threshold_low: .002 },
  suggestions: [ { ...idea('highway_center_taper_max'), reason: 'partial_drive_provisional_only' } ] })
assert.equal(partialReview.count,1)
assert.match(partialReview.title,/Part of/)
assert.match(partialReview.description,/9 of 10/)
assert.match(partialReview.description,/stayed the same/)
assert.match(partialReview.next,/will not change your tune automatically/)
const partialEmpty = reviewModel({ ...route, partial: true, partialSegments: [8,10], suggestions: [] })
assert.equal(partialEmpty.count,0)
assert.match(partialEmpty.next,/No supported suggestion/)
assert.equal(ideaStatus({reason:'partial_drive_provisional_only'}),'Partial-drive suggestion')
assert.match(ideaExplanation({ ...idea('highway_center_taper_max'), reason:'partial_drive_provisional_only' }),/will not save it automatically/)
