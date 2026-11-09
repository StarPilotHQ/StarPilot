import { GxNotice } from "./notice.js"
import { markRaw } from "../vendor/vue/vue.esm-browser.js"

const SIZE = 512
const locationDeadlines = new WeakMap()
export function project(point, zoom) {
  const latitude = Math.max(-85.05112878, Math.min(85.05112878, point.latitude))
  const sine = Math.sin(latitude * Math.PI / 180), world = SIZE * 2 ** zoom
  return [(point.longitude + 180) / 360 * world, (0.5 - Math.log((1 + sine) / (1 - sine)) / (4 * Math.PI)) * world]
}
export function unproject(x, y, zoom) {
  const world = SIZE * 2 ** zoom
  return {
    longitude: ((x / world * 360) % 360 + 360) % 360 - 180, latitude: Math.atan(Math.sinh(Math.PI * (1 - 2 * y / world))) * 180 / Math.PI
  }
}
// Screen pixels per meter at a latitude: the circles for offline areas are true ground distances.
export function metersPerPixelInverse(latitude, zoom) {
  return SIZE * 2 ** zoom / (40075016.686 * Math.max(0.01, Math.cos(latitude * Math.PI / 180)))
}
export function relativeX(x, center, world) {
  return center + ((x - center + world / 2) % world + world) % world - world / 2
}
// Match the tile style while imagery loads, including during live theme changes.
const LAND = { light: '#f5f5f3', dark: '#191a1a' }
const TILE_BITMAPS = 64  // decoded 512 px tiles are ~1 MB each; the browser's HTTP cache keeps the rest
const MAX_ZOOM = 18
const clampZoom = (zoom) => Math.max(0, Math.min(MAX_ZOOM, zoom))
export class RasterMap {
  constructor(canvas, changed = () => {}) {
    this.canvas = canvas;
    this.changed = changed;
    this.zoom = 2;
    this.center = {
      longitude: 0, latitude: 0
    };
    this.failed = new Set();
    this.tiles = new Map();
    this.pending = new Map();
    this.closed = false;
    this.theme = globalThis.document?.documentElement?.dataset.theme === 'light' ? 'light' : 'dark'
    if (globalThis.MutationObserver && globalThis.document?.documentElement) {
      this.themeObserver = new MutationObserver(() => this.setTheme(document.documentElement.dataset.theme))
      this.themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
    }
    this.data = {};
    this.initialized = false
    // Following keeps the car centered; panning or a new route overview pauses it until Recenter.
    this.follow = true
    // Circles drawn over the map ({ latitude, longitude, radiusKm, stroke, fill }) and a tap callback (latitude, longitude).
    this.overlays = [];
    this.onTap = null
    this.pointers = new Map()
    this.observer = new ResizeObserver(() => this.draw());
    this.observer.observe(canvas)
    this.down = (event) => {
      this.pointers.set(event.pointerId, [event.clientX, event.clientY])
      canvas.setPointerCapture?.(event.pointerId)
      if (this.pointers.size === 1) {
        this.press = [event.clientX, event.clientY]
        this.drag = [event.clientX, event.clientY, ...project(this.center, this.zoom)];
      } else this.startPinch()
    }
    this.move = (event) => {
      if (this.pointers.has(event.pointerId)) this.pointers.set(event.pointerId, [event.clientX, event.clientY])
      if (this.pinch && this.pointers.size >= 2) {
        const [[ax, ay], [bx, by]] = this.pointers.values(), distance = Math.max(1, Math.hypot(ax - bx, ay - by))
        this.anchor(this.pinch.point, clampZoom(this.pinch.zoom + Math.log2(distance / this.pinch.distance)), (ax + bx) / 2, (ay + by) / 2)
        return
      }
      if (!this.drag) return;
      if (this.press && Math.hypot(event.clientX - this.press[0], event.clientY - this.press[1]) >= 6) this.pause()
      this.center = unproject(this.drag[2] - event.clientX + this.drag[0], this.drag[3] - event.clientY + this.drag[1], this.zoom);
      this.requestDraw()
    }
    this.up = (event) => {
      this.pointers.delete(event?.pointerId)
      if (this.pinch) {
        if (this.pointers.size < 2) {
          // Settle on a whole zoom level so tiles are drawn pixel for pixel.
          const [x, y] = this.pinch.screen
          this.pinch = this.press = null
          this.drag = null
          this.pointers.clear()
          this.zoomAt(Math.round(this.zoom), x, y)
        }
        return
      }
      this.drag = null
      const press = this.press
      this.press = null
      if (!press || event?.type !== 'pointerup' || Math.hypot(event.clientX - press[0], event.clientY - press[1]) >= 6) return
      const now = performance.now(), last = this.lastTap
      if (last && now - last[0] < 300 && Math.hypot(event.clientX - last[1], event.clientY - last[2]) < 24) {
        this.lastTap = null
        this.pause()
        this.zoomAt(Math.round(this.zoom) + 1, ...this.local(event))
        return
      }
      this.lastTap = [now, event.clientX, event.clientY]
      if (this.onTap) this.onTap(this.pointAt(...this.local(event)))
    }
    this.key = (event) => {
      if (this.onTap && (event.key === 'Enter' || event.key === ' ')) {
        event.preventDefault()
        this.onTap({ ...this.center })
        return
      }
      const delta = {
        ArrowLeft: [-80, 0], ArrowRight: [80, 0], ArrowUp: [0, -80], ArrowDown: [0, 80]
      }
      [event.key];
      if (delta) {
        event.preventDefault();
        this.pause()
        const [x,y] = project(this.center,this.zoom);
        this.center = unproject(x+delta[0],y+delta[1],this.zoom);
        this.draw()
      } else if (event.key === '+' || event.key === '=' || event.key === '-') {
        event.preventDefault()
        this.changeZoom(event.key === '-' ? -1 : 1)
      }
    }
    this.wheel = (event) => {
      event.preventDefault();
      // Trackpads send dozens of small wheel events per gesture; one zoom level per 100px, at most every 120 ms.
      this.wheelDelta = (this.wheelDelta || 0) + (event.deltaMode === 1 ? event.deltaY * 33 : event.deltaY)
      const now = performance.now()
      if (Math.abs(this.wheelDelta) < 100 || now - (this.lastWheelZoom || 0) < 120) return
      this.zoomAt(Math.round(this.zoom) + (this.wheelDelta < 0 ? 1 : -1), ...this.local(event))
      this.wheelDelta = 0
      this.lastWheelZoom = now
    }
    for (const [name, listener] of [['pointerdown', this.down], ['pointermove', this.move], ['pointerup', this.up], ['pointercancel', this.up], ['wheel', this.wheel], ['keydown', this.key]]) canvas.addEventListener(name, listener, {
      passive: false
    })
  }
  setTheme(value) {
    const theme = value === 'light' ? 'light' : 'dark'
    if (this.closed || this.theme === theme) return
    this.theme = theme
    this.clearTiles()
    this.changed('')
    this.draw()
  }
  clearTiles() {
    for (const controller of this.pending.values()) controller.abort()
    this.pending.clear()
    for (const image of this.tiles.values()) image.close()
    this.tiles.clear()
    this.failed.clear()
    this.failures = 0
    clearTimeout(this.retryTimer)
    this.retryTimer = null
  }
  pause() {
    if (this.follow) { this.follow = false; this.changed() }
  }
  startPinch() {
    const [[ax, ay], [bx, by]] = this.pointers.values(), x = (ax + bx) / 2, y = (ay + by) / 2
    const [left, top] = this.offset()
    this.drag = this.press = null
    this.pause()
    this.pinch = { zoom: this.zoom, distance: Math.max(1, Math.hypot(ax - bx, ay - by)), point: this.pointAt(x - left, y - top), screen: [x - left, y - top] }
  }
  offset() {
    const rect = this.canvas.getBoundingClientRect?.()
    return rect ? [rect.left, rect.top] : [0, 0]
  }
  local(event) {
    const [left, top] = this.offset()
    return [event.clientX - left, event.clientY - top]
  }
  // The map coordinate under a point on the canvas.
  pointAt(x, y) {
    const [cx, cy] = project(this.center, this.zoom)
    return unproject(cx - this.canvas.clientWidth / 2 + x, cy - this.canvas.clientHeight / 2 + y, this.zoom)
  }
  // Show `point` under the canvas position (clientX, clientY) at `zoom` (pinch keeps the pinched place under the fingers).
  anchor(point, zoom, clientX, clientY) {
    const [left, top] = this.offset(), [px, py] = project(point, zoom)
    this.zoom = zoom
    this.pinch.screen = [clientX - left, clientY - top]
    this.center = unproject(px - (clientX - left) + this.canvas.clientWidth / 2, py - (clientY - top) + this.canvas.clientHeight / 2, zoom)
    this.requestDraw()
  }
  zoomAt(zoom, x, y) {
    zoom = clampZoom(zoom)
    const point = this.pointAt(x, y), [px, py] = project(point, zoom)
    this.zoom = zoom
    if (!this.follow || !this.lastLocation) this.center = unproject(px - x + this.canvas.clientWidth / 2, py - y + this.canvas.clientHeight / 2, zoom)
    this.draw()
  }
  update(data, stale) {
    clearTimeout(this.locationTimer)
    const now = performance.now()
    if (data && !locationDeadlines.has(data)) {
      locationDeadlines.set(data, data.location?.expiresAt ?? now + (data.location?.validForMs ?? 0))
    }
    const remaining = Math.max(0, (data && locationDeadlines.get(data) || 0) - now)
    this.locationFresh = !stale && remaining > 0
    if (this.locationFresh) {
      this.locationTimer = setTimeout(() => {
        this.locationFresh = false;
        this.changed();
        this.draw()
      }, remaining)
    }
    if (data?.location && (this.locationFresh || data.location.lastKnown === true && !this.lastLocation)) {
      this.lastLocation = { ...data.location, bearing: Number.isFinite(data.location.bearing) ? data.location.bearing : this.lastLocation?.bearing }
    }
    const provider = data?.mapProvider === 'google' && data?.hasGoogleKey && !data?.googleMap?.failure ? 'google' : 'mapbox'
    if (this.provider && this.provider !== provider) this.clearTiles()
    this.provider = provider
    this.data = data || {}
    this.stale = stale
    // A newly chosen route is shown whole once; Recenter returns to following the car.
    const routeKey = this.data.destination && this.data.route?.length > 1 ? `${this.data.destination.id}|${this.data.routeKey ?? this.data.route.length}` : null
    if (routeKey && routeKey !== this.overviewKey) {
      this.overviewKey = routeKey
      if (this.fit([...this.data.route, ...(this.lastLocation ? [this.lastLocation] : [])])) { this.follow = false; this.initialized = true }
    } else if (!routeKey) this.overviewKey = null
    if (this.follow && this.lastLocation && (this.locationFresh || !this.initialized)) {
      this.center = { latitude: this.lastLocation.latitude, longitude: this.lastLocation.longitude }
      if (!this.initialized) this.zoom = 15
      this.initialized = true
    } else if (!this.initialized && data?.destination) {
      this.recenter()
      this.initialized = true
    }
    this.changed()
    this.draw()
  }
  // Frame these points with a margin, on a whole zoom level. False when there is nothing to frame.
  fit(points) {
    const width = this.canvas.clientWidth, height = this.canvas.clientHeight
    if (!points.length || width < 2 || height < 2) return false
    const xs = points.map((point) => project(point, 0)[0]), ys = points.map((point) => project(point, 0)[1])
    const spanX = Math.max(1e-9, Math.max(...xs) - Math.min(...xs)), spanY = Math.max(1e-9, Math.max(...ys) - Math.min(...ys))
    this.zoom = clampZoom(Math.floor(Math.min(16, Math.log2(width * 0.75 / spanX), Math.log2(height * 0.75 / spanY))))
    this.center = unproject((Math.max(...xs) + Math.min(...xs)) / 2 * 2 ** this.zoom, (Math.max(...ys) + Math.min(...ys)) / 2 * 2 ** this.zoom, this.zoom)
    return true
  }
  recenter() {
    this.follow = true
    const point = this.lastLocation || this.data.destination;
    if (point) {
      this.center = { latitude: point.latitude, longitude: point.longitude };
      this.zoom = this.lastLocation ? 15 : 2
    }
    this.changed()
    this.draw()
  }
  requestDraw() {
    this.frameScheduled ??= requestAnimationFrame(() => { this.frameScheduled = null; this.draw() })
  }
  // The nearest loaded ancestor of a missing tile, and the part of it that covers the tile.
  placeholder(zoom, x, y) {
    for (let up = 1; up <= 5 && zoom - up >= 0; up++) {
      const image = this.tiles.get(`${zoom - up}/${x >> up}/${y >> up}`)
      if (image) {
        const span = (image.width || SIZE) / 2 ** up
        return [image, (x - ((x >> up) << up)) * span, (y - ((y >> up) << up)) * span, span]
      }
    }
    return null
  }
  changeZoom(delta) {
    this.zoom = clampZoom(Math.round(this.zoom) + delta);
    this.draw()
  }
  draw() {
    if (this.closed) return
    const width = Math.max(1, Math.round(this.canvas.clientWidth)), height = Math.max(1, Math.round(this.canvas.clientHeight));
    // Draw at the screen's pixel density so roads, labels and the route stay sharp on phones.
    const ratio = Math.min(3, Math.max(1, globalThis.devicePixelRatio || 1))
    if (this.canvas.width !== Math.round(width * ratio)) this.canvas.width = Math.round(width * ratio);
    if (this.canvas.height !== Math.round(height * ratio)) this.canvas.height = Math.round(height * ratio)
    const ctx = this.canvas.getContext('2d'), [cx, cy] = project(this.center, this.zoom), world = SIZE * 2 ** this.zoom, needed = []
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0)
    ctx.imageSmoothingQuality = 'high'
    ctx.fillStyle = LAND[this.theme];
    ctx.fillRect(0, 0, width, height)
    // Tiles come from the nearest whole zoom level, scaled while a pinch is between levels.
    const tileZoom = clampZoom(Math.round(this.zoom)), n = 2 ** tileZoom, span = SIZE * 2 ** (this.zoom - tileZoom)
    for (let ty = Math.floor((cy - height / 2) / span); ty <= Math.floor((cy + height / 2) / span); ty++) {
      if (ty < 0 || ty >= n) continue
      for (let tx = Math.floor((cx - width / 2) / span); tx <= Math.floor((cx + width / 2) / span); tx++) {
        const x = ((tx % n) + n) % n, key = `${tileZoom}/${x}/${ty}`, left = tx * span - cx + width / 2, top = ty * span - cy + height / 2
        needed.push([key, Math.hypot(left + span / 2 - width / 2, top + span / 2 - height / 2)])
        const image = this.tiles.get(key)
        if (image) ctx.drawImage(image, left, top, span, span)
        else {
          // A stretched coarser tile while this one loads, so zooming in never shows blank squares.
          const cover = this.placeholder(tileZoom, x, ty)
          if (cover) ctx.drawImage(cover[0], cover[1], cover[2], cover[3], cover[3], left, top, span, span)
        }
      }
    }
    const keys = new Set(needed.map(([key]) => key))
    for (const [key, controller] of this.pending) if (!keys.has(key)) {
      controller.abort();
      this.pending.delete(key)
    }
    // The middle of the screen loads first.
    needed.sort((a, b) => a[1] - b[1])
    for (const [key] of needed) if (!this.tiles.has(key) && !this.pending.has(key) && !this.failed.has(key) && this.pending.size < 6) this.load(key)
    const pixel = (point) => {
      const [x, y] = project(point, this.zoom);
      return [relativeX(x, cx, world) - cx + width / 2, y - cy + height / 2]
    }
    for (const circle of this.overlays) {
      const [x, y] = pixel(circle), radius = metersPerPixelInverse(circle.latitude, this.zoom) * circle.radiusKm * 1000
      ctx.beginPath(); ctx.arc(x, y, Math.max(2, radius), 0, Math.PI * 2)
      ctx.fillStyle = circle.fill; ctx.fill()
      ctx.setLineDash(circle.dash || []); ctx.strokeStyle = circle.stroke; ctx.lineWidth = circle.width || 2; ctx.stroke(); ctx.setLineDash([])
    }
    const line = (points, casing, color, size) => {
      ctx.beginPath();
      points.forEach((point, index) => { const xy = pixel(point); index ? ctx.lineTo(...xy) : ctx.moveTo(...xy) })
      ctx.lineJoin = ctx.lineCap = 'round'
      ctx.strokeStyle = casing; ctx.lineWidth = size + 3; ctx.stroke()
      ctx.strokeStyle = color; ctx.lineWidth = size; ctx.stroke()
    }
    for (const choice of this.data.alternatives || []) {
      if (choice.index === this.data.selectedRoute || !choice.geometry?.length) continue
      line(choice.geometry, '#727e8d', '#a9b4c2', 5)
    }
    if (this.data.route?.length) line(this.data.route, '#2f7ac6', '#56a8fb', 6)
    if (this.data.destination) {
      // A pin whose tip marks the place.
      const [x, y] = pixel(this.data.destination)
      ctx.beginPath(); ctx.moveTo(x, y); ctx.bezierCurveTo(x - 4, y - 8, x - 11, y - 14, x - 11, y - 21)
      ctx.arc(x, y - 21, 11, Math.PI, 0); ctx.bezierCurveTo(x + 11, y - 14, x + 4, y - 8, x, y); ctx.closePath()
      ctx.fillStyle = '#e5484d'; ctx.fill(); ctx.strokeStyle = '#fff'; ctx.lineWidth = 2; ctx.stroke()
      ctx.beginPath(); ctx.arc(x, y - 21, 4, 0, Math.PI * 2); ctx.fillStyle = '#fff'; ctx.fill()
    }
    if (this.lastLocation) {
      const xy = pixel(this.lastLocation), color = this.locationFresh ? '#1a73e8' : '#8a94a6'
      if (Number.isFinite(this.lastLocation.bearing)) {
        ctx.save(); ctx.translate(...xy); ctx.rotate(this.lastLocation.bearing * Math.PI / 180);
        ctx.beginPath(); ctx.moveTo(0, -22); ctx.lineTo(-8, -10); ctx.lineTo(8, -10); ctx.closePath();
        ctx.fillStyle = color; ctx.fill(); ctx.restore()
      }
      ctx.beginPath(); ctx.arc(...xy, 8, 0, Math.PI * 2)
      ctx.fillStyle = color; ctx.fill(); ctx.strokeStyle = '#fff'; ctx.lineWidth = 3; ctx.stroke()
    }
  }
  async load(key) {
    const controller = new AbortController();
    this.pending.set(key, controller);
    let timedOut = false
    const timer = setTimeout(() => {
      timedOut = true;
      controller.abort()
    }, 8000)
    const providerQuery = this.provider === 'google' ? '&provider=google' : ''
    try {
      const response = await fetch(`./api/navigation/map/tiles/${key}.png?theme=${this.theme}${providerQuery}`, {
        credentials: 'same-origin', signal: controller.signal
      });
      if (!response.ok) throw new Error('Map tiles unavailable')
      const image = await createImageBitmap(await response.blob());
      if (this.closed || controller.signal.aborted) {
        image.close();
        return
      }
      this.tiles.set(key, image);
      this.failed.delete(key);
      this.failures = 0
      while (this.tiles.size > TILE_BITMAPS) {
        const oldest = this.tiles.keys().next().value;
        this.tiles.get(oldest).close();
        this.tiles.delete(oldest)
      }
      this.changed('')
      if (this.failed.size && !this.retryTimer) {
        this.retryTimer = setTimeout(() => { this.retryTimer = null; this.retry() }, 400)
      }
    } catch (error) {
      if (this.pending.get(key) === controller && (timedOut || !controller.signal.aborted)) {
        this.failed.add(key);
        if (this.failed.size > 64) this.failed.delete(this.failed.values().next().value);
        // A quick refusal (a busy moment) is retried quickly and silently; a hung request or a run of failures is reported.
        this.failures = (this.failures || 0) + 1
        const persistent = timedOut || this.failures >= 8
        if (persistent) this.changed('Map tiles unavailable. Check the saved key and connection. Reconnecting automatically…')
        this.retryTimer ??= setTimeout(() => { this.retryTimer = null; this.retry() }, persistent ? 3000 : 600)
      }
    } finally {
      clearTimeout(timer);
      if (this.pending.get(key) === controller) this.pending.delete(key);
      if (!this.closed) {
        /* Paint completed tiles without continuously retrying failures. */ this.paintScheduled ??= requestAnimationFrame(() => {
          this.paintScheduled = null;
          this.draw()
        })
      }
    }
  }
  retry() {
    this.failed.clear();
    this.draw()
  }
  close() {
    this.closed = true;
    clearTimeout(this.locationTimer);
    clearTimeout(this.retryTimer);
    this.observer.disconnect();
    this.themeObserver?.disconnect();
    cancelAnimationFrame(this.paintScheduled);
    if (this.frameScheduled) cancelAnimationFrame(this.frameScheduled);
    for (const controller of this.pending.values()) controller.abort();
    for (const image of this.tiles.values()) image.close();
    this.tiles.clear();
    for (const [name, listener] of [['pointerdown', this.down], ['pointermove', this.move], ['pointerup', this.up], ['pointercancel', this.up], ['wheel', this.wheel], ['keydown', this.key]]) this.canvas.removeEventListener(name, listener)
  }
}
export const NavigationMap = {
  components: { GxNotice },
  props: ['data', 'stale'], data: () => ({
    error: '', map: null, locationFresh: false, lastLocation: null, following: true
  }),
  computed: {
    googleMap() { return this.data?.mapProvider === 'google' && !!this.data?.hasGoogleKey && !this.data?.googleMap?.failure },
  },
  mounted() {
    this.map = markRaw(new RasterMap(this.$refs.canvas, (error) => {
      if (error !== undefined) this.error = error
      this.locationFresh = this.map?.locationFresh || false
      this.lastLocation = this.map?.lastLocation || null
      this.following = this.map?.follow ?? true
    }));
    this.map.update(this.data, this.stale)
  },
  beforeUnmount() {
    this.map.close()
  },
  methods: {
    overview() {
      if (this.map?.fit(this.data?.route || [])) {
        this.map.pause()
        this.map.draw()
      }
    },
  },
  watch: {
    data(value) {
      this.map?.update(value, this.stale)
    }, stale(value) {
      this.map?.update(this.data, value)
    }
  },
  template: `<section class="gx-navigation-map" aria-label="Interactive navigation map">
    <canvas tabindex="0" ref="canvas" aria-label="Map. Drag to pan, pinch or double-tap to zoom" />
    <div v-if="!locationFresh && lastLocation" class="gx-navigation-map__last">{{ data?.route?.length ? 'Preview from last saved location · GPS unavailable' : 'Last saved location · GPS unavailable' }}</div>
    <div class="gx-navigation-map__controls">
    <button v-if="data?.route?.length" type="button" class="gx-navigation-map__control" @click="overview" aria-label="Show whole route" title="Show whole route"><i class="bi bi-bounding-box" aria-hidden="true"></i></button>
    <button type="button" class="gx-navigation-map__control" @click="map.changeZoom(1)" aria-label="Zoom in"><i class="bi bi-plus-lg" aria-hidden="true"></i></button>
    <button type="button" class="gx-navigation-map__control" @click="map.changeZoom(-1)" aria-label="Zoom out"><i class="bi bi-dash-lg" aria-hidden="true"></i></button>
    <button type="button" class="gx-navigation-map__control" :class="{'gx-navigation-map__control--active':!following}" @click="map.recenter()" :aria-pressed="following" aria-label="Recenter and follow"><i class="bi bi-crosshair" aria-hidden="true"></i></button>
    </div>
    <GxNotice v-if="error" tone="danger">{{error}}</GxNotice>
    <a v-if="!googleMap" class="gx-navigation-map__logo" href="https://www.mapbox.com/" target="_blank" rel="noopener noreferrer" aria-label="Mapbox">
    </a>
    <div v-if="googleMap" class="gx-navigation-map__credits">
    <a class="gx-link" href="https://www.google.com/maps" target="_blank" rel="noopener noreferrer">Google Maps</a> · <span>{{ data?.googleMap?.copyright || 'Map data © Google' }}</span> · <a class="gx-link" href="https://www.google.com/intl/en-US_US/help/terms_maps/" target="_blank" rel="noopener noreferrer">Terms</a>
    </div>
    <div v-else class="gx-navigation-map__credits">
    <a class="gx-link" href="https://www.mapbox.com/about/maps/" target="_blank" rel="noopener noreferrer">© Mapbox</a> · <a class="gx-link" href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">© OpenStreetMap</a> · <a class="gx-link" href="https://apps.mapbox.com/feedback/" target="_blank" rel="noopener noreferrer">Improve this map</a>
    </div>
    </section>`
}
