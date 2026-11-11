import assert from 'node:assert/strict'
import { compile } from '../web/vendor/vue/vue.esm-browser.js'
import { PhoneNotifications, validPhoneNotifications } from '../web/js/phone-notifications.js'
import { StarPortPage } from '../web/js/starport.js'

const app = { id: 'com.whatsapp', name: 'WhatsApp', source: 'android', messaging: true, allowed: true, chosen: false }
const value = { enabled: true, hideWhileMoving: false, apps: [app] }
assert.equal(validPhoneNotifications(value), true)
assert.equal(validPhoneNotifications({ ...value, enabled: 'yes' }), false)
assert.equal(validPhoneNotifications({ ...value, apps: [{ ...app, source: 'web' }] }), false)
assert.equal(validPhoneNotifications({ ...value, apps: [{ ...app, id: 'bad id' }] }), false)
assert.equal(validPhoneNotifications({ ...value, apps: Array(65).fill(app) }), false)
assert.equal(validPhoneNotifications({ ...value, apps: [{ ...app, sender: 'Alice' }] }), true, 'extra keys are ignored, never rendered')
assert.equal(validPhoneNotifications(null), false)
compile(PhoneNotifications.template, { decodeEntities: text => text })
for (const text of ['Show phone notifications on the car screen', 'Hide while moving', 'never stored', 'Share notifications', 'Phone notifications'])
  assert.ok(PhoneNotifications.template.includes(text), text)
assert.ok(StarPortPage.components.PhoneNotifications === PhoneNotifications)
const page = StarPortPage.template
assert.ok(page.indexOf('<PhoneNotifications v-if="setup"') > 0 && page.indexOf('<PhoneNotifications') < page.indexOf('Still having trouble?'))

globalThis.document = { hidden: false, addEventListener() {}, removeEventListener() {} }
function instance(mode = 'local') {
  const vm = { ...PhoneNotifications.data(), mode, unauthorized() {} }
  for (const [name, fn] of Object.entries(PhoneNotifications.methods)) vm[name] = fn.bind(vm)
  return vm
}

let requests = []
globalThis.fetch = async (url, options = {}) => {
  requests.push({ url, method: options.method || 'GET', body: options.body })
  return { ok: true, json: async () => options.method === 'POST' ? { ...value, hideWhileMoving: true } : value }
}
const sample = instance('sample')
sample.start()
assert.equal(requests.length, 0)
assert.equal(sample.active, false)

const vm = instance()
vm.start()
await new Promise(done => setTimeout(done, 0))
assert.deepEqual(vm.snapshot, value)
assert.equal(requests[0].url, './api/starport/notifications')
assert.notEqual(vm.timer, null)
assert.equal(await vm.save({ hideWhileMoving: true }), true)
assert.equal(vm.snapshot.hideWhileMoving, true)
assert.deepEqual(JSON.parse(requests.at(-1).body), { hideWhileMoving: true })
vm.stop()
assert.equal(vm.timer, null)

globalThis.document.hidden = true
const hidden = instance()
requests = []
hidden.start()
assert.equal(requests.length, 0, 'no polling while the page is hidden')
globalThis.document.hidden = false

const broken = instance()
globalThis.fetch = async () => ({ ok: true, json: async () => ({ enabled: true }) })
broken.active = true
await broken.refresh()
assert.equal(broken.snapshot, null)
assert.match(broken.error, /Invalid phone notification settings/)
broken.stop()

let auth = 0
const expired = instance()
expired.unauthorized = () => auth++
globalThis.fetch = async () => ({ ok: false, status: 401, json: async () => ({ error: 'Sign in to Galaxy' }) })
expired.active = true
await expired.refresh()
assert.equal(auth, 1)
assert.equal(expired.active, false)
assert.equal(expired.timer, null)
console.log('Phone notifications: schema, template, StarPort placement, sample/hidden gating, save and authentication passed')
