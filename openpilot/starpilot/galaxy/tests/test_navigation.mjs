import assert from "node:assert/strict"
import { NavigationPage, NavigationClient, validNavigation, validSearchResult, searchUuid, routePath } from "../web/js/navigation.js"

const place = { id: "place", name: "Library", latitude: 41, longitude: -88 }
const snapshot = (revision = "a") => ({ enabled: true, isMetric: false, hasKey: true, destination: null, favorites: [],
  status: "noDestination", instruction: null, route: [], revision })
const response = (value, status = 200) => ({ ok: status === 200, status, json: async () => value })
const flush = async () => { for (let i = 0; i < 8; i++) await Promise.resolve() }

assert(validNavigation(snapshot()))
for (const key of ['avoidTolls', 'avoidHighways', 'avoidFerries']) {
  for (const value of [true, false]) assert(validNavigation({ ...snapshot(), [key]: value }))
  for (const value of ['true', 1, null, []]) assert(!validNavigation({ ...snapshot(), [key]: value }))
}
assert(!validNavigation({ ...snapshot(), route: [{ latitude: 90, longitude: Infinity }] }))
for (const value of ['backup', 'comma', 'phone']) assert(validNavigation({ ...snapshot(), gpsSource: value }))
assert(!validNavigation({ ...snapshot(), gpsSource: 'tunnel' }))
const located = { latitude: 1, longitude: 2, validForMs: 1000 }
assert(validNavigation({ ...snapshot(), location: { ...located, source: 'phone' } }))
assert(!validNavigation({ ...snapshot(), location: { ...located, source: 'satellite' } }))
assert(!validNavigation({ ...snapshot(), destination: { ...place, latitude: 120 } }))
assert(!validNavigation({ ...snapshot(), instruction: { text: "Turn left" } }))
for (const network of ['online', 'offline', 'unknown']) assert(validNavigation({ ...snapshot(), network }))
assert(!validNavigation({ ...snapshot(), network: 'bad' }))
assert.equal(routePath([]), "")
assert.equal(routePath([place]), "")
assert.match(routePath([place, { latitude: 41.1, longitude: -88.1 }]), /^M[\d.,]+ L[\d.,]+$/)
assert(!routePath([{ latitude: 0, longitude: 179.9 }, { latitude: 0, longitude: -179.9 }]).includes("NaN"))

assert.equal(NavigationPage.computed.statusLabel.call({ data: { ...snapshot(), status: "waitingForLocation", route: [place] } }),
  "Route preview from last saved location")
assert.equal(NavigationPage.computed.statusLabel.call({ data: { ...snapshot(), status: "waitingForLocation" } }), "Waiting for GPS")
assert.equal(NavigationPage.computed.statusLabel.call({ data: { ...snapshot(), status: "routing", location: { ...place, lastKnown: true } } }),
  "Finding a route from last saved location…")

{
  const state = { mode: 'local', ready: true, stale: false, data: { ...snapshot(), network: 'online',
    location: { ...place, validForMs: 0, lastKnown: true } } }
  assert.equal(NavigationPage.computed.usingSavedLocation.call(state), true)
  assert.equal(NavigationPage.computed.offline.call(state), false)
  state.data.network = 'offline'
  assert.equal(NavigationPage.computed.usingSavedLocation.call(state), false)
  assert.equal(NavigationPage.computed.offline.call(state), true)
  for (const network of ['unknown', undefined]) {
    state.data.network = network
    assert.equal(NavigationPage.computed.offline.call(state), false)
    assert.equal(NavigationPage.computed.usingSavedLocation.call(state), true)
  }
  state.data.network = 'offline'
  state.stale = true
  assert.equal(NavigationPage.computed.offline.call(state), false)
  const paths = [], page = { tab: 'route', go: path => paths.push(path) }
  NavigationPage.methods.selectTab.call(page, 'maps')
  assert.equal(page.tab, 'maps')
  assert.deepEqual(paths, ['/navigation/maps'])
  NavigationPage.methods.selectTab.call(page, 'route')
  assert.equal(page.tab, 'route')
  assert.equal(paths.at(-1), '/navigation')
  assert.equal(NavigationPage.data.call({initialTab:'maps'}).tab, 'maps')
  NavigationPage.watch.initialTab.call(page, 'maps')
  assert.equal(page.tab, 'maps')
}

function setup() {
  const requests = [], states = [], timers = new Map()
  let sequence = 0, unauthorized = 0
  const client = new NavigationClient({ publish: (state) => states.push(state), unauthorized: () => unauthorized++,
    later: (fn, ms) => { const key = ++sequence; timers.set(key, { fn, ms }); return key },
    cancelTimer: (key) => timers.delete(key),
    fetcher: (url, options) => new Promise((resolve, reject) => requests.push({ url, options, resolve, reject })),
  })
  return { client, requests, states, timers, unauthorized: () => unauthorized }
}

{
  const { client, requests, states } = setup()
  const start = client.start()
  requests[0].resolve(response(snapshot()))
  await start
  const search = client.search("Library")
  requests[1].resolve(response({ results: [place] }))
  await search
  assert.equal(states.at(-1).results[0].name, "Library")
  const action = client.action("select", { destination: place })
  assert.deepEqual(JSON.parse(requests[2].options.body), { action: "select", revision: "a", destination: place })
  requests[2].resolve(response({ ...snapshot("b"), destination: place }))
  await action
  assert.equal(states.at(-1).data.revision, "b")
  assert.deepEqual(states.at(-1).results, [])
  client.stop()
}

{
  const { client, requests, states } = setup()
  const start = client.start()
  requests[0].resolve(response(snapshot()))
  await start
  const poll = client.load()
  const action = client.action("clear")
  assert(requests[1].options.signal.aborted)
  requests[2].resolve(response(snapshot("new")))
  await action
  requests[1].resolve(response(snapshot("old")))
  await poll
  assert.equal(states.at(-1).data.revision, "new")
  client.stop()
}

{
  const { client, requests, states } = setup()
  const start = client.start()
  requests[0].resolve(response(snapshot()))
  await start
  const poll = client.load()
  requests[1].reject(new Error("offline"))
  await poll
  assert.equal(states.at(-1).data.revision, "a")
  assert.equal(states.at(-1).stale, true)
  await client.action("clear")
  assert.equal(requests.length, 2)
  const refresh = client.load()
  requests[2].resolve(response(snapshot("fresh")))
  await refresh
  assert.equal(states.at(-1).stale, false)
  client.stop()
}

{
  const { client, requests, states, unauthorized } = setup()
  const start = client.start()
  requests[0].resolve(response(snapshot()))
  await start
  const action = client.action("configure", { patch: { token: "pk.secret" } })
  requests[1].resolve(response({ error: "Sign in" }, 401))
  await action
  assert.equal(unauthorized(), 1)
  assert.equal(states.at(-1).data, null)
  assert(!JSON.stringify(states).includes("pk.secret"))
}

{
  const { client, requests, states } = setup()
  const start = client.start()
  client.stop()
  requests[0].resolve(response(snapshot()))
  await start
  await flush()
  assert.equal(states.at(-1).data, null)
  assert(!client.active)
}

console.log("Navigation client: validation, route geometry, mutation ordering, transient loss, authentication and teardown passed")

{
  const { client, requests, states } = setup()
  const start = client.start()
  requests[0].resolve(response(snapshot()))
  await start
  const search = client.search("Coffee")
  const sent = JSON.parse(requests[1].options.body)
  const poi = { id: "poi/id", name: "Coffee Shop", description: "123 Main St, Springfield", searchId: sent.searchId }
  assert(validSearchResult(poi))
  assert(!validSearchResult({ ...poi, searchId: "bad" }))
  assert(!validSearchResult({ ...poi, description: "x".repeat(513) }))
  assert.notEqual(sent.searchId, sent.clientId)
  requests[1].resolve(response({ results: [poi] }))
  await search
  const choose = client.choose(poi)
  assert.deepEqual(JSON.parse(requests[2].options.body), { action: "selectPlace", revision: "a", id: "poi/id", searchId: sent.searchId })
  requests[2].resolve(response({ ...snapshot("poi"), destination: { ...place, address: "123 Main St" }, recents: [place] }))
  await choose
  assert.equal(states.at(-1).data.destination.address, "123 Main St")
  assert.equal(states.at(-1).data.recents[0].name, "Library")
  assert.deepEqual(states.at(-1).results, [])
  client.stop()
  assert.equal(JSON.parse(requests.at(-1).options.body).action, "cancelSearch")
}
console.log("POI suggestions: validation, selected-only retrieval, destination state and cancellation passed")

{
  // Saved and recent places travel without their ids; suggestions are saved through the search that found them.
  const { client, requests } = setup()
  const start = client.start()
  requests[0].resolve(response({ ...snapshot(), favorites: [{ ...place, label: "home" }], recents: [{ ...place, id: "other", address: "1 Main" }] }))
  await start
  client.choose({ ...place, label: "home" })
  assert.deepEqual(JSON.parse(requests[1].options.body), { action: "select", revision: "a", destination: { name: "Library", latitude: 41, longitude: -88 } })
  requests[1].resolve(response(snapshot("b"))); await flush()
  client.save({ id: "poi/id", name: "Coffee", searchId: "0".repeat(36) })
  assert.deepEqual(JSON.parse(requests[2].options.body), { action: "favoritePlace", revision: "b", id: "poi/id", searchId: "0".repeat(36) })
  requests[2].resolve(response(snapshot("c"))); await flush()
  client.save({ ...place, address: "1 Main" })
  assert.deepEqual(JSON.parse(requests[3].options.body).destination, { name: "Library", latitude: 41, longitude: -88, address: "1 Main" })
  client.stop()
  assert(!validNavigation({ ...snapshot(), recents: [{ ...place, label: "gym" }] }))
  assert(!validNavigation({ ...snapshot(), recents: Array(11).fill(place) }))
}
console.log("Favorites and recents: choose, save and validation passed")

{
  // Polls send the held route key; an unchanged reply reuses the lines already drawn.
  const { client, requests, states } = setup()
  const line = [{ latitude: 41, longitude: -88 }, { latitude: 41.1, longitude: -88.1 }]
  const guiding = { ...snapshot(), destination: place, status: "routing", route: line, routeKey: "0123456789abcdef" }
  const start = client.start()
  assert.equal(requests[0].url, "./api/navigation/status")
  requests[0].resolve(response(guiding))
  await start
  const held = states.at(-1).data
  assert(Object.isFrozen(held.route))
  const poll = client.load()
  assert.equal(requests[1].url, "./api/navigation/status?routeKey=0123456789abcdef")
  const { route, ...light } = guiding
  requests[1].resolve(response({ ...light, routeUnchanged: true }))
  await poll
  assert.equal(states.at(-1).data, held, "an identical reply keeps the same object, so nothing re-renders")
  const moved = client.load()
  requests[2].resolve(response({ ...light, routeUnchanged: true, status: "guiding" }))
  await moved
  assert.equal(states.at(-1).data.status, "guiding")
  assert.equal(states.at(-1).data.route, held.route)
  const lost = client.load()
  requests[3].resolve(response({ ...light, routeKey: "fedcba9876543210", routeUnchanged: true }))
  await lost
  assert.deepEqual(states.at(-1).data.route, [])
  const again = client.load()
  assert.equal(requests[4].url, "./api/navigation/status", "a reply for lines the page does not hold asks for them again")
  requests[4].resolve(response(guiding)); await again
  client.stop()
}
console.log("Route key polling: light replies, reuse, and recovery passed")

{
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, "crypto")
  const realCrypto = globalThis.crypto
  Object.defineProperty(globalThis, "crypto", { configurable: true, value: {
    getRandomValues: (bytes) => realCrypto.getRandomValues(bytes),
  } })
  try {
    assert.match(searchUuid(), /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/)
    const { client } = setup()
    assert.match(client.clientId, /^[0-9a-f-]{36}$/)
  } finally { Object.defineProperty(globalThis, "crypto", descriptor) }
}
console.log("Local HTTP search UUID fallback passed")

{
  const actions = []
  const page = { token: ' pk.example ', client: { action: async (...args) => actions.push(args) } }
  await NavigationPage.methods.saveKey.call(page, true)
  assert.equal(page.token, '')
  assert.deepEqual(actions[0], ['configure', { patch: { token: 'pk.example', enabled: true } }])
  page.token = 'pk.replacement'; await NavigationPage.methods.saveKey.call(page, false)
  assert.deepEqual(actions[1], ['configure', { patch: { token: 'pk.replacement' } }])
  assert.ok(NavigationPage.template.includes("selectTab('setup')"))
  assert.ok(NavigationPage.template.includes('Save key'))
  assert.ok(NavigationPage.template.includes('<BluetoothNotice v-if="bluetooth" feature="Map preview"'))
  assert.ok(NavigationPage.template.includes('<NavigationMap v-else-if="ready"'))
}

{
  // Shared text the comma can't pin down comes back as a 400 with the search to show; navigation turns on for it.
  const sent = [], states = []
  let off = true
  const answers = { share: (body) => body.text.includes("!3d") ? response({ ...snapshot("s"), enabled: true, destination: place, shared: place }) :
      body.text === "Atlantis" ? response({ error: "No map location found for Atlantis" }, 400) :
      response({ error: "Open StarPilot to search for Starbucks", query: "Starbucks" }, 400),
    configure: () => { off = false; return response({ ...snapshot("c"), enabled: true }) } }
  const client = new NavigationClient({ publish: (state) => states.push(state), later: () => 0, cancelTimer: () => {},
    fetcher: async (url, options) => {
      const body = options.body ? JSON.parse(options.body) : null
      sent.push({ url, body })
      if (url.includes("/search")) return response({ results: [place], provider: "google" })
      if (body?.action) return answers[body.action](body)
      return response({ ...snapshot(), enabled: !off })
    } })
  await client.start()
  const page = { client, query: "" }
  await NavigationPage.methods.openShared.call(page, "Starbucks")
  assert.deepEqual(sent.slice(1).map(({ url, body }) => body?.action || url), ["share", "configure", "./api/navigation/search"])
  assert.equal(page.query, "Starbucks")
  assert.equal(sent.at(-1).body.query, "Starbucks")
  const shown = states.at(-1)
  assert.deepEqual([shown.results, shown.resultsProvider, shown.error, shown.stale, shown.data.enabled], [[place], "google", "", false, true])
  sent.length = 0
  await NavigationPage.methods.openShared.call(page, "https://www.google.com/maps/place/Library/data=!3d41!4d-88")
  assert.deepEqual(sent.map(({ body }) => body?.action), ["share"])
  assert.equal(states.at(-1).data.destination.name, "Library")
  sent.length = 0
  page.query = ""
  await NavigationPage.methods.openShared.call(page, "Atlantis")
  assert.deepEqual([sent.length, page.query, states.at(-1).error], [1, "", "No map location found for Atlantis"])
  assert.equal(client.refusal, null)
  client.stop()
  assert.equal(NavigationPage.computed.googlePlaces.call({ data: { routeProvider: "google", hasGoogleKey: true } }), true)
  assert.equal(NavigationPage.computed.googlePlaces.call({ data: { routeProvider: "mapbox", hasGoogleKey: true } }), false)
  const tests = NavigationPage.computed.googleTests.call({ googleTest: { ok: true, message: "map", routes: { ok: false, failure: "disabled", message: "off" },
    places: { ok: false, failure: "budget", message: "used up" } } })
  assert.deepEqual(tests.map((row) => [row.name, row.ok, row.missing, row.link?.href]), [["Map Tiles API", true, false, undefined],
    ["Routes API", false, true, "https://console.cloud.google.com/apis/library/routes.googleapis.com"], ["Places API (New)", false, false, undefined]])
  assert.equal(NavigationPage.computed.googleWorking.call({ googleTests: tests }), "2 of 3 APIs working")
  assert.ok(NavigationPage.template.includes("Google isn't used until the Mapbox key above is added too."))
  console.log("Shared places: comma lookup, 400 query search, navigation turned on, Google source and key test summary passed")
}
await import("./test_navigation_map.mjs")
