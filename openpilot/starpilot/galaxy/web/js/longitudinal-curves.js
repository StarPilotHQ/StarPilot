import { WikiHelp } from './wiki-help.js'
import { GxDialog } from "./dialog.js"
import { GxState } from "./state.js"
import { GxNotice } from "./notice.js"
import { reactive } from "../vendor/vue/vue.esm-browser.js"
import { SettingsFeed, settingsBusy } from "./settings.js"
import { GalaxySettingRow } from "./galaxy-setting-row.js"

export const PROFILES = Object.freeze(["aggressive", "standard", "relaxed"])
export const CATEGORIES = Object.freeze(["acceleration", "braking", "following"])

export function savedCurve(page, rows) {
  if (!PROFILES.some((profile) => CATEGORIES.some((category) => page === `${profile}/${category}`))) return null
  if (!Array.isArray(rows) || rows[0]?.label !== "Preset" || rows[0]?.value !== "custom") return null
  const points = rows.slice(1)
  if (points.length !== 10) return null
  const unit = page.endsWith("/following") ? "s" : "m/s²"
  const values = points.map((row, index) => {
    const value = Number(row.value)
    if (row.label !== `${index * 10} mph point` || row.unit !== unit || typeof row.value !== "string" || !row.value.trim() ||
        !Number.isFinite(value) || !Number.isFinite(row.minimum) || !Number.isFinite(row.maximum)) return null
    return { index: index + 1, speed: index * 10, value, unit, minimum: row.minimum, maximum: row.maximum,
      available: row.available === true }
  })
  return values.every(Boolean) ? values : null
}

export function curveGeometry(points) {
  if (!Array.isArray(points) || points.length !== 10) return null
  const values = points.map((point) => point.value)
  if (values.some((value) => !Number.isFinite(value))) return null
  const min = Math.min(...values)
  const max = Math.max(...values)
  const span = Math.max(max - min, 0.1)
  const bottom = min - (span - (max - min)) / 2
  const top = bottom + span
  const coords = points.map((point, index) => ({ x: 44 + index * 32, y: 116 - (point.value - bottom) / span * 88 }))
  return { coords, line: coords.map(({ x, y }) => `${x},${y}`).join(" "), bottom, top }
}

export const LongitudinalCurvesPage = {
  components: { WikiHelp, GxDialog, GxState, GxNotice, GalaxySettingRow },
  props: { mode: { type: String, required: true }, unauthorized: { type: Function, required: true }, go: { type: Function, required: true } },
  setup(props) {
    const state = reactive({ status: "idle", data: null, pending: null, error: "", profile: "standard", category: "acceleration" })
    const feed = new SettingsFeed({ publish: (update) => Object.assign(state, update), unauthorized: props.unauthorized })
    return { state, feed, profiles: PROFILES, categories: CATEGORIES }
  },
  computed: {
    busy() { return settingsBusy(this.state) },
    page() { return `${this.state.profile}/${this.state.category}` },
    points() { return savedCurve(this.state.data?.page, this.state.data?.rows) },
    graph() { return curveGeometry(this.points) },
    preset() { return this.state.data?.rows?.[0] },
  },
  mounted() { if (this.mode === "local") this.feed.start(this.page) },
  beforeUnmount() { this.feed.stop() },
  methods: {
    selectProfile(profile) { if (PROFILES.includes(profile) && !this.busy && !this.feed.saving) { this.state.profile = profile; this.feed.load(this.page) } },
    selectCategory(category) { if (CATEGORIES.includes(category) && !this.busy && !this.feed.saving) { this.state.category = category; this.feed.load(this.page) } },
  },
  template: `
    <section class="gx-long-curves" aria-label="Longitudinal curves" :inert="busy" :aria-busy="busy || undefined">
      <header class="gx-settings__header gx-page-header"><div><h2>Longitudinal Curves</h2>
        <p>Adjust acceleration, braking, and following curves for each driving personality.</p></div><WikiHelp /></header>
      <GxState v-if="mode !== 'local'">Local saved curves are unavailable in preview.</GxState>
      <template v-else>
        <div class="gx-long-curves__tabs gx-tabs" role="group" aria-label="Personality profile">
          <button v-for="profile in profiles" :key="profile" type="button" class="gx-btn gx-btn--tonal" :aria-pressed="state.profile === profile"
            @click="selectProfile(profile)">{{ profile }}</button></div>
        <div class="gx-long-curves__tabs gx-tabs" role="group" aria-label="Curve category">
          <button v-for="category in categories" :key="category" type="button" class="gx-btn gx-btn--tonal" :aria-pressed="state.category === category"
            @click="selectCategory(category)">{{ category }}</button></div>
        <GxState v-if="state.status === 'loading'" loading>Loading saved curve…</GxState>
        <GxNotice tone="danger" v-else-if="state.status === 'unavailable' && !state.error">Saved curve is unavailable.</GxNotice>
        <GxNotice tone="danger" v-if="state.error">{{ state.error }}
          </GxNotice>
        <article v-if="state.data" class="gx-card gx-long-curves__body">
          <div class="gx-section__header"><i class="bi bi-graph-up" aria-hidden="true"></i><span class="gx-section__title">{{ state.data.title }}</span></div>
          <p>Enable saved profiles and this personality in All profile settings to use its curve.</p>
          <p v-if="!state.data.parked" class="gx-note">Editing requires fresh parked vehicle evidence.</p>
          <GalaxySettingRow v-if="preset" :key="state.data.page + ':preset'" :row="preset" :index="0"
            :busy="state.status !== 'ready' || !!state.pending" :disabled="!state.data.parked || !!state.error"
            :save-value="(index, value) => feed.previewValue(index, value)" @review="(index, direction) => feed.preview(index, direction)" @reset-default="index => feed.resetDefault(index)" />
          <template v-if="points && graph">
            <svg class="gx-long-curves__graph" viewBox="0 0 370 155" role="img" :aria-label="state.data.title + ' saved curve, 0 to 90 miles per hour'">
              <line x1="44" y1="28" x2="44" y2="116" class="gx-long-curves__axis"/><line x1="44" y1="116" x2="332" y2="116" class="gx-long-curves__axis"/>
              <text x="5" y="32">{{ graph.top.toFixed(2) }}</text><text x="5" y="118">{{ graph.bottom.toFixed(2) }}</text>
              <text x="43" y="140">0</text><text x="307" y="140">90 mph</text>
              <polyline :points="graph.line" class="gx-long-curves__line"/>
              <circle v-for="(point, index) in graph.coords" :key="index" :cx="point.x" :cy="point.y" r="4" class="gx-long-curves__point"/>
            </svg>
            <p class="gx-note">Saved custom points in {{ points[0].unit }}. Use the controls below to change one point at a time.</p>
            <div class="gx-long-curves__points">
              <GalaxySettingRow v-for="point in points" :key="state.data.page + ':' + point.index" :row="state.data.rows[point.index]" :index="point.index"
                :busy="state.status !== 'ready' || !!state.pending" :disabled="!state.data.parked || !!state.error"
                :save-value="(index, value) => feed.previewValue(index, value)" />
            </div>
          </template>
          <p v-else class="gx-note">{{ preset?.value === 'custom' ? 'Saved custom points are unavailable; review the saved profile.' : 'Choose the Custom preset to edit saved points. Preset curves remain managed by the vehicle profile.' }}</p>
          <button type="button" class="gx-btn gx-btn--tonal" @click="go('/driving/profiles')">All profile settings</button>
        </article>
        <GxDialog v-if="state.pending" labelledby="gx-longitudinal-curves-confirm-title" @close="feed.cancel()"><h3 id="gx-longitudinal-curves-confirm-title">Confirm Saved Preference</h3><p>{{ state.pending.question }}</p>
            <div class="gx-settings__controls gx-actions"><button type="button" class="gx-btn gx-btn--tonal" @click="feed.cancel()">Cancel</button>
              <button type="button" class="gx-btn" @click="feed.confirm()">Save</button></div></GxDialog>
      </template>
    </section>`,
}
