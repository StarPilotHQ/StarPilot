import assert from 'node:assert/strict'
import { compile } from '../web/vendor/vue/vue.esm-browser.js'
import { CompanionUpdates, validCompanion } from '../web/js/companion-updates.js'
globalThis.window = {}
const value = { protocol: 1, enabled: false, connected: false, active: false, appAvailable: true, updateAvailable: false,
  bytes: 0, shippedVersion: '1.0.0', state: 'disabled' }
assert.equal(validCompanion(value), true)
assert.equal(validCompanion({ ...value, bytes: -1 }), false)
compile(CompanionUpdates.template, { decodeEntities: v => v })
const vm = { ...CompanionUpdates.data(), mode: 'sample', eligible: true, unauthorized() {}, $emit() {} }
for (const [key, fn] of Object.entries(CompanionUpdates.methods)) vm[key] = fn.bind(vm)
let requests = 0, resolve
globalThis.fetch = () => { requests++; return new Promise(done => { resolve = done }) }
vm.start()
assert.equal(requests, 0)
vm.mode = 'local'; vm.alive = true
const reading = vm.refresh()
vm.stop()
resolve({ ok: true, json: async () => value })
await reading
assert.equal(vm.status, null)
let auth = 0
vm.unauthorized = () => auth++
globalThis.fetch = async () => ({ ok: false, status: 401, json: async () => ({ error: 'Sign in' }) })
vm.alive = true
await vm.refresh()
assert.equal(auth, 1)
assert.equal(vm.alive, false)
console.log('Companion UI: schema, template, preview exclusion, late response and authentication passed')
