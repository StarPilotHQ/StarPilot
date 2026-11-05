import { reactive } from "../vendor/vue/vue.esm-browser.js"

const GAIN_MIN = 0.6
const GAIN_MAX = 1.6
const SAMPLE_STEP = 8
const DESATURATE = 0.75

export function whiteBalanceGains(data, width, height) {
  let r = 0, g = 0, b = 0, count = 0
  for (let y = 0; y < height; y += SAMPLE_STEP) {
    for (let x = 0; x < width; x += SAMPLE_STEP) {
      const i = (y * width + x) * 4
      r += data[i]; g += data[i + 1]; b += data[i + 2]; count++
    }
  }
  if (!count) return [1, 1, 1]
  const mean = [r / count / 255, g / count / 255, b / count / 255]
  const luma = 0.299 * mean[0] + 0.587 * mean[1] + 0.114 * mean[2]
  if (luma < 0.02) return [1, 1, 1]
  return mean.map(channel => Math.min(GAIN_MAX, Math.max(GAIN_MIN, luma / Math.max(channel, 1e-3))))
}

// Cancels the cabin camera's IR magenta cast, leaving clipped highlights white.
export function whiteBalance(data, width, height) {
  const gains = whiteBalanceGains(data, width, height)
  for (let i = 0; i < data.length; i += 4) {
    const rgb = [data[i] / 255, data[i + 1] / 255, data[i + 2] / 255]
    const peak = Math.max(...rgb)
    const t = Math.min(1, Math.max(0, (peak - 0.95) / 0.05))
    const mixed = rgb.map((v, c) => v * gains[c] * (1 - t) + v * t)
    const gray = 0.299 * mixed[0] + 0.587 * mixed[1] + 0.114 * mixed[2]
    for (let c = 0; c < 3; c++) {
      const out = gray + (mixed[c] - gray) * DESATURATE
      data[i + c] = Math.round(Math.min(1, Math.max(0, out)) * 255)
    }
  }
  return data
}

export function balancedBlobUrl(src) {
  return new Promise((resolve, reject) => {
    const image = new Image()
    image.onload = () => {
      const canvas = document.createElement("canvas")
      canvas.width = image.naturalWidth
      canvas.height = image.naturalHeight
      const ctx = canvas.getContext("2d", { willReadFrequently: true })
      ctx.drawImage(image, 0, 0)
      const frame = ctx.getImageData(0, 0, canvas.width, canvas.height)
      whiteBalance(frame.data, canvas.width, canvas.height)
      ctx.putImageData(frame, 0, 0)
      canvas.toBlob(blob => blob ? resolve(URL.createObjectURL(blob)) : reject(new Error("Balance failed")), "image/jpeg", 0.92)
    }
    image.onerror = () => reject(new Error("Balance failed"))
    image.src = src
  })
}

const KEY = "gx-snapshot-white-balance"
const ENDPOINT = "./api/cameras/white-balance"
const send = (body) => fetch(ENDPOINT, { method: "POST", credentials: "same-origin", cache: "no-store",
  headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
  .then(response => response.ok ? response.json() : null).then(result => typeof result?.enabled === "boolean" ? result.enabled : null, () => null)

// The device owns this choice, so the on-screen cameras follow it; the browser copy is the fallback.
export const whiteBalancePref = reactive({
  on: (() => { try { return localStorage.getItem(KEY) !== "0" } catch { return true } })(),
  loaded: false,
  remember(on) { this.on = on; try { localStorage.setItem(KEY, on ? "1" : "0") } catch {} },
  async load() {
    if (this.loaded) return
    this.loaded = true
    const saved = await send({})
    if (saved !== null) this.remember(saved)
  },
  async set(on) {
    this.remember(on)
    const saved = await send({ enabled: on })
    if (saved !== null && saved !== on) this.remember(saved)
  },
})

export const WhiteBalanceSwitch = {
  name: "WhiteBalanceSwitch",
  setup: () => ({ pref: whiteBalancePref }),
  mounted() { this.pref.load() },
  template: `<div class="gx-row gx-row--borderless"><div class="gx-row__info"><span class="gx-row__label">White balance</span><small>Corrects the cabin camera's magenta tint.</small></div>
    <label class="gx-switch"><input type="checkbox" role="switch" aria-label="White balance" :checked="pref.on" @change="pref.set($event.target.checked)"><span class="gx-switch__track"></span><span class="gx-switch__thumb"></span></label></div>`,
}

export const BalancedImage = {
  name: "BalancedImage",
  props: { src: { type: String, required: true }, balance: { type: Boolean, default: true } },
  setup: () => ({ pref: whiteBalancePref }),
  data: () => ({ shown: "" }),
  computed: { active() { return this.balance && this.pref.on } },
  watch: { src() { this.refresh() }, active() { this.refresh() } },
  mounted() { this.pref.load(); this.refresh() },
  beforeUnmount() { this.token++; this.release() },
  methods: {
    release() { if (this.shown && this.shown !== this.src) URL.revokeObjectURL(this.shown); this.shown = "" },
    async refresh() {
      const token = this.token = (this.token || 0) + 1
      if (!this.active || !this.src) { this.release(); return }
      let url = ""
      try { url = await balancedBlobUrl(this.src) } catch { url = "" }
      if (token !== this.token) { if (url) URL.revokeObjectURL(url); return }
      this.release()
      this.shown = url
    },
  },
  template: `<img :src="shown || src">`,
}
