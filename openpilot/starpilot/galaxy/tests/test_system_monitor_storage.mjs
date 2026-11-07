import assert from 'node:assert/strict'
import { createRenderer, h, nextTick } from '../web/vendor/vue/vue.esm-browser.js'
import { Home } from '../web/js/home.js'
import { SystemMonitor } from '../web/js/system-monitor.js'

const node = (type, text = '') => ({ type, text, props: {}, children: [], parent: null, style: {},
  addEventListener() {}, removeEventListener() {} })
const renderer = createRenderer({
  createElement: type => node(type), createText: text => node('text', text), createComment: text => node('comment', text),
  setText: (item, text) => { item.text = text }, setElementText: (item, text) => { item.text = text },
  patchProp: (item, key, before, after) => { item.props[key] = after },
  insert(item, parent, anchor = null) {
    if (item.parent) { const index = item.parent.children.indexOf(item); if (index >= 0) item.parent.children.splice(index, 1) }
    item.parent = parent
    const index = parent.children.indexOf(anchor)
    parent.children.splice(index < 0 ? parent.children.length : index, 0, item)
  },
  remove(item) { const index = item.parent?.children.indexOf(item); if (index >= 0) item.parent.children.splice(index, 1) },
  parentNode: item => item.parent, nextSibling(item) { const siblings = item.parent?.children || []; return siblings[siblings.indexOf(item) + 1] || null },
})
const text = item => (item.type === 'comment' ? '' : item.text) + item.children.map(text).join('')

const snapshot = { source: 'Local system', deviceType: 'tici', sampledAt: 1_700_000_000, cpuPercent: 10,
  cores: [], processes: [], processCount: 0, uptimeSeconds: 300, vitals: {}, memory: { percent: 25 },
  storage: { usedGiB: 20.6, totalGiB: 29.8, freeGiB: 7.6 },
  recordingStorage: { usedGiB: 598, totalGiB: 916, freeGiB: 273 } }
const noNetwork = { render() { return null } }
for (const component of [Home, SystemMonitor]) {
  // Render the actual templates and computed values. Polling is covered by
  // feed tests; only unrelated child controls and network hooks are replaced.
  const View = { ...component, created: undefined, mounted: undefined, beforeUnmount: undefined,
    components: { ...component.components, LocalAccess: noNetwork, WeatherVitals: noNetwork,
      GalaxySelect: { render() { return h('select', null, this.$slots.default?.()) } } },
    data: () => ({ ...component.data(), status: 'current', snapshot,
      monitor: { status: 'current', snapshot } }),
  }
  const host = node('host')
  const app = renderer.createApp(View, { mode: 'local', unauthorized() {}, go() {} })
  app.config.compilerOptions.decodeEntities = raw => raw.replaceAll('&amp;', '&')
  const vm = app.mount(host)
  const update = async next => { vm.snapshot = next; vm.monitor = { status: 'current', snapshot: next }; await nextTick() }
  await nextTick()
  assert.match(text(host), /Internal storage/)
  assert.match(text(host), /Recording drive/)
  assert.match(text(host), /273(?:\.0)? GiB free/)
  assert.match(text(host), /7\.6 GiB free/)
  await update({ ...snapshot, recordingStorage: { ...snapshot.recordingStorage, freeGiB: 0 } })
  assert.match(text(host), /0(?:\.0)? GiB free/)
  await update({ ...snapshot, recordingStorage: null })
  assert.doesNotMatch(text(host), /Recording drive/)
  assert.match(text(host), /Internal storage/)
  for (const deviceType of ['tizi', 'mici', 'pc', undefined]) {
    await update({ ...snapshot, deviceType })
    assert.doesNotMatch(text(host), /Recording drive|Internal storage|GiB free/)
    assert.match(text(host), /Storage/)
  }
  await update({ ...snapshot, recordingStorage: { usedGiB: null, totalGiB: null, freeGiB: null } })
  assert.match(text(host), /Recording drive/)
  assert.doesNotMatch(text(host), /273(?:\.0)? GiB free/)
  app.unmount()
}
console.log('Actual Home and System Monitor Vue rendering: C3 dual drive/free, absent drive, non-C3 exclusion and unavailable readings passed')
