// Real Chromium, declared UI fixtures only. No device or production settings access.
import assert from 'node:assert/strict'
import { createServer } from 'node:http'
import { readFile, writeFile, mkdtemp, mkdir, rm } from 'node:fs/promises'
import { existsSync } from 'node:fs'
import { spawn } from 'node:child_process'
import { once } from 'node:events'
import { tmpdir } from 'node:os'
import { resolve, dirname, join, extname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { SURFACE_NAMES, FRICTION_NAMES, validTrainingResults } from '../web/js/flm-training-results.js'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../web')
const output = resolve('ci-results/flm-offroad.json')
const screenshots = [], checks = [], requests = [], errors = [], contrastResults = [], fullPageReflow = []
const targetIndex = SURFACE_NAMES.indexOf('highway_center_taper_max')
const running = child => child.exitCode === null && child.signalCode === null
assert.equal(running({ exitCode: null, signalCode: 'SIGTERM' }), false, 'A signal-reaped child must not be awaited for a second exit')
let scenario = 'ideas', parked = true, saveError = false, reportError = false, pending = null
const manual = Object.fromEntries(SURFACE_NAMES.map(name => [name, 0]))
const hints = [['highway_center_taper_max', .005], ['friction_threshold_low', .002],
  ['friction_threshold_mid', .004], ['friction_threshold_fast', .005], ['friction_threshold_highway', .006]]
function report() {
  if (scenario === 'empty') return { version: 1, history: [], current: null }
  const saved = scenario === 'saved' || scenario === 'applied'
  const application = scenario === 'applied'
  const rows = SURFACE_NAMES.map(name => [name, saved && name === 'highway_center_taper_max' ? .005 : 0])
  const start = 1_000_000_000, points = Array.from({ length: 24 }, (_, i) =>
    [start + i * 80_000_000, .17 * Math.cos(i / 6), .17 * Math.cos((i - 2) / 6)])
  const value = { version: 1, history: [], vehicle: scenario === 'wrong-car' ? 'OTHER_DECLARED_CAR' : 'DECLARED_UI_CAR', preference: { version: 1, valid: true, enabled: true, parked, vehicle: 'DECLARED_UI_CAR' },
    current: { fresh: true, ageSeconds: 0, enabled: application, status: 'collecting', reason: 'no_new_native_feasible_training_step',
      progress: { samples: 71357, blocks: 197, requiredBlocks: 6 }, fit: null,
      application: { applied: application, reason: application ? 'current_controller_application' : 'saved_configuration_is_not_current_application' },
      details: { 'Completed drive analyzed': 'declared-ui-drive', 'Recorded steering observations': 71357,
        'Analysis decision': scenario === 'saved' ? 'bounded_trial_configured_for_next_drive' : 'no_new_native_feasible_training_step',
        'Automatic configuration changed': saved, 'Saved route adjustment count': saved ? 1 : 0,
        'Recorded coordinate recommendations': scenario === 'none' ? [] : hints.map(([name, target], index) => [name, 0,
          scenario === 'conflict' && index === 0 ? null : target, index === 0 ? 148 : 14,
          scenario === 'conflict' && index === 0 ? 'conflicting_recorded_candidates' : index ? 'heldout_profile_does_not_support_training_step' : 'recorded_candidate_requires_automatic_checks']),
        'Recorded steering response': { version: 1, domain: 'lateral_acceleration', samples: 46231, windows: 234, error_rms: .062,
          route: 'declared-ui-drive', segment: 1, source_start_ns: start, source_end_ns: points.at(-1)[0],
          sampling: 'input_ordered_original_endpoints_no_interpolation', points } } } }
  if (saved) Object.assign(value.current.details, { 'Saved route configuration state': 'ready_for_next_drive',
    'Full surface coordinates': rows, 'Saved friction-curve adjustments': FRICTION_NAMES.map(name => [name, 0]) })
  if (application) value.current.details['Actual full surface coordinates'] = rows
  if (scenario === 'stale') { value.current.fresh = false; value.current.ageSeconds = 20 }
  if (scenario === 'retained') {
    value.current.status = 'fitting'; value.current.reason = 'analyzing_last_completed_drive'
    value.current.details['Earlier analysis shown while updating'] = true
  }
  if (scenario === 'rollback') value.current.details['Saved route configuration state'] = 'rolled_back'
  if (scenario === 'incomplete') delete value.current.details['Recorded steering observations']
  if (scenario === 'unknown-change') value.current.details['Automatic configuration changed'] = true
  assert(validTrainingResults(value), 'Browser fixtures must pass the real report validator')
  return value
}
function settings() {
  return { page: 'tuning', title: 'Manual tuning', view: 'declared-ui-view', parked,
    rows: [...SURFACE_NAMES.map(coordinate => ({ coordinate, label: coordinate,
      // This highlighted dial uses the published help; other fixture rows stay generic.
      reason: coordinate === 'highway_center_taper_max' ? 'Adds straight-ahead softening at highway speeds. Higher softens small corrections more; lower keeps more steering response. This changes steering feel, not your target position in the lane. Parked only. Manual overrides pause Auto Learn.' :
        'Shapes small steering corrections at this stage. Higher and lower values change the controller response. Parked only. Manual overrides pause Auto Learn.',
      value: manual[coordinate].toFixed(4), minimum: -25, maximum: 25, step: .25, unit: '%', defaultValue: 0,
      resetAvailable: parked, available: parked, action: true })),
    { label: 'Reset all manual overrides', reason: 'Remove the thirty manual overrides.', value: 'Reset', confirm: true, available: parked, action: true }] }
}
const stylesheetLinks = [...(await readFile(join(root, 'index.html'), 'utf8')).matchAll(/<link[^>]*rel="stylesheet"[^>]*>/g)].map(row => row[0].replaceAll('href="./', 'href="/')).join('\n')
const shell = `<!doctype html><html lang="en" data-theme="dark"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
${stylesheetLinks}
<style>body{margin:0}#fixture{width:100%}.fixture-label{font:11px sans-serif;color:var(--text-muted)}</style></head>
<body><div id="galaxy-bg" aria-hidden="true"></div><div id="fixture"></div>
<script type="module">import {createApp} from '/vendor/vue/vue.esm-browser.js';import {FlmPage} from '/js/flm.js';
import {ToggleSearch} from '/js/toggle-search.js';import {DeviceState} from '/js/device-state.js';import {BottomNav} from '/js/bottom-nav.js';
window.uiErrors=[];window.addEventListener('error',e=>window.uiErrors.push(e.message));
const app=createApp({components:{FlmPage,ToggleSearch,DeviceState,BottomNav},methods:{ignore(){}},template:
'<div class="gx-app"><header class="gx-appbar"><button class="gx-icon-btn gx-appbar__back gx-back-btn" aria-label="Back"><i class="bi bi-arrow-left"></i></button><div class="gx-appbar__pill"><button class="gx-appbar__home" aria-label="Galaxy Home"><span class="gx-brand" aria-hidden="true"></span><span class="gx-appbar__title">Galaxy</span></button><ToggleSearch :enabled="true" :settings-enabled="false" :tools="[]" :unauthorized="ignore" :open-page="ignore"/><div class="gx-appbar__right"><DeviceState :unauthorized="ignore" connection="Declared UI fixture"/></div></div><button class="gx-icon-btn gx-theme-toggle" aria-label="Switch theme"><i class="bi bi-sun-fill"></i></button><button class="gx-icon-btn gx-appbar__menu" aria-label="Menu"><i class="bi bi-list"></i></button></header><main class="gx-content"><p class="fixture-label">Declared UI fixtures · no car connected</p><FlmPage ref="flm" mode="local" :unauthorized="ignore"/></main><BottomNav path="/tuning/flm" @navigate="ignore"/></div>'});
app.config.errorHandler=e=>window.uiErrors.push(e.stack||e.message);const shell=app.mount('#fixture');window.flm=shell.$refs.flm;window.fixtureReady=true;</script></body></html>`
const mime = { '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html', '.svg': 'image/svg+xml', '.woff2': 'font/woff2' }
const server = createServer(async (req, res) => {
  try {
    const pathname = new URL(req.url, 'http://127.0.0.1').pathname
    const json = (value, status = 200) => { res.writeHead(status, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(value)) }
    if (pathname === '/favicon.ico') { res.writeHead(204);return res.end() }
    if (pathname === '/api/plots/live') return json({ schemaVersion: 1, sessionId: '0000000000000001', state: 'unavailable', sampleIndex: 0, sampleAgeSeconds: null, error: 'Declared parked fixture', bootStabilizing: false, values: null })
    if (pathname === '/api/device/state') return json({ state: parked ? 'parked' : 'driving', maxAgeMs: 3000 })
    if (pathname === '/api/flm/training-results') return json(reportError ? { error: 'Declared connection error' } : report(), reportError ? 503 : 200)
    if (pathname === '/api/settings/pages/tuning') return json(settings())
    if (pathname.startsWith('/api/settings/')) {
      let raw = ''; for await (const chunk of req) raw += chunk
      const body = JSON.parse(raw); requests.push({ path: pathname, body })
      if (!parked || saveError) return json({ error: 'Declared save refusal' }, 503)
      if (pathname.endsWith('/preview') || pathname.endsWith('/reset-default')) {
        pending = { intent: 'declared-intent-' + requests.length, row: body.row, value: pathname.endsWith('/reset-default') ? 0 : body.value }
        return json({ ...pending, question: 'Remove the thirty manual overrides?', proposed: body.value === undefined ? 'Reset' : body.value })
      }
      if (pathname.endsWith('/confirm')) {
        assert.equal(body.intent, pending.intent)
        if (pending.row === SURFACE_NAMES.length) for (const name of SURFACE_NAMES) manual[name] = 0
        else manual[SURFACE_NAMES[pending.row]] = Number(pending.value)
        pending = null; return json({ saved: true, page: settings() })
      }
    }
    if (pathname === '/__fixture') { res.setHeader('Content-Type', 'text/html'); return res.end(shell) }
    const path = resolve(root, '.' + decodeURIComponent(pathname))
    if (!path.startsWith(root + '/')) { res.writeHead(403); return res.end() }
    res.setHeader('Content-Type', mime[extname(path)] || 'application/octet-stream');res.end(await readFile(path))
  } catch (error) { errors.push(error.stack);res.writeHead(500);res.end('Fixture error') }
})
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
const profile = await mkdtemp(join(tmpdir(), 'flm-browser-'))
const driverPath = process.env.CHROMEWEBDRIVER ? join(process.env.CHROMEWEBDRIVER, 'chromedriver') : 'chromedriver'
const driver = spawn(driverPath, ['--port=0', '--allowed-ips=127.0.0.1'], { stdio: ['ignore', 'pipe', 'pipe'] })
let driverLog = '', port, session, lastCommand = '', browserVersion = null
const browserStartedAt = Date.now()
driver.on('error', error => { driverLog += error.stack })
for (const stream of [driver.stdout, driver.stderr]) stream.on('data', data => { driverLog += data })
const deadline = Date.now() + 45_000
async function until(fn) {
  while (Date.now() < deadline) { const value = await fn();if (value) return value;await new Promise(resolve => setTimeout(resolve, 50)) }
  throw new Error('FLM browser check deadline exceeded\n' + driverLog.slice(-3000))
}
async function command(path, body = {}, method = 'POST', requestMs = 8000) {
  lastCommand = path
  const response = await fetch(`http://127.0.0.1:${port}${path}`, { method, ...(method === 'GET' || method === 'DELETE' ? {} : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }), signal: AbortSignal.timeout(requestMs) })
  const data = await response.json();if (data.value?.error) throw new Error(JSON.stringify(data.value));return data.value
}
const execute = (script, ...args) => command(`/session/${session}/execute/sync`, { script, args })
const click = async selector => {
  await execute(`const e=document.querySelector(arguments[0]);if(!e)throw new Error('Missing click target');
    e.scrollIntoView({block:'center',behavior:'instant'});
    const header=document.querySelector('.gx-appbar').getBoundingClientRect(),nav=document.querySelector('.gx-tuner__tabs').getBoundingClientRect(),footer=document.querySelector('.blur-nav'),fr=footer.getBoundingClientRect();
    const top=(e.closest('.gx-tuner__tabs')?header.bottom:Math.max(header.bottom,nav.top<innerHeight?nav.bottom:0))+8;
    const bottom=getComputedStyle(footer).position==='fixed'&&getComputedStyle(footer).display!=='none'&&fr.height>0?Math.min(innerHeight,fr.top):innerHeight;
    const r=e.getBoundingClientRect();window.scrollBy({top:r.top+r.height/2-(top+bottom)/2,behavior:'instant'});`,selector)
  const hitTest=await execute(`const e=document.querySelector(arguments[0]),r=e.getBoundingClientRect(),h=document.querySelector('.gx-appbar').getBoundingClientRect(),n=document.querySelector('.gx-tuner__tabs').getBoundingClientRect(),f=document.querySelector('.blur-nav'),fr=f.getBoundingClientRect(),hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);
    const bottom=getComputedStyle(f).position==='fixed'&&getComputedStyle(f).display!=='none'&&fr.height>0?Math.min(fr.top,innerHeight):innerHeight;return {clear:r.top>=Math.max(h.bottom,e.closest('.gx-tuner__tabs')?0:n.top<innerHeight?n.bottom:0)&&r.bottom<=bottom&&r.left>=0&&r.right<=innerWidth&&(hit===e||e.contains(hit)),target:{top:r.top,bottom:r.bottom,left:r.left,right:r.right},headerBottom:h.bottom,tabsBottom:n.bottom,footerDisplay:getComputedStyle(f).display,footerPosition:getComputedStyle(f).position,availableBottom:bottom,hit:hit?.tagName}`,selector)
  assert(hitTest.clear,selector+': user-scrollable target must be fully visible and unobstructed before a native click: '+JSON.stringify(hitTest))
  const node=await command(`/session/${session}/element`,{using:'css selector',value:selector})
  await command(`/session/${session}/element/${node['element-6066-11e4-a52e-4f735466cecf']}/click`)
}
async function clickVisible(selector) {
  const visible = await execute('const e=document.querySelector(arguments[0]);const r=e.getBoundingClientRect();const h=document.querySelector(".gx-appbar").getBoundingClientRect();const hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);return {clear:r.top>=h.bottom&&r.bottom<=innerHeight&&r.left>=0&&r.right<=innerWidth&&(hit===e||e.contains(hit)),top:r.top,header:h.bottom}', selector)
  assert(visible.clear, selector + ': must be visible below the real header and pass hit testing without forced scrolling')
  const node = await command(`/session/${session}/element`, { using: 'css selector', value: selector })
  await command(`/session/${session}/element/${node['element-6066-11e4-a52e-4f735466cecf']}/click`)
}
async function change(name, options = {}) {
  await until(() => execute('return !window.flm.request'))
  scenario = name;parked = options.parked ?? true;saveError = false;reportError = false
  await execute('window.flm.manualFeed?.stop();window.flm.view="overview";window.flm.syncManualControls();window.scrollTo(0,0);return window.flm.load()')
  await until(() => execute('return window.flm.report && !window.flm.request'))
}
async function viewport(width, height = 844, scale = 1) {
  await command(`/session/${session}/goog/cdp/execute`, { cmd: 'Emulation.setDeviceMetricsOverride', params: { width, height, deviceScaleFactor: scale, mobile: false } })
  assert.equal(await execute('return innerWidth'), width)
}
async function tabsClear(label) {
  const rows = await execute(`const header=document.querySelector('.gx-appbar').getBoundingClientRect();return [...document.querySelectorAll('.gx-tuner__tabs>button')].map(e=>{const r=e.getBoundingClientRect(),hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);return {text:e.textContent.trim(),clear:r.top>=header.bottom&&r.bottom<=innerHeight&&r.left>=0&&r.right<=innerWidth&&(hit===e||e.contains(hit))}})`)
  assert(rows.every(row => row.clear), label + ': initial/zoom tabs must be visible and unobstructed: ' + JSON.stringify(rows))
  checks.push(label)
}
function contrast(a, b) {
  const luminance = text => {
    const rgb = text.match(/[\d.]+/g).slice(0, 3).map(Number).map(value => value / 255).map(value => value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4)
    return .2126 * rgb[0] + .7152 * rgb[1] + .0722 * rgb[2]
  }
  const x=luminance(a),y=luminance(b);return (Math.max(x,y)+.05)/(Math.min(x,y)+.05)
}
async function graphContrast(theme) {
  const colors = await execute(`const p=document.querySelector('.gx-tuner__response'),a=p.querySelector('.gx-plot-desired'),b=p.querySelector('.gx-plot-actual');return {background:getComputedStyle(p).backgroundColor,a:getComputedStyle(a).stroke,b:getComputedStyle(b).stroke,dash:getComputedStyle(b).strokeDasharray,legendA:getComputedStyle(p.querySelector('.gx-tuner__requested'),'::before').borderTopStyle,legendB:getComputedStyle(p.querySelector('.gx-tuner__measured'),'::before').borderTopStyle}`)
  const requestedRatio=contrast(colors.a,colors.background),measuredRatio=contrast(colors.b,colors.background)
  contrastResults.push({theme,...colors,requestedRatio,measuredRatio})
  assert(requestedRatio>=3&&measuredRatio>=3, theme + ': both actual strokes need >=3:1 contrast')
  assert.notEqual(colors.dash,'none');assert.equal(colors.legendA,'solid');assert.equal(colors.legendB,'dashed')
  checks.push('contrast/'+theme)
}
async function layout(label) {
  const result = await execute(`return {overflow:document.documentElement.scrollWidth>innerWidth+1, errors:window.uiErrors,
    targets:[...document.querySelectorAll('.gx-tuner button,.gx-tuner summary')].filter(e=>e.getClientRects().length).map(e=>({text:e.textContent.trim(),height:e.getBoundingClientRect().height}))}`)
  assert.equal(result.overflow, false, label + ': no horizontal overflow')
  assert.deepEqual(result.errors, [], label + ': no Vue/browser errors')
  assert(result.targets.every(row => row.height >= 43), label + ': 44px interaction targets')
  checks.push(label)
}
async function show(selector) { await execute('document.querySelector(arguments[0]).scrollIntoView({block:"center",behavior:"instant"})', selector) }
async function capture(name) {
  const base64 = await command(`/session/${session}/screenshot`, {}, 'GET')
  screenshots.push({ name, png_base64: base64 })
}
let failure = null
try {
  await until(() => { port = Number(driverLog.match(/started successfully on port (\d+)/)?.[1]);return port })
  const created = await command('/session', { capabilities: { alwaysMatch: { browserName: 'chrome',
    'goog:chromeOptions': { args: ['--headless=new', '--no-sandbox', '--disable-dev-shm-usage', '--user-data-dir=' + profile] } } } }, 'POST', 20000)
  session = created.sessionId
  browserVersion = created.capabilities?.browserVersion || null
  await command(`/session/${session}/goog/cdp/execute`, { cmd: 'Emulation.setEmulatedMedia', params: { features: [{ name: 'prefers-reduced-motion', value: 'reduce' }] } })
  await command(`/session/${session}/url`, { url: `http://127.0.0.1:${server.address().port}/__fixture` })
  await until(() => execute('return window.fixtureReady && window.flm.report && !window.flm.request'))
  for (const width of [320, 416, 720, 1280]) {
    await viewport(width, width === 720 ? 480 : 900)
    for (const theme of ['dark', 'light']) {
      await execute('document.documentElement.dataset.theme=arguments[0]', theme)
      await change('ideas');await layout(`${width}/${theme}/overview`)
      assert.equal(await execute('return scrollY'),0, 'Initial interaction check runs before any scrolling')
      await tabsClear(`${width}/${theme}/initial-tabs`)
      if (width === 416) await graphContrast(theme)
      assert.equal(await execute('return window.flm.review.count'), 5)
      assert.equal(await execute('return window.flm.review.saved.length'), 0)
      if (width === 320 || width === 416 || width === 1280 || width === 720 && theme === 'dark') await capture(`${theme}-${width}-overview`)
      if (width === 320 || width === 416 || width === 1280) { await show('.gx-tuner__response');await capture(`${theme}-${width}-graph`) }
      await click('#tuner-tab-tune');await until(() => execute('return !!window.flm.manualState.data'))
      assert.equal(await execute('return document.querySelectorAll(".gx-tuner__groups>button").length'), 7)
      assert.equal(await execute('return document.querySelectorAll(".gx-tuner__idea-grid .gx-tuner__idea").length'), 1)
      assert.equal(await execute('return document.querySelectorAll(".gx-tuner__curve-summary [data-coordinate]").length'), 4)
      await layout(`${width}/${theme}/ideas`)
      if (width === 1280) assert(await execute('const a=document.querySelector(".gx-tuner__idea-grid").getBoundingClientRect(),b=document.querySelector(".gx-tuner__curve-summary").getBoundingClientRect();return Math.abs(a.top-b.top)<1&&a.right<b.left'), 'Desktop groups related findings side by side without overlapping')
      if (width === 320 || width === 416 || width === 1280) {
        await capture(`${theme}-${width}-ideas`)
        await show('.gx-tuner__idea-grid');await capture(`${theme}-${width}-finding`)
        await show('.gx-tuner__groups');await capture(`${theme}-${width}-categories`)
      }
      await click('.gx-tuner__idea-grid .gx-btn')
      assert.equal(await execute('return document.activeElement.id'), 'tuner-dial-highway_center_taper_max')
      assert.equal(await execute('return document.querySelector("#tuner-dial-highway_center_taper_max input").value'), '0')
      await layout(`${width}/${theme}/dial`)
      if (width === 320 || width === 416) await capture(`${theme}-${width}-dial`)
      await click('.gx-tuner__curve-summary .gx-btn')
      assert.equal(await execute('return document.querySelectorAll("#tuner-friction [data-coordinate]").length'), 5)
      assert.equal(await execute('return document.querySelectorAll("#tuner-friction input").length'), 0)
      assert(await execute('const e=document.querySelector("#tuner-friction"),r=e.getBoundingClientRect(),t=document.querySelector(".gx-tuner__tabs").getBoundingClientRect(),h=e.querySelector("h3").getBoundingClientRect();return document.activeElement===e&&r.top>=t.bottom&&h.top>=t.bottom&&h.bottom<innerHeight-100'), 'The curve destination includes its fully visible title below the app header and tabs')
      await layout(`${width}/${theme}/curve`)
      if (width === 320 || width === 416) await capture(`${theme}-${width}-curve`)
      await clickVisible('#tuner-tab-overview')
      assert.equal(await execute('return window.flm.view'), 'overview')
      assert(await execute('const r=document.querySelector(".gx-tuner__drive-copy h3").getBoundingClientRect(),t=document.querySelector(".gx-tuner__tabs").getBoundingClientRect();return r.top>=t.bottom&&r.bottom<innerHeight-100'), 'Returning to Overview exposes the drive verdict, rather than the bottom of the old scroll position')
      await click('#tuner-tab-tune');await until(() => execute('return !!window.flm.manualState.data'))
      await click('.gx-tuner__groups>button:nth-child(4)')
      assert.equal(await execute('return window.flm.group'), 'entry')
      assert(await execute('const e=document.querySelector(".gx-tuner__parameters"),r=e.getBoundingClientRect(),t=document.querySelector(".gx-tuner__tabs").getBoundingClientRect();return document.activeElement===e&&r.top>=t.bottom&&e.querySelector("h3").getBoundingClientRect().bottom<innerHeight-100'), 'A category click brings the selected controls into view with focus')
      await layout(`${width}/${theme}/deep-scroll-navigation`)
    }
  }
  await viewport(416);await execute('document.documentElement.dataset.theme="dark"')
  for (const name of ['none', 'empty', 'saved', 'rollback', 'conflict', 'stale', 'retained', 'wrong-car', 'incomplete', 'unknown-change']) {
    await change(name);await layout(`state/${name}`);await capture(name)
    if (name === 'retained') assert(await execute('return !/Refresh|Reconnect/.test(window.flm.review.description)&&window.flm.review.description.includes("checking your completed drive")'))
    if (name === 'stale' || name === 'retained') assert.equal(await execute('return window.flm.review.title'), 'Earlier drive findings')
    if (name === 'wrong-car' || name === 'incomplete') {
      assert.equal(await execute('return window.flm.offroad'), null)
      assert.equal(await execute('return window.flm.review.title'), name === 'wrong-car' ? 'This report is for another vehicle' : 'The drive report is incomplete')
    }
    if (name === 'unknown-change') {
      assert.equal(await execute('return window.flm.review.title'),'A configuration change was reported')
      assert(await execute('return document.querySelector(".gx-tuner__summary").textContent.includes("Saved state not confirmed")&&!document.querySelector(".gx-tuner__summary").textContent.includes("0 saved")'))
    }
    if (name === 'stale') assert(await execute('return !/Refresh|Reconnect/.test(window.flm.review.description)&&window.flm.savedSummary==="Earlier report"'))
    if (name === 'saved') assert.equal(await execute('return window.flm.currentApplied'), false)
    if (name === 'conflict') { await click('#tuner-tab-tune');assert(await execute('return document.querySelector(".gx-tuner__idea-grid").textContent.includes("No single suggested value")')) }
  }
  assert.equal(requests.length, 0, 'Reviewing findings, categories and curves must not stage or save a manual change')
  await change('applied', { parked: false });assert.equal(await execute('return window.flm.currentApplied'), true)
  await click('#tuner-tab-tune');await until(() => execute('return window.flm.manualState.data?.parked===false'))
  await execute('return window.flm.reviewSetting("highway_center_taper_max")')
  assert(await execute('const inputs=[...document.querySelectorAll(".gx-tuner__manual input")];return inputs.length>0&&inputs.every(e=>e.disabled)'))
  const before = requests.length
  await execute('return window.flm.saveManual(arguments[0], .5)', targetIndex);assert.equal(requests.length, before, 'Driving blocks direct saves')
  assert.equal(await execute('return getComputedStyle(document.querySelector(".gx-tuner__dial .gx-row__desc")).opacity'), '1', 'Driving keeps help readable')
  await capture('driving-locked')
  await change('ideas');await click('#tuner-tab-tune');await until(() => execute('return window.flm.manualState.data?.parked===true'))
  await execute('return window.flm.reviewSetting("highway_center_taper_max")')
  await execute('const e=document.querySelector("#tuner-dial-highway_center_taper_max input");e.value="0.5";e.dispatchEvent(new Event("input",{bubbles:true}));e.dispatchEvent(new Event("change",{bubbles:true}))')
  await until(() => manual.highway_center_taper_max === .5)
  await until(() => execute('return window.flm.manualState.status==="ready" && Number(window.flm.manualState.data.rows[arguments[0]].value)===.5', targetIndex))
  assert.equal(requests.at(-1).path, '/api/settings/confirm')
  assert.equal(await execute('return window.flm.offroad.saved'), false, 'Manual save does not fabricate Auto Learn approval')
  assert(await execute('return document.querySelector("#tuner-dial-highway_center_taper_max").textContent.includes("Manual override saved")'))
  await capture('manual-saved')
  await execute('document.querySelector("#tuner-tab-tune").focus()')
  await command(`/session/${session}/actions`, { actions: [{ type: 'key', id: 'keyboard', actions: [{ type: 'keyDown', value: '\uE014' }, { type: 'keyUp', value: '\uE014' }] }] })
  assert.equal(await execute('return window.flm.view'), 'activity')
  assert(await execute('const e=document.activeElement,r=e.getBoundingClientRect(),h=document.querySelector(".gx-appbar").getBoundingClientRect();return e.id==="tuner-tab-activity"&&e.matches(":focus-visible")&&getComputedStyle(e).outlineStyle!=="none"&&r.top>=h.bottom&&r.bottom<=innerHeight'), 'Keyboard tab selection keeps visible focus below the app header')
  await capture('activity-keyboard-focus')
  await click('#tuner-tab-tune');await until(() => execute('return !!window.flm.manualState.data'))
  await execute('return window.flm.reviewManualReset(30,1)')
  assert(await execute('return !!document.querySelector("[role=dialog]")'))
  await capture('reset-review')
  assert(await execute('return document.querySelector("[role=dialog]").contains(document.activeElement)'))
  await command(`/session/${session}/actions`, { actions: [{ type: 'key', id: 'keyboard', actions: [{ type: 'keyDown', value: '\uE00C' }, { type: 'keyUp', value: '\uE00C' }] }] })
  assert.equal(await execute('return !!document.querySelector("[role=dialog]")'), false)
  assert.equal(manual.highway_center_taper_max, .5, 'Escape cancels reset without saving')
  await execute('return window.flm.reviewManualReset(30,1)')
  await click('[role=dialog] .gx-dialog__actions>button:last-child');await until(() => Object.values(manual).every(value => value === 0));await until(() => execute('return window.flm.manualState.status==="ready" && window.flm.manualState.data.rows.filter(row=>row.coordinate).every(row=>Number(row.value)===0)'))
  saveError = true;await execute('return window.flm.saveManual(arguments[0],.5)', targetIndex)
  assert(await execute('return !!window.flm.manualState.error && window.flm.manualErrorText.includes("Could not confirm this change was saved")'))
  assert(Object.values(manual).every(value => value === 0))
  await until(() => execute('const e=document.querySelector(".gx-tuner__manual-error");if(!e)return false;const r=e.getBoundingClientRect(),t=document.querySelector(".gx-tuner__tabs").getBoundingClientRect();return document.activeElement===e&&r.top>=t.bottom&&r.bottom<innerHeight-100'))
  await capture('save-error')
  saveError = false;await until(() => execute('return !window.flm.manualState.error && window.flm.manualWrite.status==="checked"'));assert(await execute('return window.flm.manualReadbackText.includes("requested 0.5% is not the current saved value")'));await capture('save-error-recovered')
  reportError = true;await execute('return window.flm.load()')
  await until(() => execute('return !!window.flm.error'));await capture('connection-error')
  reportError = false;await change('ideas');await click('#tuner-tab-tune')
  await execute('document.querySelector(".gx-tuner").style.zoom="2"')
  await layout('200-percent-zoom');await capture('large-text')
  await execute('return window.flm.reviewSetting("friction_threshold_low")')
  await clickVisible('#tuner-tab-overview')
  assert.equal(await execute('return window.flm.view'), 'overview')
  await clickVisible('#tuner-tab-tune');await until(() => execute('return !!window.flm.manualState.data'))
  await click('.gx-tuner__groups>button:nth-child(5)')
  assert.equal(await execute('return window.flm.group'), 'exit')
  await layout('200-percent-zoom/deep-scroll-navigation');await capture('large-text-navigation')
  assert(await execute('return [...document.querySelectorAll(".gx-tuner__tabs>button")].every(e=>{const r=e.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth})'), 'All enlarged-text tabs fit without horizontal clipping')
  await clickVisible('#tuner-tab-plots');await until(() => execute('return window.flm.view==="plots"'))
  await layout('200-percent-zoom/live-plots');await capture('large-text-live-plots')
  await clickVisible('#tuner-tab-overview');assert.equal(await execute('return window.flm.view'),'overview')
  // Whole-document equivalent of 200% page zoom: halved CSS viewport and DPR2,
  // so the header, footer and feature all reflow and paint at the original physical size.
  await execute('document.querySelector(".gx-tuner").style.zoom=""')
  const writesBeforeReflow=requests.length
  for (const [width,height,physical] of [[208,450,'416'],[360,240,'720']]) {
    await viewport(width,height,2);await change('ideas')
    assert.equal(await execute('return devicePixelRatio'),2)
    assert.equal(await execute('return !!document.querySelector(".gx-tuner").style.zoom'),false)
    await layout('full-page-200/'+physical+'/overview');await capture('full-page-200-'+physical+'-overview')
    await click('[data-action=view-findings]');await until(() => execute('return !!window.flm.manualState.data'))
    await tabsClear('full-page-200/'+physical+'/tabs')
    await layout('full-page-200/'+physical+'/tune');await capture('full-page-200-'+physical+'-tune')
    fullPageReflow.push(await execute('return {cssWidth:innerWidth,cssHeight:innerHeight,dpr:devicePixelRatio,rootZoom:document.querySelector(".gx-tuner").style.zoom,headerHeight:document.querySelector(".gx-appbar").getBoundingClientRect().height,footerPosition:getComputedStyle(document.querySelector(".blur-nav")).position}'))
    await show('.blur-nav')
    assert(await execute('const h=document.querySelector(".gx-appbar").getBoundingClientRect();return [...document.querySelectorAll(".blur-nav button")].every(e=>{const r=e.getBoundingClientRect(),hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);return r.top>=h.bottom&&r.bottom<=innerHeight&&r.width>=44&&r.height>=44&&(hit===e||e.contains(hit))})'), 'Full-page reflow retains all primary navigation targets')
    checks.push('full-page-200/'+physical+'/primary-nav');await capture('full-page-200-'+physical+'-footer')
  }
  assert.equal(requests.length,writesBeforeReflow,'Full-page review/navigation must not write settings')
  assert.deepEqual(errors, [])
  console.log(`Real Chromium FLM UI: ${checks.length} responsive/theme/state checks, 5 findings/7 destinations, parked save/reset, driving lockout, errors and retained evidence passed`)
} catch (error) {
  failure = error.stack
  if (session) { try { await capture('failure') } catch {} }
  throw error
} finally {
  // Preserve actual browser pixels in the existing always-uploaded CI report.
  if (existsSync(output)) {
    const result = JSON.parse(await readFile(output, 'utf8'))
    assert(result && typeof result === 'object' && !Array.isArray(result))
    result.flmUxBrowser = { declared_fixtures: true, hardware_contact: false, checks, failure, screenshots, contrastResults, fullPageReflow, browserVersion, lastCommand, browser_elapsed_ms: Date.now()-browserStartedAt, driver_log: driverLog.slice(-6000) }
    await writeFile(output, JSON.stringify(result))
  } else {
    await mkdir(dirname(output), { recursive: true })
    await writeFile(output, JSON.stringify({ flmUxBrowser: { checks, failure, screenshots } }))
  }
  if (session) { try { await command(`/session/${session}`, {}, 'DELETE') } catch {} }
  if (running(driver)) {
    const exited = once(driver, 'exit')
    driver.kill('SIGTERM')
    await Promise.race([exited, new Promise(resolve => setTimeout(resolve, 2000))])
    if (running(driver)) {
      driver.kill('SIGKILL')
      await Promise.race([exited, new Promise((_, reject) => setTimeout(() => reject(new Error('Browser driver did not exit')), 2000))])
    }
  }
  assert(!running(driver), 'Driver must be reaped on success and failure')
  server.closeAllConnections()
  await new Promise(resolve => server.close(resolve))
  const result = JSON.parse(await readFile(output, 'utf8'))
  result.flmUxBrowser.cleanup = { driver_exit_code: driver.exitCode, driver_signal: driver.signalCode, driver_reaped: !running(driver), server_closed: !server.listening }
  await writeFile(output, JSON.stringify(result))
  console.log('FLM browser cleanup:', JSON.stringify(result.flmUxBrowser.cleanup))
  await rm(profile, { recursive: true, force: true })
}
