const ROUTE = /^[a-zA-Z0-9_+|.-]{1,128}$/
const DATE = /^\d{4}-\d{2}-\d{2}$/
const number = (value) => typeof value === "number" && Number.isFinite(value) && value >= 0
const optionalNumber = (value) => value === null || number(value)
const integer = (value) => Number.isSafeInteger(value) && value >= 0
const text = (value, limit = 128) => typeof value === "string" && value.length <= limit &&
  !/[\x00-\x1f\x7f]/.test(value)
const optionalText = (value, limit = 128) => value === null || text(value, limit)
// Optional additions preserve compatibility with older statistics responses.
const validAol = (value, aggregate = false) => {
  if (value.aolSeconds === undefined && value.aolPercent === undefined) return true
  if (![value.aolSeconds, value.aolPercent].every(optionalNumber) ||
      (value.aolSeconds === null) !== (value.aolPercent === null) ||
      (value.aolPercent !== null && value.aolPercent > 100)) return false
  const duration = aggregate ? value.aolDurationSeconds : value.durationSeconds
  return (value.aolSeconds === null || (number(duration) && value.aolSeconds <= duration)) &&
    (!aggregate || (number(duration) && integer(value.aolDrives) && value.aolDrives <= value.drives))
}

const validDrive = (drive) => drive && ROUTE.test(drive.routeId) &&
  [drive.startTime, drive.endTime].every((value) => value === null || number(value)) &&
  [drive.distanceMeters, drive.durationSeconds, drive.engagedSeconds, drive.engagedPercent,
    drive.distractedMoments, drive.unresponsiveMoments].every(optionalNumber) &&
  optionalText(drive.model) && optionalText(drive.reason, 256) &&
  typeof drive.complete === "boolean" && typeof drive.ignored === "boolean" &&
  (drive.partial === undefined || (typeof drive.partial === "boolean" && !(drive.partial && drive.complete))) &&
  integer(drive.segmentCount) && drive.segmentCount <= 10000 &&
  (drive.engagedPercent === null || drive.engagedPercent <= 100) && validAol(drive)

export function validDriveStats(value) {
  if (value?.schemaVersion !== 1 || typeof value.isMetric !== "boolean" || !value.analysis || !value.totals || !value.week ||
      !Array.isArray(value.recentDrives) || value.recentDrives.length > 100 ||
      !Array.isArray(value.records) || value.records.length > 24 ||
      !Array.isArray(value.models) || value.models.length > 100) return false
  const { analysis, totals, week, lastDrive, recentDrives, records, models } = value
  if (!validAol(totals, true) || !validAol(week, true)) return false
  if (week.timezone !== undefined && !text(week.timezone)) return false
  if (typeof analysis.running !== "boolean" || !integer(analysis.pending) || !integer(analysis.failed) ||
      typeof analysis.scanIncomplete !== "boolean") return false
  if (![totals.distanceMeters, totals.durationSeconds, totals.engagedSeconds].every(optionalNumber) ||
      !integer(totals.drives) ||
      ![week.distanceMeters, week.durationSeconds, week.engagedPercent].every(optionalNumber) ||
      (week.engagedPercent !== null && week.engagedPercent > 100) || !integer(week.drives) ||
      !Array.isArray(week.days) || week.days.length !== 7 ||
      !week.days.every((day) => day && DATE.test(day.date) && Number.isFinite(Date.parse(`${day.date}T00:00:00Z`)) &&
        optionalNumber(day.distanceMeters))) return false
  const routes = new Set()
  if (lastDrive !== null && !validDrive(lastDrive)) return false
  if (!recentDrives.every((drive) => {
    if (!validDrive(drive) || routes.has(drive.routeId)) return false
    routes.add(drive.routeId)
    return true
  })) return false
  const recordIds = new Set()
  if (!records.every((record) => {
    if (!record || !text(record.id) || !text(record.label) ||
        !(record.value === null || number(record.value) || text(record.value)) || !optionalText(record.unit, 32) ||
        recordIds.has(record.id)) return false
    recordIds.add(record.id)
    return true
  })) return false
  const names = new Set()
  return models.every((model) => {
    if (!model || !text(model.name) || names.has(model.name) ||
        ![model.distanceMeters, model.durationSeconds].every(optionalNumber) || !integer(model.drives)) return false
    names.add(model.name)
    return true
  })
}

export class DriveStatsFeed {
  constructor({ publish, unauthorized = () => {}, fetcher = (...args) => fetch(...args),
                later = (fn, ms) => setTimeout(fn, ms), cancelTimer = (id) => clearTimeout(id) }) {
    Object.assign(this, { publish, unauthorized, fetcher, later, cancelTimer })
    this.active = false
    this.generation = 0
    this.controller = null
    this.timeout = null
    this.poll = null
    this.data = null
    this.status = "idle"
    this.error = ""
    this.busy = false
  }

  emit() { this.publish({ status: this.status, data: this.data, error: this.error, busy: this.busy }) }

  stop() {
    this.active = false
    this.generation++
    this.controller?.abort()
    if (this.timeout !== null) this.cancelTimer(this.timeout)
    if (this.poll !== null) this.cancelTimer(this.poll)
    this.controller = this.timeout = this.poll = null
    this.data = null
    this.status = "idle"
    this.error = ""
    this.busy = false
    this.emit()
  }

  start() { this.stop(); this.active = true; return this.load() }

  schedule(delay = null) {
    if (!this.active || this.poll !== null || this.controller !== null) return
    const generation = this.generation
    const analyzing = this.data?.analysis?.running
    this.poll = this.later(() => {
      this.poll = null
      if (this.active && generation === this.generation) this.load()
    }, delay ?? (analyzing ? 5000 : 60000))
  }

  async request(body = null) {
    if (!this.active || this.controller !== null) return null
    const generation = this.generation
    const controller = new AbortController()
    this.controller = controller
    if (this.poll !== null) this.cancelTimer(this.poll)
    this.poll = null
    this.status = this.data ? "ready" : this.error ? "unavailable" : "loading"
    this.busy = body !== null
    if (body !== null) this.error = ""
    this.emit()
    this.timeout = this.later(() => {
      if (!this.active || generation !== this.generation || this.controller !== controller) return
      controller.abort()
      this.generation++
      this.controller = this.timeout = null
      this.busy = false
      this.status = this.data ? "ready" : "unavailable"
      this.error = "Drive history timed out. Refresh to try again."
      this.emit()
      this.schedule(5000)
    }, 5000)
    try {
      const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC"
      const statsUrl = "./api/drives/stats?timezone=" + encodeURIComponent(timezone)
      let response = await this.fetcher(body === null ? statsUrl : "./api/drives/ignore", {
        credentials: "same-origin", cache: "no-store", signal: controller.signal,
        ...(body === null ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
      })
      if (!this.active || generation !== this.generation || controller.signal.aborted) return null
      if (response.status === 401) { this.stop(); this.unauthorized(); return null }
      if (response.status === 503) {
        const detail = await response.json().catch(() => null)
        if (!this.active || generation !== this.generation || controller.signal.aborted) return null
        if (["setup_required", "access_unavailable"].includes(detail?.code)) {
          this.stop(); this.unauthorized(); return null
        }
      }
      if (!response.ok) throw new Error("Drive history could not be updated. Refresh to try again.")
      if (body !== null) {
        response = await this.fetcher(statsUrl, { credentials: "same-origin", cache: "no-store", signal: controller.signal })
        if (response.status === 401) { this.stop(); this.unauthorized(); return null }
        if (!response.ok) throw new Error("Drive history could not be refreshed.")
      }
      const data = await response.json()
      if (!this.active || generation !== this.generation || controller.signal.aborted) return null
      if (!validDriveStats(data)) throw new Error("Drive history is unavailable. Refresh to try again.")
      this.data = data
      this.error = ""
      this.status = "ready"
      this.emit()
      return data
    } catch (error) {
      if (this.active && generation === this.generation) {
        this.status = this.data ? "ready" : "unavailable"
        this.error = controller.signal.aborted ? "Drive history timed out. Refresh to try again." : error.message
        this.emit()
      }
      return null
    } finally {
      if (this.controller === controller) {
        if (this.timeout !== null) this.cancelTimer(this.timeout)
        this.controller = this.timeout = null
        this.busy = false
        this.emit()
        this.schedule()
      }
    }
  }

  load() { return this.request() }

  ignore(routeId, ignored) {
    if (!this.data?.recentDrives?.some((drive) => drive.routeId === routeId && drive.ignored !== ignored) ||
        typeof ignored !== "boolean") return Promise.resolve(null)
    return this.request({ routeId, ignored })
  }
}
