import { GxState } from "./state.js"
import { GxIconButton } from "./icon-button.js"
import { GxNotice } from "./notice.js"
import { requestJson } from "./startup.js"
import { connectionError } from "./polling.js"
import { reactive } from "../vendor/vue/vue.esm-browser.js"

const text = (value, max = 1024) => typeof value === "string" && value.length <= max
const rows = value => Array.isArray(value) && value.length <= 128 && value.every(row => row && text(row.label, 128) && text(row.value))
export function validTroubleshoot(value) {
  return !!value && value.schemaVersion === 1 && [null, "parked", "driving", "standby"].includes(value.device?.state) &&
    typeof value.vehicle?.available === "boolean" && typeof value.vehicle.longitudinal === "boolean" &&
    ["fingerprint", "brand", "steering"].every(key => text(value.vehicle[key])) && rows(value.snapshot) &&
    Array.isArray(value.sections) && value.sections.length <= 32 &&
    value.sections.every(section => text(section.title, 128) && rows(section.rows)) && text(value.note, 2048)
}
export function validTmux(value) {
  return !!value && typeof value.available === "boolean" && text(value.reason) && text(value.pane, 128) &&
    typeof value.truncated === "boolean" && typeof value.text === "string" &&
    new TextEncoder().encode(value.text).length <= 65536 && value.text.split("\n").length <= 301
}
export function troubleshootReport(data) {
  if (!data) return ""
  return ["StarPilot Troubleshoot", `Device: ${data.device.state || "Unavailable"}`,
    `Vehicle: ${data.vehicle.available ? data.vehicle.fingerprint : "Unavailable"}`,
    `Brand: ${data.vehicle.brand}`, `Longitudinal: ${data.vehicle.longitudinal ? "Available" : "Unavailable"}`, `Steering: ${data.vehicle.steering}`,
    ...data.snapshot.map(row => `${row.label}: ${row.value}`),
    ...data.sections.flatMap(section => ["", section.title, ...section.rows.map(row => `${row.label}: ${row.value}`)]),
    "", data.note].join("\n")
}

export class DiagnosticFeed {
  constructor({ endpoint, validate, publish, unauthorized = () => {}, interval = 0,
    fetcher = (...args) => fetch(...args), later = (fn, ms) => setTimeout(fn, ms), cancel = id => clearTimeout(id) }) {
    Object.assign(this, { endpoint, validate, publish, unauthorized, interval, fetcher, later, cancel })
    this.active = false; this.visible = true; this.live = true; this.data = null
    this.request = this.timer = null; this.generation = 0
  }
  start(mode, visible = true) { this.stop(); this.mode = mode; this.visible = visible; this.active = true
    if (mode !== "local") { this.data = null; this.publish({ data: null, error: "This tool is unavailable in sample mode.", loading: false }); return }
    if (visible) return this.refresh()
  }
  cancelPending() { this.generation++; this.request?.abort(); this.request = null
    if (this.timer !== null) this.cancel(this.timer)
    this.timer = null
  }
  stop() { this.active = false; this.cancelPending() }
  setVisible(value) { this.visible = value; this.cancelPending(); if (value && this.active && this.live) return this.refresh() }
  setLive(value) { this.live = value; this.cancelPending(); this.publish({ live: value, loading: false }); if (value && this.visible) return this.refresh() }
  async refresh() {
    if (!this.active || !this.visible || this.mode !== "local" || this.request) return
    if (this.timer !== null) this.cancel(this.timer)
    this.timer = null
    const generation = ++this.generation, request = new AbortController()
    this.request = request; this.publish({ loading: !this.data })
    try {
      const data = await requestJson(this.endpoint, { fetcher: this.fetcher, timeout: 4000, later: this.later, cancel: this.cancel, request: { signal: request.signal } })
      if (!this.active || generation !== this.generation) return
      if (!this.validate(data)) throw new Error("Unsupported diagnostic response")
      this.data = data; this.publish({ data, error: "" })
    } catch (error) {
      if (this.active && generation === this.generation) {
        if (error.status === 401 || ["setup_required", "access_unavailable"].includes(error.code)) { this.stop(); this.unauthorized(); return }
        this.publish({ error: connectionError(error) })
      }
    } finally {
      if (this.active && generation === this.generation) {
        this.request = null; this.publish({ loading: false })
        if (this.interval && this.live && this.visible) this.timer = this.later(() => { this.timer = null; this.refresh() }, this.interval)
      }
    }
  }
}

const props = { mode: { type: String, required: true }, unauthorized: { type: Function, required: true } }
function setup(props, consoleTail) {
  const state = reactive({ loading: false, live: true, data: null, error: "", notice: "" })
  const feed = new DiagnosticFeed({ endpoint: consoleTail ? "./api/tmux/live" : "./api/troubleshoot",
    validate: consoleTail ? validTmux : validTroubleshoot, interval: consoleTail ? 2000 : 10000,
    publish: update => Object.assign(state, update), unauthorized: props.unauthorized })
  return { state, feed }
}
const lifecycle = {
  mounted() { this._visibility = () => this.feed.setVisible(!document.hidden)
    document.addEventListener("visibilitychange", this._visibility); this.feed.start(this.mode, !document.hidden) },
  beforeUnmount() { document.removeEventListener("visibilitychange", this._visibility); this.feed.stop() },
  watch: { mode(value) { this.feed.start(value, !document.hidden) } },
}
const methods = {
  async copyText(value) { try { await navigator.clipboard.writeText(value); this.state.notice = "Visible text copied." }
    catch { this.state.notice = "Copy is unavailable in this browser. Select the visible text to copy it." } },
  saveText(value) { const url = URL.createObjectURL(new Blob([value], { type: "text/plain;charset=utf-8" }))
    const link = document.createElement("a"); link.href = url; link.download = "starpilot-console.txt"; link.click(); URL.revokeObjectURL(url) },
}
export const TroubleshootPage = {
  components: { GxState, GxIconButton, GxNotice }, props, ...lifecycle,
  setup: props => setup(props, false), methods: { ...methods, report: troubleshootReport },
  template: `<section class="gx-settings"><header class="gx-settings__header gx-page-header"><div><h2>Troubleshoot</h2>
    <p>Read-only device, vehicle and settings snapshot. Copy it when reporting a problem.</p></div></header>
    <GxNotice tone="danger" v-if="state.error">{{ state.error }}</GxNotice>
    <GxNotice v-if="state.notice && !state.error" tone="info">{{ state.notice }}</GxNotice>
    <div class="gx-settings__controls gx-actions"><button class="gx-btn gx-btn--tonal" :disabled="mode !== 'local'" @click="$emit('navigate', '/settings/recovery')">Changed Settings</button><button class="gx-btn gx-btn--tonal" :disabled="mode !== 'local'" @click="$emit('navigate', '/settings/recovery')">Reset to Default</button><button class="gx-btn gx-btn--tonal" @click="$emit('navigate', '/logs/monitor')">System Monitor</button><button class="gx-btn gx-btn--tonal" @click="$emit('navigate', '/logs/crashes')">Crash Reports</button><GxIconButton label="Copy report" icon="bi-clipboard" :disabled="!state.data" @click="copyText(report(state.data))" /></div>
    <GxState v-if="state.loading && !state.error" loading>Loading report…</GxState>
    <template v-if="state.data"><section class="gx-card gx-panel"><div class="gx-section__header"><i class="bi bi-cpu" aria-hidden="true"></i><span class="gx-section__title">Device Snapshot</span></div><p>State: {{ state.data.device.state || 'Unavailable' }}</p><p>Vehicle: {{ state.data.vehicle.available ? state.data.vehicle.fingerprint : 'Unavailable' }}</p><p>Brand: {{ state.data.vehicle.brand || 'Unavailable' }} · Longitudinal: {{ state.data.vehicle.longitudinal ? 'Available' : 'Unavailable' }} · Steering: {{ state.data.vehicle.steering || 'Unavailable' }}</p><dl class="gx-details"><template v-for="row in state.data.snapshot" :key="row.label"><dt>{{ row.label }}</dt><dd>{{ row.value }}</dd></template></dl></section>
      <section v-for="section in state.data.sections" :key="section.title" class="gx-card gx-panel"><div class="gx-section__header"><i class="bi bi-list-check" aria-hidden="true"></i><span class="gx-section__title">{{ section.title }}</span></div><dl class="gx-details"><template v-for="row in section.rows" :key="row.label"><dt>{{ row.label }}</dt><dd>{{ row.value }}</dd></template></dl></section><p class="gx-note">{{ state.data.note }}</p></template></section>`,
}
export const TmuxPage = {
  components: { GxState, GxIconButton, GxNotice }, props, ...lifecycle,
  setup: props => setup(props, true), methods,
  template: `<section class="gx-settings"><header class="gx-settings__header gx-page-header"><div><h2>tmux Live View</h2>
    <p>Live view of the launcher console, refreshed every few seconds.</p></div></header>
    <GxState v-if="state.loading && !state.error" loading>Loading console…</GxState><GxNotice tone="danger" v-if="state.error">{{ state.error }}</GxNotice><GxNotice v-if="state.notice && !state.error" tone="info">{{ state.notice }}</GxNotice>
    <div class="gx-settings__controls gx-actions"><GxIconButton :label="state.live ? 'Pause console updates' : 'Resume console updates'" :icon="state.live ? 'bi-pause-fill' : 'bi-play-fill'" :disabled="mode !== 'local'" @click="feed.setLive(!state.live)" /><GxIconButton label="Copy console text" icon="bi-clipboard" :disabled="!state.data?.text" @click="copyText(state.data.text)" /><GxIconButton label="Download console text" icon="bi-download" :disabled="!state.data?.text" @click="saveText(state.data.text)" /></div>
    <section v-if="state.data" class="gx-card gx-crash-preview gx-panel"><p v-if="!state.data.available">{{ state.data.reason }}</p><pre class="gx-pre">{{ state.data.text }}</pre></section>
    <p class="gx-note gx-tool-footer" role="status">{{ state.live ? 'Live · every 2 seconds while visible' : 'Paused' }}<span v-if="state.data?.truncated"> · showing the latest console lines</span></p></section>`,
}
