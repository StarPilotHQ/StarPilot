import assert from "node:assert/strict"
import { SpotifyFeed, SpotifyPage, archivePreflight, importProblem, installChecks, releaseInfo, serviceLabel,
  positionNow, formatTime, muteLabel, DOWNLOAD_PAGE } from "../web/js/spotify.js"

const setup = {
  enabled: true, parked: true, keyPresent: true, audioReady: true,
  release: { installed: true, version: "1.3.9.32", expires: "2027-01-14T00:00:00+00:00", daysLeft: 80, level: "ok", expired: false },
  preferences: { deviceName: "StarPilot", cacheMb: 256, initialVolume: 100, output: "bluetooth", outputAddress: "AA:BB:CC:DD:EE:FF", outputName: "Car" },
  devices: [{ address: "AA:BB:CC:DD:EE:FF", name: "Car", connected: true }], service: null,
  import: { state: "idle" }, maxUploadBytes: 64 * 1024 * 1024, archiveName: "soloist_release_arm64.tar.gz", cacheChoices: [100, 256],
  links: { download: DOWNLOAD_PAGE, key: "https://developer.spotify.com/dashboard/soloist", help: "https://developer.spotify.com/" },
}

assert.equal(archivePreflight({ name: "soloist_release_arm64.tar.gz" }), "")
assert.equal(archivePreflight({ name: "soloist_release_arm64.tgz" }), "")
assert.match(archivePreflight({ name: "soloist_release_x86_64.tar.gz" }), /different computer/)
assert.match(archivePreflight({ name: "soloist_release_arm64.tar" }), /unpacked the download/)
assert.match(archivePreflight({ name: "soloist" }), /unpacked player/)
assert.match(archivePreflight({ name: "photo.jpg" }), /Choose the soloist_release_arm64/)

assert.match(importProblem({ state: "failed", code: "NOT_OFFICIAL" }), /doesn’t match/)
assert.match(importProblem({ state: "failed", code: "OFFLINE" }), /internet/)
assert.equal(importProblem({ state: "done" }), "")
assert.deepEqual(installChecks({ state: "running", stage: "verifying" }).map((c) => c.status), ["done", "done", "active", "pending", "pending"])
assert.deepEqual(installChecks({ state: "failed", stage: "verifying", code: "NOT_OFFICIAL" }).map((c) => c.status),
  ["done", "done", "failed", "pending", "pending"])
assert.ok(installChecks({ state: "done" }).every((c) => c.status === "done"))

assert.equal(releaseInfo({ installed: false }).level, "none")
assert.equal(releaseInfo({ ...setup.release, level: "soon", daysLeft: 1 }, "en-US").label, `Version 1.3.9.32 · expires in 1 day (${new Date(setup.release.expires).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })})`)
assert.equal(releaseInfo({ ...setup.release, expired: true }, "en-US").level, "expired")
assert.match(releaseInfo(setup.release, "en-US").label, /expires on .*2027/)
assert.match(releaseInfo({ ...setup.release, level: "soon", daysLeft: 0 }).label, /expires within a day/)
assert.match(releaseInfo({ ...setup.release, expired: true }).label, /expired on/)
assert.match(releaseInfo({ installed: true }).label, /expiry date unavailable/)

assert.equal(serviceLabel({ enabled: false }), "Off")
assert.equal(serviceLabel({ enabled: true, service: { state: "waiting_output", message: "Waiting for Car to connect over Bluetooth." } }),
  "Waiting for Car to connect over Bluetooth.")
assert.equal(serviceLabel({ enabled: true, service: { state: "waiting", message: "" } }), "Signing in to Spotify…")
assert.match(serviceLabel({ enabled: true, service: { state: "ready", message: "", outputWaiting: "Car" } }),
  /Pick this device.*Audio waits for Car\./)
assert.equal(serviceLabel({ enabled: true, service: { state: "waiting_network", message: "Waiting for the comma’s network…" } }),
  "Waiting for the comma’s network…")
assert.equal(serviceLabel({ enabled: true, service: { state: "retrying", message: "Couldn’t sign in to Spotify yet. Trying again…" } }),
  "Couldn’t sign in to Spotify yet. Trying again…")
assert.equal(serviceLabel({ enabled: true, service: { state: "ready", player: { status: "paused" } } }), "Paused")

assert.equal(positionNow({ ms: 1000, at: 10_000, speed: 1 }, 5000, 12_000), 3000)
assert.equal(positionNow({ ms: 1000, at: 10_000, speed: 0 }, 5000, 99_000), 1000)
assert.equal(positionNow({ ms: 4000, at: 10_000, speed: 1 }, 5000, 99_000), 5000)
assert.equal(formatTime(65_000), "1:05")

// The key is only ever sent; status responses never carry it, and the page has no field that shows it.
assert.match(SpotifyPage.template, /type="password" v-model="keyValue"/)
assert.match(SpotifyPage.template, /rel="noopener noreferrer"/)
assert.doesNotMatch(SpotifyPage.template, /setup\.key\b/)

const published = []
const requests = []
const feed = new SpotifyFeed({
  publish: (value) => published.push(value),
  later: () => 1, cancelTimer: () => { },
  fetcher: async (path, options) => {
    requests.push([path, options?.method || "GET", options?.body])
    return { ok: true, status: 200, json: async () => setup }
  },
})
await feed.start()
assert.deepEqual(feed.setup, setup)
await feed.post("./api/spotify/key", { key: "sk_live_0123456789abcdef" })
assert.equal(requests.at(-2)[0], "./api/spotify/key")
assert.equal(feed.error, "")
await feed.control("volume", { volume: 40 })
assert.deepEqual(JSON.parse(requests.at(-1)[2]), { action: "volume", args: { volume: 40 } })
feed.stop()
console.log("spotify ok")

// Phone setup does not require a Soloist release or key, but does require a selected phone and output.
assert.equal(SpotifyPage.computed.setupDone.call({ phoneSource: true, outputChosen: true,
  setup: { preferences: { phoneAddress: "11:22:33:44:55:66" } } }), true)
assert.equal(SpotifyPage.computed.setupDone.call({ phoneSource: true, outputChosen: true,
  setup: { preferences: { phoneAddress: "" } } }), false)
const sourceRequests = []
const musicPage = { feed: { post: (...args) => sourceRequests.push(args) } }
SpotifyPage.methods.chooseSource.call(musicPage, { target: { value: "phone" } })
SpotifyPage.methods.choosePhone.call(musicPage, { target: { value: "11:22:33:44:55:66" } })
assert.deepEqual(sourceRequests, [
  ["./api/spotify/preferences", { source: "phone" }],
  ["./api/spotify/preferences", { phoneAddress: "11:22:33:44:55:66" }],
])

// Installing Soloist needs the LAN or tunnel: the page blocks it when the comma says so, and never sends the upload.
assert.equal(SpotifyPage.computed.installBlocked.call({ setup: { ...setup, uploadAllowed: false } }), true)
assert.equal(SpotifyPage.computed.installBlocked.call({ setup }), false)
assert.match(SpotifyPage.computed.uploadReason.call({ installBlocked: true }), /local network or the Galaxy tunnel/)
assert.match(SpotifyPage.template, /Installing Spotify needs your local network or the Galaxy tunnel\. Phone audio works over Bluetooth/)
const blockedRequests = []
const blocked = new SpotifyFeed({
  publish: () => { }, later: () => 1, cancelTimer: () => { },
  fetcher: async (path, options) => {
    blockedRequests.push([path, options?.method || "GET"])
    return { ok: true, status: 200, json: async () => ({ ...setup, uploadAllowed: false }) }
  },
  uploader: async (path) => { blockedRequests.push([path, "UPLOAD"]); return { ok: true, status: 202, json: async () => ({}) } },
})
await blocked.start()
assert.equal(await blocked.upload({ name: "soloist_release_arm64.tar.gz", size: 1024 }), false)
assert.ok(!blockedRequests.some(([, method]) => method === "UPLOAD" || method === "POST"))
blocked.stop()

// Over the phone Bluetooth link the page polls much less often.
const delays = []
for (const [overBluetooth, expected] of [[false, 4000], [true, 15000]]) {
  const slow = new SpotifyFeed({
    publish: () => { }, later: (_fn, ms) => { delays.push(ms); return 1 }, cancelTimer: () => { }, overBluetooth: () => overBluetooth,
    fetcher: async () => ({ ok: true, status: 200, json: async () => setup }),
  })
  delays.length = 0
  await slow.start()
  assert.ok(delays.includes(expected), `${overBluetooth}: ${delays}`)
  slow.stop()
}
assert.match(SpotifyPage.template, /Pair phone/)

// Pause/skip smoothing sliders: live-tunable, never gated on Park.
assert.equal(muteLabel(0), "Off")
assert.equal(muteLabel(0.25), "250 ms")
assert.equal(muteLabel(undefined), "Off")
assert.match(SpotifyPage.template, /aria-label="Pause smoothing"[^>]*:disabled="busy"/)
assert.match(SpotifyPage.template, /aria-label="Skip smoothing"[^>]*:disabled="busy"/)
assert.doesNotMatch(SpotifyPage.template, /Pause smoothing[^>]*parked/)
{
  const posts = []
  const page = { pauseDraft: 0.9, skipDraft: 0.9, feed: { post: (path, body) => { posts.push([path, body]); return Promise.resolve() } } }
  SpotifyPage.methods.commitMute.call(page, "pause", { target: { value: "0.3" } })
  SpotifyPage.methods.commitMute.call(page, "skip", { target: { value: "0" } })
  SpotifyPage.methods.commitMute.call(page, "skip", { target: { value: "not a number" } })
  assert.deepEqual(posts, [["./api/spotify/preferences", { pauseMute: 0.3 }], ["./api/spotify/preferences", { skipMute: 0 }]])
  assert.equal(page.pauseDraft, null)
  assert.equal(page.skipDraft, null)
  assert.equal(SpotifyPage.computed.pauseMuteLabel.call({ pauseDraft: 0.6, setup: { preferences: { pauseMute: 0.5 } } }), "600 ms")
  assert.equal(SpotifyPage.computed.skipMuteLabel.call({ skipDraft: null, setup: { preferences: { skipMute: 0.25 } } }), "250 ms")
}

assert.equal(serviceLabel({ enabled: true, service: { state: "ready", player: { status: "paused" }, outputWaiting: "Car" } }), "Paused Audio waits for Car.")

// Audio outputs require the receiving-music profile; the phone source list stays separate.
{
  const devices = [
    { address: "AA:BB:CC:DD:EE:01", name: "Car", connected: true, audioSink: true },
    { address: "AA:BB:CC:DD:EE:02", name: "Speaker", connected: false, audioSink: true },
    { address: "AA:BB:CC:DD:EE:03", name: "Phone", connected: true, audioSink: false },
    { address: "AA:BB:CC:DD:EE:04", name: "Radar detector", connected: true },
  ]
  const deviceChoices = SpotifyPage.computed.deviceChoices.call({ setup: { devices } })
  const outputs = SpotifyPage.computed.outputChoices.call({ deviceChoices })
  assert.deepEqual(outputs.map(choice => choice.label), ["Car", "Speaker (Not Connected)"])
  assert.equal(deviceChoices.length, 4)
}
