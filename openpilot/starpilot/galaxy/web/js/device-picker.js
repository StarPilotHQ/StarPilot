import { GxNotice } from "./notice.js"

const SLUG = /^[A-Za-z0-9]{16}$/
const unsupported = (response) => response.status === 404 || response.status === 405

async function readName(device) {
  try {
    const response = await fetch(`/${device.slug}/api/galaxy/device-name`, {
      cache: "no-store", credentials: "same-origin", signal: AbortSignal.timeout(5000),
    })
    if (!response.ok) return device
    const data = await response.json()
    if (typeof data.name === "string" && Array.from(data.name).length <= 40) return { ...device, name: data.name || device.name }
  } catch {}
  return device
}

async function namedDevices(devices) {
  const result = [...devices]
  let next = 0
  const worker = async () => {
    while (next < devices.length) {
      const index = next++
      result[index] = await readName(devices[index])
    }
  }
  await Promise.all(Array.from({ length: Math.min(4, devices.length) }, worker))
  return result
}

export const DevicePicker = {
  name: "DevicePicker",
  components: { GxNotice },
  data: () => ({ devices: [], activeSlug: "", loading: true, editing: "", draft: "", error: "", saving: false }),
  computed: { visible() { return location.hostname === "galaxy.firestar.link" && this.devices.length > 1 } },
  mounted() { if (location.hostname === "galaxy.firestar.link") this.load() },
  methods: {
    async load() {
      try {
        const response = await fetch("/_gateway/devices", { cache: "no-store", credentials: "same-origin" })
        if (!response.ok) return
        const data = await response.json()
        this.activeSlug = SLUG.test(data?.activeSlug || "") ? data.activeSlug : ""
        this.devices = Array.isArray(data?.devices) ? data.devices.filter((device) => SLUG.test(device?.slug || "")).slice(0, 40) : []
        this.devices = await namedDevices(this.devices)
      } catch {} finally { this.loading = false }
    },
    select(device) {
      if (SLUG.test(device.slug) && device.slug !== this.activeSlug) window.location.assign(`/${device.slug}/`)
    },
    edit(device) { this.editing = device.slug; this.draft = device.name || ""; this.error = "" },
    async save() {
      if (!SLUG.test(this.editing)) return
      const slug = this.editing
      const name = Array.from(this.draft.trim()).slice(0, 40).join("")
      this.saving = true
      this.error = ""
      try {
        let response = await fetch(`/${slug}/api/galaxy/device-name`, {
          method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
          signal: AbortSignal.timeout(5000), body: JSON.stringify({ name }),
        })
        if (unsupported(response)) {
          response = await fetch(`/_gateway/devices/${slug}/name`, {
            method: "PUT", credentials: "same-origin", headers: { "Content-Type": "application/json" },
            signal: AbortSignal.timeout(7000), body: JSON.stringify({ name }),
          })
        }
        const data = await response.json().catch(() => ({}))
        if (!response.ok || typeof data.name !== "string") throw new Error(data.error || "Could not rename comma")
        this.devices = this.devices.map((device) => device.slug === slug ? { ...device, name: data.name } : device)
        if (this.editing === slug) this.editing = ""
      } catch (error) { this.error = error.message }
      finally { this.saving = false }
    },
  },
  template: `
    <div v-if="visible" class="gx-nav-section" :inert="saving" :aria-busy="saving || undefined">
      <div class="gx-nav-section__title">Commas</div>
      <div v-for="(device, index) in devices" :key="device.slug" class="gx-device-picker__row">
        <button type="button" class="gx-nav-item gx-grow" :class="{active:device.slug === activeSlug}" @click="select(device)">
          <i class="bi bi-cpu"></i><span class="gx-truncate"><span translate="no">{{ device.name || 'Comma ' + (index + 1) }}</span></span>
          <small v-if="device.slug === activeSlug">Current</small>
        </button>
        <button type="button" class="gx-icon-btn" :aria-label="'Rename ' + (device.name || 'comma')" @click="edit(device)"><i class="bi bi-pencil"></i></button>
      </div>
      <form v-if="editing" @submit.prevent="save" class="gx-device-picker__form">
        <label for="gx-device-name">Rename comma</label>
        <input id="gx-device-name" class="gx-field" v-model="draft" maxlength="40" autocomplete="off" />
        <div class="gx-actions gx-tags"><button type="submit" class="gx-btn">Save</button><button type="button" class="gx-btn gx-btn--tonal" @click="editing=''">Cancel</button></div>
        <GxNotice v-if="error" tone="danger">{{ error }}</GxNotice>
      </form>
    </div>
  `,
}
