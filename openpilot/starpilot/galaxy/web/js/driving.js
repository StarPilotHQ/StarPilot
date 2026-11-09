import { GxState } from "./state.js"
import { MenuTile } from "./menu-tile.js"
export const DRIVING_PAGES = Object.freeze({
  aol: "aol",
  conditional: "conditional",
  curve: "curve",
  slc: "slc",
  torque: "torque",
  profiles: "profiles",
  traffic: "traffic",
  lane: "lane",
  lane_change: "lane_change",
})

export const DRIVING_GROUPS = Object.freeze([
  { title: "Steering and buttons", items: [
    { page: "aol", title: "Always On Lateral", detail: "Steering assistance independent of cruise control" },
    { page: "lane", title: "Lane centering", detail: "Saved steering behavior" },
    { page: "lane_change", title: "Lane changes", detail: "Saved lane-change choices" },
    { page: "torque", title: "Torque tuning", detail: "Saved steering tune and vehicle-source selection" },
  ] },
  { title: "Speed and longitudinal", items: [
    { page: "conditional", title: "Conditional driving modes", detail: "Experimental and Chill conditions" },
    { page: "curve", title: "Curve Speed Controller", detail: "Saved curve-speed choices" },
    { page: "slc", title: "Speed Limit Controller", detail: "Dashboard and qualified vision source choices" },
    { page: "profiles", title: "Longitudinal profiles", detail: "Saved acceleration, braking, and following profiles" },
    { page: "traffic", title: "Traffic Mode", detail: "Saved follow, jerk, and profile curves" },
  ] },
])

export function drivingPage(path) {
  const match = /^\/driving\/([a-z_]+)$/.exec(path)
  return match && DRIVING_PAGES[match[1]] || null
}

export const DrivingPage = {
  components: { GxState, MenuTile },
  props: { mode: { type: String, required: true }, go: { type: Function, required: true } },
  data: () => ({ groups: DRIVING_GROUPS }),
  template: `
    <section class="gx-driving" aria-label="Driving features">
      <header class="gx-settings__header gx-page-header"><div><h2>Driving Settings</h2>
        <p>Choose how StarPilot steers and controls speed.</p></div></header>
      <GxState v-if="mode !== 'local'">Local driving settings are unavailable in preview.</GxState>
      <template v-else>
        <section v-for="group in groups" :key="group.title" class="gx-driving__group">
          <h3>{{ group.title }}</h3>
          <div class="gx-grid">
            <MenuTile v-for="item in group.items" :key="item.page" icon="bi-sliders" :title="item.title" :description="item.detail" @select="go('/driving/' + item.page)" />
          </div>
        </section>
        <div class="gx-driving__extras gx-actions"><button type="button" class="gx-btn gx-btn--tonal" @click="go('/tuning/plots')">Live plots</button>
          <button type="button" class="gx-btn gx-btn--tonal" @click="go('/tuning')">Tuning and Analysis</button></div>
      </template>
    </section>`,
}
