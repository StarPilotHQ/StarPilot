import assert from 'node:assert/strict'
import { compile } from '../web/vendor/vue/vue.esm-browser.js'
import { OUTCOME_TEXT, PhoneNotifications, ago, validPhoneNotifications } from '../web/js/phone-notifications.js'
import { StarPortPage } from '../web/js/starport.js'
import { SettingsPage, SETTINGS_SECTIONS } from '../web/js/settings.js'

const app = { id: 'com.whatsapp', name: 'WhatsApp', source: 'android', messaging: true, allowed: true, chosen: false }
const value = { enabled: true, hideWhileMoving: false, apps: [app] }
assert.equal(validPhoneNotifications(value), true)
assert.equal(validPhoneNotifications({ ...value, enabled: 'yes' }), false)
assert.equal(validPhoneNotifications({ ...value, apps: [{ ...app, source: 'web' }] }), false)
assert.equal(validPhoneNotifications({ ...value, apps: [{ ...app, id: 'bad id' }] }), false)
assert.equal(validPhoneNotifications({ ...value, apps: Array(65).fill(app) }), false)
assert.equal(validPhoneNotifications({ ...value, apps: [{ ...app, sender: 'Alice' }] }), true, 'extra keys are ignored, never rendered')
assert.equal(validPhoneNotifications(null), false)
const live = { ...value, recent: [{ app: 'com.whatsapp', appName: 'WhatsApp', source: 'android', outcome: 'appOff', ageSeconds: 4 }],
  showing: { appName: 'WhatsApp', remainingSeconds: 6, sender: 'Alice', text: 'Hi' }, contentVisible: true }
assert.equal(validPhoneNotifications(live), true)
assert.equal(validPhoneNotifications({ ...live, recent: [{ ...live.recent[0], outcome: 'boom' }] }), false)
assert.equal(validPhoneNotifications({ ...live, showing: { appName: 'X', remainingSeconds: 'soon' } }), false)
assert.equal(validPhoneNotifications({ ...live, showing: { appName: 'X', remainingSeconds: 2, text: 5 } }), false)
assert.equal(ago(5), '5 s ago')
assert.equal(ago(125), '2 min ago')
for (const outcome of ['shown', 'appOff', 'notMessage', 'tooOld', 'disabled']) assert.ok(OUTCOME_TEXT[outcome])
compile(PhoneNotifications.template, { decodeEntities: text => text })
for (const text of ['Show phone notifications', 'comma screen', 'Hide while moving', 'never stored', 'Share notifications', 'Phone notifications', 'Send test notification'])
  assert.ok(PhoneNotifications.template.includes(text), text)
assert.ok(SettingsPage.components.PhoneNotifications === PhoneNotifications)
assert.ok(SettingsPage.template.includes('<PhoneNotifications v-if="phoneOpen"'))
const phone = SETTINGS_SECTIONS.find(section => section.id === 'phone')
assert.equal(phone?.label, 'Phone')
assert.equal(SETTINGS_SECTIONS.at(-1).id, 'developer', 'Developer stays the last section')
assert.ok(!StarPortPage.components.PhoneNotifications && !StarPortPage.template.includes('<PhoneNotifications'))
const phoneOpen = (initialPage, id) => SettingsPage.computed.phoneOpen.call({ initialPage, activeSection: { id } })
assert.equal(phoneOpen('hub', 'phone'), true)
assert.equal(phoneOpen('hub', 'visual'), false)
assert.equal(phoneOpen('sounds', 'phone'), false)

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
assert.deepEqual(vm.snapshot, { recent: [], showing: null, contentVisible: false, ...value })
assert.equal(requests[0].url, './api/starport/notifications')
assert.notEqual(vm.timer, null)
assert.equal(await vm.save({ hideWhileMoving: true }), true)
assert.equal(vm.snapshot.hideWhileMoving, true)
vm.snapshot = { ...vm.snapshot, recent: live.recent, showing: live.showing, contentVisible: true }
vm.adopt(value)
assert.deepEqual(vm.snapshot.recent, live.recent, 'a settings-only reply keeps the live state')
assert.equal(vm.pollMs(), 1000)
vm.adopt({ ...value, recent: [], showing: null, contentVisible: false })
assert.equal(vm.snapshot.showing, null)
assert.equal(vm.pollMs(), 5000)
assert.deepEqual(JSON.parse(requests.at(-1).body), { hideWhileMoving: true })
vm.stop()
assert.equal(vm.timer, null)

globalThis.fetch = async (url, options = {}) => {
  requests.push({ url, method: options.method || 'GET', body: options.body })
  return { ok: true, json: async () => url.endsWith('/test') ? { ok: true, accepted: false, enabled: false, test: true } : value }
}
const tester = instance()
tester.active = true
assert.equal(await tester.sendTest(), false)
const testRequest = requests.find(request => request.url.endsWith('/test'))
assert.equal(testRequest?.method, 'POST')
assert.equal(requests.at(-1).url, './api/starport/notifications', 'a test refreshes the live view right away')
assert.match(tester.testResult, /Turn on Show phone notifications/)
assert.equal(tester.busy, false)
tester.stop()

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
console.log('Phone notifications: schema, template, Toggles placement, sample/hidden gating, save and authentication passed')
