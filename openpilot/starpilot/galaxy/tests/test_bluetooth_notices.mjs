import assert from 'node:assert/strict'
import { createRenderer, nextTick } from '../web/vendor/vue/vue.esm-browser.js'
import { BluetoothNotice, bluetoothConnection } from '../web/js/bluetooth-notice.js'
import { LocalHistoryFeed, LocalRecordingsPage } from '../web/js/record-history.js'
import { CamerasPage, CameraSnapshotFeed, LiveCameraPreview } from '../web/js/cameras.js'
import { PipPage } from '../web/js/pip.js'
import { VasmPage } from '../web/js/vasm.js'
import { NavigationPage } from '../web/js/navigation.js'
import { OfflineRoadMapsPanel } from '../web/js/offline-road-maps.js'
import { StarPortPage } from '../web/js/starport.js'

globalThis.window = {}
assert.equal(bluetoothConnection(), false)
let connections = 0, requests = 0, timers = 0
window.StarPilotApp = { bluetooth: () => true, connectionOptions: () => connections++ }
assert.equal(bluetoothConnection(), true)
globalThis.document = { hidden: false, activeElement: null, addEventListener() {}, removeEventListener() {} }
globalThis.fetch = () => { requests++; throw new Error('Blocked feature attempted a request') }

const hostNode = (type, text = '') => ({ type, text, props: {}, children: [], parent: null, addEventListener() {}, removeEventListener() {} })
const renderer = createRenderer({
  createElement: type => hostNode(type), createText: text => hostNode('text', text), createComment: text => hostNode('comment', text),
  setText: (item, text) => { item.text = text }, setElementText: (item, text) => { item.text = text; item.children = [] },
  patchProp: (item, key, before, after) => { item.props[key] = after },
  insert: (item, parent, anchor = null) => {
    if (item.parent) item.parent.children.splice(item.parent.children.indexOf(item), 1)
    item.parent = parent
    const index = parent.children.indexOf(anchor)
    parent.children.splice(index < 0 ? parent.children.length : index, 0, item)
  },
  remove: item => { const index = item.parent?.children.indexOf(item); if (index >= 0) item.parent.children.splice(index, 1) },
  parentNode: item => item.parent, nextSibling: item => item.parent?.children[item.parent.children.indexOf(item) + 1] ?? null,
})
const all = item => [item, ...item.children.flatMap(all)]
const text = item => [item.text, ...item.children.map(text)].join(' ')
function mount(component, props = {}, overrides = {}) {
  const host = hostNode('host')
  const app = renderer.createApp({ ...component, ...overrides }, { mode: 'local', unauthorized() {}, go() {}, ...props })
  app.config.compilerOptions.decodeEntities = raw => raw.replaceAll('&amp;', '&')
  const vm = app.mount(host)
  return { host, vm, unmount: () => app.unmount() }
}
function notice(host, feature) {
  const notices = all(host).filter(item => item.props.class?.includes('gx-alert--bluetooth'))
  const found = notices.find(item => text(item).includes(feature))
  assert.ok(found, feature)
  assert.equal(found.props.role, 'status')
  assert.ok(found.props.class.includes('gx-alert--info'))
  assert.match(text(found), /Not available over Bluetooth.*Wi-Fi or LAN/s)
  assert.ok(!all(host).some(item => item.props.class?.includes('gx-alert--danger')), 'Expected limitation is never a red error')
  return found
}

for (const [component, feature] of [[LocalRecordingsPage, 'Local recordings and replay'], [CamerasPage, 'Camera snapshots']]) {
  const view = mount(component)
  try {
    const info = notice(view.host, feature)
    all(info).find(item => item.type === 'button').props.onClick()
    assert.ok(!all(view.host).some(item => item.type === 'img' || item.type === 'video'))
  } finally { view.unmount() }
}
assert.equal(connections, 2)
assert.equal(requests, 0)

const history = new LocalHistoryFeed({ publish() {}, later: () => { timers++; return 1 }, cancelTimer() {} })
history.start()
assert.equal(history.active, false)
const camera = new CameraSnapshotFeed({ publish() {}, later: () => { timers++; return 1 }, cancelTimer() {} })
await camera.capture('cabin')
const preview = new LiveCameraPreview({ publish() {}, redraw() {}, enabled: () => true,
  later: () => { timers++; return 1 }, cancelTimer() {} })
preview.start(); preview.refresh(); preview.stop()
assert.equal(timers, 0)
assert.equal(requests, 0, 'Neither initial loads nor retries send unsupported recording/camera requests')

// Render the real editor templates without starting their supported settings feeds.
const presentation = { created() {}, mounted() {}, updated() {}, beforeUnmount() {} }
for (const component of [PipPage, VasmPage]) {
  const view = mount(component, {}, presentation)
  try {
    notice(view.host, 'Cabin camera preview')
    const refresh = all(view.host).find(item => item.type === 'button' && text(item).includes('Take new snapshot'))
    assert.equal(refresh.props.disabled, true)
    assert.match(text(view.host), /Saved settings remain available/)
  } finally { view.unmount() }
}

let mapMounts = 0
const navigation = mount(NavigationPage, {}, { ...presentation,
  components: { ...NavigationPage.components, NavigationMap: { mounted() { mapMounts++ }, template: '<canvas />' } } })
try {
  notice(navigation.host, 'Map preview')
  assert.equal(mapMounts, 0)
  assert.doesNotMatch(text(navigation.host), /Set up navigation to show the map/)
  navigation.vm.error = 'Server rejected the destination'
  await nextTick()
  assert.ok(all(navigation.host).some(item => item.props.class?.includes('gx-alert--danger')), 'Real navigation errors stay red')
} finally { navigation.unmount() }

OfflineRoadMapsPanel.methods.ensureMap.call({ bluetooth: true, $refs: { canvas: {} }, hasKey: true })
let uploaded = 0
const aa = { bluetooth: true, packageFile: {}, busy: false, feed: { upload() { uploaded++ } } }
aa.uploadReason = StarPortPage.computed.uploadReason.call(aa)
assert.match(aa.uploadReason, /not available over Bluetooth/)
await StarPortPage.methods.upload.call(aa)
assert.equal(uploaded, 0)
assert.equal(requests, 0)

window.StarPilotApp.bluetooth = () => false
assert.equal(bluetoothConnection(), false)
{
  const { readFileSync } = await import('node:fs')
  const lines = readFileSync(new URL('../web/js/bluetooth.js', import.meta.url), 'utf8').split('\n')
  const chained = lines.findIndex((line) => line.trim() === '<template v-else-if="status">')
  assert.ok(chained > 0)
  assert.doesNotMatch(lines[chained - 1], /v-if="status"/, 'a notice before the device cards would swallow their v-else-if')
}
console.log('Bluetooth limits: blue notices, connection action, no blocked requests, supported settings and real errors verified')
