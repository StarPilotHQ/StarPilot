import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { ModelsPage, MODEL_SORTS, hardwareTag } from "../web/js/models.js"

const template = ModelsPage.template

// Single model / Model pair selector: one mode's settings at a time.
assert.ok(template.includes(`@click="setDriveMode('single')"><i aria-hidden="true" class="bi bi-cpu"></i> Single model</button>`))
assert.match(template, /<template v-if="driveMode === 'single'">/)
assert.match(template, /<div v-else id="gx-model-laboratory" ref="laboratory"/)
assert.doesNotMatch(template, /aria-label="Enable model pair"/, "the selector replaces the pair switch")

const page = (enabled, available = () => true) => {
  const calls = []
  const view = { ...ModelsPage.data(), lab: { configuration: { enabled } }, labActionAvailable: available,
    runLabAction: action => { calls.push(action); return Promise.resolve({ message: "ok" }) } }
  Object.defineProperty(view, "driveMode", { get() { return ModelsPage.computed.driveMode.call(this) } })
  view.enablePair = () => ModelsPage.methods.enablePair.call(view)
  return { view, calls }
}
{
  const { view, calls } = page(false)
  assert.equal(view.driveMode, "single")
  await ModelsPage.methods.setDriveMode.call(view, "pair")
  assert.deepEqual(calls, ["enable"], "Model pair switches the pair on when it can run")
}
{
  const { view, calls } = page(false, () => false)
  await ModelsPage.methods.setDriveMode.call(view, "pair")
  assert.deepEqual(calls, [], "a pair that cannot run yet is not switched on")
  assert.equal(view.driveMode, "pair", "the pair settings show so the missing parts can be chosen")
}
{
  const { view, calls } = page(true)
  assert.equal(view.driveMode, "pair")
  await ModelsPage.methods.setDriveMode.call(view, "single")
  assert.deepEqual(calls, ["disable"], "Single model switches the pair off")
  assert.equal(view.pairView, null)
}

// Compact list; filters sit in a funnel menu beside the search; Sort by stays a row of chips.
assert.match(template, /<ul v-else class="gx-model-list" aria-label="Models">/)
assert.match(template, /class="gx-icon-btn gx-model-filter__button"[^>]*aria-haspopup="true"/)
assert.match(template, /'bi-funnel-fill' : 'bi-funnel'/)
assert.match(template, /<div v-if="filtersOpen" id="gx-model-filter-menu"/)
assert.doesNotMatch(template, /class="gx-model-filters__label">Filters</)
assert.match(template, /class="gx-model-filters__label">Sort by</)
// No Download Missing Models; the catalog check is a small refresh button.
assert.doesNotMatch(template, /Download Missing Models/)
assert.doesNotMatch(template, /Check model catalog/)
assert.match(template, /class="gx-icon-btn gx-model-refresh"[^>]*aria-label="Check for new models"[^>]*@click="runAction\('refresh'\)"/)
assert.match(template, /Cancel download/)
// No overview strip at the top; Jetlink starts collapsed.
assert.doesNotMatch(template, /gx-model-overview|label: 'Running model'/)
assert.match(template, /class="gx-section__header gx-model-collapse" :aria-expanded="String\(jetlinkOpen\)"/)
assert.match(template, /<div v-show="jetlinkOpen" id="gx-model-jetlink-panel"/)
assert.equal(ModelsPage.data().jetlinkOpen, false)
// Rows: tags instead of the hardware text, no format version or model ID; active models are marked.
const list = template.slice(template.indexOf('<ul v-else class="gx-model-list"'))
assert.doesNotMatch(list, /m\.version|gx-model-item__id|\{\{ m\.value \}\}/, "the pair pickers keep the version; the list does not")
assert.match(template, /class="gx-model-tag" :class="'gx-model-tag--' \+ hardwareTag\(m\)\.tone"/)
assert.match(template, /<span v-if="isPairable\(m\)" class="gx-model-tag gx-model-tag--pair"/)
// Active Small / Big sit with the hardware tag, not by the name; the tag alone marks the row (no border or tint).
assert.match(template, /hardwareTag\(m\)\.label \}\}<\/span>\s*<span v-for="role in activeRoles\(m\)" :key="role" class="gx-model-tag gx-model-tag--active">\{\{ role \}\}<\/span>/)
assert.doesNotMatch(template.slice(template.indexOf('class="gx-model-item__title"'), template.indexOf('class="gx-model-item__meta"')), /activeRoles/)
const css = readFileSync(new URL("../web/css/model-list.css", import.meta.url), "utf8")
assert.doesNotMatch(css, /\.gx-model-item--active \{/)
assert.deepEqual([true, false, undefined].map(requiresGpu => hardwareTag({ requiresGpu }).tone), ["egpu", "small", "unknown"])

// Sorts: newest, name, size; ties fall back to the name.
const models = [
  { value: "a", label: "Alpha", version: "v9", released: "2025-01-01", artifactSize: 300 },
  { value: "b", label: "Beta", version: "v16", released: "2026-11-05", artifactSize: 100 },
  { value: "c", label: "Gamma", version: "v12", released: "2026-03-01", fileSizeBytes: 900 },
]
const order = sortMode => ModelsPage.computed.sorted.call({ ...ModelsPage.data(), hardwareFilter: "both", labRow: () => null, models, sortMode }).map(m => m.value)
assert.deepEqual(order("release_date"), ["b", "c", "a"])
assert.deepEqual(order("alphabetical"), ["a", "b", "c"])
assert.deepEqual(order("size"), ["c", "a", "b"])
assert.deepEqual(Object.keys(MODEL_SORTS), ["release_date", "alphabetical", "size"])

// The funnel shows how many filters are on (search is separate).
const count = state => ModelsPage.computed.filterCount.call({ hardwareFilter: "both", installedFilter: false, userFilter: "all", communityFilter: "all", pairableFilter: false, ...state })
assert.equal(count({}), 0)
assert.equal(count({ hardwareFilter: "gpu", installedFilter: true, pairableFilter: true }), 3)

// eGPU variant details only with Model pair or Pairable only.
assert.equal(ModelsPage.computed.showLabVariants.call({ driveMode: "single", pairableFilter: false }), false)
assert.equal(ModelsPage.computed.showLabVariants.call({ driveMode: "pair", pairableFilter: false }), true)
assert.equal(ModelsPage.computed.showLabVariants.call({ driveMode: "single", pairableFilter: true }), true)

// The Active Small / Big pickers use the list's Small / eGPU tags.
assert.match(template, /for="gx-model-small">Active Small <span class="gx-model-tag gx-model-tag--small"[^>]*>Small<\/span><\/label>/)
assert.match(template, /for="gx-model-big">Active Big <span class="gx-model-tag gx-model-tag--egpu"[^>]*>eGPU<\/span><\/label>/)
// Browse models opens from its header (closed by default); refresh sits beside the search.
assert.equal(ModelsPage.data().browseOpen, false)
assert.match(template, /class="gx-section__header gx-model-collapse" :aria-expanded="String\(browseOpen\)" aria-controls="gx-model-browse-panel"/)
assert.match(template, /<div v-if="browseOpen" id="gx-model-browse-panel">/)
assert.ok(template.indexOf('gx-model-refresh') > template.indexOf('class="gx-model-searchbar"'), "refresh is in the search row")
// Catalog checks never use the top banner (it pushed the page down); the header chip reports them.
assert.match(template, /<GxNotice v-if="status.downloading && !status.refreshing" :title="'Downloading ' \+ downloadTargetLabel">/)
assert.match(template, /<template v-if="status.refreshing">Checking for new models…<\/template><template v-else-if="refreshNote">/)
// Galaxy's server no longer checks the catalog by itself while the page polls.
const server = readFileSync(new URL("../server.py", import.meta.url), "utf8")
assert.match(server, /ModelManager\(parked=authority\.parked, refresh_catalog=False\)/)

const html = readFileSync(new URL("../web/index.html", import.meta.url), "utf8")
assert.match(html, /css\/model-list\.css/)
console.log("Model Manager selector, compact list, tags, filter menu and sorts passed")
