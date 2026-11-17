import assert from "node:assert/strict"
import { OnroadLayoutPage } from "../web/js/onroad-layout.js"

const originalStorage = globalThis.localStorage
const saved = new Map()
const editor = projection => {
  const vm = { ...OnroadLayoutPage.setup({ projection, mode: "local", unauthorized() {} }), projection }
  for (const name of ["sceneHidden", "previewHidden", "togglePreviewVisibility"])
    vm[name] = OnroadLayoutPage.methods[name].bind(vm)
  return vm
}
try {
  globalThis.localStorage = { getItem: key => saved.get(key) ?? null, setItem: (key, value) => saved.set(key, value) }
  for (const projection of [false, true]) {
    const vm = editor(projection)
    assert.equal(vm.state.scene, "general")
    assert.equal(vm.previewHidden("stopped_timer"), false)
    assert.equal(vm.previewHidden("current_speed"), false, "targets have separate preferences")
    vm.togglePreviewVisibility("stopped_timer")
    vm.togglePreviewVisibility("current_speed")
    vm.state.profile = "compact"
    vm.togglePreviewVisibility("driver_monitor")
    const reopened = editor(projection)
    assert.equal(reopened.previewHidden("stopped_timer"), true, "General edit eye preferences survive reopening")
    assert.equal(reopened.previewHidden("current_speed"), true)
    assert.equal(reopened.previewHidden("driver_monitor"), false)
    reopened.state.profile = "compact"
    assert.equal(reopened.previewHidden("driver_monitor"), true)
    assert.equal(reopened.previewHidden("current_speed"), false, "profiles have separate preferences")
    assert.equal(reopened.state.draft, null, "visibility is independent of the saved driving layout")
    reopened.state.scene = "stopped"
    assert.equal(reopened.previewHidden("stopped_timer"), false, "General edit cannot hide the stopped scene's timer")
    reopened.togglePreviewVisibility("stopped_timer")
    assert.equal(reopened.previewHidden("stopped_timer"), true)
    reopened.state.scene = "engaged"
    assert.equal(reopened.previewHidden("stopped_timer"), true)
    reopened.togglePreviewVisibility("stopped_timer")
    assert.equal(reopened.previewHidden("stopped_timer"), true, "Driving scenes cannot show an impossible timer")
    reopened.togglePreviewVisibility("current_speed")
    reopened.state.scene = "experimental"
    assert.equal(reopened.previewHidden("current_speed"), false, "Eye preferences do not leak between scenes")
    assert.equal(reopened.previewHidden("stopped_timer"), true)
  }
  const starport = editor(true)
  const before = [...saved]
  starport.togglePreviewVisibility("car_exit")
  assert.equal(starport.previewHidden("car_exit"), false)
  assert.deepEqual([...saved], before)

  for (const projection of [false, true]) {
    const vm = editor(projection)
    vm.state.scene = 'slc_pending'
    vm.state.profile = 'large'
    vm.layout = {clock:{enabled:true}}
    vm.state.art = {status:'ready', scene:'slc_pending', profile:projection ? 'projection' : 'large',
      sprites:{sprites:{clock:{visible:false}}}}
    assert.equal(vm.sceneHidden('clock'), true, 'Native prompt occlusion hides the preview and its eye control')
    vm.layout.clock.enabled = false
    assert.equal(vm.sceneHidden('clock'), false, 'A disabled clock can still be added from the tray')
    vm.layout.clock.enabled = true
    for (const change of [{status:'updating'}, {scene:'engaged'}, {profile:'compact'}]) {
      const native = vm.state.art
      vm.state.art = {...native, ...change}
      assert.equal(vm.sceneHidden('clock'), false, 'Retained artwork cannot apply stale visibility')
      vm.state.art = native
    }
  }

  saved.delete('gx-layout-preview-visibility-v2:comma')
  saved.set('gx-layout-preview-visibility-v1:comma', JSON.stringify({ large: { current_speed: true }, compact: { stopped_timer: true } }))
  const migrated = editor(false)
  assert.equal(migrated.previewHidden('current_speed'), true, 'Old preview preferences become General edit preferences')
  migrated.state.profile = 'compact'
  assert.equal(migrated.previewHidden('stopped_timer'), false, 'The old automatic timer default does not hide General edit samples')

  for (const stored of ["broken JSON", "null", JSON.stringify({ large: [true], compact: "invalid" }),
    JSON.stringify({ large: { stopped_timer: "false", current_speed: 1 } })]) {
    globalThis.localStorage.getItem = () => stored
    const vm = editor(false)
    assert.equal(vm.previewHidden("stopped_timer"), false)
    assert.equal(vm.previewHidden("current_speed"), false)
  }
  globalThis.localStorage = {
    getItem() { throw new Error("Storage unavailable") },
    setItem() { throw new Error("Storage unavailable") },
  }
  const unavailable = editor(false)
  unavailable.togglePreviewVisibility("current_speed")
  assert.equal(unavailable.previewHidden("current_speed"), true)
} finally {
  if (originalStorage === undefined) delete globalThis.localStorage
  else globalThis.localStorage = originalStorage
}
console.log("Preview visibility: General edit persistence, scene/target/profile isolation, timer eligibility, required exit and unavailable storage passed")
