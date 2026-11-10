import { ONROAD_LAYOUT_TEMPLATE } from "./onroad-layout-template.js"
import { GxState } from "./state.js"
import { guardUnload } from "./unload-guard.js"
import { PollTimer, connectionError } from "./polling.js"
import { reactive, watch } from "../vendor/vue/vue.esm-browser.js"
import { LayoutPreviewFeed, PREVIEW_SCENES, PreviewQueue, previewScene, sceneHidesWidget } from "./layout-preview.js"
import { LEAD_INFO, PROJECTION_WORDMARK, editorSnapshot, projectionPayload } from "./projection-layout.js"
import { GalaxySelect } from "./galaxy-select.js"
import { GxNotice } from "./notice.js"
import { GxDialog } from "./dialog.js"
import { GalaxySettingRow, switchRow } from "./galaxy-setting-row.js"
import { showDraftChange } from "./save-feedback.js"
import { snapTargets, snapMove, snapResize, snapThreshold } from "./layout-snap.js"

import { PROFILES, ROAD_COLORS, PATH_MODES, HEX, PROJECTION_DISPLAY_SWITCHES, layoutDocument, previewDocument, withDisplay, orderedWidgetIds, widgetPalette, withAlpha, widgetSize, placementLimits, clampBox, clampPlacement, overlapsReserved, previewPoint, validDocument, validSnapshot } from "./onroad-layout-document.js"
export { layoutDocument, previewDocument, withDisplay, orderedWidgetIds, widgetPalette, withAlpha, widgetSize, placementLimits, clampBox, clampPlacement, overlapsReserved, previewPoint, validDocument, validSnapshot } from "./onroad-layout-document.js"

const previewVisibilityKey = projection => "gx-layout-preview-visibility-v2:" + (projection ? "starport" : "comma")
const oldProjectionVisibilityKey = "gx-layout-preview-visibility-v2:android-auto"
function loadPreviewVisibility(projection) {
  const hidden = { large: {}, compact: {} }
  try {
    const currentKey = previewVisibilityKey(projection)
    const raw = localStorage.getItem(currentKey) ?? (projection ? localStorage.getItem(oldProjectionVisibilityKey) : null)
    const legacy = raw === null
    const saved = JSON.parse(raw ?? localStorage.getItem(currentKey.replace('-v2:', '-v1:')))
    for (const profile of PROFILES) {
      const widgets = saved?.[profile]
      if (!widgets || typeof widgets !== "object" || Array.isArray(widgets)) continue
      hidden[profile] = {
        ...hidden[profile], ...Object.fromEntries(Object.entries(widgets)
          .filter(([id, value]) => typeof value === "boolean" && !(projection && id === "car_exit") && !(legacy && id === "stopped_timer")))
      }
    }
  } catch { /* Preview controls still work when browser storage is unavailable. */ }
  return hidden
}
const clone = (value) => JSON.parse(JSON.stringify(value))
const snapPreferenceKey = "gx-layout-snap-v1"
function loadSnapPreference() {
  try { return localStorage.getItem(snapPreferenceKey) !== "0" } catch { return true }
}
const aidsPreferenceKey = "gx-layout-aids-v1"
function loadAidsPreference() {
  try { return localStorage.getItem(aidsPreferenceKey) !== "0" } catch { return true }
}
// The backdrop hides every movable widget, so only the set of placed widgets, not their positions, changes it.
const backdropKey = (document) => ({ ...document, widgetOrder: undefined,
  layouts: Object.fromEntries(Object.entries(document.layouts).map(([profile, widgets]) => [profile, Object.keys(widgets).sort()])) })

// Open maker pages; a route switch briefly mounts the next editor before the last one leaves.
let makerPagesOpen = 0

// Screen pixels a pointer may wobble before a press on a preview widget becomes a drag.
const DRAG_SLOP = 6

export class OnroadLayoutFeed {
  constructor({ publish, projection = false, unauthorized = () => { }, fetcher = (...args) => fetch(...args),
    later = (fn, ms) => setTimeout(fn, ms), cancelTimer = (id) => clearTimeout(id) }) {
    Object.assign(this, { publish, projection, unauthorized, fetcher, later, cancelTimer })
    this.active = false
    this.generation = 0
    this.request = this.timer = this.data = null
    this.poller = new PollTimer({ read: () => this.load(true), interval: 5000, later, cancel: cancelTimer })
    this.error = ""
    this.needsReload = false
  }

  stop() {
    this.poller.stop()
    this.active = false
    this.generation++
    this.request?.abort()
    if (this.timer !== null) this.cancelTimer(this.timer)
    this.request = this.timer = null
  }

  start() { this.stop(); this.active = true; this.data = null; this.needsReload = false; this.poller.start(); return this.load() }
  load(background = false) { return this.run(null, { background }) }
  save(document) {
    if (!this.data?.editable || this.needsReload || !validDocument(document, this.data.metadata)) return false
    return this.run({ revision: this.data.revision, document: clone(document) })
  }

  async run(body = null, { background = false } = {}) {
    if (!this.active) return
    if (this.request) {
      // Polls yield to anything in flight; an explicit reload replaces a pending poll.
      if (background || this.saving) return
      this.request.abort(); this.generation++; this.cancelTimer(this.timer)
      this.request = this.timer = null
    }
    const saving = body !== null, generation = ++this.generation, request = new AbortController()
    this.request = request
    this.saving = saving
    if (saving || !background || !this.data && !this.error) this.publish({ status: saving ? "saving" : "loading", notice: "" })
    const fail = (message) => {
      this.error = message
      this.needsReload ||= saving
      this.publish({ status: this.data ? "ready" : "unavailable", error: message, needsReload: this.needsReload })
    }
    this.timer = this.later(() => {
      if (!this.active || generation !== this.generation) return
      request.abort()
      this.generation++
      this.request = this.timer = null
      fail(saving ? "Saving timed out. Your edits are kept here, but the saved result is unknown. Reload before saving again." :
        "Reading colors and layouts timed out. Try reloading.")
    }, 5000)
    try {
      const response = await this.fetcher(this.projection ? "./api/starport/layout" : "./api/ui/layout", {
        credentials: "same-origin", cache: "no-store", signal: request.signal,
        ...(saving ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(this.projection ? projectionPayload(body, this.data.metadata) : body) } : {})
      })
      if (!this.active || generation !== this.generation) return
      let data = await response.json().catch(() => null)
      if (!this.active || generation !== this.generation) return
      if (response.status === 401 || ["access_unavailable", "setup_required"].includes(data?.code)) {
        this.stop(); this.unauthorized(); return
      }
      if (response.status === 409) throw new Error("Saved values or parked status changed. Your edits are kept here. Reload before saving again.")
      if (!response.ok) throw new Error(saving ? "Colors and layout could not be saved. Your edits are kept here. Reload before trying again." :
        "Colors and layout are unavailable. Try reloading.")
      const mapMoved = saving && data.mapDisabledElsewhere === true
      data = this.projection ? editorSnapshot(data) : withDisplay(data)
      if (!validSnapshot(data)) throw new Error("The device returned an unsupported colors and layout document. Reload to try again.")
      const initial = !this.data
      this.data = data
      this.error = ""
      this.needsReload = false
      this.publish({
        status: "ready", data, ...((saving || initial || !background) ? { draft: clone(data.document) } : {}), error: "", needsReload: false,
        notice: saving ? (this.projection ? "StarPort layout saved." : data.failed?.length ?
          "Layout saved, but these display options did not change: " + data.failed.join(", ") + ". Reload and try again." :
          "Colors and both layouts saved.") + (mapMoved ? " The map overlay is now off in the " + (this.projection ? "Big screen" : "StarPort") + " layout." : "") :
          !background && !initial ? "Reloaded the saved layout." : ""
      })
      return true
    } catch (error) {
      if (this.active && generation === this.generation) fail(connectionError(error))
      return false
    } finally {
      if (this.request === request) {
        if (this.timer !== null) this.cancelTimer(this.timer)
        this.request = this.timer = null
        this.saving = false
      }
    }
  }
}

export const LayoutWidgetPreview = {
  props: ["widget", "palette", "profile", "scene", "clock24Hour", "native", "art"],
  computed: {
    roadNameSize() { return Math.max(18, Math.min(this.widget.width, this.widget.height) * .075) },
  },
  methods: {
    withAlpha,
    modeLines(scene, profile) {
      const { mode, experimental } = previewScene(scene), aol = scene === "aol"
      if (profile === "compact" && (aol || experimental)) return aol ? ["Always On", "Lateral"] : ["ACC -", "Experimental"]
      return [mode]
    },
    pending(scene) { return scene === "general" || scene === "slc_pending" },
  },
  template: `
    <g aria-hidden="true" class="gx-layout-widget-art" :fill="palette.text || '#ffffff'">
      <template v-if="native || art?.url && art.sprites?.sprites?.[widget.id]">
        <svg v-if="art?.url && art.sprites?.sprites?.[widget.id]"
          :x="art.sprites.sprites[widget.id].offsetX || 0" :y="art.sprites.sprites[widget.id].offsetY || 0"
          :width="art.sprites.sprites[widget.id].width" :height="art.sprites.sprites[widget.id].height"
          :viewBox="[art.sprites.sprites[widget.id].x, art.sprites.sprites[widget.id].y, art.sprites.sprites[widget.id].width, art.sprites.sprites[widget.id].height].join(' ')"
          preserveAspectRatio="xMinYMin meet" data-preview-source="runtime">
          <image :href="art.url" :width="art.sprites.width" :height="art.sprites.height" />
        </svg>
        <g v-else data-preview-source="placeholder">
          <rect x="1" y="1" :width="widget.width - 2" :height="widget.height - 2" fill="#0c182044" stroke="#a6dfbe66" stroke-dasharray="5 5" />
          <text :x="widget.width / 2" :y="widget.height / 2" text-anchor="middle" :font-size="Math.min(24, widget.width / 10)">{{ widget.label }}</text>
        </g>
      </template>
      <template v-else>
      <template v-if="widget.kind === 'driving_mode_descriptions'">
        <text :x="widget.width / 2" :y="widget.height / 2" text-anchor="middle" dominant-baseline="middle"
          :font-size="profile === 'large' ? 48 : 21" font-weight="600" style="filter: drop-shadow(2px 2px 1px #000000aa)">
          <tspan v-for="(line, index) in modeLines(scene, profile)" :key="index" :x="widget.width / 2"
            :y="widget.height / 2 + (index - (modeLines(scene, profile).length - 1) / 2) * 25">{{ line }}</tspan>
        </text>
      </template>
      <template v-else-if="widget.kind === 'stopped_timer' && ['general', 'stopped'].includes(scene)">
        <text :x="widget.width / 2" :y="widget.height * 53 / 112" text-anchor="middle" dominant-baseline="middle" :font-size="widget.height * 64 / 112" font-weight="700" fill="#b67129"
          style="filter: drop-shadow(2px 2px 1px #000000aa)">2 minutes</text>
        <text :x="widget.width / 2" :y="widget.height * 101 / 112" text-anchor="middle" dominant-baseline="middle" :font-size="widget.height * 30 / 112" font-weight="500" fill="#fffffff2"
          style="filter: drop-shadow(2px 2px 1px #000000aa)">14 seconds</text>
      </template>
      <g v-else-if="widget.kind === 'weather'">
        <rect x="1" y="1" :width="widget.width - 2" :height="widget.height - 2" :rx="widget.height * .1"
          :fill="palette.cardFill" :stroke="palette.cardBorder" />
        <svg :x="widget.height * .1" :y="widget.height * .05" :width="widget.height * .8" :height="widget.height * .8" viewBox="0 0 80 80">
          <g fill="none" stroke="#c7aef7" stroke-width="3" stroke-linecap="round">
            <circle cx="27" cy="28" r="13" fill="#c7aef7" />
            <path d="M27 6v-4 M27 50v4 M5 28H1 M49 28h4 M11 12l-3-3 M43 12l3-3 M11 44l-3 3" />
          </g>
          <path d="M20 61a13 13 0 0 1 0-26 18 18 0 0 1 34-3 14 14 0 1 1 4 29Z" fill="#edf0ff" />
        </svg>
        <text :x="widget.height" :y="widget.height * .55" :font-size="profile === 'large' ? 44 : 24" font-weight="600">70°F</text>
        <text :x="widget.height" :y="widget.height * .88" :font-size="profile === 'large' ? 16 : 8" fill="#c7aef7">Open-Meteo</text>
      </g>
      <template v-else-if="widget.kind === 'clock'">
        <text :x="widget.width / 2" :y="widget.height / 2" text-anchor="middle" dominant-baseline="middle"
          :font-size="profile === 'large' ? 48 : 21" font-weight="600"
          style="filter: drop-shadow(2px 2px 1px #000000aa)">{{ clock24Hour ? '18:00' : '12:00 PM' }}</text>
      </template>
      <g v-else-if="widget.kind === 'now_playing'">
        <rect x="0" y="0" :width="widget.width" :height="widget.height" :rx="widget.height / 2" fill="#0f0d17" :fill-opacity="(widget.opacity ?? 85) / 100 * .92" />
        <circle :cx="widget.height / 2" :cy="widget.height / 2" r="30" fill="#c7aef7" />
        <rect :x="widget.height / 2 - 9" :y="widget.height / 2 - 12" width="6" height="24" rx="3" fill="#0f0d17" />
        <rect :x="widget.height / 2 + 3" :y="widget.height / 2 - 12" width="6" height="24" rx="3" fill="#0f0d17" />
        <text :x="widget.height + 4" y="40" font-size="30" font-weight="600" fill="#f5f2fc">Midnight City</text>
        <text :x="widget.height + 4" y="68" font-size="22" fill="#b8b2c8">M83</text>
        <rect :x="widget.height + 4" :y="widget.height - 16" :width="widget.width - widget.height * 1.5 - 4" height="4" rx="2" fill="#ffffff" fill-opacity=".2" />
        <rect :x="widget.height + 4" :y="widget.height - 16" :width="(widget.width - widget.height * 1.5 - 4) * .38" height="4" rx="2" fill="#c7aef7" />
      </g>
      <g v-else-if="widget.kind === 'jetlink_status'">
        <rect x="0" y="0" :width="widget.width" :height="widget.height" :rx="widget.height / 2" fill="#0f0d17" :fill-opacity="(widget.opacity ?? 85) / 100 * .92" />
        <circle :cx="widget.height / 2" :cy="widget.height / 2" r="9" fill="#46c86e" />
        <text :x="widget.height - 6" y="38" font-size="28" font-weight="600" fill="#f5f2fc">Link up</text>
        <text :x="widget.height - 6" y="66" font-size="22" fill="#b8b2c8">Jetlink • iPhone</text>
        <text :x="widget.width - 70" :y="widget.height / 2 + 12" text-anchor="end" font-size="34" font-weight="600" fill="#f5f2fc">24.6</text>
        <text :x="widget.width - 30" :y="widget.height / 2 + 12" text-anchor="end" font-size="22" fill="#b8b2c8">ms</text>
      </g>
      <template v-else-if="widget.kind === 'speed_limit'">
        <text x="0" y="10" font-size="24" font-weight="500">LIMIT</text>
        <path v-if="pending(scene)" d="M90 12 V-8 M84 -2 L90 -8 L96 -2" fill="none" :stroke="palette.text || '#ffffff'" stroke-width="2" />
        <text x="0" y="66" font-size="58" font-weight="700">45<tspan dx="10" font-size="36" font-weight="500">+5</tspan></text>
      </template>
      <template v-else-if="widget.kind === 'speed_limit_actions'">
        <template v-if="pending(scene)">
          <line :x1="widget.width / 2" :x2="widget.width / 2" :y1="profile === 'large' ? 46 : 6" :y2="profile === 'large' ? 98 : 42" stroke="#9bb2adaa" />
          <text v-for="(label, index) in ['Accept', 'Decline']" :key="label" :x="widget.width * (index ? .75 : .25)"
            :y="profile === 'large' ? 72 : 24" text-anchor="middle" dominant-baseline="middle"
            :font-size="profile === 'large' ? 64 : 50" font-weight="500">{{ label }}</text>
        </template>
        <text v-else :x="widget.width / 2" :y="profile === 'large' ? 72 : 24" text-anchor="middle"
          dominant-baseline="middle" :font-size="profile === 'large' ? 48 : 28">Toyota guidance</text>
      </template>
      <g v-else-if="widget.kind === 'torque_bar'" fill="none" stroke="white" stroke-linecap="round">
        <path :d="'M 12 ' + widget.height * .8 + ' Q ' + widget.width / 2 + ' ' + widget.height * .55 + ' ' + (widget.width - 12) + ' ' + widget.height * .8" :stroke-width="widget.height * .18" opacity=".3" />
        <path :d="'M ' + widget.width / 2 + ' ' + widget.height * .68 + ' Q ' + widget.width * .65 + ' ' + widget.height * .68 + ' ' + widget.width * .8 + ' ' + widget.height * .74" :stroke-width="widget.height * .18" opacity=".9" />
      </g>
      <g v-else-if="widget.kind === 'pip_camera'">
        <circle :cx="widget.width / 2" :cy="widget.height / 2" :r="widget.width / 2 - 3" fill="#152c39" stroke="#5ee5ee" stroke-width="3" />
        <text :x="widget.width / 2" :y="widget.height / 2" text-anchor="middle" :font-size="widget.height / 10">{{ widget.label }}</text>
      </g>
      <g v-else-if="['nav_home', 'nav_work', 'bookmark'].includes(widget.kind)" :opacity="(widget.opacity ?? 100) / 100">
        <rect v-if="widget.background !== false" x="1" y="1" :width="widget.width - 2" :height="widget.height - 2" rx="17" fill="#0f0d17f5" stroke="#c7aef7" stroke-width="3" />
        <g v-if="widget.display === 'icons'" transform="translate(23 23) scale(2.6667)" fill="none" stroke="#c7aef7" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">
          <path v-if="widget.kind === 'nav_home'" d="M3 11 L12 3 L21 11 M5 9 V21 H10 V14 H14 V21 H19 V9" />
          <path v-else-if="widget.kind === 'bookmark'" d="M6 3 H18 V21 L12 16 L6 21 Z" />
          <path v-else d="M8 7 V4 H16 V7 M3 7 H21 V21 H3 Z M3 12 L10 15 H14 L21 12 M10 13 V17 H14 V13" />
        </g>
        <template v-else>
        <text :x="widget.width / 2" :y="widget.kind === 'bookmark' ? 48 : 43" text-anchor="middle" :font-size="widget.kind === 'bookmark' ? 34 : 25" :font-weight="widget.kind === 'bookmark' ? 600 : 400" fill="#c7aef7">{{ widget.kind === 'bookmark' ? widget.label : 'Navigate' }}</text>
        <text :x="widget.width / 2" :y="widget.kind === 'bookmark' ? 89 : 88" text-anchor="middle" :font-size="widget.kind === 'bookmark' ? 25 : 34" :font-weight="widget.kind === 'bookmark' ? 400 : 600" fill="#c7aef7">{{ widget.kind === 'bookmark' ? 'Save event' : widget.label }}</text>
        </template>
      </g>
      <g v-else-if="widget.kind === 'car_exit'" :opacity="(widget.opacity ?? 100) / 100">
        <rect v-if="widget.background !== false" x="1" y="1" :width="widget.width - 2" :height="widget.height - 2" rx="15" fill="#0f0d17a6" stroke="#c7aef7c8" stroke-width="3" />
        <g data-preview-icon="door-exit" fill="none" stroke="#c7aef7e6" stroke-width="5" stroke-linecap="round" stroke-linejoin="round">
          <path d="M54 20 H24 V76 H54" />
          <path d="M38 48 H78 M67 37 L78 48 L67 59" />
        </g>
      </g>
      <g v-else-if="widget.kind === 'jetlink_status'">
        <rect :width="widget.width" :height="widget.height" :rx="widget.height / 2" fill="#0f0d17" :fill-opacity="235 / 255 * (widget.opacity ?? 85) / 100" />
        <circle :cx="widget.height / 2" :cy="widget.height / 2" r="9" fill="#46c86e" />
        <text :x="widget.height - 6" y="40" font-size="28" font-weight="600" fill="#f5f2fc">Link up</text>
        <text :x="widget.height - 6" y="68" font-size="22" fill="#b8b2c8">Jetlink • iPhone</text>
        <text :x="widget.width - 70" y="52" text-anchor="end" font-size="34" font-weight="600" fill="#f5f2fc">24.6</text>
        <text :x="widget.width - 30" y="52" text-anchor="end" font-size="22" fill="#b8b2c8">ms</text>
      </g>
      <g v-else-if="widget.kind === 'nav_card'">
        <rect x="1" y="1" :width="widget.width - 2" :height="widget.height - 2" rx="39" fill="#0f0d17f5" stroke="#a07edcbe" stroke-width="2" />
        <text x="24" y="66" font-size="54" font-weight="700" fill="white">500 ft</text>
        <text x="24" y="100" font-size="30" font-weight="600" fill="#ded6f4">Turn right onto Las Vegas Blvd</text>
        <g v-for="(lane, i) in [['S', 0], ['S', 0], ['S', 0], ['R', 1], ['R', 1]]" :key="i" :transform="'translate(' + ((widget.width - 320) / 2 + i * 66) + ' 116)'">
          <rect width="56" height="44" rx="13" :fill="lane[1] ? '#4e3e80' : '#222030'" />
          <g transform="translate(28 22)" fill="none" :stroke="lane[1] ? '#ffffff' : '#70688a'" stroke-width="4.5" stroke-linecap="round" stroke-linejoin="round">
            <path v-if="lane[0] === 'S'" d="M0 13 V-12 M-8 -4 L0 -12 L8 -4" />
            <path v-else d="M-8 13 V0 Q-8 -7 -1 -7 H10 M4 -13 L10 -7 L4 -1" />
          </g>
        </g>
        <rect x="24" y="171" :width="widget.width - 48 - 190" height="6" rx="3" fill="#a07edc3c" />
        <rect x="24" y="171" :width="(widget.width - 48 - 190) * .35" height="6" rx="3" fill="#b296ff" />
        <text :x="widget.width - 24" y="182" text-anchor="end" font-size="22" font-weight="600" fill="#ebe1ff">ETA 3:51 PM</text>
      </g>
      <g v-else-if="widget.kind === 'nav_map'">
        <defs>
          <radialGradient id="gx-nav-map-fade" cx=".5" cy=".55" r=".75"><stop offset=".55" stop-color="white" /><stop offset="1" stop-color="white" stop-opacity="0" /></radialGradient>
          <mask id="gx-nav-map-mask"><rect :width="widget.width" :height="widget.height" :rx="Math.min(widget.width, widget.height) * .08" fill="url(#gx-nav-map-fade)" /></mask>
        </defs>
        <g mask="url(#gx-nav-map-mask)" :transform="'scale(' + widget.width / 600 + ' ' + widget.height / 450 + ')'" fill="none" stroke-linecap="round" stroke-linejoin="round">
          <g :opacity="(widget.opacity ?? 50) / 100">
          <rect width="600" height="450" fill="#0b0f16" opacity=".45" />
          <g stroke="#000000a8" vector-effect="non-scaling-stroke">
            <path d="M-20 120 Q200 150 640 90 M90 -20 L130 480 M430 -20 Q400 200 470 480 M-20 330 L640 360 M260 -20 L250 480" stroke-width="11" vector-effect="non-scaling-stroke" />
          </g>
          <path d="M-20 120 Q200 150 640 90 M-20 330 L640 360" stroke="#ebf0f5" stroke-width="6" vector-effect="non-scaling-stroke" />
          <path d="M90 -20 L130 480 M430 -20 Q400 200 470 480" stroke="#bec8d2" stroke-width="4" vector-effect="non-scaling-stroke" />
          <path d="M260 -20 L250 480" stroke="#ffc45c" stroke-width="8" vector-effect="non-scaling-stroke" />
          </g>
          <path d="M300 330 Q258 300 254 250 L262 -20" stroke="#8f6bff55" stroke-width="20" vector-effect="non-scaling-stroke" />
          <path d="M300 330 Q258 300 254 250 L262 -20" stroke="#a98bff" stroke-width="9" vector-effect="non-scaling-stroke" />
        </g>
        <g :transform="'translate(' + widget.width / 2 + ' ' + widget.height * .7 + ')'">
          <circle r="22" fill="#8f6bff40" /><path d="M0 -17 L13 13 L0 6 L-13 13 Z" fill="white" stroke="#6e4fe0" stroke-width="3" stroke-linejoin="round" />
        </g>
        <g :transform="'translate(' + widget.width / 2 + ' ' + (widget.height * .68 + 38) + ')'">
          <rect :x="-roadNameSize * 4.4" y="0" :width="roadNameSize * 8.8" :height="roadNameSize * 1.55" :rx="roadNameSize * .78" fill="#080c14be" />
          <text x="0" :y="roadNameSize * 1.12" text-anchor="middle" :font-size="roadNameSize" fill="#eceef6">Desert Inn Road</text>
        </g>
      </g>
      <template v-else>
        <rect :width="widget.width" :height="widget.height" :fill="palette.cardFill" :stroke="palette.cardBorder" />
        <text :x="widget.width / 2" :y="widget.height / 2" text-anchor="middle" :font-size="Math.min(32, widget.width / 10)">{{ widget.label }}</text>
      </template>
      </template>
    </g>`,
}

export const OnroadLayoutPage = {
  components: { GxState, LayoutWidgetPreview, GalaxySelect, GxNotice, GxDialog, GalaxySettingRow },
  props: {
    projection: { type: Boolean, default: false }, mode: { type: String, required: true }, unauthorized: { type: Function, required: true },
    initialPanel: { type: String, default: "widgets" }
  },
  emits: ["close", "target"],
  setup(props) {
    const state = reactive({
      status: "idle", data: null, draft: null, needsReload: false, error: "", notice: "",
      profile: "large", scene: "general", selected: null, layerDrag: null, drag: null, discard: null, leavePresentation: null, removeConfirm: null, colorError: "", placementError: "",
      inspectorPanel: ["widgets", "edit", "road", "display"].includes(props.initialPanel) && !props.projection ? props.initialPanel : "widgets", showFavoriteZones: false, showEditingAids: loadAidsPreference(),
      aboutOpen: false, tipsOpen: false, moreOpen: false, widgetsHelpOpen: false, stuck: { leaveBar: false, pinned: false, sections: false }, openColor: null, art: { status: "idle", error: "", url: null, sprites: null }, backdrop: { status: "idle", error: "", url: null },
      hiddenPreviewWidgets: loadPreviewVisibility(props.projection), sceneHiddenPreviewWidgets: {}, snapEnabled: loadSnapPreference(), snapGuides: [], history: { undo: [], redo: [], group: null }
    })
    let deferred = null
    const apply = (update) => {
      if (state.drag || state.layerDrag) { deferred = { ...deferred, ...update }; return }
      if (deferred) {
        update = { ...deferred, ...update }
        deferred = null
        if (state.draft && state.data && JSON.stringify(state.draft) !== JSON.stringify(state.data.document)) {
          update = { ...update }
          delete update.draft
          if (update.data && update.data.revision !== state.data.revision) {
            update.needsReload = true
            update.error = "Saved values changed while editing. Your edits are kept here. Reload before saving again."
          }
        }
      }

      if (update.data && state.data && !Object.hasOwn(update, "draft") && JSON.stringify(update.data.document) !== JSON.stringify(state.data.document)) {
        if (JSON.stringify(state.draft) !== JSON.stringify(state.data.document)) {
          update.needsReload = true
          update.error = "Saved values changed while editing. Your edits are kept here. Reload saved to review them."
        } else update.draft = clone(update.data.document)
      }
      if (state.needsReload && update.data && !Object.hasOwn(update, "draft")) {
        update.needsReload = true
        update.error = state.error
      }
      if (update.data) {
        if (!state.data && update.data.activeProfile) state.profile = update.data.activeProfile
        if (!update.data.metadata.profiles[state.profile]) state.profile = Object.keys(update.data.metadata.profiles)[0]
        if (!Object.hasOwn(update.data.metadata.profiles[state.profile].widgets, state.selected) ||
            update.data.metadata.profiles[state.profile].widgets[state.selected]?.hidden === true)
          state.selected = Object.keys(update.data.metadata.profiles[state.profile].widgets)[0] || null
        state.colorError = ""
        state.placementError = ""
      }
      const draftChanged = Object.hasOwn(update, "draft") && JSON.stringify(state.draft) !== JSON.stringify(update.draft)
      for (const [key, value] of Object.entries(update)) {
        if (JSON.stringify(state[key]) !== JSON.stringify(value)) state[key] = value
      }
      if (draftChanged || ["Colors and both layouts saved.", "StarPort layout saved."].some(saved => update.notice?.startsWith(saved))) state.history = { undo: [], redo: [], group: null }
    }
    const feed = new OnroadLayoutFeed({ projection: props.projection, unauthorized: props.unauthorized, publish: apply })
    watch(() => !!state.drag || !!state.layerDrag, (dragging) => { if (!dragging && deferred) apply({}) }, { flush: "sync" })
    const queue = new PreviewQueue()
    const backdropFeed = new LayoutPreviewFeed({ backdrop: true, unauthorized: props.unauthorized, queue,
      publish: update => Object.assign(state.backdrop, update) })
    const artFeed = new LayoutPreviewFeed({ art: true, unauthorized: props.unauthorized, queue,
      publish: update => Object.assign(state.art, update) })
    watch(() => [state.draft && JSON.stringify(state.draft), state.profile, state.scene, !!state.drag,
                 !!state.layerDrag, state.data?.revision, state.data?.editable], () => {
      if (props.mode !== "local" || !state.data?.editable || !state.draft) {
        artFeed.stop()
        return
      }
      if (!artFeed.active) artFeed.start()
      const document = props.projection ? projectionPayload({document: state.draft}, state.data.metadata).document : layoutDocument(state.draft)
      artFeed.update(document, props.projection ? "projection" : state.profile, state.scene, !!state.drag || !!state.layerDrag)
    }, {flush: "sync"})
    // The real screen behind the arrangement: status border, road, alerts and sidebar, without the movable widgets.
    watch(() => [state.draft && JSON.stringify(backdropKey(state.draft)), state.profile, state.scene,
                 state.data?.revision, state.data?.editable], () => {
      if (props.mode !== "local" || !state.data?.editable || !state.draft) {
        backdropFeed.stop()
        return
      }
      if (!backdropFeed.active) backdropFeed.start()
      const document = props.projection ? projectionPayload({document: state.draft}, state.data.metadata).document : previewDocument(state.draft)
      backdropFeed.update(document, props.projection ? "projection" : state.profile, state.scene, false, backdropKey(state.draft))
    }, {flush: "sync"})
    watch(() => props.mode, (mode) => {
      if (mode !== "local") { backdropFeed.stop(); artFeed.stop() }
    })
    return { state, feed, backdropFeed, artFeed, scenes: PREVIEW_SCENES, projectionWordmark: PROJECTION_WORDMARK }
  },
  computed: {
    busy() { return ["loading", "saving"].includes(this.state.status) },
    editAvailable() { return !!this.state.data?.editable && !this.state.error && !this.state.needsReload && !this.state.discard },
    editable() { return this.editAvailable && !this.busy },
    dirty() { return !!this.state.draft && JSON.stringify(this.state.draft) !== JSON.stringify(this.state.data.document) },
    stockChanged() { return !!this.state.draft && JSON.stringify(this.state.draft) !== JSON.stringify(this.state.data.defaults) },
    canUndo() { return this.state.history.undo.length > 0 },
    canRedo() { return this.state.history.redo.length > 0 },
    pendingLeave() { return typeof this.state.discard === "function" || ["back", "device", "projection"].includes(this.state.discard) },
    inlineLeavePrompt() { return (this.pendingLeave || this.state.discard === "reload") && this.state.leavePresentation !== "modal" },
    modalLeavePrompt() { return this.pendingLeave && this.state.leavePresentation === "modal" },
    saveAvailable() { return !!this.state.data?.editable && !this.state.error && !this.state.needsReload && this.dirty && !this.state.drag && !this.state.layerDrag },
    canSavePending() { return this.saveAvailable && !this.busy },
    profile() {
      const profile = this.state.data?.metadata.profiles[this.state.profile], cruise = profile?.widgets?.cruise_limits
      // Speed-limit sources draw inside the cruise card or as a small card under it, so its box grows to match.
      if (!cruise?.sourcesExtent || this.state.draft?.speedSources !== true) return profile
      const grow = this.state.draft.speedSourcesCard === true ? cruise.sourcesExtent.card : cruise.sourcesExtent.inline
      return { ...profile, widgets: { ...profile.widgets, cruise_limits: { ...cruise, height: cruise.height + grow } } }
    },
    // Only the scenes that look different on this screen.
    availableScenes() {
      const screen = this.projection ? "projection" : this.state.profile
      return this.scenes.filter(scene => !scene.hiddenOn?.includes(screen))
    },
    sceneGroups() {
      return [["Editing", scene => scene.id === "general"], ["Driving", scene => scene.id !== "general" && !/^(cem|slc)_/.test(scene.id)], ["Conditional experimental", scene => scene.id.startsWith("cem_")],
        ["Speed limit", scene => scene.id.startsWith("slc_")]].map(([label, test]) => ({ label, scenes: this.availableScenes.filter(test) }))
    },
    sceneBorder() { return previewScene(this.state.scene).border },
    expandedRoad() { return !this.projection && this.state.profile === "large" && this.displayValue("DeveloperSidebar") !== "On" },
    roadBounds() { return this.expandedRoad ? { ...this.profile.bounds, width: this.profile.bounds.width + 300 } : this.profile.bounds },
    favoriteZones() {
      const zones = this.profile?.inputZones || []
      if (this.state.profile !== "large" || this.projection) return zones
      const height = 180 * Math.max(.35, Math.min(this.roadBounds.width / 2160, this.roadBounds.height / 1080))
      return zones.map(zone => ({ ...zone, y: this.roadBounds.y + this.roadBounds.height - height, height }))
    },
    quickSelectShelves() {
      if (this.projection || this.state.profile !== 'large') return []
      return (this.profile?.quickSelectShelves || []).map(zone => ({ ...zone,
        conflicts: ['current_speed', 'cruise_limits'].filter(id => {
          const widget = this.widgets.find(widget => widget.id === id)
          const at = this.layout?.[id]
          if (!widget || !at?.enabled) return false
          const x = this.previewX(id)
          return x < zone.x + zone.width && zone.x < x + widget.width && at.y < zone.y + zone.height && zone.y < at.y + widget.height
        }) }))
    },
    quickSelectBlocked() { return this.quickSelectShelves.length === 2 && this.quickSelectShelves.every(zone => zone.conflicts.length) },
    // Widgets reach the whole screen only while the developer sidebar is hidden; otherwise the road beside it.
    limitProfile() {
      if (this.expandedRoad || this.projection || !this.profile) return this.profile
      return { ...this.profile, widgets: Object.fromEntries(Object.entries(this.profile.widgets).map(([id, widget]) => {
        if (!widget.expandable) return [id, widget]
        const { bounds, ...narrow } = widget
        return [id, narrow]
      })) }
    },
    // The last screen for this profile stays up while the next scene renders, instead of flashing the sketch.
    nativeBackdropReady() { return !!this.state.backdrop.url && this.state.backdrop.profile === (this.projection ? "projection" : this.state.profile) },
    layout() { return this.state.draft?.layouts[this.state.profile] },
    projectionHasPipWidget() {
      return ["pip_left", "pip_right"].some(id => this.state.draft?.layouts?.large?.[id]?.enabled === true)
    },
    widgets() { return Object.entries(this.profile?.widgets || {})
      .filter(([id, widget]) => widget.hidden !== true && (widget.available !== false || this.layout?.[id]?.enabled))
      .map(([id, widget]) => ({ id, ...widget })) },
    layerWidgets() {
      return orderedWidgetIds(this.state.draft, this.state.data.metadata, this.state.profile)
        .slice().reverse().map(id => this.widgets.find(widget => widget.id === id)).filter(widget => widget && this.layout[widget.id].enabled)
    },
    listWidgets() { return this.state.layerDrag ? this.state.layerDrag.rows.map(id => this.widgets.find(widget => widget.id === id)) : this.layerWidgets },
    selectionBox() {
      if (!this.state.layerDrag) return null
      const widget = this.renderWidgets.find(widget => widget.id === this.state.selected)
      if (!widget) return null
      return {
        x: this.previewX(this.state.selected), y: this.selectedPosition.y - widget.visualInsetTop,
        width: widget.width, height: widget.height + widget.visualInsetTop + widget.visualInsetBottom
      }
    },
    activeWidgets() { return this.widgets.filter(({ id }) => this.layout[id].enabled) },
    renderWidgets() {
      return this.layerWidgets.filter(item => !this.previewHidden(item.id)).reverse().map((item) => {
        const below = item.visualInsetTop && this.layout[item.id].y < item.visualInsetTop
        const widget = {
          ...item, visualInsetTop: below ? 0 : item.visualInsetTop || 0,
          visualInsetBottom: below ? item.visualInsetTop : 0, visualHeaderY: below ? item.height + 8 : -(item.visualInsetTop || 0)
        }
        const [width, height] = widgetSize(widget, this.layout[widget.id])
        return {
          ...widget, width, height, ...(widget.iconSize ? { display: this.layout[widget.id].display } : {}),
          ...(widget.opacity ? { opacity: this.layout[widget.id].opacity } : {}),
          ...(widget.backgroundToggle ? { background: this.layout[widget.id].background } : {}),
          ...(widget.collapseToggle ? { collapse: this.layout[widget.id].collapse } : {})
        }
      })
    },
    inactiveWidgets() { return this.widgets.filter(({ id, available }) => available !== false &&
      (!this.layout[id].enabled || (this.state.drag?.fromTray && this.state.drag.id === id))) },
    availableProfiles() { return this.state.data?.supportedProfiles || (this.state.data?.activeProfile ? [this.state.data.activeProfile] : []) },
    selectedWidget() { return this.profile?.widgets[this.state.selected] },
    selectedPosition() { return this.layout?.[this.state.selected] },
    selectedSize() { return this.selectedWidget ? widgetSize(this.selectedWidget, this.selectedPosition) : [0, 0] },
    // A finger covers the preview chip, so touch drags show the alignment in the sticky save bar.
    snapInBar() { return !!(this.state.drag?.touch && this.state.snapGuides.length) },
    snapSummary() {
      const guides = this.state.snapGuides, centered = new Set(guides.filter(guide => guide.kind === "center").map(guide => guide.axis))
      if (centered.size === 2) return "Centered on the screen"
      // A centered axis is described once; the other axis still says what it lines up with.
      const labels = [...new Set(guides.filter(guide => !centered.has(guide.axis)).map(guide => guide.label))]
      const others = labels.filter(label => label !== "the edge").slice(0, 2)
      const text = [centered.has("x") ? "centered across" : centered.has("y") ? "centered top to bottom" : "",
        labels.includes("the edge") ? "at the edge" : "", others.length ? "lined up with " + others.join(" and ") : ""].filter(Boolean).join(", ")
      return text && text[0].toUpperCase() + text.slice(1)
    },
    selectedColors() { return this.selectedWidget ? this.widgetColors(this.state.selected) : {} },
    colorFields() {
      return (this.state.data?.metadata.paletteFields || []).filter((field) =>
        Object.hasOwn(this.selectedWidget?.colors || {}, field.id))
    },
    roadStyle() { return this.state.draft?.roadColors[this.state.profile] || {} },
    roadMode() { const mode = this.roadStyle.pathMode || 'acceleration'; return mode === 'default' ? 'acceleration' : mode },
    roadColors() { return { ...Object.fromEntries((this.state.data?.metadata.roadColorFields || []).map(field => [field.id, field.default])), ...this.roadStyle } },
    roadGradient() {
      if (this.roadMode === 'rainbow') return Array.from({ length: 12 }, (_, i) => ({
        offset: i / 11,
        color: 'hsl(' + i / 11 * 120 + ',100%,50%)', alpha: .5 - .4 * i / 11
      }))
      if (this.roadMode === 'color') return [1, .55, .10].map((factor, i) => ({
        offset: 1 - i / 2,
        color: this.roadColors.path.slice(0, 7), alpha: Math.floor(this.alpha(this.roadColors.path) * factor) / 255
      }))
      return [{ offset: 0, color: '#72ff5c', alpha: 0 }, { offset: .5, color: '#72ff5c', alpha: 89 / 255 },
      { offset: 1, color: '#0df87a', alpha: 102 / 255 }]
    },
    limits() { return this.selectedWidget ? placementLimits(this.limitProfile, this.state.selected, this.layout) : null },
    displayGroups() {
      const groups = []
      for (const row of this.state.data?.display || []) {
        if (row.group === "widget") continue
        const group = groups.find(item => item.name === row.group) || groups[groups.push({ name: row.group, rows: [] }) - 1]
        group.rows.push(row)
      }
      return groups
    },
    stockConfidence() { return this.displayValue("StockConfidenceBallWidget") === "Off" },
  },
  mounted() {
    if (this.mode === "local") this.feed.start()
    this._stopUnloadGuard = guardUnload(() => this.dirty || this.state.status === "saving")
    // Double-tap zoom stays off across the whole page, app bar included, while a maker page is open.
    if (makerPagesOpen++ === 0) document.documentElement.classList.add("gx-maker-open")
    this.observePinned()
  },
  updated() { this.observePinned() },
  beforeUnmount() { this.cancelDrag(); this.feed.stop(); this.backdropFeed.stop(); this.artFeed.stop(); this._stopUnloadGuard(); this._pinObserver?.disconnect(); this.unwatchRelease(); this._closeFloating?.()
    if (--makerPagesOpen === 0) document.documentElement.classList.remove("gx-maker-open")
    if (this._onPinScroll) window.removeEventListener("scroll", this._onPinScroll, { capture: true })
  },
  methods: {
    previewShift(id) {
      return this.expandedRoad ? (this.profile.widgets[id]?.anchorShift || 0) * 300 : 0
    },
    // A position saved for the wider screen is shown pulled inside the road that is showing, as the device does.
    previewX(id) {
      const x = this.layout[id].x + this.previewShift(id), widget = this.profile.widgets[id]
      if (!widget?.expandable) return x
      const road = this.roadBounds
      return Math.max(road.x, Math.min(x, road.x + road.width - widgetSize(widget, this.layout[id])[0]))
    },
    previewSnapTargets(id) {
      if (!this.expandedRoad) return snapTargets(this.limitProfile, this.layout, id, key => !this.previewHidden(key))
      const widget = this.profile.widgets[id], bounds = widget.bounds || this.profile.bounds, shift = this.previewShift(id)
      const profile = { ...this.profile, widgets: { ...this.profile.widgets, [id]: { ...widget, bounds: { ...bounds, x: bounds.x + shift } } } }
      const layout = Object.fromEntries(Object.entries(this.layout).map(([key, at]) => [key, { ...at, x: this.previewX(key) }]))
      return snapTargets(profile, layout, id, key => !this.previewHidden(key))
        .map(target => ({ ...target, value: target.value - (target.axis === "x" ? shift : 0) }))
    },
    switchRow,
    runtimeWidget(id) { return this.profile?.runtimePreviewWidgets?.includes(id) === true },
    toggleSnap() {
      if (this.state.drag || this.state.layerDrag) return
      this.state.snapEnabled = !this.state.snapEnabled
      try { localStorage.setItem(snapPreferenceKey, this.state.snapEnabled ? "1" : "0") } catch { }
    },
    toggleEditingAids() {
      this.state.showEditingAids = !this.state.showEditingAids
      try { localStorage.setItem(aidsPreferenceKey, this.state.showEditingAids ? "1" : "0") } catch { }
    },
    sceneHidden(id) { return sceneHidesWidget(this.state.scene, this.state.profile, id, this.layout, this.profile?.widgets) },
    previewHidden(id) {
      if (this.projection && id === "car_exit") return false
      if (this.sceneHidden(id)) return true
      const hidden = this.state.scene === "general" ? this.state.hiddenPreviewWidgets : this.state.sceneHiddenPreviewWidgets[this.state.scene]
      return hidden?.[this.state.profile]?.[id] === true
    },
    visibilityTitle(id, label) {
      return this.sceneHidden(id) ? label + ' is hidden in this scene. Choose General edit to arrange it.' :
        (this.previewHidden(id) ? 'Show ' : 'Hide ') + label + ' in this preview only'
    },
    togglePreviewVisibility(id) {
      if (this.state.drag || this.state.layerDrag || (this.projection && id === "car_exit") || this.sceneHidden(id)) return
      const preferences = this.state.scene === "general" ? this.state.hiddenPreviewWidgets :
        (this.state.sceneHiddenPreviewWidgets[this.state.scene] ||= {})
      const hidden = preferences[this.state.profile] ||= {}
      hidden[id] = !hidden[id]
      if (this.state.scene === "general") {
        try { localStorage.setItem(previewVisibilityKey(this.projection), JSON.stringify(this.state.hiddenPreviewWidgets)) } catch { }
      }
    },
    selectWidget(id) {
      this.state.selected = id
      this.state.inspectorPanel = "edit"
    },
    toggleColor(key) {
      this.finishColorEdit()
      this.state.openColor = this.state.openColor === key ? null : key
      // Bring an opened color's picker fully into view.
      if (this.state.openColor) this.$nextTick?.(() => this.$el?.querySelector?.(".gx-layout__color.is-open")?.scrollIntoView?.({ block: "nearest", behavior: "smooth" }))
    },
    // The more menu and the widget help close on any press outside them, including the app bar.
    toggleFloating(key, ref) {
      this.state[key] = !this.state[key]
      this._closeFloating?.()
      if (!this.state[key] || typeof document === "undefined") return
      const outside = (event) => { if (!this.$refs[ref]?.contains(event.target)) { this.state[key] = false; this._closeFloating?.() } }
      document.addEventListener("pointerdown", outside, true)
      this._closeFloating = () => { document.removeEventListener("pointerdown", outside, true); this._closeFloating = null }
    },
    closeFloating(key) {
      this.state[key] = false
      this._closeFloating?.()
    },
    // Keeps a focused editor field clear of the pinned save bar, preview and tabs once the keyboard opens.
    revealField(event) {
      const field = event.target
      if (!field?.matches?.("input:not([type=range]):not([type=checkbox]), .gx-field")) return
      setTimeout(() => {
        const tabs = this.$el?.querySelector?.(".gx-layout__sections")?.getBoundingClientRect()
        const view = window.visualViewport, top = (tabs?.bottom || 0) + 12
        const rect = field.getBoundingClientRect(), bottom = (view ? view.height + view.offsetTop : window.innerHeight) - 12
        if (rect.top < top) window.scrollBy(0, rect.top - top)
        else if (rect.bottom > bottom) window.scrollBy(0, rect.bottom - bottom)
      }, 350)
    },
    // Narrow screens pin the save bar and preview; the editor tabs stick just below their measured height.
    observePinned() {
      if (typeof ResizeObserver === "undefined" || !this.$el?.style) return
      this._pinObserver ||= new ResizeObserver(() => {
        for (const [name, ref] of [["--layout-bar-h", "leaveBar"], ["--layout-pin-h", "pinned"]])
          if (this.$refs[ref]) this.$el.style.setProperty(name, this.$refs[ref].getBoundingClientRect().height + "px")
      })
      // Page-colored fills only appear while a piece is actually pinned, so they never show over the backdrop otherwise.
      if (!this._onPinScroll) {
        this._onPinScroll = () => {
          for (const ref of ["leaveBar", "pinned", "sections"]) {
            const element = this.$refs[ref], style = element && getComputedStyle(element)
            const stuck = style?.position === "sticky" && element.getBoundingClientRect().top <= (parseFloat(style.top) || 0) + 1
            if (this.state.stuck[ref] !== stuck) this.state.stuck[ref] = stuck
          }
        }
        window.addEventListener("scroll", this._onPinScroll, { passive: true, capture: true })
        this._onPinScroll()
      }
      for (const ref of ["leaveBar", "pinned"]) {
        const element = this.$refs[ref]
        if (element && this._pinned?.[ref] !== element) {
          if (this._pinned?.[ref]) this._pinObserver.unobserve(this._pinned[ref])
          this._pinObserver.observe(element)
          this._pinned = { ...this._pinned, [ref]: element }
        }
      }
    },
    reorderLayer(id, target) {
      if (!this.editable || this.state.drag || id === target ||
        this.profile.widgets[id]?.frontmost || this.profile.widgets[target]?.frontmost) return
      const order = [...orderedWidgetIds(this.state.draft, this.state.data.metadata, this.state.profile)]
      const from = order.indexOf(id), to = order.indexOf(target)
      if (from < 0 || to < 0) return
      const before = clone(this.state.draft)
      order.splice(from, 1)
      order.splice(to, 0, id)
      this.state.draft.widgetOrder ||= {}
      this.state.draft.widgetOrder[this.state.profile] = order
      this.state.selected = id
      this.recordChange(before)
    },
    startLayerDrag(event, id) {
      if (!this.editable || this.state.drag || this.state.layerDrag || event.button !== 0 || this.profile.widgets[id]?.frontmost) return
      this._suppressLayerClick = false
      event.preventDefault()
      event.currentTarget.setPointerCapture(event.pointerId)
      const rect = event.currentTarget.closest("[data-layer-id]").getBoundingClientRect()
      this.state.selected = id
      this.state.layerDrag = {
        id, pointerId: event.pointerId, target: null, startX: event.clientX, startY: event.clientY,
        x: rect.x, y: rect.y, width: rect.width, offsetX: event.clientX - rect.x, offsetY: event.clientY - rect.y,
        rows: this.layerWidgets.map(widget => widget.id), before: clone(this.state.draft), moved: false
      }
      this._layerTarget = event.currentTarget
      this.watchRelease()
    },
    // A lost pointerup (capture dropped, native gesture, re-render) must never leave a drag active,
    // since an active drag disables the tabs, preview switcher and navigation.
    watchRelease() {
      if (this._onRelease || typeof window === "undefined") return
      this._onRelease = (event) => {
        if (this.state.layerDrag?.pointerId === event.pointerId) this.endLayerDrag(event, event.type === "pointercancel")
        if (this.state.drag?.pointerId === event.pointerId) event.type === "pointercancel" ? this.cancelDrag() : this.endDrag(event)
        if (!this.state.drag && !this.state.layerDrag) this.unwatchRelease()
      }
      window.addEventListener("pointerup", this._onRelease)
      window.addEventListener("pointercancel", this._onRelease)
    },
    unwatchRelease() {
      if (!this._onRelease) return
      window.removeEventListener("pointerup", this._onRelease)
      window.removeEventListener("pointercancel", this._onRelease)
      this._onRelease = null
    },
    moveLayerDrag(event) {
      const drag = this.state.layerDrag
      if (!drag || drag.pointerId !== event.pointerId) return
      if (!drag.moved && Math.hypot(event.clientX - drag.startX, event.clientY - drag.startY) < 8) return
      drag.moved = true
      drag.x = event.clientX - drag.offsetX
      drag.y = event.clientY - drag.offsetY
      this._layerPoint = [event.clientX, event.clientY]
      this.retargetLayer(event.clientX, event.clientY)
      this.autoScrollLayer()
    },
    retargetLayer(clientX, clientY) {
      const drag = this.state.layerDrag
      if (!drag) return
      // A finger held over the pinned tabs or preview targets the first row visible under them.
      const tabs = this.$el?.querySelector?.(".gx-layout__sections")?.getBoundingClientRect()
      const row = document.elementFromPoint(clientX, tabs ? Math.max(clientY, tabs.bottom + 6) : clientY)?.closest('[data-layer-id]')
      const target = row?.dataset.layerId
      if (target && target !== drag.target && !this.profile.widgets[target]?.frontmost) {
        drag.target = target
        const order = [...orderedWidgetIds(drag.before, this.state.data.metadata, this.state.profile)]
        const from = order.indexOf(drag.id), to = order.indexOf(target)
        drag.after = drag.rows.indexOf(target) > drag.rows.indexOf(drag.id)
        order.splice(from, 1)
        order.splice(to, 0, drag.id)
        this.state.draft.widgetOrder ||= {}
        this.state.draft.widgetOrder[this.state.profile] = order
      }
    },
    // Scrolls while a dragged row is held near the visible edges; the top edge is the bottom of the pinned tabs.
    autoScrollLayer() {
      if (this._layerScroll || typeof requestAnimationFrame === "undefined") return
      const step = () => {
        this._layerScroll = null
        const drag = this.state.layerDrag, point = this._layerPoint
        if (!drag?.moved || !point) return
        const tabs = this.$el?.querySelector?.(".gx-layout__sections")?.getBoundingClientRect()
        const top = Math.max(60, tabs ? tabs.bottom : 0) + 40, bottom = window.innerHeight - 60
        const speed = point[1] < top ? -Math.min(24, 6 + (top - point[1]) / 3) : point[1] > bottom ? Math.min(24, 6 + (point[1] - bottom) / 3) : 0
        if (!speed) return
        const before = window.scrollY
        window.scrollBy(0, speed)
        if (window.scrollY !== before) this.retargetLayer(point[0], point[1])
        this._layerScroll = requestAnimationFrame(step)
      }
      this._layerScroll = requestAnimationFrame(step)
    },
    endLayerDrag(event, cancel = false) {
      const drag = this.state.layerDrag
      if (!drag || drag.pointerId !== event.pointerId) return
      if (cancel) this.state.draft = drag.before
      else if (drag.moved) this.recordChange(drag.before)
      this.state.layerDrag = null
      this._layerPoint = null
      if (this._layerScroll) { cancelAnimationFrame(this._layerScroll); this._layerScroll = null }
      if (this._layerTarget?.hasPointerCapture(event.pointerId)) this._layerTarget.releasePointerCapture(event.pointerId)
      this._layerTarget = null
      this._suppressLayerClick = cancel || drag.moved
    },
    layerClick(event, id) {
      if (event.detail === 0 || !this._suppressLayerClick) this.selectWidget(id)
      this._suppressLayerClick = false
    },
    layerKey(event, id) {
      if (event.key === 'Escape' && this.state.layerDrag) {
        event.preventDefault()
        this.endLayerDrag({ pointerId: this.state.layerDrag.pointerId }, true)
        return
      }
      if (this.state.layerDrag) return
      const direction = event.key === 'ArrowUp' ? -1 : event.key === 'ArrowDown' ? 1 : 0
      if (!direction) return
      event.preventDefault()
      const index = this.layerWidgets.findIndex(widget => widget.id === id)
      const target = this.layerWidgets[index + direction]
      if (target) this.reorderLayer(id, target.id)
    },
    finishColorEdit() { this.state.history.group = null },
    recordChange(before, group = null) {
      if (JSON.stringify(before) === JSON.stringify(this.state.draft)) return
      const history = this.state.history
      if (!group || history.group !== group) {
        history.undo.push(before)
        if (history.undo.length > 50) history.undo.shift()
      }
      history.redo = []
      history.group = group
    },
    undo() {
      if (!this.editable || this.state.drag || !this.canUndo) return
      this.finishColorEdit()
      const history = this.state.history
      history.redo.push(clone(this.state.draft))
      this.state.draft = history.undo.pop()
      this.state.colorError = this.state.placementError = this.state.notice = ""
    },
    redo() {
      if (!this.editable || this.state.drag || !this.canRedo) return
      this.finishColorEdit()
      const history = this.state.history
      history.undo.push(clone(this.state.draft))
      this.state.draft = history.redo.pop()
      this.state.colorError = this.state.placementError = this.state.notice = ""
    },
    resetToStock() {
      if (!this.editable || this.state.drag || !this.stockChanged) return
      const before = clone(this.state.draft)
      this.state.draft = clone(this.state.data.defaults)
      this.recordChange(before)
      this.state.colorError = this.state.placementError = ""
      this.state.notice = "Stock StarPilot colors and both layouts are restored in your draft. Save to apply them."
    },
    selectProfile(profile) {
      if (!this.availableProfiles.includes(profile) || this.state.drag) return
      this.state.snapGuides = []
      this.finishColorEdit()
      this.state.profile = profile
      this.state.selected = Object.keys(this.profile.widgets)[0] || null
      this.state.placementError = ""
      if (!this.availableScenes.some(scene => scene.id === this.state.scene)) this.state.scene = "general"
    },
    changePosition(id, x, y) {
      if (!this.editable) return
      const position = clampPlacement(this.limitProfile, id, x, y, this.layout)
      if (position) {
        const before = this.state.drag ? null : clone(this.state.draft)
        Object.assign(this.layout[id], position)
        if (before) this.recordChange(before)
        this.state.placementError = ""
      } else this.state.placementError = "Keep widgets within the screen and clear of Speed limit actions. The last valid position is kept."
      this.state.notice = ""
    },
    add(id) {
      if (!this.editable || this.state.drag || !Object.hasOwn(this.profile.widgets, id) || this.profile.widgets[id].available === false) return
      const before = clone(this.state.draft)
      this.layout[id].enabled = true
      if (this.sceneHidden(id)) this.state.scene = "general"
      this.recordChange(before)
      this.state.selected = id
      this.state.notice = ""
    },
    remove(id = this.state.selected) {
      if (!this.editable || this.state.drag || !Object.hasOwn(this.profile.widgets, id) || this.profile.widgets[id].required) return
      const before = clone(this.state.draft)
      this.layout[id].enabled = false
      this.recordChange(before)
      this.state.notice = ""
    },
    requestRemove(id = this.state.selected) {
      if (!this.editable || this.state.drag || this.state.layerDrag || !this.layout?.[id]?.enabled ||
        !Object.hasOwn(this.profile.widgets, id) || this.profile.widgets[id].required) return
      this.state.selected = id
      this.state.removeConfirm = id
    },
    cancelRemove() { this.state.removeConfirm = null },
    confirmRemove() {
      const id = this.state.removeConfirm
      this.state.removeConfirm = null
      this.remove(id)
    },
    setClock24Hour(event) {
      if (!this.editable || this.state.drag || this.state.layerDrag || this.selectedWidget?.kind !== "clock") return
      const before = clone(this.state.draft)
      this.state.draft.clock24Hour = event.target.checked
      this.recordChange(before)
      this.state.notice = ""
      showDraftChange()
    },
    setReduceMotion(value) {
      if (this.state.profile !== 'large' || !this.editable || this.projection || this.state.drag || this.state.layerDrag || typeof value !== 'boolean') return
      const before = clone(this.state.draft)
      this.state.draft.reduceMotion = value
      this.recordChange(before)
      this.state.notice = ''
      showDraftChange()
    },
    displayRow(key) { return this.state.data?.display?.find(row => row.key === key) },
    displayValue(key) { return this.state.draft?.display?.[key] },
    displayAvailable(row) { return !!row?.available && (!row.requires || this.displayValue(row.requires) === "On") },
    displaySettingRow(row) {
      const blocked = row.requires && this.displayValue(row.requires) !== "On"
      return {
        label: row.label, value: this.displayValue(row.key), choices: row.choices, available: this.displayAvailable(row),
        reason: blocked ? "Turn on Lead Indicator first" : row.reason, action: true
      }
    },
    setDisplay(key, value) {
      const row = this.displayRow(key)
      if (!this.editable || this.state.drag || this.state.layerDrag || !this.displayAvailable(row) || !row.choices.includes(value)) return
      const before = clone(this.state.draft)
      this.state.draft.display[key] = value
      this.recordChange(before)
      this.state.notice = ""
    },
    setShowMaxSpeed(event) {
      if (!this.editable || this.state.drag || this.state.layerDrag || this.selectedWidget?.kind !== "cruise_limits") return
      const before = clone(this.state.draft)
      this.state.draft.showMaxSpeed = event.target.checked
      this.recordChange(before)
      this.state.notice = ""
      showDraftChange()
    },
    setSpeedSources(key, value) {
      if (!this.editable || this.state.drag || this.state.layerDrag || this.selectedWidget?.kind !== "cruise_limits" ||
        !["speedSources", "speedSourcesCard"].includes(key)) return
      const before = clone(this.state.draft), oldHeight = this.profile.widgets.cruise_limits.height
      const widgets = this.state.data.metadata.profiles[this.state.profile].widgets
      const dx = widgets.speed_limit_actions.default.x - widgets.cruise_limits.default.x
      const gap = widgets.speed_limit_actions.default.y - widgets.cruise_limits.default.y - widgets.cruise_limits.height
      const sourceGap = widgets.cruise_limits.sourcesExtent?.actionGap ?? gap
      const oldGap = this.state.draft.speedSources === true ? sourceGap : gap
      this.state.draft[key] = value === true
      const card = this.layout.cruise_limits, actions = this.layout.speed_limit_actions, height = this.profile.widgets.cruise_limits.height
      // Actions that sat right under the card follow its new bottom; both stay on screen.
      if (actions && actions.x === card.x + dx && actions.y === card.y + oldHeight + oldGap) {
        const nextGap = this.state.draft.speedSources === true ? sourceGap : gap
        card.y = Math.min(card.y, placementLimits(this.limitProfile, "speed_limit_actions", this.layout).maxY - height - nextGap)
        actions.y = card.y + height + nextGap
      }
      for (const id of ["cruise_limits", "speed_limit_actions"].filter(id => this.layout[id])) {
        const limits = placementLimits(this.limitProfile, id, this.layout)
        this.layout[id].y = Math.max(limits.minY, Math.min(this.layout[id].y, limits.maxY))
      }
      this.recordChange(before)
      this.state.notice = ""
    },
    setLargeUiGammaTrial(value) {
      if (!this.projection || !this.editable || this.state.drag || this.state.layerDrag) return
      const before = clone(this.state.draft)
      this.state.draft.largeUiGammaTrial = value === "On"
      this.recordChange(before)
      this.state.notice = "Save to apply the camera effect on StarPort."
    },
    setProjectionDisplay(key, value) {
      if (!this.projection || !this.editable || this.state.drag || this.state.layerDrag || !PROJECTION_DISPLAY_SWITCHES.includes(key)) return
      if (key === "blindSpotCamera" && !this.projectionHasPipWidget) return
      const before = clone(this.state.draft)
      this.state.draft[key] = value === "On"
      this.recordChange(before)
      this.state.notice = "Save to apply the screen options on StarPort."
    },
    setProjectionLeadInfo(value) {
      if (!this.projection || !this.editable || this.state.drag || this.state.layerDrag ||
        this.state.draft.leadIndicator !== true || !LEAD_INFO.includes(value)) return
      const before = clone(this.state.draft)
      this.state.draft.leadInfo = value
      this.recordChange(before)
      this.state.notice = "Save to apply the screen options on StarPort."
    },
    projectionLeadInfoRow() {
      const marker = this.state.draft.leadIndicator === true
      return { label: "Lead Info", value: LEAD_INFO.includes(this.state.draft.leadInfo) ? this.state.draft.leadInfo : "Off",
        choices: LEAD_INFO, available: marker, reason: marker ? "" : "Turn on Lead Indicator first", action: true }
    },
    setFavoriteDisplay(display) {
      if (!this.editable || this.state.drag || this.state.layerDrag || !this.selectedWidget?.iconSize ||
        !["words", "icons"].includes(display)) return
      this.finishColorEdit()
      const before = clone(this.state.draft)
      const [oldWidth] = widgetSize(this.selectedWidget, this.selectedPosition)
      this.selectedPosition.display = display
      if (["nav_home", "nav_work"].includes(this.selectedWidget.kind)) {
        const [newWidth] = widgetSize(this.selectedWidget, this.selectedPosition)
        this.selectedPosition.x += (oldWidth - newWidth) / 2
      }
      Object.assign(this.selectedPosition, clampPlacement(this.limitProfile, this.state.selected,
        this.selectedPosition.x, this.selectedPosition.y, this.layout))
      this.recordChange(before)
      this.state.notice = ""
    },
    positionInput(axis, event) {
      const number = event.target.value.trim() === "" ? NaN : Number(event.target.value)
      if (Number.isFinite(number)) this.changePosition(this.state.selected,
        axis === "x" ? number : this.selectedPosition.x, axis === "y" ? number : this.selectedPosition.y)
      event.target.value = this.selectedPosition[axis]
    },
    resizeSelected(event) {
      if (!this.editable || this.state.drag || !this.selectedWidget?.resizable) return
      const size = Number(event.target.value), range = this.selectedWidget.resizable
      if (!Number.isInteger(size) || size < range.min || size > range.max) {
        event.target.value = this.selectedPosition.size
        return
      }
      const before = clone(this.state.draft), position = this.selectedPosition
      position.size = size
      const next = clampPlacement(this.limitProfile, this.state.selected, position.x, position.y, this.layout)
      if (next) {
        Object.assign(position, next)
        this.recordChange(before)
        this.state.placementError = ""
      } else {
        Object.assign(position, before.layouts[this.state.profile][this.state.selected])
        this.state.placementError = "That size would cover Speed limit actions. The last valid size is kept."
      }
      event.target.value = position.size
    },
    onKey(id, event) {
      if (!this.editable) return
      const movement = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[event.key]
      if (movement) {
        event.preventDefault()
        const step = event.shiftKey ? 10 : 1, position = this.layout[id]
        this.changePosition(id, position.x + movement[0] * step, position.y + movement[1] * step)
      } else if (["Delete", "Backspace"].includes(event.key) && !this.profile.widgets[id]?.required) { event.preventDefault(); this.requestRemove(id) }
      else if (["Enter", " "].includes(event.key)) { event.preventDefault(); this.state.selected = id }
    },
    startDrag(id, event, fromTray = false) {
      if (!this.editable || event.button > 0 || this.state.drag || !Object.hasOwn(this.profile.widgets, id)) return
      if (fromTray && this.profile.widgets[id].available === false) return
      if (fromTray && this.sceneHidden(id)) this.state.scene = "general"
      const point = previewPoint(event, this.$refs.preview.getBoundingClientRect(), this.profile)
      if (!point) return
      const position = this.layout[id], widget = this.profile.widgets[id]
      const [width, height] = widgetSize(widget, position)
      this.state.selected = id
      this.finishColorEdit()
      this.state.drag = {
        id, pointerId: event.pointerId, touch: event.pointerType === "touch", fromTray, before: { ...position }, historyBefore: clone(this.state.draft), inside: !fromTray,
        startX: event.clientX, startY: event.clientY, moved: false,
        offsetX: fromTray ? width / 2 + this.previewShift(id) : point.x - position.x,
        offsetY: fromTray ? height / 2 : point.y - position.y
      }
      this._dragTarget = this.$refs.preview
      this.watchRelease()
      try { this._dragTarget.setPointerCapture?.(event.pointerId) } catch { }
      event.preventDefault()
    },
    startResize(id, event) {
      const widget = this.profile.widgets[id]
      if (!this.editable || event.button > 0 || this.state.drag || !widget?.box) return
      const point = previewPoint(event, this.$refs.preview.getBoundingClientRect(), this.profile)
      if (!point) return
      const position = this.layout[id]
      this.state.selected = id
      this.finishColorEdit()
      this.state.drag = {
        id, pointerId: event.pointerId, touch: event.pointerType === "touch", resize: true, before: { ...position }, historyBefore: clone(this.state.draft),
        inside: true, offsetX: point.x - position.width, offsetY: point.y - position.height
      }
      this._dragTarget = this.$refs.preview
      this.watchRelease()
      try { this._dragTarget.setPointerCapture?.(event.pointerId) } catch { }
      event.stopPropagation()
      event.preventDefault()
    },
    resizeBox(id, width, height, group = null) {
      if (!this.editable) return false
      const position = this.layout[id], next = clampBox(this.limitProfile, id, position, width, height)
      if (!next) return false
      const before = this.state.drag ? null : clone(this.state.draft)
      Object.assign(position, next)
      if (before) this.recordChange(before, group)
      this.state.placementError = this.state.notice = ""
      return true
    },
    boxSlider(axis, event) {
      const number = Number(event.target.value)
      if (!Number.isFinite(number) || this.state.drag || this.state.layerDrag) return
      this.resizeBox(this.state.selected, axis === "width" ? number : this.selectedPosition.width,
        axis === "height" ? number : this.selectedPosition.height, this.state.profile + ":" + this.state.selected + ":" + axis)
    },
    sizeSlider(event) {
      const size = Number(event.target.value), range = this.selectedWidget?.resizable
      if (!this.editable || this.state.drag || this.state.layerDrag || !range || !Number.isInteger(size) || size < range.min || size > range.max) return
      const before = clone(this.state.draft), position = this.selectedPosition, previous = position.size
      position.size = size
      const next = clampPlacement(this.limitProfile, this.state.selected, position.x, position.y, this.layout)
      if (next) {
        Object.assign(position, next)
        this.recordChange(before, this.state.profile + ":" + this.state.selected + ":size")
        this.state.placementError = ""
      } else {
        position.size = previous
        event.target.value = previous
        this.state.placementError = "That size would cover Speed limit actions. The last valid size is kept."
      }
    },
    boxInput(axis, event) {
      const number = event.target.value.trim() === "" ? NaN : Number(event.target.value)
      if (Number.isFinite(number)) this.resizeBox(this.state.selected, axis === "width" ? number : this.selectedPosition.width,
        axis === "height" ? number : this.selectedPosition.height)
      event.target.value = this.selectedPosition[axis]
    },
    opacityInput(event) {
      const value = Number(event.target.value), range = this.selectedWidget?.opacity
      if (!this.editable || this.state.drag || this.state.layerDrag || !range || !Number.isInteger(value) || value < range.min || value > range.max) return
      const before = clone(this.state.draft)
      this.selectedPosition.opacity = value
      this.recordChange(before, this.state.profile + ":" + this.state.selected + ":opacity")
    },
    setWidgetBackground(value) {
      if (!this.editable || this.state.drag || this.state.layerDrag || !this.selectedWidget?.backgroundToggle) return
      this.finishColorEdit()
      const before = clone(this.state.draft)
      this.selectedPosition.background = value === 'On'
      this.recordChange(before)
      this.state.notice = ""
    },
    setWidgetCollapse(value) {
      if (!this.editable || this.state.drag || this.state.layerDrag || !this.selectedWidget?.collapseToggle) return
      this.finishColorEdit()
      const before = clone(this.state.draft)
      this.selectedPosition.collapse = value === 'On'
      this.recordChange(before)
      this.state.notice = ""
    },
    moveDrag(event) {
      const drag = this.state.drag
      if (!drag || drag.pointerId !== event.pointerId) return
      const rect = this.$refs.preview.getBoundingClientRect(), point = previewPoint(event, rect, this.profile)
      this.state.snapGuides = []
      if (!point) return
      const snapping = this.state.snapEnabled && !event.altKey
      if (drag.resize) {
        const raw = { width: point.x - drag.offsetX, height: point.y - drag.offsetY }, position = this.layout[drag.id]
        if (snapping) {
          drag.targets ||= this.previewSnapTargets(drag.id)
          const snapped = snapResize({ x: position.x, y: position.y, ...raw }, drag.targets, snapThreshold(this.profile, rect, event.pointerType))
          if (this.resizeBox(drag.id, snapped.width, snapped.height)) {
            if (position.width === snapped.width && position.height === snapped.height) this.state.snapGuides = snapped.guides
            return
          }
        }
        this.resizeBox(drag.id, raw.width, raw.height)
        return
      }
      // A finger wobbles a few pixels on every tap; only a deliberate move drags the widget.
      if (!drag.moved && Math.hypot(event.clientX - drag.startX, event.clientY - drag.startY) < DRAG_SLOP) return
      drag.moved = true
      drag.inside = point.x >= 0 && point.y >= 0 && point.x <= this.profile.width && point.y <= this.profile.height
      if (!drag.fromTray || drag.inside) {
        const raw = { x: point.x - drag.offsetX, y: point.y - drag.offsetY }
        let next = raw
        if (snapping) {
          const [width, height] = widgetSize(this.profile.widgets[drag.id], this.layout[drag.id])
          drag.targets ||= this.previewSnapTargets(drag.id)
          // Pushing past an edge parks the widget on it, so snap from where the clamp will leave it.
          const { minX, maxX, minY, maxY } = placementLimits(this.limitProfile, drag.id, this.layout)
          const inside = { x: Math.max(minX, Math.min(maxX, raw.x)), y: Math.max(minY, Math.min(maxY, raw.y)) }
          const snapped = snapMove({ ...inside, width, height }, drag.targets, snapThreshold(this.profile, rect, event.pointerType))
          const clamped = clampPlacement(this.limitProfile, drag.id, snapped.x, snapped.y, this.layout)
          if (clamped) {
            next = snapped
            if (clamped.x === Math.round(snapped.x) && clamped.y === Math.round(snapped.y)) this.state.snapGuides = snapped.guides
          }
        }
        if (drag.fromTray) drag.inside = clampPlacement(this.limitProfile, drag.id, next.x, next.y, this.layout) !== null
        this.changePosition(drag.id, next.x, next.y)
      }
      if (drag.fromTray) this.layout[drag.id].enabled = drag.inside
    },
    endDrag(event) {
      const drag = this.state.drag
      if (!drag || drag.pointerId !== event.pointerId) return
      this.moveDrag(event)
      if (drag.fromTray && !drag.inside) Object.assign(this.layout[drag.id], drag.before)
      const before = drag.historyBefore, position = this.layout[drag.id]
      if (!drag.fromTray && !drag.resize && !drag.moved) this.state.inspectorPanel = "edit"
      this.releaseDrag()
      this.recordChange(before)
    },
    releaseDrag() {
      const id = this.state.drag?.pointerId
      this.state.snapGuides = []
      this.state.drag = null
      if (this._dragTarget?.hasPointerCapture?.(id)) this._dragTarget.releasePointerCapture(id)
      this._dragTarget = null
    },
    cancelDrag() {
      if (this.state.layerDrag) this.endLayerDrag({ pointerId: this.state.layerDrag.pointerId }, true)
      const drag = this.state.drag
      if (drag) Object.assign(this.layout[drag.id], drag.before)
      this.releaseDrag()
    },
    resetLayout() {
      if (!this.editable || this.state.drag) return
      const before = clone(this.state.draft)
      this.state.draft.layouts[this.state.profile] = clone(this.state.data.defaults.layouts[this.state.profile])
      if (this.state.draft.widgetOrder) {
        delete this.state.draft.widgetOrder[this.state.profile]
        if (!Object.keys(this.state.draft.widgetOrder).length) delete this.state.draft.widgetOrder
      }
      this.recordChange(before)
      this.state.placementError = ""
      this.state.notice = "This layout is reset in your draft. Save to apply it."
    },
    resetColors() {
      if (!this.editable || this.state.drag || !this.colorFields.length) return
      const before = clone(this.state.draft)
      const profile = this.state.profile, id = this.state.selected
      delete this.state.draft.widgetColors[profile][id]
      const inherited = this.selectedColors
      const stock = widgetPalette(this.state.data.defaults, this.state.data.metadata, profile, id)
      const overrides = Object.fromEntries(Object.entries(stock).filter(([key, value]) => value !== inherited[key]))
      if (Object.keys(overrides).length) this.state.draft.widgetColors[profile][id] = overrides
      this.recordChange(before)
      this.state.colorError = ""
      this.state.notice = "This widget’s colors are reset in your draft. Save to apply them."
    },
    roadPoints(points) {
      const r = this.roadBounds
      return points.map(([x, y]) => (r.x + x * r.width) + ',' + (r.y + y * r.height)).join(' ')
    },
    roadLabel(id) {
      if (id === 'pathEdge') return this.state.profile === 'large' ? 'Path border' : 'Closest lane markings'
      if (id === 'laneLines') return this.state.profile === 'large' ? 'Lane lines' : 'Outer lane lines'
      return 'Path'
    },
    setRoadMode(mode) {
      if (!this.editable || this.state.drag || !PATH_MODES.includes(mode)) return
      const before = clone(this.state.draft)
      this.state.draft.roadColors[this.state.profile].pathMode = mode
      this.recordChange(before)
    },
    roadColor(id, value, group = null) {
      if (!this.editable || this.state.drag || !ROAD_COLORS.includes(id)) return
      if (!HEX.test(value)) { this.state.colorError = "Use eight hex digits, for example #30FF9CFF."; return }
      const before = clone(this.state.draft)
      this.state.draft.roadColors[this.state.profile][id] = value.toUpperCase()
      if (id === 'path') this.state.draft.roadColors[this.state.profile].pathMode = 'color'
      this.recordChange(before, group && this.state.profile + ':road:' + group)
      this.state.colorError = this.state.notice = ''
    },
    roadRgb(id, event) {
      const alpha = this.roadColors[id].slice(7)
      this.roadColor(id, event.target.value + (alpha === '00' ? 'FF' : alpha), 'rgb:' + id)
    },
    roadAlpha(id, event) {
      const value = Number(event.target.value)
      if (Number.isInteger(value) && value >= 0 && value <= 255)
        this.roadColor(id, this.roadColors[id].slice(0, 7) + value.toString(16).padStart(2, '0'), 'alpha:' + id)
    },
    resetRoad() {
      if (!this.editable || this.state.drag) return
      const before = clone(this.state.draft)
      this.state.draft.roadColors[this.state.profile] = {}
      this.recordChange(before)
      this.state.colorError = ''
    },
    widgetColors(id) { return widgetPalette(this.state.draft, this.state.data.metadata, this.state.profile, id) },
    color(id, value, group = null) {
      if (!this.editable || this.state.drag || !Object.hasOwn(this.selectedWidget?.colors || {}, id)) return
      if (!HEX.test(value)) { this.state.colorError = "Use eight hex digits, for example #000000A6. The last two digits set opacity."; return }
      const before = clone(this.state.draft)
      const profile = this.state.profile, widget = this.state.selected
      this.state.draft.widgetColors[profile][widget] ||= {}
      this.state.draft.widgetColors[profile][widget][id] = value.toUpperCase()
      this.recordChange(before, group && profile + ':' + widget + ':' + group)
      this.state.colorError = this.state.notice = ""
    },
    colorText(id, event) { this.color(id, event.target.value.trim()); this.finishColorEdit(); event.target.value = this.selectedColors[id] },
    colorRgb(id, event) {
      const alpha = this.selectedColors[id]?.slice(7)
      if (alpha) this.color(id, event.target.value + (alpha === '00' ? (id === 'cardFill' ? 'A6' : 'FF') : alpha), "rgb:" + id)
    },
    colorAlpha(id, event) {
      const value = Number(event.target.value)
      if (Number.isInteger(value) && value >= 0 && value <= 255)
        this.color(id, this.selectedColors[id].slice(0, 7) + value.toString(16).padStart(2, "0"), "alpha:" + id)
    },
    alpha(color) { return parseInt(color.slice(7), 16) },
    flashLeavePrompt() {
      const bar = this.$refs.leaveBar
      if (!bar) return
      const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches
      bar.scrollIntoView({ block: 'nearest', behavior: reduced ? 'instant' : 'smooth' })
      bar.querySelector('button')?.focus({ preventScroll: true })
    },
    requestLeave(action, presentation = "inline") {
      if (this.inlineLeavePrompt) { this.flashLeavePrompt(); return }
      // Saving must finish first; a load or a stray drag must not swallow navigation.
      if (this.state.status === "saving") return
      if (this.state.drag || this.state.layerDrag) this.cancelDrag()
      this.state.removeConfirm = null
      if (this.dirty) {
        this.state.discard = action
        this.state.leavePresentation = (typeof action === "function" || ["back", "device", "projection"].includes(action)) ? presentation : "inline"
      }
      else this.leave(action)
    },
    cancelLeave() {
      if (this.busy) return
      this.state.discard = null
      this.state.leavePresentation = null
    },
    leave(action) {
      this.state.discard = null
      this.state.leavePresentation = null
      if (typeof action === "function") action()
      else if (action === "back") this.$emit("close")
      else if (["projection", "device"].includes(action)) this.$emit("target", action)
      else this.feed.load()
    },
    save() { if (this.editable && this.dirty && !this.state.drag && !this.state.layerDrag) return this.feed.save(this.state.draft) },
    async saveAndLeave() {
      if (!this.canSavePending || !this.pendingLeave) return false
      const action = this.state.discard
      const saved = await this.feed.save(this.state.draft)
      if (saved && this.state.discard === action) this.leave(action)
      return saved
    },
  },
  template: ONROAD_LAYOUT_TEMPLATE,
}
