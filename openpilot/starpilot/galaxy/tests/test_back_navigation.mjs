import assert from "node:assert/strict"
import { SettingsPage } from "../web/js/settings.js"
import { OnroadLayoutPage } from "../web/js/onroad-layout.js"

const listeners = new Map()
globalThis.window = { scrollTo() {}, addEventListener(name, callback) { listeners.set(name, callback) } }
let current = new URL("https://galaxy.test/app#/cameras/pip")
globalThis.location = { get hash() { return current.hash }, pathname: "/app", search: "" }
const entries = [{ url: current.href, state: null }]
let cursor = 0
globalThis.history = {
  get state() { return entries[cursor].state },
  replaceState(state, _title, url) { current = new URL(url, current); entries[cursor] = { state, url: current.href } },
  pushState(state, _title, url) { entries.splice(cursor + 1); current = new URL(url, current); entries.push({ state, url: current.href }); cursor++ },
  go(delta) { if (cursor + delta >= 0 && cursor + delta < entries.length) { cursor += delta; current = new URL(entries[cursor].url); listeners.get("popstate")() } },
  back() { if (cursor > 0) { cursor--; current = new URL(entries[cursor].url); listeners.get("popstate")() } },
  forward() { if (cursor < entries.length - 1) { cursor++; current = new URL(entries[cursor].url); listeners.get("popstate")() } },
}
const { route, navigate, navigateBack, startRouter } = await import("../web/js/router.js")
startRouter()
navigateBack()
assert.equal(route.path, "/") // A direct link cannot navigate outside Galaxy.
navigate("/settings"); navigate("/appearance"); navigate("/theme_maker")
navigateBack(); assert.equal(route.path, "/appearance")
navigateBack(); assert.equal(route.path, "/settings")
history.forward(); assert.equal(route.path, "/appearance")
navigate("/cameras"); navigate("/cameras/pip")
navigateBack(); assert.equal(route.path, "/cameras")
const length = entries.length
navigate("/cameras"); assert.equal(entries.length, length)

let returned = 0, cancelled = 0
const settings = { busy: false, state: { data: { page: "standard/following" } }, initialPage: "hub", atSectionRoot: false,
  back() { returned++ }, feed: { cancel() { cancelled++ } } }
assert.equal(SettingsPage.methods.navigateBack.call(settings), true)
assert.equal(returned, 1)
settings.atSectionRoot = true
assert.equal(SettingsPage.methods.navigateBack.call(settings), false)
settings.state.pending = {}; settings.busy = true
assert.equal(SettingsPage.methods.navigateBack.call(settings), true)
assert.equal(cancelled, 1)

let closed = 0
const editor = { busy: false, dirty: true, state: { drag: null, discard: null },
  leave: OnroadLayoutPage.methods.leave, $emit(event) { if (event === "close") closed++ } }
settings.state = { layoutOpen: true }; settings.busy = false
settings.$refs = { layoutEditor: { requestLeave(action) { OnroadLayoutPage.methods.requestLeave.call(editor, action) } } }
assert.equal(SettingsPage.methods.navigateBack.call(settings), true)
assert.equal(editor.state.discard, "back"); assert.equal(closed, 0)
editor.state.discard = null // Cancel discard keeps the editor.
assert.equal(closed, 0)
editor.leave("back"); assert.equal(closed, 1)
console.log("Galaxy Back history and editor guard passed")

// Parent is the page actually visited, not a guessed personality parent.
const nested = { initialPage: 'hub', state: { section: 'longitudinal', query: '', parents: [], data: {page: 'hub'} },
  activeSection: {id: 'longitudinal'}, feed: {load(page) { nested.state.data = {page} }} }
SettingsPage.methods.open.call(nested, 'profiles')
SettingsPage.methods.back.call(nested)
assert.equal(nested.state.data.page, 'hub')
SettingsPage.methods.open.call(nested, 'standard')
SettingsPage.methods.open.call(nested, 'standard/following')
SettingsPage.methods.back.call(nested)
assert.equal(nested.state.data.page, 'standard')
SettingsPage.methods.back.call(nested)
assert.equal(nested.state.data.page, 'hub', 'direct personality returns to Longitudinal hub')
SettingsPage.methods.open.call(nested, 'profiles')
SettingsPage.methods.open.call(nested, 'standard')
SettingsPage.methods.back.call(nested)
assert.equal(nested.state.data.page, 'profiles', 'personality opened through planner returns there')
SettingsPage.methods.selectSection.call({...nested,busy:false,feed:{active:true,load(){}}}, {id:'wheel',pages:['wheel']})
assert.deepEqual(nested.state.parents, [], 'changing sections retires nested history')

nested.initialPage = 'profiles'; nested.state.data = {page:'profiles'}
SettingsPage.methods.open.call(nested,'standard')
SettingsPage.methods.open.call(nested,'standard/following')
SettingsPage.methods.back.call(nested)
assert.equal(nested.state.data.page,'standard','dedicated tools also retain each nested parent')
SettingsPage.methods.back.call(nested)
assert.equal(nested.state.data.page,'profiles')

// Routed tools must return to the Settings entry that opened them. Reload
// saved values from the device; keep only the navigation state in memory.
navigate('/settings')
const settingsView = route.view
const props = {initialPage:'hub',initialSection:null,unauthorized() {}}
const original = {...SettingsPage.setup(props), ...props}
Object.assign(original.state, {data:{page:'pip',rows:[{value:'old device value'}]},
  section:'visual',parents:['hub']})
settingsView.scroll = {x:0,y:720}
SettingsPage.beforeUnmount.call(original)
assert(!JSON.stringify(settingsView).includes('old device value'))
navigate('/cameras/pip')
assert.notEqual(route.view, settingsView)
navigateBack()
assert.equal(route.view, settingsView)
const restored = {...SettingsPage.setup(props), ...props, mode:'local'}
assert.equal(restored.restoredPage, 'pip')
assert.equal(restored.state.section, 'visual')
assert.deepEqual(restored.state.parents, ['hub'])
assert.equal(restored.state.data, null, 'saved settings are always read again')
const positions = []
window.scrollTo = (x,y) => positions.push([x,y])
restored.feed = {active:true,async start(page) {assert.equal(page,'pip')}}
// Route changes capture the live scroll before unmount, independently of the
// Settings request lifecycle. Use an explicit restored position here.
settingsView.scroll = {x:0,y:720}
await SettingsPage.mounted.call(restored)
assert.deepEqual(positions.at(-1), [0,720])
history.forward()
assert.notEqual(route.view, settingsView)
navigateBack()
assert.equal(route.view, settingsView)
navigate('/tools')
navigate('/settings')
assert.notEqual(route.view, settingsView, 'a new Settings visit has a fresh context')
const fresh = SettingsPage.setup(props)
assert.equal(fresh.restoredPage, undefined)
assert.equal(fresh.state.section, 'lateral')

// Search and ordinary Toggles views cannot overwrite each other.
const searchProps = {...props,initialPage:'standard/following'}
const search = {...SettingsPage.setup(searchProps),...searchProps}
search.state.data = {page:'standard/following'}
SettingsPage.beforeUnmount.call(search)
assert.equal(route.view.searchPage, 'standard/following')
const hub = {...SettingsPage.setup(props),...props}
assert.equal(hub.restoredPage, undefined)
hub.state.data = {page:'hub'}
SettingsPage.beforeUnmount.call(hub)
assert.equal(route.view.searchPage, '')
const searchAgain = SettingsPage.setup(searchProps)
assert.equal(searchAgain.restoredPage, undefined, 'a new search after returning to Toggles starts at its requested page')
console.log('Settings entry, fresh values, search isolation and scroll restoration passed')
