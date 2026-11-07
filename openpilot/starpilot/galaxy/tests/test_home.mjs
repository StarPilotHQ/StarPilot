import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { Home, homeSummary, driveSummary } from "../web/js/home.js"

const absent = homeSummary({ status: "unavailable", snapshot: null }, { status: "stale", data: null },
  { status: "unavailable", data: null })
assert.equal(absent.system.cpu, "Unavailable")
assert.equal(absent.system.storage, "Unavailable")
assert.equal(absent.system.recordingStorage, null)
assert.equal(absent.model.health, "Unavailable")
assert.equal(absent.software.update, "Unavailable")

const runtimeStall = homeSummary({ status: "unavailable", snapshot: null },
  { status: "ready", data: { loadedId: "bundled-current", health: "active", variant: "small", fallbackReason: "chestnut-run-stalled" } },
  { status: "unavailable", data: null })
assert.equal(runtimeStall.model.fallback, "Chestnut output stopped; Small restarted for this drive")

const current = homeSummary({ status: "current", snapshot: { sampledAt: 1_700_000_000, cpuPercent: 22.4,
  memory: { percent: null }, storage: { usedGiB: 12.34, totalGiB: 64 } } },
{ status: "ready", data: { loadedId: "bundled-current", health: "active", variant: "chestnut", fallbackReason: null } },
{ status: "ready", data: { installed: { version: "fixture", displayVersion: "StarPilot 0.11.2", branch: null },
  updater: { finalizedUpdateReady: null } } })
assert.equal(current.system.cpu, "22%")
assert.equal(current.system.memory, "Unavailable")
assert.equal(current.system.storage, "12.3 GiB / 64.0 GiB")
assert.equal(current.system.recordingStorage, null)
const c3Snapshot = { sampledAt: 1_700_000_000, deviceType: "tici",
  storage: { usedGiB: 20.6, totalGiB: 29.8, freeGiB: 7.6 },
  recordingStorage: { usedGiB: 598, totalGiB: 916, freeGiB: 273 } }
const storageSummary = (snapshot, status = "current") => homeSummary({ status, snapshot },
  { status: "unavailable" }, { status: "unavailable" }).system
const dualStorage = storageSummary(c3Snapshot)
assert.equal(dualStorage.storage, "20.6 GiB / 29.8 GiB")
assert.equal(dualStorage.storageLabel, "Internal storage")
assert.equal(dualStorage.storageFree, "7.6 GiB")
assert.deepEqual(dualStorage.recordingStorage, { usage: "598.0 GiB / 916.0 GiB", free: "273.0 GiB" })
assert.equal(storageSummary({ ...c3Snapshot, recordingStorage: null }).recordingStorage, null)
assert.equal(storageSummary(c3Snapshot, "stale").recordingStorage, null)
for (const deviceType of ["tizi", "mici", "pc", undefined]) {
  const summary = storageSummary({ ...c3Snapshot, deviceType })
  assert.equal(summary.storageLabel, "Storage")
  assert.equal(summary.storageFree, null)
  assert.equal(summary.recordingStorage, null)
}
assert.match(Home.template, /v-if="summary.system.recordingStorage"/)
assert.match(Home.template, /Recording drive:/)
assert.match(Home.template, /summary.system.recordingStorage.free }} free/)
assert.equal(current.model.variant, "Chestnut big")
assert.equal(current.software.branch, "Unavailable")
assert.equal(current.software.version, "StarPilot 0.11.2")
assert.equal(current.software.update, "Unavailable")
assert.equal(homeSummary({ status: "unavailable", snapshot: { cpuPercent: 100 } },
  { status: "stale", data: { health: "active" } }, { status: "idle", data: {} }).model.health, "Unavailable")

const priorDocument = globalThis.document
const priorFetch = globalThis.fetch
const listeners = new Map(), requested = []
const monitor = { schemaVersion: 1, mode: "local-runtime", source: "local", sampledAt: 1_700_000_000,
  cores: [], processes: [], memory: { totalMiB: 4096, usedMiB: 1000, availableMiB: 3000, percent: 24.4 },
  storage: { usedGiB: 8, totalGiB: 64 }, cpuPercent: 12, uptimeSeconds: 300, processCount: 0, vitals: {} }
const model = { schemaVersion: 1, catalog: [{ id: "bundled-current", name: "Bundled driving model", selectable: true }],
  requestedId: "bundled-current", loadedId: "bundled-current", variant: "small", health: "active",
  artifactSha256: "a".repeat(64), fallbackReason: null, pendingNextStart: false }
const software = { schemaVersion: 1, installed: { version: "fixture", branch: "Dom", commit: "a".repeat(40) },
  updater: { state: "idle", targetBranch: null, lastSuccessAt: null, lastFetchAt: null,
    targetChangeFound: null, finalizedUpdateReady: false, failedCount: 0 } }
const route = { routeId: "2026-09-27--12-00-00", startTime: 1_790_509_200, endTime: 1_790_512_800,
  distanceMeters: 12000, durationSeconds: 3600, engagedSeconds: 2700, engagedPercent: 75,
  model: "Chestnut", distractedMoments: null, unresponsiveMoments: 0,
  complete: true, reason: null, segmentCount: 2, ignored: false }
const drives = { schemaVersion: 1, isMetric: true, analysis: { running: false, pending: 0, failed: 0, scanIncomplete: false },
  totals: { distanceMeters: 12000, durationSeconds: 3600, engagedSeconds: 2700, drives: 1 },
  week: { days: Array.from({ length: 7 }, (_, i) => ({ date: `2026-09-${String(21 + i).padStart(2, "0")}`,
    distanceMeters: i === 6 ? 12000 : 0 })), distanceMeters: 12000, durationSeconds: 3600,
    engagedPercent: 75, drives: 1, timezone: "UTC" },
  lastDrive: route, recentDrives: [route], records: [{ id: "longestDrive", label: "Longest drive", value: 12000, unit: "m" }],
  models: [{ name: "Chestnut", distanceMeters: 12000, durationSeconds: 3600, drives: 1 }] }
const driving = driveSummary({ status: "ready", data: drives })
assert.equal(driving.last.distance, "12.0 km")
assert.equal(driving.last.distracted, "Unavailable")
assert.equal(driving.last.aol, "Unavailable")
assert.equal(driving.last.aolDuration, "Unavailable")
assert.equal(driving.totals.aol, "Unavailable")
const withAol = driveSummary({ status: "ready", data: { ...drives,
  lastDrive: { ...route, aolSeconds: 900, aolPercent: 25 },
  totals: { ...drives.totals, aolSeconds: 900, aolDrives: 1 },
  week: { ...drives.week, aolPercent: 25, aolDrives: 1 },
  recentDrives: [{ ...route, aolPercent: 25 }] } })
assert.equal(withAol.last.aol, "25%")
assert.equal(withAol.last.aolDuration, "15m")
assert.equal(withAol.totals.aol, "15m")
assert.equal(withAol.totals.aolDrives, 1)
assert.equal(withAol.week.aol, "25%")
assert.equal(withAol.recent[0].aol, "25%")
assert.match(Home.template, /AOL steering only/)
assert.equal(driving.week.days[0].height, 0)
assert.equal(driving.week.days[6].height, 100)
assert.equal(driving.records[0].display, "12.0 km")
const incomplete = { ...route, complete: false, reason: "End of route was not recorded" }
const paused = driveSummary({ status: "ready", data: { ...drives, lastDrive: incomplete, recentDrives: [incomplete],
  analysis: { ...drives.analysis, failures: [{ routeId: route.routeId, reason: incomplete.reason, retryStopped: true }] } } })
assert.equal(paused.last.analysisLabel, "Incomplete recording")
assert.equal(paused.recent[0].analysisError, undefined)
assert.equal(paused.recent[0].analysisLabel, "Incomplete recording")
const retrying = driveSummary({ status: "ready", data: { ...drives, recentDrives: [incomplete],
  analysis: { ...drives.analysis, failures: [{ routeId: route.routeId, reason: "TimeoutError", retryStopped: false }] } } })
assert.equal(retrying.recent[0].analysisLabel, "Incomplete recording")
assert.equal(retrying.recent[0].analysisError, undefined)
const pendingAnalysis = driveSummary({ status: "ready", data: { ...drives, recentDrives: [incomplete] } })
assert.equal(pendingAnalysis.recent[0].analysisLabel, "Incomplete recording")
assert.equal(pendingAnalysis.recent[0].analysisError, undefined)
assert.doesNotMatch(Home.template, /'Analyzing'|still being analyzed|Waiting for analysis/)
const imperial = driveSummary({ status: "ready", data: { ...drives, isMetric: false } })
assert.equal(imperial.last.distance, "7.5 mi")
assert.equal(imperial.week.days[6].distance, "7.5 mi")
assert.equal(imperial.records[0].display, "7.5 mi")
const previousTZ = process.env.TZ
process.env.TZ = "America/Chicago"
const midnightDrive = { ...route, startTime: Date.parse("2026-09-27T04:30:00Z") / 1000,
  endTime: Date.parse("2026-09-27T06:30:00Z") / 1000 }
const local = driveSummary({ status: "ready", data: { ...drives, lastDrive: midnightDrive } })
assert.match(local.last.date, /Sep 26, 2026/)
assert.match(local.last.date, /Sep 27, 2026/)
assert.doesNotMatch(local.last.date, /UTC/)
if (previousTZ === undefined) delete process.env.TZ
else process.env.TZ = previousTZ
const unequal = driveSummary({ status: "ready", data: { ...drives, week: { ...drives.week,
  days: drives.week.days.map((day, i) => ({ ...day, distanceMeters: [0, 1000, 2000, 4000, 8000, null, 16000][i] })) } } })
assert.deepEqual(unequal.week.days.map((day) => day.height), [0, 6.25, 12.5, 25, 50, 0, 100])
assert.equal(unequal.week.days[5].distance, "Unavailable")
assert.equal(driveSummary({ status: "unavailable", data: drives }), null)
globalThis.document = { hidden: true, addEventListener: (name, fn) => listeners.set(name, fn),
  removeEventListener: (name, fn) => { if (listeners.get(name) === fn) listeners.delete(name) } }
globalThis.fetch = async (url) => {
  requested.push(url)
  return { ok: true, status: 200, json: async () => url.includes("monitor") ? monitor : url.includes("models") ? model :
    url.includes("drives") ? drives : software }
}
const page = { mode: "local", monitor: { snapshot: null, status: "idle", error: "" },
  model: { data: null, status: "idle", error: "" }, software: { data: null, status: "idle", error: "" },
  drives: { data: null, status: "idle", error: "", busy: false },
  unauthorized() { throw new Error("unexpected revocation") } }
for (const [name, method] of Object.entries(Home.methods)) page[name] = method.bind(page)
Home.created.call(page)
Home.mounted.call(page)
assert.equal(requested.length, 0, "hidden mount must not fetch")
document.hidden = false
listeners.get("visibilitychange")()
for (let i = 0; i < 12; i++) await Promise.resolve()
assert.deepEqual(new Set(requested), new Set(["./api/system/monitor", "./api/models/status", "./api/software/status?history=0", "./api/drives/stats?timezone=" + encodeURIComponent(Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC")]))
assert.equal(homeSummary(page.monitor, page.model, page.software).model.variant, "Small")
assert.equal(homeSummary(page.monitor, page.model, page.software).software.update, "None reported")
assert.equal(driveSummary(page.drives).totals.distance, "12.0 km")
let ignoredRequest = null
page.driveFeed.ignore = (routeId, ignored) => { ignoredRequest = { routeId, ignored }; return Promise.resolve() }
await page.ignoreDrive(driveSummary(page.drives).recent[0])
assert.deepEqual(ignoredRequest, { routeId: route.routeId, ignored: true })
document.hidden = true
listeners.get("visibilitychange")()
assert.equal(homeSummary(page.monitor, page.model, page.software).system.cpu, "Unavailable")
assert.equal(homeSummary(page.monitor, page.model, page.software).model.health, "Unavailable")
assert.equal(driveSummary(page.drives), null)
Home.beforeUnmount.call(page)
assert.equal(listeners.size, 0)

// Any revoked Home source ends the whole view; older replies from its peers
// cannot refill a card after sign-out.
const pending = []
let revoked = 0
globalThis.fetch = (url, options) => new Promise((resolve) => pending.push({ url, signal: options.signal, resolve }))
const revokedPage = { mode: "local", monitor: { snapshot: null, status: "idle", error: "" },
  model: { data: null, status: "idle", error: "" }, software: { data: null, status: "idle", error: "" },
  drives: { data: null, status: "idle", error: "", busy: false },
  unauthorized() { revoked++ } }
for (const [name, method] of Object.entries(Home.methods)) revokedPage[name] = method.bind(revokedPage)
Home.created.call(revokedPage)
document.hidden = false
Home.mounted.call(revokedPage)
assert.equal(pending.length, 4)
pending.find((entry) => entry.url.includes("software")).resolve({ ok: false, status: 401 })
for (let i = 0; i < 12; i++) await Promise.resolve()
assert.equal(revoked, 1)
assert.equal(pending.every((entry) => entry.signal.aborted), true)
pending.find((entry) => entry.url.includes("monitor")).resolve({ ok: true, status: 200, json: async () => monitor })
pending.find((entry) => entry.url.includes("models")).resolve({ ok: true, status: 200, json: async () => model })
pending.find((entry) => entry.url.includes("drives")).resolve({ ok: true, status: 200, json: async () => drives })
for (let i = 0; i < 12; i++) await Promise.resolve()
assert.equal(homeSummary(revokedPage.monitor, revokedPage.model, revokedPage.software).system.cpu, "Unavailable")
assert.equal(homeSummary(revokedPage.monitor, revokedPage.model, revokedPage.software).model.health, "Unavailable")
assert.equal(driveSummary(revokedPage.drives), null)
assert.equal(revoked, 1)
Home.beforeUnmount.call(revokedPage)
assert.equal(listeners.size, 0)
globalThis.document = priorDocument
globalThis.fetch = priorFetch

const app = readFileSync(new URL("../web/js/app.js", import.meta.url), "utf8")
const router = readFileSync(new URL("../web/js/router.js", import.meta.url), "utf8")
assert.match(app, /<Home v-else-if="route\.path === '\/'"/)
assert.match(router, /\|\| "\/"/)
assert.match(Home.template, /Last drive/)
assert.match(Home.template, /Distance per day/)
assert.match(Home.template, /Ignore drive stats/)
assert.match(Home.template, /Recorded models/)
assert.match(Home.template, /gx-home__model-tag/)
assert.match(Home.template, /\{\{ day\.distance \}\}/)
const manifest = JSON.parse(readFileSync(new URL("../web/manifest.webmanifest", import.meta.url), "utf8"))
assert.equal(manifest.start_url, "./")
assert.equal(manifest.scope, "./")
assert.ok(manifest.icons.every(icon => icon.purpose === "any"))
for (const base of ["https://example.test/", "https://example.test/device-slug/"]) {
  assert.equal(new URL(manifest.icons[0].src, base).pathname, new URL("./assets/galaxy-icon.svg", base).pathname)
}
const icon = readFileSync(new URL("../web/assets/galaxy-icon.svg", import.meta.url), "utf8")
assert.match(icon, /fill="#fff"/)
assert.match(icon, /fill="#6f42a6"/)
assert.doesNotMatch(icon, /<image|<script/)
console.log("Galaxy Home source, lifecycle, and route checks passed")

assert.doesNotMatch(Home.template, /drive analysis failure|failure.reason|analysisError/)

const batterySummary = snapshot => homeSummary({status:'current',snapshot}, {status:'idle'}, {status:'idle'}).system.battery
assert.equal(batterySummary({batteryVoltage:12.64}),'12.6 V')
assert.equal(batterySummary({batteryVoltage:14.42}),'14.4 V')
assert.equal(batterySummary({}),'Unavailable')
assert.equal(absent.system.battery,'Unavailable')
assert.match(Home.template,/12 V battery/)
