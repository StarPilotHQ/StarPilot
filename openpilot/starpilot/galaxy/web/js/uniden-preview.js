// Live preview of the detector's full-screen warning, drawn the way onroad_uniden.py draws it on the car screen.
// Runs only while it is on screen and the page is visible, and holds still under reduced motion.

const W = 1860, H = 1080, FRAME = 30
const CONTENT = { x: FRAME, y: FRAME, w: W - 2 * FRAME, h: H - 2 * FRAME }
const RADIUS = 0.12 * CONTENT.h / 2
const EDGE_GAP = 12, EDGE_LINE = 8, DASH = 56, DASH_GAP = 30
const GLOW_DEPTH = [35, 135], GLOW_STEPS = 12, VEIL_ALPHA = 0.62
const CHEVRON = { size: 48, width: 13, fromSpeed: 300, fromEdge: 44 }
const BAND_RANK = { K: 0, KA: 1, LASER: 2 }
const FREQUENCY = { K: '24.150 GHz', KA: '34.700 GHz', LASER: '' }

// Galaxy update progress colors, secondary teal to primary purple, as the card's signal bars draw them.
export function signalColor(strength, index) {
  const t = index / Math.max(1, strength - 1)
  return `rgb(${Math.round(77 + 80 * t)},${Math.round(232 - 118 * t)},${Math.round(232 + 23 * t)})`
}
export const chevronCount = strength => Math.ceil(Math.min(8, Math.max(1, strength)) / 2)

// Brightness before pulses, mute and fade: the line and chevrons stay bright; with the glow, strength sets it.
export function edgeAlpha(parts, band, strength) {
  const s = (strength - 1) / 7
  return parts.glow ? (band === 'LASER' ? 0.95 : 0.45 + 0.45 * s) : (band === 'LASER' ? 1 : 0.85)
}

// The same pulse rules as EdgeMotion on the device.
export class EdgeMotion {
  constructor() { this.alert = null; this.baseline = 0; this.pulses = []; this.ended = null }
  update(alert, now, still) {
    if (!alert) { if (this.alert) { this.ended = [now, this.alert]; this.alert = null } return }
    const previous = this.alert
    this.alert = alert; this.ended = null
    const fresh = !previous || previous.band !== alert.band
    if (fresh) {
      this.baseline = alert.strength
      if (!previous || (BAND_RANK[alert.band] ?? 0) > (BAND_RANK[previous.band] ?? 0))
        this.pulse(now, still, alert.band === 'LASER' ? 3 : previous ? 1 : 2)
    } else if (alert.strength >= this.baseline + 2) {
      this.baseline = alert.strength
      this.pulse(now, still, 1)
    } else this.baseline = Math.min(this.baseline, alert.strength)
  }
  pulse(now, still, count) { if (!still) this.pulses.push([now, count, count === 3 ? 0.25 : 0.3]) }
  boost(now) {
    this.pulses = this.pulses.filter(([at, count, period]) => now - at < count * period)
    return this.pulses.reduce((best, [at, , period]) => Math.max(best, Math.sin(Math.PI * (((now - at) / period) % 1))), 0)
  }
  fading(now) {
    if (!this.ended || now - this.ended[0] >= 0.5) { this.ended = null; return null }
    return [this.ended[1], 1 - (now - this.ended[0]) / 0.5]
  }
}

function roundRect(ctx, x, y, w, h, r) {
  r = Math.max(0, Math.min(r, w / 2, h / 2))
  ctx.beginPath()
  ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r)
  ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath()
}
const rgba = (rgb, a) => `rgba(${rgb[0]},${rgb[1]},${rgb[2]},${Math.max(0, Math.min(1, a)).toFixed(3)})`
const hexRgb = hex => { const n = parseInt(hex.slice(1, 7), 16); return [n >> 16 & 255, n >> 8 & 255, n & 255] }

function glowRing(ctx, depth, alpha, rgb) {
  const band = depth / GLOW_STEPS
  for (let i = 0; i < GLOW_STEPS; i++) {
    const inset = i * band + band / 2, fall = 1 - i / GLOW_STEPS
    ctx.strokeStyle = rgba(rgb, alpha * fall * fall); ctx.lineWidth = band + 0.6
    roundRect(ctx, CONTENT.x + inset, CONTENT.y + inset, CONTENT.w - 2 * inset, CONTENT.h - 2 * inset, RADIUS - inset)
    ctx.stroke()
  }
}

function sideGlow(ctx, side, depth, alpha, rgb) {
  const { x, y, w, h } = CONTENT
  const geometry = { top: [0, y, 0, y + depth, x + RADIUS, y, w - 2 * RADIUS, depth],
    bottom: [0, y + h, 0, y + h - depth, x + RADIUS, y + h - depth, w - 2 * RADIUS, depth],
    left: [x, 0, x + depth, 0, x, y + RADIUS, depth, h - 2 * RADIUS],
    right: [x + w, 0, x + w - depth, 0, x + w - depth, y + RADIUS, depth, h - 2 * RADIUS] }[side]
  const g = ctx.createLinearGradient(...geometry.slice(0, 4))
  g.addColorStop(0, rgba(rgb, alpha)); g.addColorStop(0.45, rgba(rgb, alpha * 0.35)); g.addColorStop(1, rgba(rgb, 0))
  ctx.fillStyle = g; ctx.fillRect(...geometry.slice(4))
}

function chevronSets(direction, speedX) {
  const top = [['top', speedX - CHEVRON.fromSpeed], ['top', speedX + CHEVRON.fromSpeed]]
  return { front: top, '': top, rear: [['bottom', speedX]], side: [['left', H / 2], ['right', H / 2]] }[direction] || top
}

function drawChevrons(ctx, direction, strength, color, speedX) {
  const { size, fromEdge } = CHEVRON
  ctx.lineWidth = CHEVRON.width; ctx.strokeStyle = color; ctx.lineCap = 'butt'; ctx.lineJoin = 'miter'
  for (const [side, along] of chevronSets(direction, speedX)) {
    for (let i = 0; i < chevronCount(strength); i++) {
      const off = fromEdge + i * size * 0.8 + size * 0.4
      const [cx, cy, dx, dy] = { top: [along, CONTENT.y + off, 0, -1], bottom: [along, CONTENT.y + CONTENT.h - off, 0, 1],
        left: [CONTENT.x + off, along, -1, 0], right: [CONTENT.x + CONTENT.w - off, along, 1, 0] }[side]
      ctx.beginPath()
      ctx.moveTo(cx - dy * size - dx * size * 0.5, cy + dx * size - dy * size * 0.5)
      ctx.lineTo(cx + dx * size * 0.5, cy + dy * size * 0.5)
      ctx.lineTo(cx + dy * size - dx * size * 0.5, cy - dx * size - dy * size * 0.5)
      ctx.stroke()
    }
  }
}

// Draw one alert in the Edge style. `look` is { parts, color, mutedPercent }.
export function drawEdge(ctx, alert, look, boost, presence, speedX = W / 2) {
  const mute = alert.muted ? look.mutedPercent / 100 : 1
  if (mute === 0) return
  const { parts } = look, rgb = hexRgb(look.color), s = (alert.strength - 1) / 7
  const alpha = Math.min(1, edgeAlpha(parts, alert.band, alert.strength) * (1 + 0.9 * boost)) * mute * presence
  const depth = (GLOW_DEPTH[0] + (GLOW_DEPTH[1] - GLOW_DEPTH[0]) * s) * (1 + 0.35 * boost)
  const sides = { front: ['top'], rear: ['bottom'], side: ['left', 'right'] }[alert.direction]
  ctx.save()
  roundRect(ctx, CONTENT.x, CONTENT.y, CONTENT.w, CONTENT.h, RADIUS); ctx.clip()
  if (parts.veil) glowRing(ctx, depth * 1.3, VEIL_ALPHA * Math.min(1, alpha / 0.6), [0, 0, 0])
  if (parts.glow && sides) {
    glowRing(ctx, Math.max(18, depth * 0.3), alpha * 0.5, rgb)
    for (const side of sides) sideGlow(ctx, side, depth * 1.6, alpha, rgb)
  } else if (parts.glow) glowRing(ctx, depth, alpha, rgb)
  ctx.restore()
  if (parts.line) {
    const half = EDGE_GAP + EDGE_LINE / 2
    ctx.lineWidth = EDGE_LINE; ctx.strokeStyle = rgba(rgb, alpha * 1.15)
    ctx.setLineDash(parts.dashes ? [DASH, DASH_GAP] : [])
    roundRect(ctx, CONTENT.x + half, CONTENT.y + half, CONTENT.w - 2 * half, CONTENT.h - 2 * half, RADIUS - half); ctx.stroke()
    ctx.setLineDash([])
  }
  if (parts.chevrons) drawChevrons(ctx, alert.direction, alert.strength, rgba(rgb, alpha * 1.1), speedX)
}

function curveGlow(ctx, kind, t) {
  if (kind === 'off') return
  const s = { learning: 0.7, gentle: 0.79, tight: 1 }[kind], f = (s - 0.7) / 0.3
  const blend = (a, b, x) => a.map((v, i) => v + Math.trunc((b[i] - v) * Math.max(0, Math.min(1, x))))
  const rgb = kind === 'learning' ? [112, 192, 216] : f < 0.5 ? blend([34, 197, 94], [251, 191, 36], f / 0.5) :
    f < 0.6 ? blend([251, 191, 36], [234, 88, 12], (f - 0.5) / 0.1) : blend([234, 88, 12], [201, 34, 49], (f - 0.6) / 0.4)
  const cycle = 4.5 / (1 + 2 * f), amplitude = 0.3 + 0.7 * f
  const breath = Math.max(0, Math.min(1, 0.5 + amplitude * Math.sin((t % cycle) / cycle * 2 * Math.PI)))
  const alpha = 200 * s * (0.6 + 0.4 * breath) / 255, span = 75, { x, y, w, h } = CONTENT
  for (const [x0, y0, x1, y1, rx, ry, rw, rh] of [[0, y, 0, y + span, x, y, w, span], [0, y + h, 0, y + h - span, x, y + h - span, w, span],
    [x, 0, x + span, 0, x, y, span, h], [x + w, 0, x + w - span, 0, x + w - span, y, span, h]]) {
    const g = ctx.createLinearGradient(x0, y0, x1, y1)
    g.addColorStop(0, rgba(rgb, alpha)); g.addColorStop(1, rgba(rgb, 0))
    ctx.fillStyle = g; ctx.fillRect(rx, ry, rw, rh)
  }
}

function paintRoad(canvas) {
  const g = canvas.getContext('2d')
  const sky = g.createLinearGradient(0, 0, 0, H * 0.55)
  sky.addColorStop(0, '#5f87a8'); sky.addColorStop(1, '#c9d3d6')
  g.fillStyle = sky; g.fillRect(0, 0, W, H * 0.55)
  g.fillStyle = '#3d4a3c'; g.fillRect(0, H * 0.47, W, H * 0.1)
  const asphalt = g.createLinearGradient(0, H * 0.55, 0, H)
  asphalt.addColorStop(0, '#5a5e63'); asphalt.addColorStop(1, '#2c2f33')
  g.fillStyle = '#7f8f74'; g.fillRect(0, H * 0.55, W, H * 0.45)
  g.fillStyle = asphalt
  g.beginPath(); g.moveTo(W * 0.47, H * 0.55); g.lineTo(W * 0.53, H * 0.55); g.lineTo(W * 1.05, H); g.lineTo(-W * 0.05, H); g.closePath(); g.fill()
  g.strokeStyle = 'rgba(240,240,230,.85)'; g.lineWidth = 8
  for (let i = 0; i < 6; i++) { const t0 = 0.56 + i * 0.075; g.beginPath(); g.moveTo(W / 2, H * t0); g.lineTo(W / 2, H * (t0 + 0.035)); g.stroke() }
}

function drawCard(ctx, alert, font) {
  const x = 700, y = 560, w = 460, h = 76
  roundRect(ctx, x, y, w, h, 14); ctx.fillStyle = 'rgba(0,0,0,.62)'; ctx.fill()
  ctx.font = `600 23px ${font}`
  const muteWidth = alert.muted ? ctx.measureText('MUTED').width : 0
  ctx.save()
  if (alert.muted) {
    ctx.beginPath(); ctx.rect(x - 2, y - 2, w + 4, h + 4)
    ctx.rect(x + w - 36 - muteWidth, y - 2, muteWidth + 16, 4); ctx.clip('evenodd')
  }
  roundRect(ctx, x, y, w, h, 14)
  ctx.lineWidth = 2; ctx.strokeStyle = '#9D72FF'; ctx.stroke()
  ctx.restore()
  const tone = alert.band === 'K' ? '#FFBA4E' : '#FF5B65'
  ctx.textBaseline = 'middle'
  ctx.fillStyle = tone; ctx.font = `600 36px ${font}`; ctx.fillText({ K: 'K', KA: 'Ka', LASER: 'Laser' }[alert.band], x + 24, y + h / 2)
  ctx.fillStyle = '#fff'; ctx.font = `600 28px ${font}`; ctx.fillText(FREQUENCY[alert.band] || 'Gatso', x + 112, y + h / 2)
  const barsX = x + w - 160
  for (let i = 0; i < 8; i++) {
    const bh = 8 + i * 3
    ctx.fillStyle = i < alert.strength ? signalColor(alert.strength, i) : '#35434d'
    ctx.fillRect(barsX + i * 11, y + h / 2 + 15 - bh, 8, bh)
  }
  if (alert.muted) {
    ctx.font = `600 23px ${font}`; ctx.fillStyle = '#FF5B65'; ctx.fillText('MUTED', x + w - 28 - muteWidth, y + 1)
  }
}

// Preview scenarios: what is on the road, not settings. `drive` plays a scripted pass on repeat.
const scene = (label, band, strength, direction, extra = {}) => ({ label, alert: { band, strength, direction, muted: false, ...extra.alert }, curve: extra.curve || 'off', border: extra.border || '#167F40' })
// Grouped by setting, then K, Ka and Laser together inside each group.
export const PREVIEW_SCENARIOS = {
  'On the road': {
    drive: { label: 'K drive-by: builds, gets muted, passes', script: true, curve: 'off', border: '#167F40' },
    'k-ahead': scene('K ahead', 'K', 4, 'front'),
    'k-weak': scene('K ahead, weak', 'K', 1, 'front'),
    'k-behind': scene('K behind', 'K', 3, 'rear'),
    'k-muted': scene('K muted on the detector', 'K', 4, 'front', { alert: { muted: true } }),
    r4: scene('K on an R4, no direction', 'K', 4, ''),
    'ka-strong': scene('Ka ahead, strong', 'KA', 7, 'front'),
    'ka-beside': scene('Ka beside', 'KA', 5, 'side'),
    laser: scene('Laser', 'LASER', 8, 'front'),
  },
  'With openpilot': {
    'learning-curve': scene('K while curves are learning', 'K', 4, 'front', { curve: 'learning' }),
    experimental: scene('K in Experimental mode', 'K', 4, 'front', { border: '#DA6F25' }),
    chill: scene('K in Chill mode', 'K', 4, 'front', { border: '#FFD600' }),
    disengaged: scene('K while disengaged', 'K', 4, 'front', { border: '#122839' }),
    'tight-curve': scene('Ka in a tight curve', 'KA', 5, 'front', { curve: 'tight' }),
    switchback: scene('Ka with Switchback', 'KA', 5, 'side', { border: '#8B6CC5' }),
    'laser-curve': scene('Laser in a tight curve', 'LASER', 8, 'front', { curve: 'tight' }),
  },
}
export const previewScenario = id => Object.values(PREVIEW_SCENARIOS).map(group => group[id]).find(Boolean) || PREVIEW_SCENARIOS['On the road'].drive

// One pass, as on the road: [seconds, change]. The pass repeats after it ends.
const DRIVE = [[0.4, { active: true }], [1.6, { strength: 3 }], [2.6, { strength: 4 }], [3.6, { strength: 6 }], [5.6, { muted: true }],
  [7.2, { direction: 'side' }], [8.2, { strength: 4 }], [9, { direction: 'rear', strength: 2 }], [10.4, { active: false }]]
const DRIVE_START = { active: false, band: 'K', strength: 2, direction: 'front', muted: false }, DRIVE_LAP = 12

export const EdgePreview = {
  props: { config: { type: Object, required: true }, swatches: { type: Object, required: true },
    scenario: { type: String, default: 'drive' }, replay: { type: Number, default: 0 } },
  watch: {
    scenario() { this.restart() },
    replay() { this.restart() },
    config: { deep: true, handler() { this.loop() } },
  },
  mounted() {
    this.visible = true
    this.road = document.createElement('canvas'); this.road.width = W; this.road.height = H; paintRoad(this.road)
    this.reduce = window.matchMedia('(prefers-reduced-motion: reduce)')
    this.font = getComputedStyle(document.body).fontFamily || 'system-ui, sans-serif'
    this.observer = typeof IntersectionObserver === 'function' ? new IntersectionObserver(([entry]) => { this.visible = entry.isIntersecting; this.loop() }) : null
    this.observer?.observe(this.$el)
    this.onVisibility = () => this.loop()
    document.addEventListener('visibilitychange', this.onVisibility)
    this.restart()
  },
  unmounted() {
    cancelAnimationFrame(this.frame); this.frame = null
    this.observer?.disconnect(); document.removeEventListener('visibilitychange', this.onVisibility)
  },
  methods: {
    now() { return performance.now() / 1000 },
    still() { return this.reduce.matches },
    restart() {
      this.motion = new EdgeMotion(); this.started = this.now(); this.step = -1
      const chosen = previewScenario(this.scenario)
      this.state = chosen.script ? { ...DRIVE_START } : { active: true, ...chosen.alert }
      this.motion.update(this.alert(), this.started, this.still())
      this.loop()
    },
    alert() { return this.state.active ? { band: this.state.band, strength: this.state.strength, direction: this.state.direction, muted: this.state.muted } : null },
    // Advance the scripted pass to time t; the pass restarts after DRIVE_LAP seconds.
    advance(t) {
      if (!previewScenario(this.scenario).script) return
      if (t - this.started >= DRIVE_LAP) { this.restart(); return }
      while (this.step + 1 < DRIVE.length && t - this.started >= DRIVE[this.step + 1][0]) {
        this.step++; Object.assign(this.state, DRIVE[this.step][1])
        this.motion.update(this.alert(), t, this.still())
      }
    },
    look(band) {
      return { parts: this.config.border_edge, color: this.swatches[this.config.border_colors[band]] || '#FFFFFF',
        mutedPercent: this.config.border_muted_percent }
    },
    loop() {
      const running = this.visible && !document.hidden && !this.still()
      if (running && !this.frame) this.frame = requestAnimationFrame(() => { this.frame = null; this.draw(); this.loop() })
      else if (!running) this.draw()
    },
    draw() {
      const canvas = this.$el
      if (!canvas?.getContext) return
      const t = this.now(), chosen = previewScenario(this.scenario)
      // With motion reduced the pass would never move, so show its strongest moment instead.
      if (this.still() && chosen.script) Object.assign(this.state, { active: true, strength: 6, muted: false, direction: 'front' })
      else this.advance(t)
      const ctx = canvas.getContext('2d')
      ctx.fillStyle = '#000'; ctx.fillRect(0, 0, W, H)
      ctx.save(); roundRect(ctx, CONTENT.x, CONTENT.y, CONTENT.w, CONTENT.h, RADIUS); ctx.clip()
      ctx.drawImage(this.road, 0, 0); ctx.fillStyle = 'rgba(0,0,0,.18)'; ctx.fillRect(0, 0, W, H); ctx.restore()
      curveGlow(ctx, chosen.curve, t)
      let alert = this.alert(), presence = 1
      if (!alert) { const fade = this.motion.fading(t); if (fade) [alert, presence] = fade }
      if (alert && this.config.border_bands.includes(alert.band)) drawEdge(ctx, alert, this.look(alert.band), this.motion.boost(t), presence)
      ctx.lineWidth = FRAME; ctx.strokeStyle = chosen.border
      roundRect(ctx, CONTENT.x - FRAME / 2, CONTENT.y - FRAME / 2, CONTENT.w + FRAME, CONTENT.h + FRAME, RADIUS + FRAME / 2); ctx.stroke()
      ctx.fillStyle = '#fff'; ctx.textAlign = 'center'; ctx.textBaseline = 'alphabetic'
      ctx.font = `700 150px ${this.font}`; ctx.fillText('47', W / 2, 220)
      ctx.font = `500 34px ${this.font}`; ctx.fillText('mph', W / 2, 262); ctx.textAlign = 'left'
      if (this.state.active) drawCard(ctx, this.state, this.font)
    },
  },
  template: `<canvas class="gx-layout__preview gx-uniden__preview-canvas" width="1860" height="1080" role="img" aria-label="Preview of the full-screen warning on the driving screen"></canvas>`,
}
