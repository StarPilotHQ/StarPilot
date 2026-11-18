import assert from 'node:assert/strict'
import { createRenderer, nextTick } from '../web/vendor/vue/vue.esm-browser.js'
import { GalaxyPage } from '../web/js/galaxy.js'
import { RemoteAccess } from '../web/js/remote-access.js'
import { APP_DOWNLOADS } from '../web/js/app-downloads.js'

Object.defineProperty(globalThis, 'navigator', { configurable: true, value: { userAgent: 'Android', clipboard: { writeText: async () => {} } } })
globalThis.window = { confirm: () => true }
globalThis.document = { activeElement: null }
let paired = true, requests = [], unauthorized = 0
const link = 'https://galaxy.firestar.link/device'
globalThis.fetch = async (url, options) => {
  requests.push([url, options])
  if (url.endsWith('/pair')) paired = true
  if (url.endsWith('/unpair')) paired = false
  return { ok: true, json: async () => url.endsWith('/session') ? { paired, sessionToken: 'test%20key' } :
    { paired, url: paired ? link : '', tunnelClientAvailable: true } }
}
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
const flush = async () => { await new Promise(resolve => setImmediate(resolve)); await nextTick() }
const host = node('host')
const app = renderer.createApp({ ...GalaxyPage, components: { ...GalaxyPage.components,
  GalaxyHotspot: { template: '<div>Hotspot settings</div>' },
  InstallApp: { props: ['url'], template: '<div class="web-link">{{ url }}</div>' },
} }, { mode: 'local', unauthorized() { unauthorized++ } })
app.config.compilerOptions.decodeEntities = value => value.replaceAll('&amp;', '&')
const vm = app.mount(host)
await flush()
try {
  const nodes = all(host), links = nodes.filter(item => item.type === 'a')
  for (const download of Object.values(APP_DOWNLOADS)) {
    assert.equal(links.filter(item => item.props.href === download.href).length, 1, 'Each store link appears exactly once')
  }
  assert.match(text(host), /Set up remote access.*Get the app/s)
  assert.doesNotMatch(text(host), /Local access/)
  const stepList = nodes.find(item => item.type === 'ol' && item.props.class === 'gx-steps')
  assert.equal(stepList.children.filter(item => item.type === 'li' && item.props.class?.includes('gx-step')).length, 2)
  assert.equal(nodes.filter(item => item.props.class === 'gx-step__head').length, 2)
  assert.doesNotMatch(text(host), /MB today|Status:|Connections|offline app version/)
  assert.equal(requests.length, 1, 'A browser reads remote pairing status without polling Android internet-sharing status')
  assert.equal(requests[0][0], './api/galaxy/status')
  assert.equal(vm.url, link, 'Remote link reaches the web installer')
  const steps = vm.$.subTree.children.find(child => child.props?.class === 'gx-steps')
  const remote = steps.children.find(child => child.type === RemoteAccess).component.proxy
  await remote.showAppKey()
  await nextTick()
  assert.match(text(host), /test key/)
  await remote.unpair()
  await nextTick()
  assert.equal(vm.url, '')
  assert.equal(remote.appKey, '')
  assert.equal(remote.keyVisible, false)
  remote.password = 'new-password'
  await remote.pair()
  await nextTick()
  assert.equal(vm.url, link)
  assert.equal(remote.password, '')
  assert.equal(unauthorized, 0)
} finally { app.unmount() }

// A secret response arriving after unmount must never restore the key.
let resolve
const state = { ...RemoteAccess.data(), mode: 'local', paired: true, url: link, unauthorized() {}, $emit() {} }
for (const [key, method] of Object.entries(RemoteAccess.methods)) state[key] = method.bind(state)
globalThis.fetch = () => new Promise(done => { resolve = done })
const pending = state.showAppKey()
RemoteAccess.beforeUnmount.call(state)
resolve({ ok: true, json: async () => ({ paired: true, sessionToken: 'stale-secret' }) })
await pending
assert.equal(state.appKey, '')
assert.equal(state.keyVisible, false)
console.log('Galaxy access: unique store links, browser isolation, remote pairing/link propagation and secret cleanup passed')
