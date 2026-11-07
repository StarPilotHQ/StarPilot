import { reactive } from "../vendor/vue/vue.esm-browser.js"
import { SETTINGS_SECTIONS, isPipCropRow } from "./settings.js"
import { menuPlace, settingsSearchPath } from "./menu.js"

const SPECIAL_PAGES = new Set(["ui_layout", "favorites"])
const PAGE_SECTIONS = new Map(SETTINGS_SECTIONS.flatMap((section) =>
  section.pages.filter((page) => !SPECIAL_PAGES.has(page)).map((page) => [page, section.label])))
const INITIAL_PAGES = [...PAGE_SECTIONS.keys()]
const normalize = (value) => value.toLocaleLowerCase().replace(/&/g, " and ")

const searchable = (row, title, section) => normalize([row.label, row.value, row.reason, row.unit, title, section]
  .filter((value) => typeof value === "string").join(" "))

export function searchSettings(entries, query, limit = 30) {
  const words = normalize(query.trim()).split(/\s+/).filter(Boolean)
  if (!words.length) return []
  return entries.filter((entry) => words.every((word) => entry.search.includes(word)))
    .sort((a, b) => Number(normalize(b.label).startsWith(words[0])) - Number(normalize(a.label).startsWith(words[0])) ||
      (a.section || "").localeCompare(b.section || "") || a.label.localeCompare(b.label)).slice(0, limit)
}

export function toolSearchEntries(tools) {
  const entries = []
  const visit = (tool, parents = [], visited = new Set()) => {
    entries.push({ path: tool.path, label: tool.name, kind: "Tool", context: parents.join(" · "),
      search: normalize([tool.name, tool.description, ...parents, "Tools"].join(" ")) })
    if (visited.has(tool.path)) return
    const next = new Set(visited).add(tool.path)
    for (const child of tool.children || []) visit(child, [...parents, tool.name], next)
  }
  for (const tool of tools) visit(tool)
  return entries
}

export class SettingsSearchIndex {
  constructor({ fetcher = (...args) => fetch(...args), publish = () => {}, unauthorized = () => {} } = {}) {
    Object.assign(this, { fetcher, publish, unauthorized })
    this.entries = []
    this.status = "idle"
    this.generation = 0
    this.controller = null
    this.pending = null
  }

  stop() {
    this.generation++
    this.controller?.abort()
    this.controller = this.pending = null
    this.entries = []
    this.status = "idle"
    this.publish({ status: "idle", entries: [], failed: 0 })
  }

  async load() {
    if (this.pending) return this.pending
    if (this.status === "ready") return
    const generation = ++this.generation
    const controller = new AbortController()
    this.controller = controller
    this.status = "loading"
    this.entries = []
    this.publish({ status: "loading", entries: [], failed: 0 })
    this.pending = this.collect(generation, controller).finally(() => {
      if (this.generation === generation) this.pending = null
    })
    return this.pending
  }

  async collect(generation, controller) {
    const seen = new Set(INITIAL_PAGES)
    let queue = [...INITIAL_PAGES]
    let failed = 0
    while (queue.length && this.generation === generation) {
      const batch = queue
      queue = []
      let cursor = 0
      const worker = async () => {
        while (cursor < batch.length && this.generation === generation) {
          const page = batch[cursor++]
          let timer
          const timed = new AbortController()
          const abort = () => timed.abort()
          controller.signal.addEventListener("abort", abort, { once: true })
          try {
            timer = setTimeout(abort, 5000)
            const response = await this.fetcher(`./api/settings/pages/${encodeURIComponent(page)}`,
              { credentials: "same-origin", cache: "no-store", signal: timed.signal })
            if (this.generation !== generation) return
            if (response.status === 401) {
              this.stop()
              this.unauthorized()
              return
            }
            if (!response.ok) throw new Error("Settings unavailable")
            const data = await response.json()
            if (this.generation !== generation) return
            if (data?.page !== page || !Array.isArray(data.rows) || typeof data.title !== "string") throw new Error("Invalid settings page")
            const section = PAGE_SECTIONS.get(page.split("/")[0]) || "Toggles"
            for (const row of data.rows) {
              if (typeof row?.label !== "string" || !row.label) continue
              if (typeof row.page === "string" && row.page && !seen.has(row.page) && seen.size < 64) {
                seen.add(row.page)
                queue.push(row.page)
              }
              if (row.page || isPipCropRow(row)) continue
              // Galaxy menu: show where the setting now lives (menu.js), e.g. "Driving › Steering › Lane Changes".
              const place = menuPlace(settingsSearchPath({ page, label: row.label }))
              this.entries.push({ page, label: row.label, title: data.title, section, kind: "Toggle", context: place || `${section} · ${data.title}`,
                search: searchable(row, data.title, section) })
            }
          } catch {
            if (this.generation === generation) failed++
          } finally {
            clearTimeout(timer)
            controller.signal.removeEventListener("abort", abort)
          }
        }
      }
      await Promise.all(Array.from({ length: Math.min(4, batch.length) }, worker))
    }
    if (this.generation !== generation) return
    this.status = failed ? "partial" : "ready"
    this.publish({ status: this.status, entries: [...this.entries], failed })
  }
}

export const ToggleSearch = {
  props: { enabled: { type: Boolean, required: true }, unauthorized: { type: Function, required: true },
    settingsEnabled: { type: Boolean, required: true }, tools: { type: Array, required: true },
    openPage: { type: Function, required: true } },
  setup(props) {
    const state = reactive({ query: "", open: false, active: 0, status: "idle", entries: [], failed: 0, rect: null })
    const index = new SettingsSearchIndex({ publish: (update) => Object.assign(state, update), unauthorized: props.unauthorized })
    return { state, index }
  },
  computed: {
    results() { return searchSettings([...toolSearchEntries(this.tools), ...this.state.entries], this.state.query) },
    showResults() { return this.enabled && this.state.open && !!this.state.query.trim() },
    resultsStyle() {
      const rect = this.state.rect
      return rect ? { top: `${rect.top}px`, left: `${rect.left}px`, width: `${rect.width}px` } : {}
    },
  },
  watch: {
    enabled(value) { if (!value) { this.index.stop(); this.close() } },
    settingsEnabled(value) { if (!value) this.index.stop() },
  },
  mounted() {
    this.onViewportChange = () => { if (this.state.open) this.measure() }
    window.addEventListener("resize", this.onViewportChange)
    window.addEventListener("scroll", this.onViewportChange, true)
    document.addEventListener("pointerdown", this.onOutsidePointer, true)
  },
  beforeUnmount() {
    this.index.stop()
    window.removeEventListener("resize", this.onViewportChange)
    window.removeEventListener("scroll", this.onViewportChange, true)
    document.removeEventListener("pointerdown", this.onOutsidePointer, true)
  },
  methods: {
    close() { this.state.query = ""; this.state.open = false; this.state.active = 0; this.state.rect = null },
    openSearch() {
      this.state.open = true
      this.$nextTick(() => { this.$refs.input?.focus(); this.measure() })
    },
    measure() {
      const input = this.$refs.input
      if (!input) return
      const rect = input.getBoundingClientRect()
      const gap = 8, margin = 12
      const viewport = document.documentElement.clientWidth
      const next = viewport < 768
        ? { top: Math.round(rect.bottom + gap), left: margin, width: viewport - margin * 2 }
        : { top: Math.round(rect.bottom + gap),
            left: Math.round(Math.max(gap, Math.min(rect.left, viewport - margin - Math.max(340, rect.width)))),
            width: Math.round(Math.max(340, rect.width)) }
      const current = this.state.rect
      if (!current || current.top !== next.top || current.left !== next.left || current.width !== next.width) this.state.rect = next
    },
    toggle() { this.state.open ? this.close() : this.openSearch() },
    onOutsidePointer(event) {
      if (!this.state.open) return
      if (this.$el?.contains(event.target) || this.$refs.results?.contains(event.target)) return
      this.close()
    },
    onInput() { this.state.open = true; this.state.active = 0; if (this.settingsEnabled) this.index.load(); this.$nextTick(() => this.measure()) },
    onFocus() { this.openSearch() },
    onKey(event) {
      if (event.key === "Escape") {
        this.state.query = ""; this.state.open = false; event.preventDefault()
        this.$nextTick(() => { if (this.$refs.toggle?.getClientRects().length) this.$refs.toggle.focus() })
        return
      }
      if (!this.showResults) return
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault()
        this.state.active = (this.state.active + (event.key === "ArrowDown" ? 1 : -1) + this.results.length) % Math.max(1, this.results.length)
      } else if (event.key === "Enter" && this.results.length) { event.preventDefault(); this.choose(this.results[this.state.active] || this.results[0]) }
    },
    choose(hit) { this.state.query = ""; this.state.open = false; this.state.rect = null; this.openPage(hit) },
  },
  template: `
    <div class="gx-searchbox" :class="{ open: state.open }">
      <button type="button" ref="toggle" class="gx-icon-btn gx-search-toggle" :aria-label="state.open ? 'Close search' : 'Search Galaxy'" :aria-expanded="state.open"
        :disabled="!enabled" @click="toggle"><i class="bi" :class="state.open ? 'bi-x-lg' : 'bi-search'"></i><span v-if="!state.open" class="gx-search-toggle__label" aria-hidden="true">Search</span></button>
      <div class="gx-searchwrap">
      <i class="bi bi-search gx-search__icon" aria-hidden="true"></i>
      <input ref="input" v-model="state.query" class="gx-search gx-appbar__search" type="search" placeholder="Search Galaxy…"
        aria-label="Search Galaxy" :disabled="!enabled" role="combobox" aria-autocomplete="list"
        :aria-expanded="showResults" aria-controls="gx-toggle-search-results"
        :aria-activedescendant="showResults && results.length ? 'gx-toggle-result-' + state.active : undefined"
        @input="onInput" @focus="onFocus" @keydown="onKey">
      </div>
      <Teleport to="body">
        <div v-if="showResults && state.rect" id="gx-toggle-search-results" ref="results" class="gx-search-results" :style="resultsStyle" role="listbox" aria-label="Galaxy search results">
          <p v-if="state.status === 'loading' && !results.length" class="gx-search-results__status" role="status">Searching…</p>
          <button v-for="(hit, index) in results" :key="(hit.path || hit.page) + ':' + hit.label" type="button" role="option"
            :id="'gx-toggle-result-' + index" class="gx-search-results__item" :class="{active:index === state.active}"
            :aria-selected="index === state.active" @click="choose(hit)">
            <strong>{{ hit.label }}</strong><span>{{ hit.kind }}<template v-if="hit.context"> · {{ hit.context }}</template></span>
          </button>
          <p v-if="state.status === 'partial' && !state.entries.length" class="gx-search-results__status" role="status">Some settings couldn't be searched. Try again.</p>
          <p v-if="(state.status === 'ready' || !settingsEnabled) && !results.length" class="gx-search-results__status">No matching results.</p>
        </div>
      </Teleport>
    </div>`,
}
