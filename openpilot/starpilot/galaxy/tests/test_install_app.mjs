import { APP_DOWNLOADS, AppStoreLinks } from '../web/js/app-downloads.js'
import assert from 'node:assert/strict'
import { installHelp, InstallApp, rememberInstallPrompt, isIosDevice, isAndroidDevice } from '../web/js/install-app.js'
assert.match(installHelp({ secure: true, agent: 'iPhone' }), /Share.*Add to Home Screen/)
assert.match(installHelp({ secure: false, agent: 'Android' }), /web app.*https:\/\/galaxy\.firestar\.link/)
assert.match(installHelp({ secure: true, agent: 'Android' }), /Install app/)
assert.equal(APP_DOWNLOADS.android.href, 'https://play.google.com/store/apps/details?id=com.starpilot.galaxy')
assert.match(installHelp({ secure: true, agent: 'Chrome' }), /Install app/)
const events = new Map()
let modeListener
const mode = { matches: false, addEventListener: (_, fn) => { modeListener = fn }, removeEventListener: () => {} }
globalThis.window = { isSecureContext: true, matchMedia: () => mode,
  addEventListener: (name, fn) => events.set(name, fn), removeEventListener: (name) => events.delete(name) }
Object.defineProperty(globalThis, 'navigator', { configurable: true, value: { userAgent: 'Chrome' } })
const state = { ...InstallApp.data() }
InstallApp.mounted.call(state)
assert.equal(state.installed, false)
let prevented = 0, prompted = 0
events.get('beforeinstallprompt')({ preventDefault: () => prevented++, prompt: async () => prompted++, userChoice: Promise.resolve({ outcome: 'dismissed' }) })
assert.equal(prevented, 1)
await InstallApp.methods.installWeb.call(state)
assert.equal(prompted, 1)
assert.equal(state.prompt, null)
assert.equal(state.installed, false)
await InstallApp.methods.installWeb.call(state)
assert.equal(state.message, '')
assert.match(InstallApp.template, /v-else-if="url" class="gx-btn" :href="url"/)
window.location = { hostname: 'galaxy.firestar.link' }
assert.equal(InstallApp.computed.onPublicGalaxy(), true)
events.get('appinstalled')()
assert.equal(state.installed, true)
mode.matches = true; modeListener()
assert.equal(state.installed, true)
InstallApp.beforeUnmount.call(state)
assert.equal(events.size, 0)
console.log('install app prompt and platform guidance passed')

// Browser can offer installation before the tool is mounted.
const early = {preventDefault(){}, prompt: async () => prompted++, userChoice: Promise.resolve({outcome:'dismissed'})}
rememberInstallPrompt(early)
const late = {...InstallApp.data()}
InstallApp.mounted.call(late)
assert.equal(late.prompt, early)
await InstallApp.methods.installWeb.call(late)
assert.equal(prompted, 2)
InstallApp.beforeUnmount.call(late)

// Store metadata lives outside the web installer.
assert.equal(APP_DOWNLOADS.ios.href, 'https://apps.apple.com/ca/app/starpilot-bt/id6821039119')
assert.match(AppStoreLinks.template, /app.href/)
assert.doesNotMatch(InstallApp.template, /Google Play|App Store|offline app|app\.apk/)
const android = { ...InstallApp.data() }
// Choosing the web app on Android always keeps web-install guidance.
const webHelp = InstallApp.methods.installHelp.call(android)
assert.match(webHelp, /Install app/)
assert.doesNotMatch(webHelp, /download the Android app/i)

// iOS never sees the Android offline install; the web app install remains.
assert.equal(isIosDevice('iPhone'), true)
assert.equal(isIosDevice('iPad'), true)
assert.equal(isIosDevice('Android'), false)
assert.equal(isAndroidDevice('Android'), true)
assert.equal(isAndroidDevice('iPhone'), false)
navigator.userAgent = 'iPhone'
const ios = { ...InstallApp.data() }
assert.match(InstallApp.methods.installHelp.call(ios), /Share.*Add to Home Screen/)
