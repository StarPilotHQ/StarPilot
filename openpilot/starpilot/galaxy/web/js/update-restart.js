import { requestJson } from "./startup.js"

export const UPDATE_RESTART_KEY = "galaxy-update-restart"
export const UPDATE_RESTART_PREPARED_KEY = "galaxy-update-prepared"
export const UPDATE_RESTART_MESSAGE = "StarPilot is restarting after an update. Galaxy is waiting for your device to come online and will reconnect automatically."
export const UPDATE_RESTART_TIMEOUT = 5 * 60 * 1000
export const UPDATE_RESTART_TIMEOUT_MESSAGE = "Galaxy could not verify the update after five minutes. Your device may still be updating. Check its connection and refresh to read its status before trying another update."

export function savedUpdateRestart(storage = globalThis.sessionStorage) {
  try {
    const value = JSON.parse(storage.getItem(UPDATE_RESTART_KEY))
    return typeof value?.commit === "string" && typeof value?.branch === "string" ? value : null
  } catch { return null }
}

function savedPreparedUpdate(storage) {
  try {
    const value = JSON.parse(storage.getItem(UPDATE_RESTART_PREPARED_KEY))
    return typeof value?.commit === "string" && typeof value?.branch === "string" ? value : null
  } catch { return null }
}

// Keep restart recovery outside the page so authentication changes cannot dismiss it.
export class UpdateRestart {
  constructor({ publish, storage = globalThis.sessionStorage, request = requestJson,
    later = (fn, ms) => setTimeout(fn, ms), cancel = id => clearTimeout(id), reload = () => location.reload(), now = () => Date.now() }) {
    Object.assign(this, { publish, storage, request, later, cancel, reload, now })
    this.prepared = savedPreparedUpdate(storage)
    this.baseline = savedUpdateRestart(storage) || this.prepared
    this.offline = !!this.baseline
    this.polling = false
    this.retry = null
    this.deadline = null
    this.expired = false
    this.generation = 0
    this.publish(!!this.baseline)
    if (this.baseline) { this.watchDeadline(); if (this.baseline) this.poll() }
  }

  start(baseline, { provisional = false } = {}) {
    if (this.expired) return
    if (!provisional) this.cancelPreparation()
    if (this.baseline) {
      this.baseline = { ...this.baseline, ...baseline, instance: this.baseline.instance ?? baseline.instance, createdAt: this.baseline.createdAt }
      try { this.storage.setItem(UPDATE_RESTART_KEY, JSON.stringify(this.baseline)) } catch {}
      return
    }
    this.baseline = { ...baseline, createdAt: this.now() }
    this.watchDeadline()
    this.publish(true)
    this.poll()
  }

  prepare(baseline) {
    if (this.baseline) return
    this.expired = false
    this.prepared = { ...baseline, createdAt: this.now() }
    try { this.storage.setItem(UPDATE_RESTART_PREPARED_KEY, JSON.stringify(this.prepared)) } catch {}
  }

  cancelPreparation() {
    this.prepared = null
    try { this.storage.removeItem(UPDATE_RESTART_PREPARED_KEY) } catch {}
  }

  watchDeadline() {
    const createdAt = this.baseline.createdAt
    this.baseline.createdAt = Number.isFinite(createdAt) && createdAt <= this.now() ? createdAt : this.now()
    try { this.storage.setItem(UPDATE_RESTART_KEY, JSON.stringify(this.baseline)) } catch {}
    const remaining = UPDATE_RESTART_TIMEOUT - (this.now() - this.baseline.createdAt)
    if (remaining <= 0) { this.finish(true); return }
    this.deadline = this.later(() => this.finish(true), remaining)
  }

  finish(expired = false) {
    this.generation++
    this.baseline = null
    this.expired = expired
    if (this.retry !== null) this.cancel(this.retry)
    if (this.deadline !== null) this.cancel(this.deadline)
    this.retry = this.deadline = null
    try { this.storage.removeItem(UPDATE_RESTART_KEY) } catch {}
    this.cancelPreparation()
    this.publish(false, expired ? UPDATE_RESTART_TIMEOUT_MESSAGE : "")
    if (!expired) this.reload()
  }

  async poll() {
    if (this.polling || !this.baseline) return
    this.polling = true
    const generation = this.generation
    try {
      const data = await this.request("./api/software/status?history=0")
      if (!this.baseline || generation !== this.generation) return
      const installed = data?.installed, request = data?.operations?.request
      if (data?.schemaVersion !== 1 || !installed || !data.operations) throw new Error("Status unavailable")
      const changed = installed.commit && (installed.commit !== this.baseline.commit || installed.branch !== this.baseline.branch)
      const finished = request?.state === "failed" || request?.outcome === "up_to_date"
      const restarted = typeof this.baseline.instance === "string" && typeof data.instance === "string" &&
        data.instance !== this.baseline.instance
      const returned = data.updater?.state === "idle" &&
        (!request && (changed || restarted) || restarted && request?.state !== "pending")
      const notStarted = this.prepared && data.updater?.state === "idle" && !request &&
        !changed && !restarted && this.now() - this.prepared.createdAt > 30000
      if (finished || returned || notStarted) {
        this.finish()
      }
    } catch {
      this.offline = true
    } finally {
      this.polling = false
      if (this.baseline && this.retry === null) this.retry = this.later(() => {
        this.retry = null
        this.poll()
      }, 2000)
    }
  }
}
