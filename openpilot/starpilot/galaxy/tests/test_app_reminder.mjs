import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { APP_DOWNLOADS as downloads } from '../web/js/app-downloads.js'
import { appDownloadPlatform, APP_DOWNLOADS, appReminderPreference, reminderHidden, reminderDismissed,
  HIDE_APP_REMINDER, DISMISSED_APP_REMINDER, AppDownloadReminder } from '../web/js/app-reminder.js'

const iphone = { navigator: { userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X)' } }
const android = { navigator: { userAgent: 'Mozilla/5.0 (Linux; Android 15; Pixel 9)' } }
assert.equal(appDownloadPlatform(iphone), 'ios')
assert.equal(appDownloadPlatform(android), 'android')
assert.equal(appDownloadPlatform({ navigator: { userAgent: 'Macintosh', platform: 'MacIntel', maxTouchPoints: 5 } }), 'ios')
for (const userAgent of ['Macintosh Safari', 'Windows Chrome', 'Linux x86_64 Firefox', '']) {
  assert.equal(appDownloadPlatform({ navigator: { userAgent } }), null)
}
assert.equal(appDownloadPlatform({ ...android, StarPilotApp: {} }), null)
assert.equal(appDownloadPlatform({ ...iphone, webkit: { messageHandlers: { galaxyDiagnostics: {} } } }), null)
assert.equal(appDownloadPlatform({ ...iphone, webkit: {} }), 'ios', 'Safari itself must not be mistaken for the native app')
assert.equal(APP_DOWNLOADS, downloads, 'Reminders use the shared store metadata')
const values = new Map()
const target = { localStorage: { getItem: k => values.get(k), setItem: (k, v) => values.set(k, v) },
  sessionStorage: { getItem: k => values.get(k), setItem: (k, v) => values.set(k, v) } }
assert.equal(reminderHidden(target), false)
assert.equal(appReminderPreference.set(true, target), true)
assert.equal(values.get(HIDE_APP_REMINDER), '1')
assert.equal(reminderHidden(target), true, 'permanent choice must survive component remounts')
assert.equal(appReminderPreference.set(false, target), true)
assert.equal(reminderHidden(target), false)
assert.equal(reminderDismissed(target), false)
target.sessionStorage.setItem(DISMISSED_APP_REMINDER, '1')
assert.equal(reminderDismissed(target), true)
const denied = { localStorage: { setItem() { throw Error('Denied') } } }
assert.equal(appReminderPreference.set(true, denied), false)
assert.equal(appReminderPreference.hidden, false, 'failed persistence must not pretend the toggle was saved')
assert.ok(appReminderPreference.error)
assert.equal(reminderHidden({}), false)
assert.equal(reminderDismissed({}), false)
const context = { download: APP_DOWNLOADS.ios, pref: { hidden: false }, dismissed: false }
assert.match(AppDownloadReminder.template, /The most seamless way to connect to your StarPilot via Bluetooth, Wi-Fi, or over the network\./)
assert.ok(AppDownloadReminder.computed.visible.call(context))
context.pref.hidden = true
assert.equal(AppDownloadReminder.computed.visible.call(context), false)
const dismissal = { dismissed: false, pref: { set: hidden => appReminderPreference.set(hidden, target) } }
AppDownloadReminder.methods.dismiss.call(dismissal)
assert.equal(dismissal.dismissed, true)
assert.equal(reminderHidden(target), true, 'dismissing must persist across browser sessions')
values.delete(DISMISSED_APP_REMINDER)
assert.equal(reminderDismissed(target), false)
assert.equal(reminderHidden(target), true, 'ending the session must not bring the prompt back')
assert.equal(appReminderPreference.set(false, target), true, 'browser preferences can enable reminders again')
const later = { dismissed: false }
const originalSessionStorage = globalThis.sessionStorage
try {
  globalThis.sessionStorage = target.sessionStorage
  AppDownloadReminder.methods.showLater.call(later)
  assert.equal(later.dismissed, true)
  assert.equal(reminderDismissed(target), true, 'show later dismisses for the tab session')
  assert.equal(reminderHidden(target), false, 'show later must not disable future reminders')
  values.delete(DISMISSED_APP_REMINDER)
  assert.equal(reminderDismissed(target), false, 'a new session allows the reminder again')
} finally {
  if (originalSessionStorage === undefined) delete globalThis.sessionStorage
  else globalThis.sessionStorage = originalSessionStorage
}
const failedDismissal = { dismissed: false, pref: { set: hidden => appReminderPreference.set(hidden, denied) } }
AppDownloadReminder.methods.dismiss.call(failedDismissal)
assert.equal(failedDismissal.dismissed, true, 'storage failure must not prevent closing the prompt')
assert.equal(appReminderPreference.hidden, false)
assert.match(AppDownloadReminder.template, /Don't Remind Me/)
assert.match(AppDownloadReminder.template, /@click="showLater">Not Now/)
assert.doesNotMatch(AppDownloadReminder.template, /download.name/)
context.pref.hidden = false
context.dismissed = true
assert.equal(AppDownloadReminder.computed.visible.call(context), false)
const app = readFileSync(new URL('../web/js/app.js', import.meta.url), 'utf8')
assert.match(app, /<AppDownloadReminder v-if="route.path === '\/' && !state.loading && !state.updateRestarting/)
const settings = readFileSync(new URL('../web/js/settings.js', import.meta.url), 'utf8')
assert.match(settings, /<AppReminderSettings v-if="state.data\?\.page === 'browser_preferences'"/)
console.log('App reminder platform, native exclusion, persistence and browser preferences wiring passed')
