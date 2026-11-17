import assert from 'node:assert/strict'
import { compile } from '../web/vendor/vue/vue.esm-browser.js'
import { NetworksPage, validNetworks } from '../web/js/networks.js'
const network = (uuid, name, extra = {}) => ({ uuid, name, autoJoin: true, whileDriving: true, connected: false, metered: 'Auto', signal: null, ...extra })
const first = '11111111-1111-4111-8111-111111111111', second = '22222222-2222-4222-8222-222222222222'
const value = { version: 1, editable: true, prefer: 'wifi', pauseHotspot: true, cellular: { enabled: true, connected: true, type: 'lte', operator: 'Carrier' },
  link: { link: 'wifi', changedAt: 0, changes: 0 }, networks: [network(first, 'Home', { connected: true }), network(second, 'Phone', { signal: 60 })] }
assert.equal(validNetworks(value), true)
assert.equal(validNetworks({ ...value, prefer: 'both' }), false)
assert.equal(validNetworks({ ...value, networks: [{ ...value.networks[0], metered: 'Sometimes' }] }), false)
compile(NetworksPage.template, { decodeEntities: text => text })
assert.ok(NetworksPage.template.includes('<GalaxyHotspot'))
assert.ok(NetworksPage.template.includes('<GxDialog v-if="joinOpen"'))
for (const label of ['Cellular data', 'Prefer', 'Join while StarPort is on', 'Pause Galaxy hotspot while joining Wi-Fi'])
  assert.ok(NetworksPage.template.includes(label), label)
function instance() {
  const vm = { ...NetworksPage.data(), mode: 'local', unauthorized() {} }
  for (const [name, fn] of Object.entries(NetworksPage.methods)) vm[name] = fn.bind(vm)
  return vm
}
const vm = instance()
vm.active = true
vm.adopt(value)
assert.equal(vm.signalText(value.networks[0]), 'Connected')
assert.equal(vm.signalText(value.networks[1]), 'In range, signal 60%')
assert.equal(vm.networkSummary(value.networks[0]), 'Connected · Auto-join')
assert.equal(vm.isOpen(value.networks[0]), false)
vm.toggleNetwork(first)
assert.equal(vm.isOpen(value.networks[0]), true)
vm.toggleNetwork(first)
assert.equal(vm.isOpen(value.networks[0]), false)
const sent = []
globalThis.fetch = async (_url, options) => { sent.push(options?.body ? JSON.parse(options.body) : null); return { ok: true, json: async () => value } }
await vm.move(1, -1)
assert.deepEqual(sent.at(-1), { action: 'order', uuids: [second, first] })
assert.equal(vm.move(0, -1), undefined)
await vm.setNetwork(value.networks[1], 'whileDriving', false)
assert.deepEqual(sent.at(-1), { action: 'network', uuid: second, setting: 'whileDriving', value: false })
await vm.openScan()
assert.equal(vm.adding, true)
assert.deepEqual(sent.at(-1), { action: 'scan' })
await vm.selectAvailable({ ssid: 'Cafe', signal: 75, security: 'open' })
assert.deepEqual(sent.at(-1), { action: 'join', ssid: 'Cafe', password: '' })
vm.selectAvailable({ ssid: 'Office', signal: 80, security: 'wpa' })
assert.equal(vm.joiningSsid, 'Office')
vm.joinPassword = 'my-wifi-password'
await vm.submitJoin()
assert.deepEqual(sent.at(-1), { action: 'join', ssid: 'Office', password: 'my-wifi-password', hidden: false })
assert.equal(vm.joiningSsid, '')
const sentBeforeForget = sent.length
assert.equal(await vm.forgetSaved(value.networks[0]), false)
assert.equal(sent.length, sentBeforeForget)
await vm.forgetSaved(value.networks[0])
assert.deepEqual(sent.at(-1), { action: 'forget', uuid: value.networks[0].uuid })
assert.equal(vm.forgetUuid, null)
assert.equal(validNetworks({ ...value, lastJoin: { ssid: 'Office', state: 'joining', error: '' } }), true)
assert.equal(validNetworks({ ...value, lastJoin: { ssid: 'Office', state: 'maybe', error: '' } }), false)
vm.adopt({ ...value, lastJoin: { ssid: 'Office', state: 'joining', error: '' } })
vm.joining = vm.snapshot.lastJoin.state === 'joining'
const sentWhileJoining = sent.length
assert.equal(await vm.send({ action: 'scan' }), false)
assert.equal(sent.length, sentWhileJoining)
vm.joining = false
vm.adopt({ ...value, editable: false })
const before = sent.length
assert.equal(await vm.send({ action: 'cellular', value: false }), false)
assert.equal(sent.length, before)
vm.stop()
