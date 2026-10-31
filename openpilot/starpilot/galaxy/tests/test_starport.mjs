import { settingControl } from "../web/js/galaxy-setting-row.js"
import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { StarPortFeed, StarPortPage, validPairingStatus, validPromptInput, uploadPackage, uploadTimeout,
  expiryInfo, packagePreflight, importProblem, installChecks } from "../web/js/starport.js"

const setup = { enabled: true, bluetoothEnabled: true, parked: true, installReady: true, serviceReady: true,
  identity: { installed: true, message: "Package ready" },
  import: { state: "idle" }, maxUploadBytes: 200 * 1024 * 1024 }
const prompt = { id: "a".repeat(32), kind: "confirmation", value: "123456", displayOnly: false }
const pairing = { active: true, receiver: { address: "AA:BB:CC:DD:EE:FF", name: "Car" }, prompt, approved: false }

assert.equal(validPairingStatus(pairing), true)
assert.equal(validPairingStatus({ ...pairing, receiver: { ...pairing.receiver, address: "bad" } }), false)
assert.equal(validPairingStatus({ ...pairing, prompt: { ...prompt, id: "bad" } }), false)
assert.equal(validPromptInput({ kind: "pin" }, "1234"), true)
assert.equal(validPromptInput({ kind: "pin" }, "\n"), false)
assert.equal(validPromptInput({ kind: "passkey" }, "1234567"), false)
assert.equal(validPromptInput(prompt, ""), true)
assert.match(StarPortPage.template, /Check that this code matches your car/)
assert.match(StarPortPage.template, /Cancel Search \/ Pairing/)
assert.doesNotMatch(StarPortPage.template, /:row="wiredRow"/)
assert.match(StarPortPage.template, /Read the Android Auto troubleshooting guide/)
assert.doesNotMatch(StarPortPage.template, /v-html|autoaccept/i)
const app = readFileSync(new URL("../web/js/app.js", import.meta.url), "utf8")
assert.match(app, /StarPortPage v-else-if="route\.path === '\/starport'"/)

const replies = {
  "./api/starport/setup": setup,
  "./api/starport/pairing/status": { pairing, selectedReceiver: null },
  "./api/starport/receivers": { receivers: [{ address: "AA:BB:CC:DD:EE:FF", name: "Saved car" }] },
}
const calls = [], states = [], timers = new Map()
let timerId = 0
const response = (body, code = 200) => ({ status: code, ok: code >= 200 && code < 300, json: async () => structuredClone(body) })
const feed = new StarPortFeed({ publish: (value) => states.push(value),
  uploader: async (path, options, progress) => { calls.push([path, options]); progress({ loaded: 16, total: 32 }); return response({ ok: true }) },
  fetcher: async (path, options) => {
    calls.push([path, options])
    return response(replies[path] || { ok: true })
  }, later: (fn, ms) => { timers.set(++timerId, { fn, ms }); return timerId }, cancelTimer: (id) => timers.delete(id) })
await feed.start()
assert.equal(feed.setup.identity.installed, true)
assert.equal(feed.pairing.prompt.value, "123456")
assert.deepEqual(calls.slice(0, 2).map(([path]) => path), ["./api/starport/setup", "./api/starport/pairing/status"])
assert(calls.every(([, options]) => options.credentials === "same-origin"))
assert([...timers.values()].some((timer) => timer.ms === 1000))
await feed.action("./api/starport/pairing/response", { prompt_id: prompt.id, accepted: true, value: "" })
assert.deepEqual(JSON.parse(calls.find(([path]) => path.endsWith("/response"))[1].body),
  { prompt_id: prompt.id, accepted: true, value: "" })
assert.equal(await feed.upload({ size: setup.maxUploadBytes + 1 }), false)
assert.match(feed.error, /size limit/)
const packageFile = { size: 32 }
assert.equal(await feed.upload(packageFile), true)
assert.equal(calls.find(([path]) => path.endsWith("/upload"))[1].body, packageFile)
assert.equal(await feed.removePackage(), true)
assert.equal(calls.find(([path]) => path.endsWith("/identity"))[1].method, "DELETE")
assert.match(StarPortPage.template, /Delete Package/)
feed.stop(true)
assert(calls.some(([path, options]) => path.endsWith("/cancel") && options.keepalive))
await feed.start()
replies["./api/starport/pairing/status"] = {
  pairing: { active: false, receiver: null, prompt: null, approved: false }, selectedReceiver: null,
  runtime: { state: "idle", running: false, auto_connect: false },
}
await feed.refresh()
assert.equal(await feed.loadReceivers(), true)
assert.deepEqual(feed.receivers, [{ address: "AA:BB:CC:DD:EE:FF", name: "Saved car" }])
assert.equal(await feed.control("select_receiver", { address: "AA:BB:CC:DD:EE:FF" }), true)
assert.deepEqual(JSON.parse(calls.find(([path]) => path.endsWith("/control"))[1].body),
  { action: "select_receiver", address: "AA:BB:CC:DD:EE:FF" })
assert.match(StarPortPage.template, /Connect to car/)
assert.match(StarPortPage.template, />Disconnect</)
assert.match(StarPortPage.template, /Automatic Connection/)
assert.equal(await feed.setEnabled(false), true)
assert.deepEqual(JSON.parse(calls.find(([path]) => path.endsWith("/enable"))[1].body), { enabled: false })
assert.equal(await feed.setEnabled(true, true), true)
assert.deepEqual(JSON.parse(calls.filter(([path]) => path.endsWith("/enable")).at(-1)[1].body),
  { enabled: true, enableBluetooth: true })
assert.equal(await feed.setEnabled(false, true), false, "Bluetooth consent cannot be used to disable Android Auto")
feed.stop()
replies["./api/starport/pairing/status"] = { pairing: { active: false, receiver: null, prompt: null, approved: false },
  selectedReceiver: null }
await feed.start()
assert.equal(feed.pairing.active, false, "reconnect must discard the old prompt")
replies["./api/starport/pairing/status"].selectedReceiver = { address: "AA:BB:CC:DD:EE:FF", name: "My car" }
await feed.refresh()
assert.equal(feed.selected.name, "My car")
feed.stop()

const expiredReplies = { pairing, selectedReceiver: null }
const expired = new StarPortFeed({ publish: () => {}, fetcher: async (path) => response(path.endsWith("/setup") ? setup : expiredReplies),
  later: () => 1, cancelTimer: () => {} })
await expired.start()
expiredReplies.pairing = { active: false, receiver: null, prompt: null, approved: false }
await expired.refresh()
assert.equal(expired.endReason, "ended")
expired.stop()

let release
const stale = new StarPortFeed({ publish: () => {}, fetcher: () => new Promise((resolve) => { release = resolve }),
  later: () => 1, cancelTimer: () => {} })
const loading = stale.start()
stale.stop()
release(response(setup))
await loading
assert.equal(stale.setup, null)

let revoked = 0
const unauthorized = new StarPortFeed({ publish: () => {}, unauthorized: () => revoked++,
  fetcher: async () => response({ error: "Sign in" }, 401), later: () => 1, cancelTimer: () => {} })
await unauthorized.start()
assert.equal(revoked, 1)
assert.equal(unauthorized.active, false)

const timeoutTimers = new Map()
let timeoutId = 0
const timedOut = new StarPortFeed({ publish: () => {}, fetcher: (_path, options) => new Promise((_resolve, reject) => {
  options.signal.addEventListener("abort", () => reject(new Error("aborted")))
}), later: (fn, ms) => { timeoutTimers.set(++timeoutId, { fn, ms }); return timeoutId },
cancelTimer: (id) => timeoutTimers.delete(id) })
const waitForTimeout = timedOut.start()
const timeout = [...timeoutTimers.values()][0]
assert.equal(timeout.ms, 8000)
timeout.fn()
await waitForTimeout
assert.match(timedOut.error, /too long/)
timedOut.stop()

const parked = new StarPortFeed({ publish: () => {}, fetcher: async (path) => response(path.endsWith("/setup") ?
  { ...setup, parked: false } : { pairing: { active: false, receiver: null, prompt: null, approved: false },
    selectedReceiver: null }),
  later: () => 1, cancelTimer: () => {} })
await parked.start()
assert.equal(await parked.action("./api/starport/pairing"), false)
parked.uploader = async (_path, options) => {
  assert.equal(options.body, packageFile)
  return response({ ok: true })
}
for (const enabled of [true, false]) {
  parked.setup.enabled = enabled
  assert.equal(await parked.upload(packageFile), true, "uploads work onroad with Android Auto on or off")
  parked.setup.enabled = enabled
  parked.runtime = { running: true }
  assert.equal(await parked.removePackage(), true, "deletion works onroad with Android Auto on, off, or connected")
}
parked.setup.import.state = "running"
assert.equal(await parked.upload(packageFile), false, "overlapping imports remain blocked")
assert.equal(await parked.removePackage(), false, "deletion does not race an active import")
assert.equal(await parked.setEnabled(true), true, "enabling works onroad")
parked.setup.installReady = false
assert.equal(await parked.setEnabled(true), false, "enabling still requires the display and encoder")
assert.equal(await parked.setEnabled(false), true, "disabling works even when hardware is unavailable")
parked.stop()
assert.equal(await parked.upload(packageFile), false, "inactive sessions cannot upload")
assert.equal(await parked.setEnabled(true), false, "inactive sessions cannot enable Android Auto")

function page(selectedSetup = setup) {
  const vm = { ...StarPortPage.data(), setup: structuredClone(selectedSetup) }
  for (const [key, getter] of Object.entries(StarPortPage.computed)) Object.defineProperty(vm, key, { get: () => getter.call(vm) })
  for (const [key, method] of Object.entries(StarPortPage.methods)) vm[key] = method.bind(vm)
  return vm
}
const wiredSetup = page()
wiredSetup.runtime = { connection: "wired", running: false, wired_busy: "Turn off Jetlink to use wired Android Auto" }
assert.equal(wiredSetup.wiredJetlinkBusy, true)
assert.equal(wiredSetup.wiredStatus.tone, "expired")
assert.equal(wiredSetup.wiredStatus.label, "Wired StarPort not enabled")
assert.match(wiredSetup.connectReason, /Jetlink/)
assert.match(StarPortPage.template, /class="gx-link" href="#\/manage_models"/)
assert.match(StarPortPage.template, /bi-arrow-right/)
wiredSetup.runtime.wired_busy = ""
assert.equal(wiredSetup.wiredStatus.tone, "ready")
assert.equal(wiredSetup.wiredStatus.label, "Wired StarPort enabled")
assert.equal(wiredSetup.connectReason, "")
wiredSetup.runtime.wired_blocked = "USB is unavailable"
assert.equal(wiredSetup.wiredStatus.tone, "expired")
assert.equal(wiredSetup.connectReason, "USB is unavailable")
wiredSetup.runtime.wired_blocked = ""
wiredSetup.setup.enabled = false
assert.equal(wiredSetup.wiredStatus.tone, "expired")

const uploadPage = page({ ...setup, enabled: false })
const uploadCalls = []
uploadPage.feed = {
  async setEnabled(value) { uploadCalls.push(["enable", value]); uploadPage.setup.enabled = value; return true },
  async upload(file) { uploadCalls.push(["upload", file]); return true },
}
for (const [name, type] of [["android-auto.apk", ""], ["android-auto.xapk", "application/zip"], ["android-auto.apkm", "application/octet-stream"]]) {
  const file = { name, size: 5 * 1024 * 1024, type, slice: () => new Blob([new Uint8Array([0x50, 0x4b, 0x03, 0x04])]) }
  uploadPage.setup.enabled = false
  await uploadPage.choosePackage({ target: { files: [file] } })
  assert.equal(uploadPage.packageFile, file)
  assert.equal(uploadPage.uploadReason, "", "valid selection can enable and upload")
  assert.equal(await uploadPage.upload(), true)
  assert.deepEqual(uploadCalls.slice(-2), [["enable", true], ["upload", file]])
}
uploadPage.choosePackage({ target: { files: [{ name: "empty.apk", size: 0 }] } })
assert.match(uploadPage.uploadReason, /empty/)
assert.equal(await uploadPage.upload(), false)
uploadPage.choosePackage({ target: { files: [{ name: "large.apk", size: setup.maxUploadBytes + 1 }] } })
assert.match(uploadPage.uploadReason, /maximum size/)
await uploadPage.choosePackage({ target: { files: [{ name: "ok.apk", size: 5 * 1024 * 1024, slice: () => new Blob([new Uint8Array([0x50, 0x4b, 0x03, 0x04])]) }] } })
uploadPage.setup.parked = false
assert.equal(uploadPage.uploadReason, "")
for (const enabled of [true, false]) {
  uploadPage.setup.enabled = enabled
  const beforeUpload = uploadCalls.length
  assert.equal(await uploadPage.upload(), true)
  const expected = enabled ? [["upload", uploadPage.packageFile]] : [["enable", true], ["upload", uploadPage.packageFile]]
  assert.deepEqual(uploadCalls.slice(beforeUpload), expected, "onroad installation can enable Android Auto before uploading")
}
uploadPage.setup.parked = true
uploadPage.busy = true
assert.equal(uploadPage.uploadReason, "", "a request blocks interaction without changing upload eligibility")
assert.equal(await uploadPage.upload(), false, "the action guard still rejects uploads while busy")
uploadPage.busy = false
uploadPage.setup.enabled = false
uploadPage.setup.installReady = false
assert.match(uploadPage.uploadReason, /installed/)
uploadPage.setup.installReady = true
uploadPage.feed.setEnabled = async () => false
const beforeDeniedEnable = uploadCalls.length
assert.equal(await uploadPage.upload(), false)
assert.equal(uploadCalls.length, beforeDeniedEnable, "failed enable never uploads")
console.log("Android Auto: APK/XAPK/APKM uploads in either road state, enable/upload sequencing, explicit blockers and feed boundaries passed")
assert.match(StarPortPage.template, /<section[^>]*:inert="busy"[^>]*:aria-busy="busy \|\| undefined"/)
assert.match(StarPortPage.template, /@click="openInstall">Update/)
assert.match(StarPortPage.template, /<header class="gx-page-header">/)
const removePage = page({ ...setup, enabled: false, parked: false })
removePage.runtime = { running: true }
removePage.feed = { async removePackage() { return true } }
assert.equal(removePage.removeReason, "")
removePage.openRemove()
assert.equal(removePage.removeOpen, true)
assert.equal(await removePage.removePackage(), true)
assert.equal(removePage.removeOpen, false)
assert.match(StarPortPage.template, /StarPort will disconnect first if connected/)

for (const parked of [true, false]) {
  const consentPage = page({ ...setup, parked, enabled: false, bluetoothEnabled: false })
  const consentCalls = []
  consentPage.feed = {
    async setEnabled(enabled, enableBluetooth) {
      consentCalls.push(["enable", enabled, enableBluetooth])
      consentPage.setup.enabled = enabled
      consentPage.setup.bluetoothEnabled = enableBluetooth
      return true
    },
    async upload(file) { consentCalls.push(["upload", file]); return true },
  }
  const canceled = consentPage.setEnabled(true)
  assert.equal(consentPage.enableOpen, true)
  assert.deepEqual(consentCalls, [], "opening the modal must not change either setting")
  consentPage.cancelEnable()
  assert.equal(await canceled, false)
  assert.equal(consentPage.enableOpen, false)
  assert.deepEqual(consentCalls, [])
  assert.equal(consentPage.setup.enabled, false)
  assert.equal(consentPage.setup.bluetoothEnabled, false)
  consentPage.packageFile = { name: "android-auto.apk", size: 123 }
  const canceledUpload = consentPage.upload()
  assert.equal(consentPage.enableOpen, true)
  consentPage.cancelEnable()
  assert.equal(await canceledUpload, false)
  assert.deepEqual(consentCalls, [], "canceling Turn On and Install must not upload")
  const confirmedUpload = consentPage.upload()
  assert.deepEqual(consentCalls, [])
  assert.equal(await consentPage.confirmEnable(), true)
  assert.equal(await confirmedUpload, true)
  assert.deepEqual(consentCalls, [["enable", true, true], ["upload", consentPage.packageFile]])
  assert.equal(consentPage.enableOpen, false)
}
const failedConsent = page({ ...setup, enabled: false, bluetoothEnabled: false })
failedConsent.feed = { async setEnabled() { return false }, async upload() { assert.fail("uploaded after failed Bluetooth startup") } }
failedConsent.packageFile = { name: "android-auto.apk", size: 123 }
const failedUpload = failedConsent.upload()
assert.equal(await failedConsent.confirmEnable(), false)
assert.equal(await failedUpload, false)
assert.equal(failedConsent.enableOpen, false)
assert.equal(failedConsent.setup.enabled, false)
assert.match(StarPortPage.template, /Android Auto requires Bluetooth for setup and connecting to your car/)
assert.match(StarPortPage.template, /Enable Bluetooth and Turn On/)
console.log("Android Auto: Bluetooth consent modal, cancellation, onroad enabling, and install continuation passed")

class UploadRequest {
  upload = {}; status = 202; responseText = '{"ok":true}'; headers = {}; sent = null
  open(method, path) { this.method = method; this.path = path }
  setRequestHeader(key, value) { this.headers[key] = value }
  send(value) { this.sent = value }
  abort() { this.onabort() }
}
for (const event of ['load', 'error', 'timeout', 'abort']) {
  const xhr = new UploadRequest(), controller = new AbortController(), progress = []
  const body = { size: 20 * 1024 * 1024 }
  const upload = uploadPackage('./api/starport/upload', { body, signal: controller.signal }, p => progress.push(p), () => xhr)
  assert.equal(xhr.sent, body); assert.equal(xhr.withCredentials, true)
  assert.equal(xhr.headers['Content-Type'], 'application/octet-stream')
  xhr.upload.onprogress({ lengthComputable: true, loaded: body.size / 2, total: body.size })
  assert.equal(progress[0].loaded, body.size / 2)
  if (event === 'abort') controller.abort(); else xhr['on' + event]()
  if (event === 'load') assert.deepEqual(await (await upload).json(), { ok: true })
  else await assert.rejects(upload, /interrupted|timed out|canceled/)
}
const alreadyAborted = new AbortController(); alreadyAborted.abort()
const neverSent = new UploadRequest()
await assert.rejects(uploadPackage('upload', { signal: alreadyAborted.signal }, () => {}, () => neverSent), /canceled/)
assert.equal(neverSent.sent, null)
assert.equal(uploadTimeout(5 * 1024 * 1024), 180000, "small packages keep the three-minute floor")
assert.equal(uploadTimeout(100 * 1024 * 1024), 1600000, "large packages get time for a slow tunnel")
const slowLink = new UploadRequest()
uploadPackage('upload', { body: {}, timeout: 1600000 }, () => {}, () => slowLink)
assert.equal(slowLink.timeout, 1600000)
assert.doesNotMatch(StarPortPage.template, /v-if="!localAccess"|Open this comma directly/)
console.log('Remote-capable binary uploader: progress, credentials, failure, timeout and cancellation passed')

// Compile the shipped Vue template, not only its strings: plain <template>
// renders an inert DOM node and hides every setup control in real browsers.
const { compile } = await import('../web/vendor/vue/vue.esm-browser.js')
const render = compile(StarPortPage.template, { decodeEntities: value => value })
const vm = page({ ...setup, identity: { ...setup.identity, installed: false } })
vm.mode = 'local'
vm.installOpen = true  // the package picker lives in the install sheet, shown only before setup
const tree = render(vm, [])
const tags = []
function visit(node) {
  if (!node || typeof node !== 'object') return
  if (typeof node.type === 'string') tags.push(node.type)
  if (Array.isArray(node.children)) node.children.forEach(visit)
}
visit(tree)
assert(!tags.includes('template'), 'setup cannot live inside an inert template element')
assert(tags.includes('input') && tags.includes('button'), 'setup renders package selection and actions')
// Once installed, Update / Install newer version opens the installer itself; no removal first.
const managed = page({ ...setup, identity: { ...setup.identity, installed: true } })
managed.openInstall()
assert.equal(managed.installState, 'choose')
const managedTags = []
const collect = (node) => { if (!node || typeof node !== 'object') return; if (typeof node.type === 'string') managedTags.push(node.type); if (Array.isArray(node.children)) node.children.forEach(collect) }
collect(render(managed, []))
assert(managedTags.includes('input'), 'an installed package can be replaced by a newer one')
assert.match(StarPortPage.template, /Install a Newer Version/)
assert.equal(page({ ...setup, identity: { ...setup.identity, installed: true, app_version: '17.8.163804-release.daily' } }).installedVersion, '17.8.163804')
assert.equal(managed.installedVersion, '', 'an unknown version shows nothing')
assert.match(StarPortPage.template, /You have \{\{ installedVersion \}\}/)
assert.match(StarPortPage.template, /Current version: \{\{ installedVersion \}\}/)
assert.doesNotMatch(StarPortPage.template, /remove it first/)
for (const [field, reason] of [['installReady', /display|encoder/],
                             ['serviceReady', /service/], ['bluetoothEnabled', /Bluetooth/], ['parked', /Park/]]) {
  const blocked = page({ ...setup, [field]: false })
  assert.match(blocked.pairingReason, reason)
}
assert.equal(page(setup).pairingReason, '')
// Pairing needs no package; only projection does, and Connect opens the installer instead.
const noPackage = page({ ...setup, identity: { installed: false } })
assert.equal(noPackage.pairingReason, '')
noPackage.selected = { address: "AA:BB:CC:DD:EE:FF", name: "Car" }
noPackage.control = () => assert.fail("projection must not start without a package")
assert.equal(noPackage.connect(), false)
assert.equal(noPackage.installOpen, true)
// Turned off is no blocker either: Find My Car enables Android Auto, then pairs once the service is up.
const off = page({ ...setup, enabled: false, serviceReady: false, identity: { installed: false } })
assert.equal(off.pairingReason, '')
off.feed = { async setEnabled(value) { assert.equal(value, true); return true }, action: async () => assert.fail("paired before the service was up") }
assert.equal(await off.startPairing(), false)
assert.equal(off.pairWhenReady, true)

// Step 1 status: expiry wording by remaining days, and the expired date from the certificate.
assert.deepEqual(expiryInfo({ installed: true, expires: "2027-10-12T00:00:00+00:00", days_left: 372 }, "en-US"),
  { level: "ready", label: "Ready · Valid until Oct 12, 2027" })
assert.deepEqual(expiryInfo({ installed: true, expires: "2027-04-12T00:00:00+00:00", days_left: 9 }, "en-US"),
  { level: "soon", label: "Expires in 9 days (Apr 12)" })
assert.deepEqual(expiryInfo({ installed: false, expired: true, expires: "2026-04-12T00:00:00+00:00" }, "en-US"),
  { level: "expired", label: "Expired on Apr 12, 2026" })
assert.equal(expiryInfo({ installed: false, error: "unusable" }).level, "broken")
assert.equal(expiryInfo({ installed: false }).level, "none")

// Wrong downloads are caught by name before the upload; the real APKMirror file name passes.
assert.equal(packagePreflight({ name: "com.google.android.projection.gearhead_17.6.663454-release-2025-apkmirror.com.apk" }), "")
assert.match(packagePreflight({ name: "APKMirror Installer (Official)_1.6.apk" }), /Installer/)
assert.match(packagePreflight({ name: "com.google.android.gms_24.apk" }), /Play Services/)
assert.equal(packagePreflight({ name: "android-auto.zip" }), "")
const wrongPage = page(setup)
wrongPage.choosePackage({ target: { files: [{ name: "APKMirror Installer.apk", size: 10 }] } })
assert.match(wrongPage.uploadReason, /Installer/)

// Server rejection codes become friendly messages and a failed checklist row.
assert.match(importProblem({ state: "failed", code: "WRONG_APP" }, "Maps.apk"), /Wrong app selected \(Maps\.apk\)/)
assert.match(importProblem({ state: "failed", code: "WRONG_APP" }, "APKMirror Installer.apk"), /Installer/)
assert.match(importProblem({ state: "failed", code: "EXPIRED", expires: "2026-04-12T00:00:00+00:00" }, "", "en-US"), /expired on Apr 12, 2026/)
assert.match(importProblem({ state: "failed", code: "CORRUPT" }), /incomplete or damaged/)
assert.equal(importProblem({ state: "failed", code: "UNSUPPORTED_VERSION", error: "Use version from server" }), "Use version from server")
assert.equal(importProblem({ state: "running" }), "")
const statuses = (job) => installChecks(job, "en-US").map((check) => check.status)
assert.deepEqual(statuses({ state: "running", stage: "searching" }), ["done", "active", "pending", "pending"])
assert.deepEqual(statuses({ state: "running", stage: "installing" }), ["done", "done", "done", "active"])
assert.deepEqual(statuses({ state: "failed", stage: "searching", code: "WRONG_APP" }), ["done", "failed", "pending", "pending"])
assert.deepEqual(statuses({ state: "failed", stage: "verifying", code: "EXPIRED" }), ["done", "done", "done", "failed"])
const done = installChecks({ state: "done", stage: "done", expires: "2027-10-12T00:00:00+00:00" }, "en-US")
assert.deepEqual(done.map((check) => check.status), ["done", "done", "done", "done"])
assert.equal(done[3].label, "Certificate valid until October 12, 2027")

// The install sheet follows only the import this page started, not an older result.
const sheet = page({ ...setup, import: { state: "failed", code: "WRONG_APP", started: 1 } })
assert.equal(sheet.installState, "choose")
sheet.installAttempted = true; sheet.installBaseline = 1
assert.equal(sheet.installState, "choose")
sheet.setup.import = { state: "running", stage: "reading", started: 2 }
assert.equal(sheet.installState, "checking")
sheet.setup.import = { state: "done", stage: "done", started: 2 }
assert.equal(sheet.installState, "done")
console.log("Android Auto: expiry badges, package preflight, friendly rejections and install checklist passed")

// Enabling can finish its refresh after the service is already ready.
const { reactive, watch, nextTick } = await import('../web/vendor/vue/vue.esm-browser.js')
const readyPage = reactive(page({ ...setup, enabled: false, serviceReady: false }))
let pairingCalls = 0
readyPage.feed = {
  async setEnabled() {
    readyPage.setup = { ...readyPage.setup, enabled: true, serviceReady: true }
    await nextTick()
    return true
  },
  async action() { pairingCalls++; return true },
}
const stopReadyWatch = watch(() => readyPage.setup.serviceReady, () => StarPortPage.watch['setup.serviceReady'].call(readyPage))
assert.equal(await readyPage.startPairing(), true)
assert.equal(pairingCalls, 1)
assert.equal(readyPage.pairWhenReady, false)
stopReadyWatch()
const connectingPage = page(setup)
connectingPage.runtime = { running: true, state: 'connecting_bluetooth' }
assert.equal(connectingPage.projecting, false)
connectingPage.runtime.state = 'streaming'
assert.equal(connectingPage.projecting, true)

assert.match(StarPortPage.template, /Remove StarPort support/)
assert.doesNotMatch(StarPortPage.template, /Manage package|>Turn On<|>Turn Off</)
assert.match(StarPortPage.template, /Install newer version/)
assert.match(StarPortPage.template, /switchRow\('StarPort', setup\.enabled/)
assert.doesNotMatch(StarPortPage.template, /Scan For Devices/)
assert.match(StarPortPage.template, /href="#\/navigation\/maps"/)
// Returning drivers get a status card; the setup steps fold away until asked for.
const returning = page(setup)
assert.equal(returning.setupDone, false, 'no car chosen yet')
returning.selected = { address: "AA:BB:CC:DD:EE:FF", name: "Civic" }
assert.equal(returning.setupDone, true)
returning.pairing = { active: true }
assert.equal(returning.setupDone, false, 'pairing keeps the steps open')
returning.pairing = null
assert.equal(page({ ...setup, enabled: false }).setupDone, false, 'turned off shows the steps')
assert.match(StarPortPage.template, /v-show="!setupDone \|\| showSteps"/)
const navigatorDescriptor = Object.getOwnPropertyDescriptor(globalThis, 'navigator')
try {
  for (const [browser, mobile] of [
    [{ userAgent: 'Mozilla/5.0 (iPhone)' }, true],
    [{ userAgent: 'Mozilla/5.0 (Linux; Android 14)', userAgentData: { mobile: false } }, true],
    [{ userAgent: 'Mozilla/5.0 (Macintosh)', platform: 'MacIntel', maxTouchPoints: 5 }, true],
    [{ userAgent: 'Mozilla/5.0 (Macintosh)', platform: 'MacIntel', maxTouchPoints: 0 }, false],
  ]) {
    Object.defineProperty(globalThis, 'navigator', { configurable: true, value: browser })
    assert.equal(StarPortPage.computed.onPhone(), mobile, 'detect phones and tablets without blocking desktop browsers')
  }
} finally {
  if (navigatorDescriptor) Object.defineProperty(globalThis, 'navigator', navigatorDescriptor)
  else delete globalThis.navigator
}
console.log('StarPort page: manage view, pairing gates and phone detection passed')

// Audio setup belongs to Android Auto; old bookmarks still open the same page.
const catalog = JSON.parse(readFileSync(new URL("../web/data/catalog.json", import.meta.url), "utf8"))
assert.ok(!catalog.tools.some(tool => tool.path === "/spotify" || tool.path === "/starport/audio"))
const starPortTool = catalog.tools.find(tool => tool.path === "/starport")
assert.equal(starPortTool.name, "StarPort")
const audioTool = starPortTool.children
  .find(tool => tool.path === "/starport/audio")
assert.equal(audioTool.name, "Spotify and Phone Audio")
assert.match(StarPortPage.template, /href="#\/starport\/audio"/)
assert.ok(app.includes(`SpotifyPage v-else-if="['/starport/audio', '/spotify'].includes(route.path)"`))
// Setup type: what is in the car decides which pairing steps appear.
const typed = page(setup)
assert.equal(typed.carType, 'builtin', 'built-in wireless is the default')
typed.runtime = { car_type: 'adapter' }
assert.equal(typed.adapterSetup, true)
assert.equal(typed.pairsHere, true)
assert.equal(typed.findLabel, 'adapter')
typed.runtime = { car_type: 'usb' }
assert.equal(typed.pairsHere, false, 'a USB cable needs no Bluetooth pairing')
typed.runtime = { running: false, connection: 'wired' }
assert.equal(typed.carType, 'usb', 'older daemons without car_type still follow the connection')
typed.runtime = { connection: 'headunitpad' }
assert.equal(typed.carType, 'app')
const typedCalls = []
typed.control = (action, extra) => typedCalls.push([action, extra])
typed.saveCarType(0, 'Wireless adapter')
typed.saveCarType(0, 'Not a type')
assert.deepEqual(typedCalls, [['car_type', { carType: 'adapter' }]])
assert.deepEqual(new Set(page(setup).carTypeRow.choices), new Set(['Built-in wireless', 'Wireless adapter', 'Wired AA', 'App']))
// A blocked wired port explains itself instead of silently refusing the pick.
const blocked = page(setup)
blocked.feed = { error: '', emit() {} }
blocked.runtime = { car_type: 'builtin', wired_blocked: 'Turn off Jetlink to use wired Android Auto' }
assert.equal(blocked.wiredUnavailable, true)
const blockedCalls = []
blocked.control = (action, extra) => blockedCalls.push([action, extra])
assert.equal(blocked.saveCarType(0, 'Wired AA'), false)
assert.deepEqual(blockedCalls, [])
assert.match(blocked.feed.error, /Turn off Jetlink/)
blocked.runtime = { car_type: 'usb', connection: 'wired', wired_blocked: 'Turn off Jetlink to use wired Android Auto' }
assert.equal(blocked.wiredUnavailable, false, 'already wired: the pick is not blocked')
// The line names the chosen tablet app.
const named = page(setup)
named.runtime = { car_type: 'app', connection: 'openheadunit' }
assert.equal(named.carTypeRow.value, 'App (Open Headunit)')
assert.ok(named.carTypeRow.choices.includes('App (Open Headunit)'))
const namedCalls = []
named.control = (action, extra) => namedCalls.push([action, extra])
named.saveCarType(0, 'Wireless adapter')
assert.deepEqual(namedCalls, [['car_type', { carType: 'adapter' }]])
// The adapter model picks the AAWireless tuning; any other adapter keeps the regular pairing.
const dongle = page(setup)
dongle.runtime = { car_type: 'adapter', car_profile: 'default', car_profile_source: 'standard' }
assert.equal(dongle.adapterRow.value, 'Choose one', 'no default: the owner picks')
assert.equal(settingControl(dongle.adapterRow), 'select', 'the initial adapter prompt must open a picker')
assert.match(dongle.pairingReason, /adapter model/)
dongle.runtime = { car_type: 'adapter', car_profile: 'default', car_profile_source: 'override' }
assert.equal(dongle.adapterRow.value, 'Motorola MA1')
assert.deepEqual(dongle.adapterRow.choices, ['AAWireless', 'Motorola MA1', 'Unofficial dongle'])
dongle.runtime = { car_type: 'adapter', car_profile: 'aawireless', car_profile_source: 'override' }
assert.equal(dongle.adapterRow.value, 'AAWireless')
const dongleCalls = []
dongle.control = (action, extra) => dongleCalls.push([action, extra])
dongle.saveAdapter(0, 'Motorola MA1')
dongle.saveAdapter(0, 'AAWireless')
dongle.saveAdapter(0, 'Unofficial dongle')
assert.deepEqual(dongleCalls, [['car_profile', { profile: 'default' }], ['car_profile', { profile: 'aawireless' }], ['car_profile', { profile: 'unofficial' }]])
assert.match(StarPortPage.template, /adapterSetup" :row="adapterRow"/)
assert.match(StarPortPage.template, /What’s in your car\?|carTypeRow/)
assert.match(StarPortPage.template, /runtime\?\.pairing_note/)
assert.match(StarPortPage.template, /'Find ' \+ findLabel/)
// A session that is only waiting or retrying never locks the pickers or Find; it is stopped first.
const idle = page(setup)
idle.runtime = { car_type: 'builtin', running: true, state: 'backoff' }
assert.equal(idle.appDisabled, false)
assert.equal(idle.pairingReason, '')
idle.runtime = { car_type: 'builtin', running: true, state: 'streaming' }
assert.equal(idle.appDisabled, true)
assert.match(idle.pairingReason, /Disconnect/)
// The home card offers the setup type; the app picker belongs to App only.
assert.match(StarPortPage.template, /v-if="app" :row="appRow"/)
assert.equal(page(setup).homeTitle, 'car')
const wiredHome = page(setup); wiredHome.runtime = { car_type: 'usb', connection: 'wired' }
assert.equal(wiredHome.homeTitle, 'Wired Android Auto')
const appHome = page(setup); appHome.runtime = { car_type: 'app', connection: 'headunitpad' }
assert.equal(appHome.homeTitle, 'Headunit Emulator')
console.log('StarPort page: setup type, adapter checklist and adapter model passed')
