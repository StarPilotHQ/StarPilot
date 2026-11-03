import assert from "node:assert/strict"
import { compile } from "../web/vendor/vue/vue.esm-browser.js"
import { snapshot } from "./test_onroad_layout.mjs"
import { editorSnapshot, projectionPayload } from "../web/js/projection-layout.js"
import { LayoutWidgetPreview, OnroadLayoutFeed, OnroadLayoutPage, validSnapshot, validDocument } from "../web/js/onroad-layout.js"

const copy = (v) => JSON.parse(JSON.stringify(v))
const native = snapshot()
const metadata = copy(native.metadata.profiles.large)
// The Android Auto metadata has its own canvas, so the Big screen's widened bounds and anchors do not apply.
for (const widget of Object.values(metadata.widgets)) for (const field of ["bounds", "anchorShift", "expandable"]) delete widget[field]
metadata.width = 2880
metadata.bounds.width = 2820
metadata.widgets.current_speed.default.x += 510
metadata.widgets.steering_wheel.default.x += 1020
metadata.widgets.driver_monitor.width = metadata.widgets.driver_monitor.height = 128
metadata.widgets.driver_monitor.default = {x: 120, y: 840, enabled: true, size: 128}
metadata.widgets.driver_monitor.resizable = {min: 64, default: 128, max: 192}
metadata.widgets.car_exit = { label: "Exit to car", kind: "car_exit", width: 96, height: 96, colors: {}, required: true, frontmost: true,
  opacity: { min: 25, max: 100, default: 75 }, backgroundToggle: true,
  note: "Returns to the car's own screen without disconnecting Android Auto. Always enabled and above other widgets.",
  default: { x: 30, y: 954, enabled: true, opacity: 75, background: false } }
metadata.widgets.jetlink_status = { label: "Jetlink status", kind: "jetlink_status", width: 420, height: 84, colors: {},
  opacity: { min: 0, max: 100, default: 85 }, available: true, default: { x: 30, y: 650, enabled: false, opacity: 85 } }
metadata.widgetOrder = [...(metadata.widgetOrder || Object.keys(native.metadata.profiles.large.widgets)), "jetlink_status", "car_exit"]
const widgets = Object.fromEntries(Object.entries(metadata.widgets).map(([id, widget]) => [id,
  { ...widget.default, ...(widget.resizable ? { size: widget.resizable.default } : {}) }]))
const doc = { version: 6, clock24Hour: false, largeUiGammaTrial: false, blindSpotBorder: false, signalBorder: false, blindSpotCamera: false, hidePersonalityAlerts: false, wheelPedalFeedback: false, leadIndicator: false, leadInfo: "Off", speedSources: false, speedSourcesCard: false, speedAlign: "center", canvas: { width: 2880, height: 1080 }, widgets }
const raw = { version: 1, document: doc, defaults: copy(doc), metadata,
  screen: { width: 1280, height: 720, margin_width: 0, margin_height: 240 },
  revision: "aa-screen-and-layout", editable: true, valid: true,
  colors: { palette: native.document.palette, widgetColors: native.document.widgetColors.large, roadColors: {} } }
const data = editorSnapshot(raw)
assert.deepEqual(editorSnapshot({ ...raw, document: { ...doc, version: 4 }, defaults: { ...doc, version: 4 } }).document,
  data.document, "version 4 layouts remain readable alongside the backend's version 6")
for (const bad of [{ version: 7 }, { version: "5" }, { canvas: { width: 1920, height: 1080 } }])
  assert.throws(() => editorSnapshot({ ...raw, document: { ...doc, ...bad } }), /does not match/)

assert.equal(validSnapshot(data), true)
assert.equal(data.document.version, 8)
assert.equal(validSnapshot({ ...data, document: { ...data.document, version: 9 } }), false)
assert.equal(validSnapshot({ ...data, defaults: { ...data.defaults, version: 9 } }), false)
assert.equal(validDocument(data.document, data.metadata), true)
assert.equal(data.document.speedAlign, "center") // Legacy preferences still load after removing the editor control.
assert.equal(data.document.layouts.large.jetlink_status.enabled, false)
const jetlinkVm = { profile: data.metadata.profiles.large, layout: copy(data.document.layouts.large) }
const widgetRows = () => OnroadLayoutPage.computed.widgets.call(jetlinkVm)
assert.ok(widgetRows().some(widget => widget.id === 'jetlink_status'))
jetlinkVm.profile.widgets.jetlink_status.available = false
assert.ok(!widgetRows().some(widget => widget.id === 'jetlink_status'))
jetlinkVm.layout.jetlink_status.enabled = true
assert.ok(widgetRows().some(widget => widget.id === 'jetlink_status'), 'turning Jetlink off retains the placed widget')
assert.equal(validDocument({...data.document, layouts: {large: jetlinkVm.layout}}, data.metadata), true)
jetlinkVm.profile.widgets.jetlink_status.available = true
assert.match(LayoutWidgetPreview.template, /24\.6/)
compile(LayoutWidgetPreview.template, { decodeEntities: value => value.replaceAll("&amp;", "&") })
assert.equal(data.document.layouts.large.car_exit.opacity, 75)
assert.equal(data.document.layouts.large.car_exit.background, false)
const resizedMonitor = copy(data.document)
resizedMonitor.layouts.large.driver_monitor.size = 192
assert.equal(validDocument(resizedMonitor, data.metadata), true)
assert.equal(projectionPayload({document: resizedMonitor}, data.metadata).document.widgets.driver_monitor.size, 192)
resizedMonitor.layouts.large.driver_monitor.size = 193
assert.equal(validDocument(resizedMonitor, data.metadata), false)
assert.match(OnroadLayoutPage.template, /switchRow\('Show background', selectedPosition.background === true\)/)
assert.doesNotMatch(OnroadLayoutPage.template, /type="checkbox" :checked="selectedPosition.background"/)
const exitOpacity = copy(data.document)
exitOpacity.layouts.large.car_exit.opacity = 24
assert.equal(validDocument(exitOpacity, data.metadata), false)
exitOpacity.layouts.large.car_exit.opacity = 25
assert.equal(validDocument(exitOpacity, data.metadata), true)
const visibilityVm = { projection: true, state: { profile: "large", hiddenPreviewWidgets: { large: { car_exit: true } } } }
assert.equal(OnroadLayoutPage.methods.previewHidden.call(visibilityVm, "car_exit"), false)
OnroadLayoutPage.methods.togglePreviewVisibility.call(visibilityVm, "car_exit")
assert.equal(visibilityVm.state.hiddenPreviewWidgets.large.car_exit, true)
const missingFrontmost = copy(data)
delete missingFrontmost.metadata.profiles.large.widgets.car_exit.frontmost
assert.equal(validSnapshot(missingFrontmost), false)
const buriedDefault = copy(data)
buriedDefault.metadata.profiles.large.widgetOrder = ["car_exit", ...buriedDefault.metadata.profiles.large.widgetOrder.filter(id => id !== "car_exit")]
assert.equal(validSnapshot(buriedDefault), false)
assert.equal(validSnapshot({ ...data, projection: false }), false)
assert.equal(validSnapshot({ ...data, metadata: { ...data.metadata, projection: false } }), false)
assert.equal(validDocument(data.document, native.metadata), false)
assert.deepEqual(projectionPayload({ revision: raw.revision, document: data.document }, data.metadata),
  { revision: raw.revision, document: raw.document })
data.document.speedSources = true // The sources panel is an Android Auto setting, saved with the layout.
assert.equal(projectionPayload({ revision: raw.revision, document: data.document }, data.metadata).document.speedSources, true)
data.document.speedSources = false
data.document.clock24Hour = true
assert.equal(projectionPayload({ revision: raw.revision, document: data.document }, data.metadata).document.clock24Hour, true)
data.document.clock24Hour = false
data.document.largeUiGammaTrial = true
assert.equal(projectionPayload({document: data.document}, data.metadata).document.largeUiGammaTrial, true)
assert.equal(validDocument({...data.document, largeUiGammaTrial: 1}, data.metadata), false)
data.document.largeUiGammaTrial = false
assert.match(OnroadLayoutPage.template, /'Large UI Gamma Effect'/)
assert.doesNotMatch(OnroadLayoutPage.template, /Effect Trial|type="checkbox" :checked="state\.draft\.wheelPedalFeedback/)
assert.match(OnroadLayoutPage.template, /switchRow\('Brake and Acceleration Colors', state\.draft\.wheelPedalFeedback === true\)/)
const gammaChanges = []
const gammaVm = { projection: true, editable: true,
  state: { draft: { largeUiGammaTrial: false }, drag: null, layerDrag: null, notice: '' },
  recordChange: before => gammaChanges.push(before) }
OnroadLayoutPage.methods.setLargeUiGammaTrial.call(gammaVm, "On")
assert.equal(gammaVm.state.draft.largeUiGammaTrial, true)
assert.deepEqual(gammaChanges, [{ largeUiGammaTrial: false }])
assert.match(gammaVm.state.notice, /Save to apply/)
for (const blocked of [{ projection: false }, { editable: false }]) {
  OnroadLayoutPage.methods.setLargeUiGammaTrial.call({ ...gammaVm, ...blocked }, "Off")
  assert.equal(gammaVm.state.draft.largeUiGammaTrial, true)
}
// Android Auto has its own border switches, both off unless saved on.
assert.equal(data.document.blindSpotBorder, false)
assert.equal(data.document.signalBorder, false)
assert.equal(editorSnapshot({ ...raw, document: { ...raw.document, blindSpotBorder: undefined, signalBorder: undefined } }).document.blindSpotBorder, false)
for (const key of ["blindSpotBorder", "signalBorder", "hidePersonalityAlerts", "wheelPedalFeedback", "leadIndicator"]) assert.equal(validDocument({ ...data.document, [key]: 1 }, data.metadata), false)
// Android Auto's lead marker and lead info start off and never follow the comma's choices.
assert.equal(data.document.leadIndicator, false)
assert.equal(data.document.leadInfo, "Off")
const legacyLead = editorSnapshot({ ...raw, document: { ...raw.document, leadIndicator: undefined, leadInfo: undefined } }).document
assert.deepEqual([legacyLead.leadIndicator, legacyLead.leadInfo], [false, "Off"])
assert.equal(validDocument({ ...data.document, leadInfo: "Speed" }, data.metadata), true)
assert.equal(validDocument({ ...data.document, leadInfo: "Both" }, data.metadata), false)
assert.equal(validDocument({ ...native.document, leadIndicator: true }, native.metadata), false, "the comma screen keeps its own lead settings")
assert.equal(projectionPayload({ document: { ...data.document, leadIndicator: true, leadInfo: "Distance" } }, data.metadata).document.leadInfo, "Distance")
assert.match(OnroadLayoutPage.template, /'Lead Indicator'/)
const leadChanges = []
const leadVm = { projection: true, editable: true, state: { draft: { leadIndicator: false, leadInfo: "Off" }, drag: null, layerDrag: null, notice: '' },
  recordChange: before => leadChanges.push(before) }
OnroadLayoutPage.methods.setProjectionLeadInfo.call(leadVm, "Speed")
assert.equal(leadVm.state.draft.leadInfo, "Off", "lead info waits for the lead marker")
assert.equal(OnroadLayoutPage.methods.projectionLeadInfoRow.call(leadVm).available, false)
OnroadLayoutPage.methods.setProjectionDisplay.call(leadVm, "leadIndicator", "On")
OnroadLayoutPage.methods.setProjectionLeadInfo.call(leadVm, "Speed")
assert.deepEqual([leadVm.state.draft.leadIndicator, leadVm.state.draft.leadInfo], [true, "Speed"])
assert.deepEqual(OnroadLayoutPage.methods.projectionLeadInfoRow.call(leadVm).choices, ["Off", "Distance", "Speed"])
OnroadLayoutPage.methods.setProjectionLeadInfo.call(leadVm, "Both")
assert.equal(leadVm.state.draft.leadInfo, "Speed")
assert.equal(leadChanges.length, 2)
assert.equal(validDocument({ ...native.document, signalBorder: true }, native.metadata), false, "the comma screen keeps its C4 settings")
assert.match(OnroadLayoutPage.template, /Personality alerts follow the comma screen setting\./)
assert.doesNotMatch(OnroadLayoutPage.template, /setProjectionDisplay\('hidePersonalityAlerts', value\)/)
assert.match(OnroadLayoutPage.template, /Red Blind-Spot Border/)
assert.match(OnroadLayoutPage.template, /Amber Signal Border/)
const borderChanges = []
const borderVm = { projection: true, editable: true, state: { draft: { blindSpotBorder: false, signalBorder: false }, drag: null, layerDrag: null, notice: '' },
  recordChange: before => borderChanges.push(before) }
OnroadLayoutPage.methods.setProjectionDisplay.call(borderVm, "signalBorder", "On")
OnroadLayoutPage.methods.setProjectionDisplay.call(borderVm, "blindSpotBorder", "On")
assert.deepEqual(borderVm.state.draft, { blindSpotBorder: true, signalBorder: true })
assert.equal(borderChanges.length, 2)
assert.match(borderVm.state.notice, /Save to apply/)
OnroadLayoutPage.methods.setProjectionDisplay.call(borderVm, "largeUiGammaTrial", "On")
for (const blocked of [{ projection: false }, { editable: false }]) OnroadLayoutPage.methods.setProjectionDisplay.call({ ...borderVm, ...blocked }, "signalBorder", "Off")
assert.deepEqual(borderVm.state.draft, { blindSpotBorder: true, signalBorder: true })
assert.equal((OnroadLayoutPage.template.match(/aria-label="StarPort screen options"/g) || []).length, 1, "one compact card for all StarPort screen options")
assert.deepEqual(projectionPayload({ document: { ...data.document, ...borderVm.state.draft } }, data.metadata).document.signalBorder, true)
// Camera popup is saved independently, and older layouts default it off.
assert.equal(data.document.blindSpotCamera, false)
const olderCameraLayout = copy(raw)
delete olderCameraLayout.document.blindSpotCamera
assert.equal(editorSnapshot(olderCameraLayout).document.blindSpotCamera, false)
for (const value of [1, 0, "true", null, [], {}])
  assert.equal(validDocument({ ...data.document, blindSpotCamera: value }, data.metadata), false)
assert.equal(validDocument({ ...native.document, blindSpotCamera: true }, native.metadata), false)
assert.match(OnroadLayoutPage.template, /Show Camera on Blind-Spot Warning/)
assert.match(OnroadLayoutPage.template, /setProjectionDisplay\('blindSpotCamera', value\)/)
const cameraChanges = []
const cameraVm = { projection: true, editable: true,
  state: { draft: { blindSpotCamera: false }, drag: null, layerDrag: null, notice: '' },
  projectionHasPipWidget: true,
  recordChange: before => cameraChanges.push(before) }
OnroadLayoutPage.methods.setProjectionDisplay.call(cameraVm, "blindSpotCamera", "On")
assert.equal(cameraVm.state.draft.blindSpotCamera, true)
assert.deepEqual(cameraChanges, [{ blindSpotCamera: false }])
const cameraPayload = projectionPayload({ document: { ...data.document, ...cameraVm.state.draft } }, data.metadata)
assert.equal(cameraPayload.document.blindSpotCamera, true)
assert.equal(editorSnapshot({ ...raw, document: cameraPayload.document }).document.blindSpotCamera, true)
for (const blocked of [{ projection: false }, { editable: false },
  { state: { ...cameraVm.state, drag: {} } }, { state: { ...cameraVm.state, layerDrag: {} } }]) {
  OnroadLayoutPage.methods.setProjectionDisplay.call({ ...cameraVm, ...blocked }, "blindSpotCamera", "Off")
  assert.equal(cameraVm.state.draft.blindSpotCamera, true)
}
OnroadLayoutPage.methods.setProjectionDisplay.call(cameraVm, "blindSpotCamera", "Off")
assert.equal(cameraVm.state.draft.blindSpotCamera, false)
const pipChanges = []
const pipVm = { projection: true, editable: true,
  state: { draft: { blindSpotCamera: false, layouts: { large: {
    pip_left: { enabled: false }, pip_right: { enabled: false }, current_speed: { enabled: true }
  } } }, drag: null, layerDrag: null, notice: '' },
  recordChange: before => pipChanges.push(before) }
Object.defineProperty(pipVm, "projectionHasPipWidget", { get: () => OnroadLayoutPage.computed.projectionHasPipWidget.call(pipVm) })
assert.equal(pipVm.projectionHasPipWidget, false)
OnroadLayoutPage.methods.setProjectionDisplay.call(pipVm, "blindSpotCamera", "On")
assert.equal(pipVm.state.draft.blindSpotCamera, false)
assert.equal(pipChanges.length, 0)
for (const side of ["pip_left", "pip_right"]) {
  pipVm.state.draft.layouts.large[side].enabled = true
  assert.equal(pipVm.projectionHasPipWidget, true, "either camera widget permits the saved camera preference")
  OnroadLayoutPage.methods.setProjectionDisplay.call(pipVm, "blindSpotCamera", "On")
  assert.equal(pipVm.state.draft.blindSpotCamera, true)
  pipVm.state.draft.layouts.large[side].enabled = false
  assert.equal(pipVm.projectionHasPipWidget, false)
  OnroadLayoutPage.methods.setProjectionDisplay.call(pipVm, "blindSpotCamera", "Off")
  assert.equal(pipVm.state.draft.blindSpotCamera, true)
  pipVm.state.draft.blindSpotCamera = false
}
assert.equal(pipChanges.length, 2)
assert.throws(() => editorSnapshot({ version: 1, screen: null, reason: "Connect StarPort once" }), /Connect StarPort once/)
assert.equal(editorSnapshot({ ...raw, editable: false, reason: "Enable StarPort" }).editable, false)
assert.ok(!OnroadLayoutPage.template.includes('<section class="gx-layout__colors" aria-label="Path'))
assert.ok(OnroadLayoutPage.template.includes('v-if="!projection && colorFields.length"'))
assert.ok(OnroadLayoutPage.template.includes('v-if="!projection && state.profile ==='))
compile(OnroadLayoutPage.template, { decodeEntities: value => value.replaceAll("&amp;", "&") }) // Actual Vue compiler, including projection conditionals.

const emissions = [], leaveVm = { projection: true, busy: false, dirty: true,
  state: { status: "ready", data: { editable: true }, draft: {}, drag: null, layerDrag: null, discard: null, leavePresentation: null, removeConfirm: null, error: "", needsReload: false },
  $emit: (...args) => emissions.push(args), feed: { load() {}, async save() { return true } } }
for (const name of ["pendingLeave", "inlineLeavePrompt", "modalLeavePrompt", "saveAvailable", "canSavePending"])
  Object.defineProperty(leaveVm, name, { get: () => OnroadLayoutPage.computed[name].call(leaveVm) })
for (const name of ["requestLeave", "cancelLeave", "leave", "saveAndLeave"])
  leaveVm[name] = OnroadLayoutPage.methods[name].bind(leaveVm)
leaveVm.requestLeave("device")
assert.equal(leaveVm.state.discard, "device")
assert.equal(leaveVm.inlineLeavePrompt, true)
assert.equal(leaveVm.modalLeavePrompt, false)
assert.deepEqual(emissions, [])
leaveVm.cancelLeave()
let proceeded = 0
leaveVm.requestLeave(() => { proceeded++ }, "modal")
assert.equal(leaveVm.inlineLeavePrompt, false)
assert.equal(leaveVm.modalLeavePrompt, true)
assert.equal(await leaveVm.saveAndLeave(), true)
assert.equal(proceeded, 1)
assert.equal(leaveVm.state.discard, null)
leaveVm.feed.save = async () => false
leaveVm.requestLeave(() => { proceeded++ }, "modal")
assert.equal(await leaveVm.saveAndLeave(), false)
assert.equal(proceeded, 1)
assert.equal(leaveVm.modalLeavePrompt, true)
leaveVm.cancelLeave()
assert.match(OnroadLayoutPage.template, /inlineLeavePrompt \? \(pendingLeave \? 'Leave without saving your changes\?'/)
assert.match(OnroadLayoutPage.template, /v-if="modalLeavePrompt"/)
assert.match(OnroadLayoutPage.template, /@click="saveAndLeave"/)

const setup = OnroadLayoutPage.setup({ projection: true, mode: "local", unauthorized() {} })
assert.equal(setup.feed.projection, true)
assert.equal(setup.state.profile, "large")
setup.feed.stop()

const requests = []
const updates = []
const feed = new OnroadLayoutFeed({ projection: true, publish: v => updates.push(v),
  later: () => 1, cancelTimer() {}, fetcher: async (url, options) => {
    requests.push({ url, body: options.body ? JSON.parse(options.body) : null })
    return { ok: true, status: 200, json: async () => raw }
  } })
await feed.start()
const draft = copy(feed.data.document)
draft.layouts.large.current_speed.x += 20
assert.equal(await feed.save(draft), true)
assert.equal(requests[0].url, "./api/starport/layout")
assert.equal(requests[1].body.document.version, 6)
assert.deepEqual(Object.keys(requests[1].body.document).sort(), ["blindSpotBorder", "blindSpotCamera", "canvas", "clock24Hour", "hidePersonalityAlerts", "largeUiGammaTrial", "leadIndicator", "leadInfo", "signalBorder", "speedAlign", "speedSources", "speedSourcesCard", "version", "wheelPedalFeedback", "widgets"])
assert.equal(requests[1].body.document.widgets.current_speed.x, 1170)
assert.equal(requests[1].body.document.speedAlign, "center")
assert.equal(updates.at(-1).notice, "StarPort layout saved.")
feed.stop()
console.log("Projection editor: isolated document, strict snapshot, routing confirmation, actual Vue template, endpoint and placement-only save passed")

const orderedRaw = copy(raw)
orderedRaw.document.widgetOrder = [...Object.keys(widgets).filter(id => id !== "car_exit").reverse(), "car_exit"]
const orderedSnapshot = editorSnapshot(orderedRaw)
assert.equal(validSnapshot(orderedSnapshot), true)
assert.deepEqual(orderedSnapshot.document.widgetOrder.large, orderedRaw.document.widgetOrder)
assert.deepEqual(projectionPayload({revision: raw.revision, document: orderedSnapshot.document}, orderedSnapshot.metadata).document,
  orderedRaw.document)
console.log('Projection layers survive editor translation and save payload')
