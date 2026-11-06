import { GalaxySelect } from "./galaxy-select.js"
import { haptic } from "./haptics.js"
import { beginSave, finishSave, showDraftChange } from "./save-feedback.js"

export const FINE_SCRUB_HOLD_MS = 300
export const FINE_SCRUB_FACTOR = 5
const FINE_SCRUB_JITTER_PX = 4

// Standalone preferences use the same pending-value lifecycle as settings pages.
export const switchRow = (label, enabled, reason = "") => ({
  label, value: enabled ? "On" : "Off", reason, choices: ["Off", "On"], available: true, action: true,
})

export function numericBounds(row) {
  const min = Number(row.minimum), max = Number(row.maximum), step = Number(row.step)
  return Number.isFinite(min) && Number.isFinite(max) && Number.isFinite(step) && max > min && step > 0
    ? { min, max, step } : null
}

export function snapNumeric(value, bounds) {
  if (!bounds || value === "" || value === null || value === undefined || !Number.isFinite(Number(value))) return null
  const { min, max, step } = bounds
  let lastStep = Math.floor((max - min) / step + 1e-9)
  if (Number((min + lastStep * step).toFixed(8)) > max) lastStep--
  const index = Math.max(0, Math.min(lastStep, Math.round((Number(value) - min) / step)))
  return Number((min + index * step).toFixed(8))
}

export function settingControl(row) {
  if (row.page) return "group"
  if (row.confirm || row.repairValue) return "action"
  if (row.choices?.length === 2 && row.choices.includes("Off") && row.choices.includes("On") && row.choices.includes(row.value)) return "switch"
  if (numericBounds(row) && ((Number.isFinite(Number(row.value)) && row.value !== "") ||
      (row.value === "Auto" && row.choices?.includes("Auto")))) return "slider"
  if (row.choices?.length && row.choices.includes(row.value)) return "select"
  return "readout"
}


export function laneStrengthDisplay(value, row) {
  const raw = Number(value), min = Number(row.minimum), max = Number(row.maximum)
  if (!Number.isFinite(raw) || min >= 100 || max <= 100) return value
  return Number((raw <= 100 ? 1 + (raw - min) * 4 / (100 - min) : 5 + (raw - 100) * 5 / (max - 100)).toFixed(2))
}

export function laneStrengthSaved(value, row) {
  const display = Number(value), min = Number(row.minimum), max = Number(row.maximum)
  if (!Number.isFinite(display) || display < 1 || display > 10) return null
  return Math.round(display <= 5 ? min + (display - 1) * (100 - min) / 4 : 100 + (display - 5) * (max - 100) / 5)
}

export function presentedSetting(row) {
  if (row.displayScale !== "lane-strength") return row
  return { ...row, value: laneStrengthDisplay(row.value, row),
    defaultValue: row.defaultValue == null ? row.defaultValue : laneStrengthDisplay(row.defaultValue, row),
    minimum: 1, maximum: 10, step: row.step > 0 ? 1 : 0, unit: "",
    reason: row.available ? "1–10; 5 is normal. Lower values reduce centering and higher values strengthen it" : row.reason }
}

export const GalaxySettingRow = {
  name: "GalaxySettingRow",
  components: { GalaxySelect },
  props: {
    row: { type: Object, required: true },
    index: { type: Number, required: true },
    disabled: { type: Boolean, default: false },
    busy: { type: Boolean, default: false },
    feedback: { type: String, default: "saved" },
    saveValue: { type: Function, required: true },
    testAlert: { type: Function, default: null },
  },
  emits: ["open", "review", "reset-default"],
  data() { return { preview: undefined, interacting: false, fineScrub: null, isFineScrubbing: false, updating: false, testingSound: false, soundError: "" } },
  computed: {
    presentedRow() { return presentedSetting(this.row) },
    control() { return settingControl(this.row) },
    showDefault() {
      const value = this.presentedRow.value, initial = this.presentedRow.defaultValue
      if (initial === null || initial === undefined || this.row.page) return false
      return Number.isFinite(Number(value)) && Number.isFinite(Number(initial)) ? Number(value) !== Number(initial) : String(value) !== String(initial)
    },
    bounds() {
      const bounds = numericBounds(this.presentedRow)
      if (!bounds || !this.row.choices?.includes("Auto")) return bounds
      const numericMax = snapNumeric(bounds.max, bounds)
      return { ...bounds, numericMax, max: Number((numericMax + bounds.step).toFixed(8)), auto: true }
    },
    locked() { return this.busy || this.updating || this.dimmed },
    dimmed() { return this.disabled || !this.row.available || (!this.row.action && !this.row.page) },
    currentValue() { return this.preview !== undefined ? this.preview : this.presentedRow.value },
    sliderValue() { return this.currentValue === "Auto" && this.bounds?.auto ? this.bounds.max : this.currentValue },
    displayValue() {
      return this.currentValue === "Auto" || (this.bounds?.auto && Number(this.currentValue) === this.bounds.max)
        ? "Auto" : `${this.currentValue}${this.presentedRow.unit ? " " + this.presentedRow.unit : ""}`
    },
  },
  methods: {
    async previewSound() {
      if (this.locked || this.testingSound || !this.testAlert || !this.row.previewSound) return
      this.testingSound = true
      this.soundError = ""
      try { await this.testAlert(this.row.previewSound) }
      catch (error) { this.soundError = error?.message || "The alert could not be played." }
      finally { this.testingSound = false }
    },
    async commit(value) {
      if (this.locked) return
      if (String(value) === String(this.presentedRow.value)) { this.preview = undefined; return }
      const savedValue = this.row.displayScale === "lane-strength" ? laneStrengthSaved(value, this.row) : value
      if (savedValue === null) return
      this.preview = value
      this.updating = true
      const token = this.feedback === "saved" && this.control === "switch" ? beginSave() : null
      try {
        const result = await this.saveValue(this.index, savedValue)
        if (token !== null) {
          await this.$nextTick()
          finishSave(token, result !== false && result !== null &&
            (result === true || result?.saved === true || String(this.presentedRow.value) === String(value)))
        } else if (this.feedback === "draft" && this.control === "switch" && result !== false) showDraftChange()
      } catch (error) {
        if (token !== null) finishSave(token, false)
        throw error
      }
      finally { this.preview = undefined; this.updating = false }
    },
    onSwitch(event) {
      const next = event.target.checked ? "On" : "Off"
      if (this.locked) { event.target.checked = this.currentValue === "On"; return }
      return this.commit(next)
    },
    onSelect(event) { return this.commit(event.target.value) },
    beginInteract() { if (!this.locked) this.interacting = true },
    flushSlider(raw) {
      if (this.locked) return
      const numeric = snapNumeric(raw, this.bounds)
      const next = numeric !== null && this.bounds?.auto && numeric === this.bounds.max ? "Auto" : numeric
      this.preview = undefined
      if (next !== null && String(next) !== String(this.presentedRow.value)) return this.commit(next)
    },
    clearHoldTimer() {
      if (this._holdTimer !== undefined) clearTimeout(this._holdTimer)
      this._holdTimer = undefined
    },
    startHoldTimer() {
      this.clearHoldTimer()
      if (!this.fineScrub || this.fineScrub.active) return
      this._holdTimer = setTimeout(() => this.activateFineScrub(), FINE_SCRUB_HOLD_MS)
    },
    activateFineScrub() {
      if (!this.fineScrub || this.fineScrub.active || this.locked) return
      this.fineScrub.active = true
      this.fineScrub.baseValue = snapNumeric(this.sliderValue, this.bounds) ?? this.bounds.min
      this.fineScrub.baseX = this.fineScrub.lastX
      this.isFineScrubbing = true
      haptic()
    },
    onSliderInput(event) {
      if (this.locked || this.fineScrub?.active) { event.target.value = this.sliderValue; return }
      this.beginInteract()
      this.preview = Number(event.target.value)
      this.startHoldTimer()
    },
    onSliderCommit(event) {
      if (this.fineScrub) return
      this.interacting = false
      return this.flushSlider(event.target.value)
    },
    onSliderBlur(event) { if (this.interacting && !this.fineScrub) return this.onSliderCommit(event) },
    onSliderPointerDown(event) {
      if (this.locked) return
      this.beginInteract()
      try { event.target.setPointerCapture?.(event.pointerId) } catch {}
      const rect = event.target.getBoundingClientRect()
      this.fineScrub = { active: false, baseValue: snapNumeric(this.sliderValue, this.bounds),
        baseX: event.clientX, lastX: event.clientX, track: rect.width || 200, pointerId: event.pointerId }
      this.startHoldTimer()
    },
    onSliderPointerMove(event) {
      const scrub = this.fineScrub
      if (!scrub || event.pointerId !== scrub.pointerId || this.locked) return
      if (!scrub.active) {
        if (Math.abs(event.clientX - scrub.lastX) > FINE_SCRUB_JITTER_PX) {
          scrub.lastX = event.clientX
          this.startHoldTimer()
        }
        return
      }
      event.preventDefault()
      const raw = scrub.baseValue + (event.clientX - scrub.baseX) * (this.bounds.max - this.bounds.min) / scrub.track / FINE_SCRUB_FACTOR
      const next = snapNumeric(raw, this.bounds)
      if (next === null) return
      this.preview = next
      if (this.$refs.slider) this.$refs.slider.value = next
    },
    releasePointer(event) {
      const pointerId = this.fineScrub?.pointerId
      this.clearHoldTimer()
      this.fineScrub = null
      this.isFineScrubbing = this.interacting = false
      try { (event?.target || this.$refs.slider)?.releasePointerCapture?.(pointerId) } catch {}
    },
    onSliderPointerEnd(event) {
      if (!this.fineScrub || event.pointerId !== this.fineScrub.pointerId) return
      const value = this.sliderValue
      this.releasePointer(event)
      return this.flushSlider(value)
    },
    onSliderCancel(event) {
      if (!this.fineScrub || event.pointerId !== this.fineScrub.pointerId) return
      this.releasePointer(event)
      this.preview = undefined
      if (this.$refs.slider) this.$refs.slider.value = this.sliderValue
    },
  },
  watch: {
    locked(value) {
      if (value) {
        this.releasePointer()
        // A save locks input but owns its preview until the saved value is read.
        if (!this.updating) this.preview = undefined
      }
    },
  },
  beforeUnmount() { this.clearHoldTimer() },
  template: `
    <div class="gx-row" :class="{ disabled: dimmed, 'gx-row--stack': control === 'slider', 'gx-row--heading': row.heading }" :role="row.heading ? 'heading' : undefined" :aria-level="row.heading ? 3 : undefined" :inert="busy || updating" :aria-busy="busy || updating || undefined">
      <div class="gx-row__info">
        <span class="gx-row__label">{{ row.label }}</span>
        <span v-if="presentedRow.reason && !row.heading" class="gx-row__desc">{{ ' ' + presentedRow.reason.replace('https://firestar.link/discord', '') }}</span>
        <a v-if="presentedRow.reason?.includes('https://firestar.link/discord')" class="gx-btn gx-btn--tonal" href="https://firestar.link/discord" target="_blank" rel="noopener">StarPilot Discord</a>
        <span v-if="control === 'group' || control === 'action'" class="gx-row__desc">{{ presentedRow.value }}</span>
      </div>
      <button v-if="control !== 'slider' && showDefault && !row.heading" type="button"
          class="gx-btn gx-btn--tonal gx-setting-default" :disabled="dimmed || !row.resetAvailable"
          :aria-label="'Reset ' + row.label + ' to default'" :title="'Default: ' + presentedRow.defaultValue"
          @click="$emit('reset-default', index)">Default</button>
      <label v-if="control === 'switch'" class="gx-switch">
        <input type="checkbox" role="switch" :aria-label="row.label" :checked="currentValue === 'On'" :disabled="dimmed" @change="onSwitch">
        <span class="gx-switch__track"></span><span class="gx-switch__thumb"></span>
      </label>
      <div v-else-if="control === 'slider'" class="gx-slider-row" :class="{ 'is-fine-scrubbing': isFineScrubbing }">
        <div class="gx-slider-header" :class="{ 'gx-slider-header--preview': testAlert && row.previewSound }"><span class="gx-row__value">{{ displayValue }}</span>
          <span v-if="interacting" class="gx-slider-hint">{{ isFineScrubbing ? 'Fine scrubbing' : 'Hold to fine scrub' }}</span>
          <span class="gx-slider-actions">
          <button v-if="testAlert && row.previewSound" type="button" class="gx-btn gx-btn--tonal gx-alert-preview"
            :disabled="locked || testingSound" :aria-label="'Test ' + row.label + ' on your comma'" :aria-busy="testingSound"
            @click="previewSound"><i class="bi" :class="testingSound ? 'bi-soundwave' : 'bi-volume-up'" aria-hidden="true"></i>
            {{ testingSound ? 'Playing…' : 'Test alert' }}</button>
          <button v-if="showDefault" type="button"
          class="gx-btn gx-btn--tonal gx-setting-default" :disabled="dimmed || !row.resetAvailable"
          :aria-label="'Reset ' + row.label + ' to default'" :title="'Default: ' + presentedRow.defaultValue"
          @click="$emit('reset-default', index)">Default</button></span></div>
        <input ref="slider" type="range" class="gx-slider" :aria-label="row.label" :aria-valuetext="displayValue"
          :min="bounds.min" :max="bounds.max" :step="bounds.step" :value="sliderValue" :disabled="dimmed"
          @input="onSliderInput" @change="onSliderCommit" @blur="onSliderBlur"
          @pointerdown="onSliderPointerDown" @pointermove="onSliderPointerMove" @pointerup="onSliderPointerEnd"
          @pointercancel="onSliderCancel" @lostpointercapture="onSliderCancel" @keydown="beginInteract">
        <p v-if="soundError || testingSound" class="gx-alert-preview-note" role="status" aria-live="polite">{{ soundError || 'Playing on your comma…' }}</p>
        <div class="gx-slider-meta"><span>{{ bounds.min }} to {{ bounds.numericMax ?? bounds.max }} {{ presentedRow.unit }}<template v-if="bounds.auto"> · Auto at right</template></span><span>Step: {{ bounds.step }} {{ presentedRow.unit }}</span></div>
      </div>
      <GalaxySelect v-else-if="control === 'select'" class="gx-field" :aria-label="row.label" :value="currentValue" :disabled="dimmed" @change="onSelect">
        <option v-for="choice in row.choices" :key="choice" :value="choice">{{ choice }}</option>
      </GalaxySelect>
      <button v-else-if="control === 'group'" type="button" class="gx-btn gx-btn--tonal" :disabled="dimmed" @click="$emit('open', row.page)">Manage</button>
      <button v-else-if="control === 'action' && row.action" type="button" class="gx-btn gx-btn--tonal" :disabled="dimmed" @click="$emit('review', index, row.confirm ? 0 : 1)">{{ row.repairValue === 'Reset' || /reset|default/i.test(row.label) ? 'Reset' : row.repairValue ? 'Set ' + row.repairValue : row.label }}</button>
      <span v-else-if="!row.heading" class="gx-row__value gx-row__readout">{{ displayValue }}</span>
    </div>`,
}
