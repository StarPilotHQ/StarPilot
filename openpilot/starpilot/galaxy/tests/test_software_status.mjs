import assert from "node:assert/strict"
import { SoftwareStatusFeed, SoftwarePage, validSoftwareSnapshot, softwareProgress } from "../web/js/software-status.js"

const flush = async () => { for (let i = 0; i < 12; i++) await Promise.resolve() }
const installed = { version: "fixture", branch: "Dom", commit: "a".repeat(40) }
const updater = { state: "idle", targetBranch: "Dom", lastSuccessAt: "2026-09-20T12:30:00Z", lastFetchAt: null,
  targetChangeFound: true, finalizedUpdateReady: false, failedCount: 0 }
const operations = { parked: true, availableBranches: ["Dom", "beta"], selectedTarget: "Dom",
  canCheck: true, canDownload: true, canSelect: true, canInstall: false, reason: null, request: null }
const snapshot = (changes = {}) => ({ schemaVersion: 1, installed, updater, operations, ...changes })
const request = (action, state = "pending", target = null, error = null) => ({ id: "one", action, target, state, error })
const response = (body, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => body })

function fixture(includeHistory = true, restarting = () => {}, preparing = () => {}, cancelPreparation = () => {}) {
  const requests = [], states = [], timers = new Map()
  let nextTimer = 0, unauthorized = 0
  const feed = new SoftwareStatusFeed({ includeHistory, publish: (state) => states.push(state), unauthorized: () => { unauthorized++ },
    restarting, preparing, cancelPreparation,
    later: (fn, ms) => { const id = ++nextTimer; timers.set(id, { fn, ms }); return id },
    cancelTimer: (id) => timers.delete(id),
    fetcher: (url, options) => new Promise((resolve, reject) => requests.push({ url, options, resolve, reject })) })
  async function reply(index, body, status = 200) { requests[index].resolve(response(body, status)); await flush() }
  async function fail(index) { requests[index].reject(new Error("Connection lost")); await flush() }
  function fire(ms) {
    const entry = [...timers.entries()].find(([, timer]) => timer.ms === ms)
    assert.ok(entry, `missing ${ms} ms timer`)
    timers.delete(entry[0]); entry[1].fn()
  }
  return { feed, requests, states, timers, reply, fail, fire, get unauthorized() { return unauthorized } }
}

assert.ok(validSoftwareSnapshot(snapshot()))
assert.ok(validSoftwareSnapshot(snapshot({ installed: { ...installed, displayVersion: "StarPilot 0.11.2" } })))
assert.equal(validSoftwareSnapshot(snapshot({ installed: { ...installed, displayVersion: 12 } })), false)
assert.ok(validSoftwareSnapshot({ schemaVersion: 1, installed, updater })) // Home can consume status-only responses.
assert.equal(validSoftwareSnapshot(snapshot({ operations: { ...operations, canInstall: "yes" } })), false)
assert.equal(validSoftwareSnapshot(snapshot({ operations: { ...operations, availableBranches: [42] } })), false)
assert.ok(validSoftwareSnapshot(snapshot({ operations: { ...operations, availableBranches: ["x".repeat(128)] } })))
assert.equal(validSoftwareSnapshot(snapshot({ operations: { ...operations, availableBranches: ["x".repeat(129)] } })), false)
assert.ok(validSoftwareSnapshot(snapshot({ operations: { ...operations,
  availableBranches: Array.from({ length: 76 }, (_, index) => `published-${index}`) } })))
assert.equal(validSoftwareSnapshot(snapshot({ operations: { ...operations,
  availableBranches: Array.from({ length: 257 }, (_, index) => `published-${index}`) } })), false)

const statusOnly = fixture()
statusOnly.feed.start()
await statusOnly.reply(0, { schemaVersion: 1, installed, updater })
assert.equal(statusOnly.states.at(-1).status, "ready")
assert.equal(statusOnly.states.at(-1).data.updater.finalizedUpdateReady, false)
assert.equal(statusOnly.states.at(-1).data.operations.canCheck, false)
statusOnly.feed.action("check")
assert.equal(statusOnly.requests.length, 1)
statusOnly.feed.stop()

const unavailable = fixture()
unavailable.feed.start()
assert.equal(unavailable.states.at(-1).status, "loading")
await unavailable.reply(0, { error: "Software status is unavailable" }, 503)
assert.equal(unavailable.states.at(-1).status, "loading")
assert.equal(unavailable.states.at(-1).error, "")
unavailable.fire(5000)
assert.equal(unavailable.states.at(-1).status, "loading")
await unavailable.reply(1, snapshot())
assert.equal(unavailable.states.at(-1).status, "ready")
assert.equal(unavailable.states.at(-1).error, "")
unavailable.feed.stop()

const persistentUnavailable = fixture()
persistentUnavailable.feed.start()
for (let attempt = 0; attempt < 3; attempt++) {
  await persistentUnavailable.reply(attempt, { error: "Updater status is unavailable" }, 503)
  if (attempt < 2) {
    assert.equal(persistentUnavailable.states.at(-1).status, "loading")
    persistentUnavailable.fire(5000)
  }
}
assert.equal(persistentUnavailable.states.at(-1).status, "unavailable")
assert.match(persistentUnavailable.states.at(-1).error, /Updater status is unavailable/)
persistentUnavailable.feed.stop()

const restartScreens = []
const preparedUpdates = []
const restartingUpdate = fixture(true, baseline => restartScreens.push(baseline), baseline => preparedUpdates.push(baseline))
restartingUpdate.feed.start()
await restartingUpdate.reply(0, snapshot({ operations: { ...operations, canFastUpdate: true } }))
restartingUpdate.feed.action("update", "Dom")
assert.equal(preparedUpdates.length, 1, "update intent is saved before the request can disconnect")
await restartingUpdate.reply(1, snapshot({ operations: { ...operations, canFastUpdate: true, request: request("update", "pending", "Dom") } }))
assert.deepEqual(restartScreens, [], "accepted updates keep their progress page visible")
restartingUpdate.fire(1000)
await restartingUpdate.reply(2, snapshot({ updater: { ...updater, state: "updating..." },
  operations: { ...operations, updaterAvailable: false, progress: { stage: "unavailable", detail: "Waiting for the device updater service…", percent: null },
    request: request("update", "pending", "Dom") } }))
assert.equal(restartScreens.at(-1).target, "Dom", "loss of the updater service during an update starts reconnecting immediately")
restartingUpdate.feed.stop()

const delayedRestarts = []
const delayedRestart = fixture(true, baseline => delayedRestarts.push(baseline))
delayedRestart.feed.start()
await delayedRestart.reply(0, snapshot({ operations: { ...operations, canFastUpdate: true } }))
delayedRestart.feed.action("update", "Dom")
await delayedRestart.reply(1, snapshot({ operations: { ...operations, canFastUpdate: true, request: request("update", "pending", "Dom") } }))
delayedRestart.fire(1000)
await delayedRestart.reply(2, { error: "Updater status is unavailable" }, 503)
assert.deepEqual(delayedRestarts, [], "an updater status error does not mean the device restarted")
delayedRestart.fire(2000)
await delayedRestart.fail(3)
assert.equal(delayedRestarts.at(-1).target, "Dom", "connection loss starts the reconnect screen")
delayedRestart.feed.stop()

let canceledPreparation = 0
const rejectedUpdate = fixture(true, () => {}, () => {}, () => canceledPreparation++)
rejectedUpdate.feed.start()
await rejectedUpdate.reply(0, snapshot({ operations: { ...operations, canFastUpdate: true } }))
rejectedUpdate.feed.action("update", "Dom")
await rejectedUpdate.reply(1, { error: "Update rejected" }, 409)
assert.equal(canceledPreparation, 1)
rejectedUpdate.feed.stop()

const normal = fixture()
normal.feed.start()
assert.equal(normal.requests[0].url, "./api/software/status")
assert.equal(normal.requests[0].options.credentials, "same-origin")
await normal.reply(0, snapshot())
assert.equal(normal.states.at(-1).busy, false)
assert.deepEqual([...normal.timers.values()].map((timer) => timer.ms), [5000])
normal.feed.load()
assert.equal(normal.states.at(-1).status, "ready")
assert.deepEqual(normal.states.at(-1).data.installed, installed) // Refresh keeps the visible build.
assert.equal(normal.states.at(-1).busy, false) // Background reads do not dim the controls.
await normal.reply(1, snapshot({ updater: { ...updater, targetChangeFound: false } }))
assert.equal(normal.states.at(-1).data.updater.targetChangeFound, false)
normal.feed.stop()
assert.equal(normal.timers.size, 0)

const interruptPoll = fixture()
interruptPoll.feed.start()
await interruptPoll.reply(0, snapshot())
interruptPoll.fire(5000)
assert.equal(interruptPoll.states.at(-1).busy, false)
interruptPoll.feed.action("check")
assert.equal(interruptPoll.requests.length, 3)
assert.equal(interruptPoll.requests[1].options.signal.aborted, true)
assert.deepEqual(JSON.parse(interruptPoll.requests[2].options.body), { action: "check" })
assert.equal(interruptPoll.states.at(-1).busy, true)
interruptPoll.feed.action("check")
interruptPoll.feed.load()
assert.equal(interruptPoll.requests.length, 3)
await interruptPoll.reply(2, snapshot({ operations: { ...operations, request: request("check") } }))
await interruptPoll.reply(1, snapshot({ operations: { ...operations, parked: false } }))
assert.equal(interruptPoll.states.at(-1).data.operations.parked, true)
assert.equal(interruptPoll.states.at(-1).data.operations.request.state, "pending")
interruptPoll.feed.stop()

const branch = fixture()
branch.feed.start()
await branch.reply(0, snapshot())
branch.feed.action("select", "unknown")
assert.equal(branch.requests.length, 1)
branch.feed.action("select", "beta")
assert.equal(branch.requests[1].url, "./api/software/action")
assert.deepEqual(JSON.parse(branch.requests[1].options.body), { action: "select", branch: "beta" })
branch.feed.action("check")
assert.equal(branch.requests.length, 2) // In-flight mutation is never duplicated.
await branch.reply(1, snapshot({ operations: { ...operations, selectedTarget: "beta", request: request("select", "complete", "beta") } }))
assert.equal(branch.states.at(-1).busy, false)
assert.equal(branch.requests.length, 2) // Staging does not start a check or download.
branch.feed.action("download", "Dom")
assert.equal(branch.requests.length, 2)
branch.feed.action("check")
assert.deepEqual(JSON.parse(branch.requests[2].options.body), { action: "check" })
await branch.reply(2, snapshot({ operations: { ...operations, selectedTarget: "beta", request: request("check", "pending", "beta") } }))
assert.equal(branch.timers.size, 1)
branch.fire(1000)
assert.equal(branch.requests[3].url, "./api/software/status")
await branch.reply(3, snapshot({ operations: { ...operations, selectedTarget: "beta", request: request("check", "complete", "beta") } }))
assert.deepEqual([...branch.timers.values()].map((timer) => timer.ms), [5000])
branch.feed.action("download", "beta")
assert.deepEqual(JSON.parse(branch.requests[4].options.body), { action: "download", branch: "beta" })
await branch.reply(4, snapshot({ operations: { ...operations, selectedTarget: "beta", request: request("download", "failed", "beta", "Download failed") } }))
assert.equal(branch.states.at(-1).data.operations.request.error, "Download failed")
assert.equal(branch.states.at(-1).busy, false)
branch.feed.stop()

const parked = fixture()
parked.feed.start()
await parked.reply(0, snapshot({ operations: { ...operations, parked: false, canCheck: false, canDownload: false, canSelect: false } }))
parked.feed.action("check")
assert.equal(parked.requests.length, 1)
parked.fire(1000)
assert.equal(parked.states.at(-1).status, "ready")
await parked.reply(1, snapshot())
assert.equal(parked.states.at(-1).data.operations.parked, true)
assert.deepEqual([...parked.timers.values()].map((timer) => timer.ms), [5000])
parked.feed.stop()

const manualVehicleRefresh = fixture()
manualVehicleRefresh.feed.start()
await manualVehicleRefresh.reply(0, snapshot({ operations: { ...operations, parked: false,
  canCheck: false, canDownload: false, canSelect: false } }))
manualVehicleRefresh.feed.load()
assert.equal(manualVehicleRefresh.requests[1].url, "./api/software/status")
assert.equal(manualVehicleRefresh.requests[1].options.cache, "no-store")
assert.equal(manualVehicleRefresh.requests[1].options.method, undefined)
await manualVehicleRefresh.reply(1, snapshot())
assert.equal(manualVehicleRefresh.states.at(-1).data.operations.canCheck, true)
manualVehicleRefresh.feed.load()
manualVehicleRefresh.feed.load()
assert.equal(manualVehicleRefresh.requests[2].options.signal.aborted, true)
await manualVehicleRefresh.reply(3, snapshot({ operations: { ...operations, parked: false,
  canCheck: false, canDownload: false, canSelect: false } }))
await manualVehicleRefresh.reply(2, snapshot())
assert.equal(manualVehicleRefresh.states.at(-1).data.operations.parked, false)
assert.equal(manualVehicleRefresh.states.at(-1).data.operations.canCheck, false)
assert.deepEqual(manualVehicleRefresh.states.at(-1).data.installed, installed)
manualVehicleRefresh.feed.action("check")
assert.equal(manualVehicleRefresh.requests.length, 4)
assert.ok(manualVehicleRefresh.requests.every(({ options }) => options.method === undefined))
manualVehicleRefresh.feed.stop()
assert.equal(manualVehicleRefresh.timers.size, 0)

const lateUpdater = fixture()
lateUpdater.feed.start()
await lateUpdater.reply(0, snapshot({ operations: { ...operations, canCheck: false, canDownload: false,
  canSelect: false, reason: "Updater is not running" } }))
assert.deepEqual([...lateUpdater.timers.values()].map((timer) => timer.ms), [5000])
lateUpdater.fire(5000)
assert.equal(lateUpdater.requests[1].url, "./api/software/status")
assert.equal(lateUpdater.states.at(-1).data.operations.canCheck, false) // Keep the visible status during refresh.
await lateUpdater.reply(1, snapshot())
assert.equal(lateUpdater.states.at(-1).data.operations.canCheck, true)
assert.deepEqual([...lateUpdater.timers.values()].map((timer) => timer.ms), [5000])
lateUpdater.feed.stop()
assert.equal(lateUpdater.timers.size, 0)

const uncertain = fixture()
uncertain.feed.start()
await uncertain.reply(0, snapshot({ operations: { ...operations, request: { ...request("check", "complete", "Dom"), id: "old" } } }))
uncertain.feed.action("check")
await uncertain.fail(1)
assert.equal(uncertain.states.at(-1).uncertain, true)
assert.equal(uncertain.states.at(-1).data.installed.commit, installed.commit)
uncertain.feed.action("check")
assert.equal(uncertain.requests.length, 2)
uncertain.fire(2000)
await uncertain.reply(2, snapshot({ operations: { ...operations, request: { ...request("check", "complete", "Dom"), id: "old" } } }))
assert.equal(uncertain.states.at(-1).uncertain, true) // A retained older request does not resolve the new one.
uncertain.feed.action("check")
assert.equal(uncertain.requests.length, 3)
uncertain.fire(1000)
await uncertain.reply(3, snapshot({ operations: { ...operations, request: request("check", "pending", "Dom") } }))
assert.equal(uncertain.states.at(-1).uncertain, false)
assert.equal(uncertain.timers.size, 1)
uncertain.feed.stop()
assert.equal(uncertain.timers.size, 0)

const serverError = fixture()
serverError.feed.start()
await serverError.reply(0, snapshot())
serverError.feed.action("check")
await serverError.reply(1, { error: "Status unavailable after signal" }, 503)
assert.equal(serverError.states.at(-1).uncertain, true) // A server error does not prove the POST had no effect.
assert.equal(serverError.states.at(-1).busy, false)
serverError.feed.action("check")
assert.equal(serverError.requests.length, 2)
serverError.feed.stop()

const uncertainSelect = fixture()
uncertainSelect.feed.start()
await uncertainSelect.reply(0, snapshot())
uncertainSelect.feed.action("select", "beta")
await uncertainSelect.fail(1)
assert.equal(uncertainSelect.states.at(-1).uncertain, true)
uncertainSelect.fire(2000)
await uncertainSelect.reply(2, snapshot({ operations: { ...operations, selectedTarget: "beta" } }))
assert.equal(uncertainSelect.states.at(-1).uncertain, false) // Select stages target without a request record.
uncertainSelect.feed.stop()

const install = fixture()
install.feed.start()
await install.reply(0, snapshot({ operations: { ...operations, canInstall: true } }))
install.feed.action("install", "Dom")
assert.deepEqual(JSON.parse(install.requests[1].options.body), { action: "install", branch: "Dom" })
await install.reply(1, snapshot({ operations: { ...operations, canInstall: false, request: request("install", "pending", "Dom") } }))
assert.match(install.states.at(-1).notice, /Waiting to reconnect/)
install.fire(1000)
await install.fail(2)
assert.equal(install.states.at(-1).data.installed.commit, installed.commit)
assert.equal(install.timers.size, 1)
install.fire(2000)
await install.reply(3, snapshot({ installed: { ...installed, commit: "b".repeat(40) },
  operations: { ...operations, canInstall: false } }))
assert.match(install.states.at(-1).notice, /verified after reconnect/)
assert.deepEqual([...install.timers.values()].map((timer) => timer.ms), [5000])
install.feed.stop()

const branchSwitch = fixture()
branchSwitch.feed.start()
await branchSwitch.reply(0, snapshot({ operations: { ...operations, selectedTarget: "beta", canInstall: true } }))
branchSwitch.feed.action("install", "beta")
await branchSwitch.reply(1, snapshot({ operations: { ...operations, selectedTarget: "beta", canInstall: false,
  request: request("install", "pending", "beta") } }))
branchSwitch.fire(1000)
await branchSwitch.reply(2, snapshot({ installed: { ...installed, branch: "beta" },
  operations: { ...operations, selectedTarget: "beta", canInstall: false } }))
assert.match(branchSwitch.states.at(-1).notice, /verified after reconnect/)
assert.equal(branchSwitch.states.at(-1).busy, false)
branchSwitch.feed.stop()

const revoked = fixture()
revoked.feed.start()
await revoked.reply(0, { error: "Sign in" }, 401)
assert.equal(revoked.unauthorized, 1)
assert.equal(revoked.states.at(-1).status, "idle")

const page = (selectedTarget = 'Dom', availableBranches = ['StarPilot', 'Dom', 'beta'], installedBranch = 'Dom') => {
  const state = { operations: { ...operations, selectedTarget, availableBranches },
    data: { installed: { ...installed, branch: installedBranch } }, actionDisabled: false,
    draftBranch: '', draftTouched: false, dialog: null, version: '', calls: [],
    async loadVersions() { this.calls.push(this.draftBranch) } }
  for (const key of ['branchOptions', 'previousVersion', 'otherBranches'])
    Object.defineProperty(state, key, {get:()=>SoftwarePage.computed[key].call(state)})
  return state
}
const staged = page()
SoftwarePage.methods.syncDraftBranch.call(staged, 'Dom')
staged.draftBranch = 'beta'
await SoftwarePage.methods.changeBranch.call(staged)
assert.equal(staged.draftTouched, true)
assert.deepEqual(staged.calls, ['beta'], 'branch changes automatically refresh metadata')
const switchPage = {...staged, operations: {...staged.operations, canFastUpdate:true}}
SoftwarePage.methods.askUpdate.call(switchPage)
assert.equal(switchPage.dialog.action, 'update')
assert.equal(switchPage.dialog.branch, 'beta')
assert.equal(SoftwarePage.methods.branchLabel('StarPilot'), 'Stable — StarPilot')
assert.equal(SoftwarePage.methods.branchLabel('Dom'), 'Development — Dom')
assert.deepEqual(page('Dom', ['Dom', 'other:']).branchOptions.map(branch=>branch.name), ['Dom'])
assert.match(SoftwarePage.template, /aria-label="Version branch"/)
assert.match(SoftwarePage.template, /@change="changeBranch"/)
assert.match(SoftwarePage.template, /Download only/)
assert.match(SoftwarePage.template, /Select previous version/)
assert.doesNotMatch(SoftwarePage.template, /Save target/)
assert.match(SoftwarePage.template, /displayVersion \?\? data\.installed\.version/)

const preferences = fixture()
preferences.feed.start()
await preferences.reply(0, snapshot({ operations: { ...operations, automaticDownloads: true, canConfigure: true } }))
preferences.feed.configureAutomaticDownloads(false)
assert.deepEqual(JSON.parse(preferences.requests[1].options.body), {
  action: 'preferences', automaticDownloads: false, expectedAutomaticDownloads: true,
})
await preferences.fail(1)
assert.equal(preferences.states.at(-1).uncertain, true)
preferences.feed.configureAutomaticDownloads(true)
assert.equal(preferences.requests.length, 2)
preferences.fire(2000)
await preferences.reply(2, snapshot({ operations: { ...operations, automaticDownloads: false, canConfigure: true } }))
assert.equal(preferences.states.at(-1).uncertain, false)
assert.equal(preferences.states.at(-1).data.operations.automaticDownloads, false)
preferences.feed.stop()

const history = { installed: [{ hash: 'a'.repeat(40), date: '2026-09-29T12:30:00Z', subject: '<script>plain text</script>' }],
  downloaded: [], currentReleaseNotes: '<script>also plain text</script>', downloadedReleaseNotes: null }
assert(validSoftwareSnapshot(snapshot({ operations: { ...operations, automaticDownloads: true, canConfigure: true, history } })))
assert(!validSoftwareSnapshot(snapshot({ operations: { ...operations, history: { ...history, installed: [{ hash: '--help' }] } } })))
assert(!validSoftwareSnapshot(snapshot({ operations: { ...operations, automaticDownloads: 'true' } })))
assert.doesNotMatch(SoftwarePage.template, /v-html/)

const fast = fixture()
fast.feed.start()
await fast.reply(0, snapshot({ operations: { ...operations, availableBranches: [], selectedTarget: "beta", canFastUpdate: true } }))
fast.feed.action("fast", "beta")
assert.equal(fast.requests.length, 1)
fast.feed.action("fast", installed.branch)
assert.deepEqual(JSON.parse(fast.requests[1].options.body), { action: "fast", branch: installed.branch })
await fast.reply(1, snapshot({ operations: { ...operations, canFastUpdate: false, request: request("fast", "pending", installed.branch) }, updater: { ...updater, state: "updating..." } }))
fast.feed.action("fast", installed.branch)
assert.equal(fast.requests.length, 2)
fast.fire(1000)
await fast.reply(2, snapshot({ operations: { ...operations, canFastUpdate: true, request: { ...request("fast", "complete", installed.branch), outcome: "up_to_date" } } }))
assert.equal(fast.feed.installBaseline, null)
assert.match(fast.feed.notice, /already up to date/)
fast.feed.stop()

const fastLost = fixture()
fastLost.feed.start()
await fastLost.reply(0, snapshot({ operations: { ...operations, canFastUpdate: true } }))
fastLost.feed.action("fast", installed.branch)
await fastLost.fail(1)
assert.equal(fastLost.feed.uncertain, true)
fastLost.feed.action("fast", installed.branch)
assert.equal(fastLost.requests.length, 2)
fastLost.fire(2000)
await fastLost.reply(2, snapshot({ installed: { ...installed, commit: "b".repeat(40) }, operations: { ...operations, canFastUpdate: true } }))
assert.equal(fastLost.feed.installBaseline, null)
assert.equal(fastLost.feed.uncertain, false)
assert.match(fastLost.feed.notice, /verified after reconnect/)
fastLost.feed.stop()

const fastPage = { actionDisabled: false, operations: { canFastUpdate: true, selectedTarget: "beta" }, data: { installed }, dialog: null }
SoftwarePage.methods.askUpdate.call(fastPage)
assert.deepEqual(fastPage.dialog, { action: "update", branch: "beta", title: "Update software", message: "Install the latest version of beta and restart?", label: "Update & restart" })
assert.equal(validSoftwareSnapshot(snapshot({ operations: { ...operations, canFastUpdate: "yes" } })), false)
assert.equal(validSoftwareSnapshot(snapshot({ operations: { ...operations, request: { ...request("fast"), outcome: "invented" } } })), false)

const rollbackPage = { actionDisabled: false, operations: { canRollback: true }, data: { installed }, dialog: null }
SoftwarePage.methods.askRollback.call(rollbackPage)
assert.equal(rollbackPage.dialog.action, "rollback")
assert.equal(rollbackPage.dialog.branch, installed.branch)
assert.equal(rollbackPage.dialog.title, "Previous version")
assert.match(rollbackPage.dialog.message, /Automatic downloads will be turned off/)
const unavailableRollback = { ...rollbackPage, operations: { canRollback: false }, dialog: null }
SoftwarePage.methods.askRollback.call(unavailableRollback)
assert.equal(unavailableRollback.dialog, null)
assert.equal(validSoftwareSnapshot(snapshot({ operations: { ...operations, canRollback: "yes" } })), false)


// Recent-version selection pins both the installed revision and listed target.
{
  const h = fixture()
  h.feed.start()
  const selected = "b".repeat(40)
  const history = { installed: [], downloaded: [], currentReleaseNotes: null, downloadedReleaseNotes: null,
    recent: { branch: "Dom", head: "c".repeat(40), entries: [{ hash: selected, date: "2026-10-05", subject: "Older build" }] } }
  await h.reply(0, snapshot({ operations: { ...operations, canFastUpdate: true, history } }))
  assert.equal(h.feed.action("version", "Dom", { expectedCommit: "f".repeat(40), selectedCommit: selected }), null)
  assert.equal(h.feed.action("version", "Dom", { expectedCommit: installed.commit, selectedCommit: "f".repeat(40) }), null)
  const pending = h.feed.action("version", "Dom", { expectedCommit: installed.commit, selectedCommit: selected })
  assert.deepEqual(JSON.parse(h.requests[1].options.body), { action: "version", branch: "Dom", expectedCommit: installed.commit, selectedCommit: selected })
  await h.reply(1, snapshot({ operations: { ...operations, canFastUpdate: false, history,
    request: { ...request("version", "pending", "Dom"), selectedCommit: selected } } }))
  await pending
  assert.equal(h.feed.installBaseline.selectedCommit, selected)
  h.fire(1000)
  await h.reply(2, snapshot({ operations: { ...operations, history,
    request: { ...request("version", "complete", "Dom"), selectedCommit: "c".repeat(40), outcome: "up_to_date" } } }))
  assert.equal(h.feed.installBaseline.selectedCommit, selected)
  h.fire(1000)
  await h.reply(3, snapshot({ installed: { ...installed, commit: "c".repeat(40) }, operations: { ...operations, history } }))
  assert.equal(h.feed.installBaseline.selectedCommit, selected)
  assert.notEqual(h.feed.notice, "Installed build verified after reconnect.")
  h.fire(1000)
  await h.reply(4, snapshot({ installed: { ...installed, commit: selected }, operations: { ...operations, history } }))
  assert.equal(h.feed.installBaseline, null)
  assert.equal(h.feed.notice, "Installed build verified after reconnect.")
  h.feed.stop()
}

// Real stage progress remains indeterminate until measured numeric progress exists.
assert.equal(softwareProgress(updater), null)
assert.equal(softwareProgress(updater, {progress: {stage: null, detail: "", percent: null}}), null)
assert.deepEqual(softwareProgress({...updater, state: "finalizing update..."}),
  {active: true, failed: false, percent: null, label: "Preparing update", detail: ""})
assert.equal(softwareProgress({...updater, state: "downloading..."}, {progress: {stage: "Downloading", detail: "", percent: 37}}).percent, 37)
for (const percent of [NaN, Infinity, -1, 101]) {
  assert.equal(validSoftwareSnapshot(snapshot({operations: {...operations, progress: {stage: "fetching", detail: "", percent}}})), false)
}
assert.equal(softwareProgress({...updater, state: "finalizing update..."}, {progress: {stage: "unavailable", detail: "Updater status unavailable", percent: null}}).active, false)
assert.ok(validSoftwareSnapshot(snapshot({updater: {...updater, lastCheckedAt: null}})))
assert.equal(validSoftwareSnapshot(snapshot({updater: {...updater, lastCheckedAt: 12}})), false)
assert.match(SoftwarePage.template, /<summary>Advanced options<\/summary>/)
assert.doesNotMatch(SoftwarePage.template, /<summary>Versions &amp; release notes<\/summary>/)
assert.match(SoftwarePage.template, /data\.updater\.lastCheckedAt/)

for (const progress of [{stage: "idle", detail: "", percent: null}, {stage: null, detail: null, percent: null}, {stage: "unknown", detail: "", percent: null}]) {
  assert.ok(validSoftwareSnapshot(snapshot({operations: {...operations, progress}})))
  assert.equal(softwareProgress(updater, {progress}), null)
}
for (const stage of ["complete", "error"]) {
  const state = {...updater, state: "updating...", fast: {stage, detail: "Finished"}}
  const progress = {stage, detail: "Finished", percent: null}
  assert.equal(softwareProgress(state, {progress}).active, false)
  assert.equal(softwareProgress(state, {progress}).percent, null)
}
assert.equal(softwareProgress({...updater, state: "finalizing update..."}, {progress: {stage: "finalizing", detail: null, percent: null}}).label, "Preparing update")

const lightweight = fixture(false)
lightweight.feed.start()
assert.equal(lightweight.requests[0].url, './api/software/status?history=0')
await lightweight.reply(0, snapshot())
assert.equal(lightweight.states.at(-1).busy, false)
assert.equal(lightweight.states.at(-1).data.operations.canCheck, true)
SoftwarePage.methods.loadHistory.call({ operations, busy: false, feed: lightweight.feed }, { target: { open: true } })
assert.equal(lightweight.requests[1].url, './api/software/status')
await lightweight.reply(1, snapshot({ operations: { ...operations, history } }))
assert.deepEqual(lightweight.states.at(-1).data.operations.history, history)
lightweight.feed.stop()

const versionRefreshActions = []
await SoftwarePage.methods.loadVersions.call({ actionDisabled: false, draftBranch: 'Dom',
  operations: { selectedTarget: 'Dom' }, feed: { action: (...args) => versionRefreshActions.push(args) } })
assert.deepEqual(versionRefreshActions, [['versions', 'Dom']], 'version refresh must use the metadata-only operation')

const directBranch = fixture()
directBranch.feed.start()
await directBranch.reply(0,snapshot({operations:{...operations,availableBranches:['Dom','beta'],canFastUpdate:true}}))
const branchVm={actionDisabled:false,draftBranch:'beta',operations:directBranch.states.at(-1).data.operations,data:{installed},dialog:null,feed:directBranch.feed}
SoftwarePage.methods.askUpdate.call(branchVm)
assert.equal(branchVm.dialog.label,'Update & restart')
const acceptedSwitch=SoftwarePage.methods.confirmDialog.call(branchVm)
assert.deepEqual(JSON.parse(directBranch.requests[1].options.body),{action:'update',branch:'beta'})
assert.equal(directBranch.requests.length,2,'switching uses one request and one confirmation')
await directBranch.reply(1,snapshot())
await acceptedSwitch
directBranch.feed.stop()

// A visit checks immediately, without interrupting an update already in progress.
for (const [canCheck, pending, state, expected] of [
  [true, false, 'idle', ['check']], [false, false, 'idle', []],
  [true, true, 'idle', []], [true, false, 'downloading...', ['check']],
]) {
  const actions = []
  const view = { mode: 'local', initialChecking: true, operations: { canCheck }, pending,
    data: { updater: { state } }, feed: { active: true, start: async () => {}, action: async action => actions.push(action) } }
  await SoftwarePage.mounted.call(view)
  assert.deepEqual(actions, expected)
  assert.equal(view.initialChecking, false)
}

const updateView = { ...SoftwarePage.data(), data: snapshot(), operations: { ...operations },
  requestMessage: SoftwarePage.methods.requestMessage }
for (const key of ['pending', 'reportedProgress', 'metadataProgress', 'backgroundProgress', 'updateProgress', 'updateAvailable', 'osUpdateRequired', 'updateBanner', 'repositoryUrl', 'commitUrl', 'commitsUrl']) {
  Object.defineProperty(updateView, key, { get: () => SoftwarePage.computed[key].call(updateView) })
}
assert.equal(updateView.updateProgress.label, 'Checking for updates')
assert.equal(updateView.updateBanner, null)
updateView.initialChecking = false
assert.deepEqual(updateView.updateBanner, { tone: 'success', message: 'Update Available' })
updateView.data.updater.osUpdate = { branch: updateView.operations.selectedTarget, required: true }
assert.equal(updateView.updateBanner.tone, 'warn')
assert.match(updateView.updateBanner.message, /operating system update required/)
updateView.data.updater.osUpdate.branch = 'another-branch'
assert.equal(updateView.updateBanner.tone, 'success')
updateView.data.updater.osUpdate = { branch: updateView.operations.selectedTarget, required: false }
assert.equal(updateView.updateBanner.tone, 'success')
assert.ok(!SoftwarePage.template.includes('View commits'))

updateView.data = snapshot({ updater: { ...updater, targetChangeFound: false } })
updateView.operations.request = request('check', 'complete')
assert.equal(updateView.updateBanner.message, 'No Update Available')
updateView.operations.request = request('check')
assert.equal(updateView.updateProgress.label, 'Checking for new versions')
assert.equal(updateView.updateBanner, null)
updateView.operations.request = request('check', 'failed', null, 'Network unavailable')
assert.equal(updateView.updateProgress, null)
assert.deepEqual(updateView.updateBanner, { tone: 'danger', message: 'Network unavailable' })
updateView.error = 'Connection lost'
assert.equal(updateView.updateBanner.message, 'Connection lost')
updateView.operations.repository = 'firestar5683/openpilot'
updateView.draftBranch = 'beta'
assert.equal(updateView.commitUrl, `https://github.com/firestar5683/openpilot/commit/${installed.commit}`)
assert.equal(updateView.commitsUrl, 'https://github.com/StarPilotHQ/StarPilot/commits/Dom/')
assert.equal(SoftwarePage.computed.commitsUrl.call({ data: { installed: { branch: 'SecretGoodStarPilot' } } }),
  'https://github.com/StarPilotHQ/StarPilot/commits/SecretGoodStarPilot/')

// The same button installs a prepared build or requests the updater-owned automatic path.
const ready = { actionDisabled: false, draftBranch: "Dom", operations: { canInstall: true, selectedTarget: "Dom" },
  data: snapshot({ updater: { ...updater, finalizedUpdateReady: true } }), askInstall() { this.installed = true } }
assert.equal(SoftwarePage.computed.canUpdate.call(ready), true)
SoftwarePage.methods.askUpdate.call(ready)
assert.equal(ready.installed, true)
const automatic = fixture()
automatic.feed.start()
await automatic.reply(0, snapshot({ operations: { ...operations, canFastUpdate: true } }))
automatic.feed.action("update", installed.branch)
assert.deepEqual(JSON.parse(automatic.requests[1].options.body), { action: "update", branch: installed.branch })
await automatic.reply(1, snapshot({ updater: { ...updater, state: "updating..." },
  operations: { ...operations, canFastUpdate: false, request: request("update", "pending", installed.branch) } }))
assert.equal(automatic.feed.installBaseline.action, "update")
automatic.fire(1000)
await automatic.reply(2, snapshot({ operations: { ...operations, request: { ...request("update", "complete", installed.branch), outcome: "up_to_date" } } }))
assert.equal(automatic.feed.installBaseline, null)
automatic.feed.stop()

// A remote tunnel can answer while the comma itself is restarting.
for (const [code, body] of [[502, null], [504, { message: 'Device unreachable' }],
                          [503, { message: 'Your comma cannot be reached' }],
                          [503, { error: 'Device offline' }], [200, null]]) {
  const restarts = []
  const f = fixture(true, value => restarts.push(value))
  f.feed.start()
  await f.reply(0, snapshot({ operations: { ...operations, canFastUpdate: true } }))
  f.feed.action('update', 'Dom')
  await f.reply(1, body, code)
  assert.ok(restarts.length > 0, `update disconnect ${code} must show restart recovery`)
  assert.equal(restarts.at(-1).action, 'update')
  assert.equal(f.feed.uncertain, true)
  f.feed.stop()
  const ordinary = fixture(true, () => { throw new Error('ordinary disconnect is not an update') })
  ordinary.feed.start()
  await ordinary.reply(0, body, code)
  ordinary.feed.stop()
}
for (const [state, outcome] of [['failed', undefined], ['complete', 'up_to_date']]) {
  let canceled = 0
  const f = fixture(true, () => { throw new Error('finished update must not imply restart') }, () => {}, () => canceled++)
  f.feed.start()
  await f.reply(0, snapshot({ operations: { ...operations, canFastUpdate: true } }))
  f.feed.action('update', 'Dom')
  await f.reply(1, snapshot({ operations: { ...operations, request: request('update', 'pending', 'Dom') } }))
  f.feed.load()
  await f.reply(2, snapshot({ operations: { ...operations, request: { ...request('update', state, 'Dom'), outcome } } }))
  assert.equal(f.feed.installBaseline, null)
  assert.equal(canceled, 1)
  f.feed.stop()
}

// Gateway error envelopes are not software snapshots, even when the tunnel uses HTTP 200.
const unreachableMessage = 'Your comma cannot be reached. Check your connection and try again'
for (const action of ['update', 'fast', 'rollback', 'install', 'version']) {
  for (const status of [200, 400, 500, 503]) for (const code of [undefined, 'connection_unavailable']) {
    for (const field of ['error', 'message']) {
      for (const duringPoll of [false, true]) {
        const restarts = []
        const f = fixture(true, baseline => restarts.push(baseline))
        f.feed.start()
        await f.reply(0, snapshot({ operations: { ...operations, canFastUpdate: true, canRollback: true, canInstall: true,
          history: { installed: [], downloaded: [{ hash: 'b'.repeat(40), date: '', subject: '' }], currentReleaseNotes: null, downloadedReleaseNotes: null,
            recent: { branch: 'Dom', head: installed.commit, entries: [{ hash: 'b'.repeat(40), date: '', subject: '' }] } } } }))
        f.feed.action(action, 'Dom', { expectedCommit: installed.commit, selectedCommit: 'b'.repeat(40) })
        assert.equal(f.requests.length, 2)
        if (duringPoll) {
          await f.reply(1, snapshot({ operations: { ...operations, request: request(action, 'pending', 'Dom') } }))
          f.fire(1000)
        }
        await f.reply(duringPoll ? 2 : 1, { [field]: unreachableMessage, code }, status)
        assert.ok(restarts.length, `${action} ${status} ${field} ${duringPoll ? 'poll' : 'POST'} must reconnect`)
        assert.equal(restarts.at(-1).action, action)
        assert.equal(f.feed.installBaseline.action, action)
        if (!duringPoll) assert.equal(f.feed.uncertain, true)
        f.feed.stop()
      }
    }
  }
}
// The same envelope outside a user-started update must remain an ordinary error.
for (const status of [200, 400, 500]) {
  const f = fixture(true, () => assert.fail('ordinary status failure must not show update recovery'))
  f.feed.start()
  await f.reply(0, { message: unreachableMessage, code: 'connection_unavailable' }, status)
  assert.equal(f.states.at(-1).error, unreachableMessage)
  f.feed.stop()
}
// Structured service and authorization errors keep their existing behavior.
for (const [status, payload, unauthorized] of [
  [401, { message: unreachableMessage }, true],
  [503, { code: 'access_unavailable', message: unreachableMessage }, true],
  [503, { code: 'setup_required', message: unreachableMessage }, true],
  [500, { code: 'update_failed', error: 'Failed to apply update' }, false],
  [503, { error: 'Updater status is unavailable' }, false],
  [409, { error: 'Update rejected' }, false],
]) {
  const f = fixture(true, () => assert.fail('structured/auth error must not show update recovery'))
  f.feed.start()
  await f.reply(0, snapshot({ operations: { ...operations, canFastUpdate: true } }))
  f.feed.action('update', 'Dom')
  await f.reply(1, payload, status)
  assert.equal(f.unauthorized, Number(unauthorized))
  if (!unauthorized) assert.equal(f.states.at(-1).error, payload.error)
  f.feed.stop()
}
console.log('Software status and tunnel-envelope regressions passed')

// Metadata checks remain separate from a background finalization and its real progress.
updateView.error = ''
updateView.operations = { ...operations, reason: 'Updater is busy', request: request('versions'),
  progress: { stage: 'finalizing', detail: 'Copying update files: 44/100 (44%)', percent: 44, background: true } }
updateView.data = snapshot({ updater: { ...updater, state: 'finalizing update...',
  fast: { stage: 'finalizing', check: { state: 'checking', detail: 'Fetching version metadata...' } } } })
assert.equal(updateView.updateProgress.label, 'Checking for new versions')
assert.equal(updateView.backgroundProgress.percent, 44)
assert.match(updateView.backgroundProgress.detail, /44\/100/)
assert.equal(SoftwarePage.computed.metadataActionDisabled.call(updateView), false)
updateView.data.updater.fast.check = { state: 'complete' }
updateView.operations.request = request('versions', 'complete')
assert.equal(updateView.updateProgress, null)
assert.equal(updateView.backgroundProgress.percent, 44)

// Clicking Check immediately replaces the previous Refresh versions result, before its POST returns.
const nextCheck = fixture()
nextCheck.feed.start()
await nextCheck.reply(0, snapshot({ operations: { ...operations, request: request('versions', 'complete') } }))
nextCheck.feed.action('check')
assert.equal(nextCheck.states.at(-1).submittingAction, 'check')
updateView.operations = { ...operations, request: request('versions', 'complete') }
updateView.data = snapshot()
updateView.busy = true
updateView.submittingAction = nextCheck.states.at(-1).submittingAction
assert.equal(updateView.updateProgress.label, 'Checking for new versions')
assert.equal(updateView.updateBanner, null)
await nextCheck.reply(1, snapshot({ operations: { ...operations, request: request('check') } }))
assert.equal(nextCheck.states.at(-1).submittingAction, null)
nextCheck.feed.stop()

updateView.busy = false
updateView.submittingAction = null
updateView.operations = { ...operations, request: null }
updateView.draftBranch = 'Dom'
updateView.data = snapshot({ updater: { ...updater, finalizedUpdateReady: true } })
assert.deepEqual(updateView.updateBanner, { tone: 'success', message: 'Downloaded · Ready to install' })

// Downloading a selected version pins it without entering restart recovery.
const selectedDownload = fixture()
const selectedHash = 'b'.repeat(40)
const selectionHistory = { installed: [], downloaded: [], currentReleaseNotes: null, downloadedReleaseNotes: null,
  recent: { branch: 'beta', head: selectedHash, entries: [{hash:selectedHash,date:'',subject:'Beta build'}] } }
selectedDownload.feed.start()
await selectedDownload.reply(0, snapshot({operations:{...operations, canFastUpdate:true, history:selectionHistory}}))
selectedDownload.feed.action('download-version', 'beta', {expectedCommit:installed.commit,selectedCommit:selectedHash})
assert.deepEqual(JSON.parse(selectedDownload.requests[1].options.body), {action:'download-version',branch:'beta',expectedCommit:installed.commit,selectedCommit:selectedHash})
assert.equal(selectedDownload.feed.installBaseline, null)
await selectedDownload.reply(1, snapshot({operations:{...operations,request:request('download-version','pending','beta')}}))
assert.equal(selectedDownload.feed.installBaseline, null)
selectedDownload.feed.stop()

const previousSelection = page('Dom',['Dom','beta'])
previousSelection.operations.history = {previous:{hash:selectedHash,date:'',subject:'Previous',branch:'beta'}}
previousSelection.syncDraftBranch = SoftwarePage.methods.syncDraftBranch.bind(previousSelection)
await SoftwarePage.methods.selectPreviousVersion.call(previousSelection)
assert.equal(previousSelection.draftBranch, 'beta')
assert.equal(previousSelection.version, selectedHash)
assert.deepEqual(previousSelection.calls, [], 'selecting a saved previous version must not refresh metadata')
assert.equal(previousSelection.dialog,null,'selecting previous must not install it')

// A finished metadata refresh must leave the actual update status visible.
updateView.error = ''
updateView.busy = false
updateView.submittingAction = null
updateView.initialChecking = false
updateView.draftBranch = 'Dom'
updateView.operations = { ...operations, request: request('versions', 'complete') }
updateView.data = snapshot({ updater: { ...updater, finalizedUpdateReady: true } })
assert.equal(updateView.updateBanner.message, 'Downloaded · Ready to install')
updateView.data.updater.finalizedUpdateReady = false
assert.equal(updateView.updateBanner.message, 'Update Available')
updateView.data.updater.targetChangeFound = false
assert.equal(updateView.updateBanner.message, 'No Update Available')
assert.equal(SoftwarePage.methods.requestMessage(request('versions', 'complete')), '')

const branchLayout = page()
branchLayout.changeBranch = SoftwarePage.methods.changeBranch.bind(branchLayout)
SoftwarePage.methods.syncDraftBranch.call(branchLayout, 'Dom')
assert.equal(branchLayout.primaryChoice, 'Dom')
branchLayout.primaryChoice = 'other:'
await SoftwarePage.methods.onPrimaryBranchChange.call(branchLayout)
assert.equal(branchLayout.draftBranch, '')
assert.deepEqual(branchLayout.calls, [], 'opening Other does not refresh metadata without a branch')
assert.deepEqual(branchLayout.otherBranches.map(branch => branch.name), ['beta'])
branchLayout.draftBranch = 'beta'
await branchLayout.changeBranch()
assert.deepEqual(branchLayout.calls, ['beta'])
branchLayout.primaryChoice = 'StarPilot'
await SoftwarePage.methods.onPrimaryBranchChange.call(branchLayout)
assert.equal(branchLayout.draftBranch, 'StarPilot')
assert.deepEqual(branchLayout.calls, ['beta', 'StarPilot'])
assert.equal(previousSelection.primaryChoice, 'other:', 'previous selection synchronizes both selectors without refreshing')

assert.doesNotMatch(SoftwarePage.template, /Refresh versions/)
assert.match(SoftwarePage.template, /@click="loadVersions">Refresh/ )
assert.match(SoftwarePage.template, /feed.action\('check'\)">Check for updates/)

assert.match(SoftwarePage.template, /GalaxySelect show-description v-model="primaryChoice"/)
assert.doesNotMatch(SoftwarePage.template, /<p v-if="primaryBranchHelp"/)

assert.match(SoftwarePage.template, /DriveStatePanel offroad-only :lockout="operations\?\.parked === false"/)


const installPreferences = fixture()
const installOptions = {...operations, canConfigure:true, automaticInstalls:false, restartSchedule:'0 0 * * *',
  installPreferenceRevision:'0'.repeat(32),restartTimezone:'UTC'}
assert.ok(validSoftwareSnapshot(snapshot({operations:installOptions})))
assert.equal(validSoftwareSnapshot(snapshot({operations:{...installOptions,automaticInstalls:'yes'}})),false)
installPreferences.feed.start()
await installPreferences.reply(0,snapshot({operations:installOptions}))
installPreferences.feed.configureInstallPreferences(true,' 0  0 * * * ','Asia/Tokyo')
assert.deepEqual(JSON.parse(installPreferences.requests[1].options.body),{action:'install-preferences',automaticInstalls:true,
  restartSchedule:'0 0 * * *',expectedRevision:'0'.repeat(32),restartTimezone:'Asia/Tokyo'})
await installPreferences.reply(1,snapshot({operations:{...installOptions,automaticInstalls:true,installPreferenceRevision:'a'.repeat(32)}}))
assert.equal(installPreferences.feed.installBaseline,null,'enabling the schedule does not install immediately')
installPreferences.feed.stop()
assert.match(SoftwarePage.template,/Install updates automatically/)
assert.match(SoftwarePage.template,/Automatic restart schedule/)
assert.deepEqual(SoftwarePage.data().restartCronFields,['0','0','*','*','*'])

const previousTimezone = process.env.TZ
try {
  process.env.TZ = 'America/Los_Angeles'
  const localTime = SoftwarePage.methods.reported('2026-11-01T00:00:00+00:00')
  assert.equal(localTime, new Date("2026-11-01T00:00:00+00:00").toLocaleString())
} finally {
  if (previousTimezone === undefined) delete process.env.TZ
  else process.env.TZ = previousTimezone
}
assert.match(SoftwarePage.template, /is ready for install\. Click Update when ready\./)
assert.match(SoftwarePage.template, /Auto install will run at/)

assert.match(SoftwarePage.template, /reported\(operations.nextRestartAt\)/)

assert.ok(SoftwarePage.computed.restartTimezones.call({restartTimezone:'Asia/Tokyo',operations:{restartTimezone:'UTC'}}).includes('Asia/Tokyo'))
assert.equal(SoftwarePage.computed.restartScheduleChanged.call({restartCronFields:['0','0','*','*','*'],restartTimezone:'Asia/Tokyo',
  operations:{restartSchedule:'0 0 * * *',restartTimezone:'UTC'}}),true)
let toggleSettings
SoftwarePage.methods.configureAutomaticInstalls.call({operations:{restartSchedule:'0 0 * * *',restartTimezone:'UTC'},restartTimezone:'Asia/Tokyo',
  feed:{configureInstallPreferences:(...args)=>{toggleSettings=args}}},true)
assert.deepEqual(toggleSettings,[true,'0 0 * * *','UTC'],'toggle preserves the saved timezone')
