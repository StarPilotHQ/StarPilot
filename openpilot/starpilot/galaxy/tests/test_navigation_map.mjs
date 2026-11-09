import { remainingLocationLease } from '../web/js/navigation.js'
import assert from 'node:assert/strict'
import { project, unproject, relativeX, RasterMap } from '../web/js/navigation-map.js'
for (const zoom of [0, 2, 13, 18]) {
  const point = { latitude: 40, longitude: -90 }
  const roundTrip = unproject(...project(point, zoom), zoom)
  assert.ok(Math.abs(roundTrip.latitude - point.latitude) < 1e-10)
  assert.ok(Math.abs(roundTrip.longitude - point.longitude) < 1e-10)
}
assert.equal(relativeX(5, 1010, 1024), 1029)
assert.ok(project({ latitude: 90, longitude: 0 }, 2).every(Number.isFinite))
const frames = new Map(); let frameId = 0
const originalRaf = globalThis.requestAnimationFrame, originalCancelRaf = globalThis.cancelAnimationFrame
globalThis.requestAnimationFrame = fn => { frames.set(++frameId, fn); return frameId }
globalThis.cancelAnimationFrame = id => frames.delete(id)
const calls = [], listeners = new Map()
globalThis.ResizeObserver = class { observe() {} disconnect() { calls.push('disconnect') } }
const ctx = new Proxy({}, { get: (obj, key) => obj[key] || (() => {}) })
const canvas = { clientWidth: 400, clientHeight: 360, getContext: () => ctx, setPointerCapture() {}, addEventListener: (name, fn) => listeners.set(name, fn), removeEventListener: name => listeners.delete(name) }
const map = new RasterMap(canvas)
map.load = () => {}
map.update({ destination: { id: 'd', latitude: 40, longitude: -90 }, route: [{ latitude: 40, longitude: -90 }, { latitude: 40.01, longitude: -89.99 }], routeKey: '0123456789abcdef' }, false)
const fitted = map.zoom
assert.ok(fitted >= 10 && Number.isInteger(fitted) && map.follow === false, 'a new route is shown whole')
assert.ok(Math.abs(map.center.latitude - 40.005) < 1e-6 && Math.abs(map.center.longitude + 89.995) < 1e-6)
const before = project(map.center, map.zoom)
listeners.get('pointerdown')({ clientX: 100, clientY: 100, pointerId: 1 })
listeners.get('pointermove')({ clientX: 150, clientY: 120 })
assert.ok(Math.abs(project(map.center, map.zoom)[0] - before[0] + 50) < 1e-6)
listeners.get('pointerup')({})
const panned = { ...map.center }
map.update(map.data, false)
assert.deepEqual(map.center, panned)
map.changeZoom(1); assert.equal(map.zoom, fitted + 1)
map.recenter(); assert.equal(map.zoom, 2); assert.equal(map.follow, true)
map.close(); assert.equal(listeners.size, 0); assert.deepEqual(calls, ['disconnect'])
console.log('Map projection, dateline, pan, zoom, recenter, poll stability and cleanup passed')
const timers = new Map(); let next = 0
const savedSet = globalThis.setTimeout, savedClear = globalThis.clearTimeout
globalThis.setTimeout = (fn, ms) => { timers.set(++next, {fn, ms}); return next }
globalThis.clearTimeout = id => timers.delete(id)
const leased = new RasterMap(canvas); leased.load = () => {}
leased.update({ location: {latitude:40,longitude:-90,validForMs:100} }, false)
assert.equal(leased.locationFresh, true); assert.ok(Math.abs([...timers.values()][0].ms - 100) < 1e-6)
leased.update({ location: {latitude:41,longitude:-89,bearing:125,validForMs:200} }, false)
assert.equal(timers.size, 1); [...timers.values()][0].fn(); assert.equal(leased.locationFresh, false)
leased.update({ location: {latitude:41,longitude:-89,validForMs:0} }, false)
assert.equal(leased.locationFresh, false); assert.equal(timers.size, 0)
leased.update({location:null}, false); assert.equal(leased.lastLocation.latitude,41); assert.equal(leased.lastLocation.bearing,125);
leased.recenter(); assert.equal(leased.center.latitude,41); assert.equal(leased.locationFresh,false);
leased.close(); globalThis.setTimeout = savedSet; globalThis.clearTimeout = savedClear
console.log('GPS lease expiry, replacement, delayed-expired response and teardown passed')

assert.equal(remainingLocationLease(2000,100,600),1500)
assert.equal(remainingLocationLease(2000,100,2200),0)
const actualPerformance = globalThis.performance
let now = 1000
globalThis.performance = { now: () => now }
globalThis.setTimeout = (fn, ms) => { timers.set(++next, {fn, ms}); return next }
globalThis.clearTimeout = id => timers.delete(id)
const samePayload = { location: {latitude:40,longitude:-90,validForMs:100} }
const stableLease = new RasterMap(canvas); stableLease.load = () => {}
stableLease.update(samePayload, false); now += 60
stableLease.update(samePayload, true); assert.equal(stableLease.locationFresh, false)
stableLease.update(samePayload, false); assert.equal([...timers.values()][0].ms, 40)
now += 41; stableLease.update(samePayload, false); assert.equal(stableLease.locationFresh, false)
stableLease.close(); timers.clear()
const realFetch = globalThis.fetch
let requests = 0, message = ''
globalThis.fetch = (_, options) => { requests++; return new Promise((resolve, reject) => options.signal.addEventListener('abort', () => reject(new Error('aborted')))) }
const deadlineMap = new RasterMap(canvas, value => { message = value }); deadlineMap.draw = () => {}
const pendingLoad = deadlineMap.load('0/0/0')
const deadline = [...timers.values()].find(timer => timer.ms === 8000)
deadline.fn(); await pendingLoad
assert.equal(deadlineMap.failed.has('0/0/0'), true); assert.ok(message.includes('Reconnecting')); assert.equal(requests, 1)
deadlineMap.close(); timers.clear()
globalThis.fetch = realFetch; globalThis.performance = actualPerformance; globalThis.setTimeout = savedSet; globalThis.clearTimeout = savedClear
console.log('Same-payload stale recovery cannot extend GPS lease; tile deadline requires explicit Retry')

const restored = new RasterMap(canvas); restored.load = () => {}
restored.update({location:{latitude:42,longitude:-88,bearing:170,lastKnown:true,validForMs:0}}, false)
assert.equal(restored.center.latitude,42); assert.equal(restored.zoom,15); assert.equal(restored.locationFresh,false)
const savedCenter = {...restored.center}
restored.update({location:null},true); assert.deepEqual(restored.center,savedCenter)
restored.update({location:{latitude:43,longitude:-87,validForMs:100}},false)
assert.equal(restored.center.latitude,43); assert.equal(restored.lastLocation.latitude,43); assert.equal(restored.lastLocation.bearing,170); assert.equal(restored.locationFresh,true)
restored.close()
console.log('Durable last-known context initializes map without a live lease; reconnect and new live fix retain bearing')

// Completed requests share one browser frame, without a 200ms wait or repeated canvas allocation.
let widthWrites = 0, heightWrites = 0, width = 0, height = 0
const measuredCanvas = { ...canvas, get width() { return width }, set width(value) { width = value; widthWrites++ }, get height() { return height }, set height(value) { height = value; heightWrites++ } }
const measured = new RasterMap(measuredCanvas); measured.load = () => {}
measured.draw(); measured.draw(); assert.equal(widthWrites, 1); assert.equal(heightWrites, 1)
measuredCanvas.clientWidth = 401; measured.draw(); assert.equal(widthWrites, 2); assert.equal(heightWrites, 1)
const savedFetch = globalThis.fetch, savedBitmap = globalThis.createImageBitmap
let closedImages = 0
globalThis.fetch = async () => ({ ok: true, blob: async () => ({}) })
globalThis.createImageBitmap = async () => ({ close() { closedImages++ } })
await Promise.all([RasterMap.prototype.load.call(measured, '0/0/0'), RasterMap.prototype.load.call(measured, '1/0/0')])
assert.equal(frames.size, 1)
const paint = [...frames.values()][0]; frames.clear(); paint(); assert.equal(widthWrites, 2)
await RasterMap.prototype.load.call(measured, '1/1/0'); assert.equal(frames.size, 1)
measured.close(); assert.equal(frames.size, 0); assert.equal(closedImages, 3)
globalThis.fetch = savedFetch; globalThis.createImageBitmap = savedBitmap
globalThis.requestAnimationFrame = originalRaf; globalThis.cancelAnimationFrame = originalCancelRaf
console.log('Tile completions coalesce into one frame; canvas allocation changes only on resize; close cancels paint')

{
  // Following keeps the car centered until a pan; pinch and double-tap zoom around the fingers and settle on whole levels.
  const gestures = new Map()
  const surface = { clientWidth: 400, clientHeight: 400, getContext: () => ctx, getBoundingClientRect: () => ({ left: 0, top: 0 }),
    addEventListener: (name, fn) => gestures.set(name, fn), removeEventListener: name => gestures.delete(name) }
  globalThis.requestAnimationFrame = fn => { fn(); return 1 }
  globalThis.cancelAnimationFrame = () => {}
  const live = new RasterMap(surface); live.load = () => {}
  live.update({ location: { latitude: 40, longitude: -90, validForMs: 2000 } }, false)
  assert.deepEqual([live.center.latitude, live.zoom, live.follow], [40, 15, true])
  live.update({ location: { latitude: 40.001, longitude: -90, validForMs: 2000 } }, false)
  assert.equal(live.center.latitude, 40.001, 'following moves with the car')
  live.changeZoom(1); assert.equal(live.center.latitude, 40.001, 'zoom buttons keep the car centered while following')
  const anchor = live.pointAt(300, 100)
  gestures.get('pointerdown')({ pointerId: 1, clientX: 280, clientY: 100 })
  gestures.get('pointerdown')({ pointerId: 2, clientX: 320, clientY: 100 })
  gestures.get('pointermove')({ pointerId: 2, clientX: 360, clientY: 100 })
  assert.ok(Math.abs(live.zoom - 17) < 1e-9 && live.follow === false, 'spreading two fingers to twice the distance zooms one level')
  const held = live.pointAt(320, 100)
  assert.ok(Math.abs(held.latitude - anchor.latitude) < 1e-9 && Math.abs(held.longitude - anchor.longitude) < 1e-9, 'the pinched place stays under the fingers')
  gestures.get('pointermove')({ pointerId: 2, clientX: 370, clientY: 100 })
  gestures.get('pointerup')({ pointerId: 2, type: 'pointerup', clientX: 370, clientY: 100 })
  assert.ok(Number.isInteger(live.zoom), 'a pinch settles on a whole zoom level')
  gestures.get('pointerup')({ pointerId: 1, type: 'pointerup', clientX: 280, clientY: 100 })
  live.update({ location: { latitude: 41, longitude: -90, validForMs: 2000 } }, false)
  assert.notEqual(live.center.latitude, 41, 'a paused map does not jump back to the car')
  const zoom = live.zoom, target = live.pointAt(100, 300)
  for (let tap = 0; tap < 2; tap++) {
    gestures.get('pointerdown')({ pointerId: 3, clientX: 100, clientY: 300 })
    gestures.get('pointerup')({ pointerId: 3, type: 'pointerup', clientX: 100, clientY: 300 })
  }
  assert.equal(live.zoom, zoom + 1, 'double-tap zooms in')
  const kept = live.pointAt(100, 300)
  assert.ok(Math.abs(kept.latitude - target.latitude) < 1e-9 && Math.abs(kept.longitude - target.longitude) < 1e-9)
  live.recenter()
  assert.deepEqual([live.center.latitude, live.zoom, live.follow], [41, 15, true])
  live.close()
  globalThis.requestAnimationFrame = originalRaf; globalThis.cancelAnimationFrame = originalCancelRaf
}
console.log('Follow, pinch and double-tap zoom passed')

// A live Galaxy theme change discards the old imagery without moving the map.
{
  const savedObserver = globalThis.MutationObserver, savedDocument = globalThis.document
  const savedFetch = globalThis.fetch, savedBitmap = globalThis.createImageBitmap
  let observeTheme, disconnected = false, oldClosed = false, lateClosed = false, finishOld
  globalThis.document = { documentElement: { dataset: { theme: 'light' } } }
  globalThis.MutationObserver = class {
    constructor(callback) { observeTheme = callback }
    observe(target, options) { assert.deepEqual(options.attributeFilter, ['data-theme']) }
    disconnect() { disconnected = true }
  }
  globalThis.requestAnimationFrame = fn => { frames.set(++frameId, fn); return frameId }
  globalThis.cancelAnimationFrame = id => frames.delete(id)
  const themed = new RasterMap(canvas)
  themed.draw = () => {}
  themed.center = { latitude: 40, longitude: -90 }; themed.zoom = 12
  themed.tiles.set('old', { close() { oldClosed = true } })
  const urls = [], signals = []
  globalThis.fetch = (url, options) => {
    urls.push(url); signals.push(options.signal)
    return new Promise(resolve => { finishOld = resolve })
  }
  globalThis.createImageBitmap = async () => ({ close() { lateClosed = true } })
  const oldRequest = themed.load('0/0/0')
  document.documentElement.dataset.theme = 'dark'; observeTheme()
  assert.equal(themed.theme, 'dark'); assert.equal(signals[0].aborted, true)
  assert.equal(oldClosed, true); assert.equal(themed.tiles.size, 0)
  assert.deepEqual(themed.center, {latitude:40, longitude:-90}); assert.equal(themed.zoom, 12)
  finishOld({ ok: true, blob: async () => ({}) }); await oldRequest
  assert.equal(lateClosed, true); assert.equal(themed.tiles.size, 0, 'late light tiles cannot enter the dark cache')
  globalThis.fetch = async url => { urls.push(url); return { ok: true, blob: async () => ({}) } }
  await themed.load('0/0/0')
  assert.ok(urls[0].endsWith('?theme=light')); assert.ok(urls[1].endsWith('?theme=dark'))
  themed.close(); assert.equal(disconnected, true)
  globalThis.MutationObserver = savedObserver; globalThis.document = savedDocument
  globalThis.fetch = savedFetch; globalThis.createImageBitmap = savedBitmap
  globalThis.requestAnimationFrame = originalRaf; globalThis.cancelAnimationFrame = originalCancelRaf
}
console.log('Live map theme changes: correct requests, cache disposal, late-response isolation, camera preservation and observer cleanup passed')

{
  // Switching mapProvider between Mapbox and Google clears cached tiles, appends provider=google, and crops 256px parent tiles.
  const savedFetch = globalThis.fetch, savedBitmap = globalThis.createImageBitmap
  globalThis.requestAnimationFrame = fn => { frames.set(++frameId, fn); return frameId }
  globalThis.cancelAnimationFrame = id => frames.delete(id)
  const urls = []
  let closed = 0
  globalThis.fetch = async url => { urls.push(url); return { ok: true, blob: async () => ({}) } }
  globalThis.createImageBitmap = async () => ({ width: 256, close() { closed++ } })
  const switched = new RasterMap(canvas)
  switched.draw = () => {}
  switched.update({ mapProvider: 'mapbox', hasGoogleKey: true }, false)
  await switched.load('1/0/0')
  assert.ok(urls.at(-1).endsWith('1/0/0.png?theme=dark'))
  assert.equal(switched.tiles.size, 1)
  const crop = switched.placeholder(2, 1, 1)
  assert.deepEqual(crop.slice(1), [128, 128, 128], '256px Google/custom tiles crop using their actual bitmap width')
  switched.update({ mapProvider: 'google', hasGoogleKey: true }, false)
  assert.equal(switched.tiles.size, 0, 'switching map provider clears cached tiles from the previous provider')
  assert.equal(closed, 1)
  await switched.load('1/0/0')
  assert.ok(urls.at(-1).endsWith('1/0/0.png?theme=dark&provider=google'))
  switched.update({ mapProvider: 'google', hasGoogleKey: true, googleMap: { failure: 'Quota reached' } }, false)
  assert.equal(switched.provider, 'mapbox', 'a Google map failure falls back to Mapbox tiles')
  switched.close()
  globalThis.fetch = savedFetch; globalThis.createImageBitmap = savedBitmap
  globalThis.requestAnimationFrame = originalRaf; globalThis.cancelAnimationFrame = originalCancelRaf
}
console.log('Map provider switching: Google tile URL, cache invalidation, fallback, and 256px placeholder cropping passed')
