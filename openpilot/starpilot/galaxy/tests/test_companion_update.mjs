import assert from 'node:assert/strict'
import { APP_DOWNLOADS } from '../web/js/app-downloads.js'
import { createRenderer, nextTick } from '../web/vendor/vue/vue.esm-browser.js'
import { CompanionUpdates, newerCompanionVersion } from '../web/js/companion-updates.js'

assert.equal(newerCompanionVersion('1.1.10', '1.1.9'), true)
assert.equal(newerCompanionVersion('1.2', '1.1.99'), true)
for (const [available, installed] of [['1.1.5', '1.1.5'], ['1.1.4', '1.1.5'], ['1.1', '1.1.0'], ['', '1.1.4'], ['1.1.5', ''], ['unknown', '1.1.4']]) {
  assert.equal(newerCompanionVersion(available, installed), false)
}
const storage = new Map()
globalThis.localStorage = { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value) }
globalThis.window = { StarPilotApp: { version: () => '1.1.4' } }
globalThis.document = { activeElement: null }
Object.defineProperty(globalThis, 'navigator', { configurable: true, value: { userAgent: 'Android' } })
const status = { protocol: 1, enabled: false, connected: false, active: false, appAvailable: true, updateAvailable: false,
  bytes: 0, shippedVersion: '1.1.5', version: null, state: 'disabled' }
let requests = 0, installs = 0
globalThis.fetch = async () => { requests++; return { ok: true, json: async () => status } }
const node = (type, text = '') => ({ type, text, props: {}, children: [], parent: null,
  addEventListener() {}, removeEventListener() {} })
const renderer = createRenderer({
  createElement: type => node(type), createText: text => node('text', text), createComment: text => node('comment', text),
  setText: (item, text) => { item.text = text }, setElementText: (item, text) => { item.text = text; item.children = [] },
  patchProp: (item, key, before, after) => { item.props[key] = after },
  insert: (item, parent, anchor = null) => {
    if (item.parent) item.parent.children.splice(item.parent.children.indexOf(item), 1)
    item.parent = parent
    const index = parent.children.indexOf(anchor)
    parent.children.splice(index < 0 ? parent.children.length : index, 0, item)
  },
  remove: item => { const index = item.parent?.children.indexOf(item); if (index >= 0) item.parent.children.splice(index, 1) },
  parentNode: item => item.parent,
  nextSibling: item => item.parent?.children[item.parent.children.indexOf(item) + 1] ?? null,
})
const all = item => [item, ...item.children.flatMap(all)]
const text = item => [item.text, ...item.children.map(text)].join(' ')
const banner = host => all(host).find(item => item.type === 'div' && text(item).includes('Android app update available') && item.props.role === 'status')
async function mount() {
  const host = node('host')
  const app = renderer.createApp(CompanionUpdates, { mode: 'local', unauthorized() { assert.fail('Unexpected authentication failure') } })
  app.config.compilerOptions.decodeEntities = raw => raw
  const vm = app.mount(host)
  await new Promise(resolve => setImmediate(resolve))
  await nextTick()
  return { host, app, vm }
}
let mounted = await mount()
try {
  assert.ok(banner(mounted.host))
  assert.match(text(mounted.host), /Version 1.1.5 is available.*Installed: 1.1.4/s)
  assert.ok(all(mounted.host).some(item => item.type === 'a' && item.props.href === APP_DOWNLOADS.android.href))
  mounted.vm.dismissUpdate()
  await nextTick()
  assert.equal(banner(mounted.host), undefined)
} finally { mounted.app.unmount() }
window.StarPilotApp.installUpdate = () => installs++
mounted = await mount()
try {
  assert.equal(banner(mounted.host), undefined, 'Dismissal persists across remounts')
  mounted.vm.status = { ...status, shippedVersion: '1.1.6' }
  await nextTick()
  all(banner(mounted.host)).find(item => item.type === 'button' && text(item).includes('Update app')).props.onClick()
  assert.equal(installs, 1)
  for (const override of [{ shippedVersion: '1.1.3', updateAvailable: true }, { appAvailable: false }]) {
    mounted.vm.status = { ...status, ...override }
    await nextTick()
    assert.equal(banner(mounted.host), undefined, 'Never offer a downgrade or missing artifact')
  }
} finally { mounted.app.unmount() }
for (const [agent, bridge] of [['Chrome', undefined], ['iPhone', { version: () => '1.1.4' }], ['Android', { version: () => '1.1.4', distribution: () => 'play' }]]) {
  navigator.userAgent = agent
  window.StarPilotApp = bridge
  const before = requests
  mounted = await mount()
  try {
    assert.equal(requests, before, 'Browsers, iOS and Play apps do not poll the legacy APK endpoint')
    assert.equal(banner(mounted.host), undefined)
    assert.doesNotMatch(text(mounted.host), /MB today|disabled|Connections/)
  } finally { mounted.app.unmount() }
}
console.log('Companion updates: version ordering, native install, persistent dismissal, platform and distribution isolation passed')
