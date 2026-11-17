import { requestJson } from './startup.js'
import { PollTimer } from './polling.js'

export function weatherTemperature(data, metric, now = Date.now() / 1000) {
  const c = data?.temperatureC
  return data?.status === 'current' && typeof c === 'number' && Number.isFinite(c) && c >= -100 && c <= 70 &&
    typeof data.observedAt === 'number' && now - data.observedAt >= -300 && now - data.observedAt <= 1800
    ? `${Math.round(metric ? c : c * 9 / 5 + 32)}°${metric ? 'C' : 'F'}` : 'Unavailable'
}

export const WeatherVitals = {
  props: { metric: Boolean, mode: String, unauthorized: Function },
  data: () => ({ data: null, active: false, request: null }),
  computed: {
    temperature() { return weatherTemperature(this.data, this.metric) },
    detail() { return this.data?.status === 'no-location' ? 'No saved location' :
      this.data?.status === 'loading' ? 'Updating weather…' : 'Estimate near last saved location' },
  },
  mounted() {
    this.active = true
    this.timer = new PollTimer({ read: () => this.load() })
    this.visible = () => {
      if (document.hidden) { this.timer.stop(); this.request?.abort(); this.data = null }
      else if (this.mode === 'local') { this.timer.start(); this.load() }
    }
    document.addEventListener('visibilitychange', this.visible)
    if (this.mode === 'local' && !document.hidden) { this.timer.start(); this.load() }
  },
  beforeUnmount() {
    this.active = false
    this.timer.stop()
    this.request?.abort()
    document.removeEventListener('visibilitychange', this.visible)
  },
  methods: {
    async load() {
      if (!this.active || this.mode !== 'local' || document.hidden || this.request) return
      const request = new AbortController()
      this.request = request
      try {
        const data = await requestJson('./api/weather', { request: { signal: request.signal } })
        if (this.active && !document.hidden && !request.signal.aborted) this.data = data
      } catch (error) {
        if (!request.signal.aborted && this.active) {
          this.data = null
          if (error.status === 401) this.unauthorized()
        }
      } finally { if (this.request === request) this.request = null }
    },
  },
  template: `<div><p>Outside temperature: <strong>{{ temperature }}</strong></p>
    <small>{{ detail }} · <a href="https://open-meteo.com/" target="_blank" rel="noopener noreferrer">Weather by Open-Meteo</a></small></div>`,
}
