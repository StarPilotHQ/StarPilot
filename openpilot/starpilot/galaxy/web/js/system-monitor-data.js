// Shared boundary; the selected transport must explicitly choose its source mode.
const object = (value) => value !== null && typeof value === "object" && !Array.isArray(value)
const number = (value, min = 0, max = Infinity) => typeof value === "number" && Number.isFinite(value) && value >= min && value <= max ? value : null
const text = (value) => typeof value === "string" && value.trim() ? value : null
const percent = (value) => number(value, 0, 100)
const integer = (value, min = 0) => Number.isSafeInteger(value) && value >= min ? value : null
const storageUsage = (raw) => {
  const usage = { usedGiB: number(raw?.usedGiB), totalGiB: number(raw?.totalGiB), freeGiB: number(raw?.freeGiB) }
  if (usage.totalGiB !== null && usage.usedGiB !== null && usage.usedGiB > usage.totalGiB) usage.usedGiB = null
  if (usage.totalGiB !== null && usage.freeGiB !== null && usage.freeGiB > usage.totalGiB) usage.freeGiB = null
  return usage
}

export function normalizeSnapshot(raw, expectedMode = "synthetic-preview") {
  if (!["synthetic-preview", "local-runtime"].includes(expectedMode) ||
      !object(raw) || raw.mode !== expectedMode || raw.schemaVersion !== 1 ||
      !Array.isArray(raw.cores) || !Array.isArray(raw.processes) || !object(raw.memory) || !object(raw.storage) ||
      number(raw.sampledAt, 1) === null || !text(raw.source)) {
    throw new Error("Invalid System Monitor sample")
  }
  const cores = raw.cores.map((core) => {
    if (!object(core) || !text(core.name)) throw new Error("Invalid CPU core in sample")
    return { name: core.name, percent: percent(core.percent) }
  })
  if (new Set(cores.map((core) => core.name)).size !== cores.length) throw new Error("Duplicate CPU core in sample")
  const processes = raw.processes.map((process) => {
    if (!object(process) || integer(process.pid, 1) === null || !text(process.name) || !text(process.user) ||
        typeof process.kernel !== "boolean" || !text(process.state)) throw new Error("Invalid process in sample")
    return { pid: process.pid, name: process.name, user: process.user, kernel: process.kernel,
      state: process.state, cpu: percent(process.cpu), memoryMiB: number(process.memoryMiB) }
  })
  if (new Set(processes.map((process) => process.pid)).size !== processes.length) throw new Error("Duplicate process PID in sample")
  const memory = { totalMiB: number(raw.memory.totalMiB, 0.01), usedMiB: number(raw.memory.usedMiB),
    availableMiB: number(raw.memory.availableMiB), percent: percent(raw.memory.percent) }
  const storage = storageUsage(raw.storage)
  const deviceType = ["tici", "tizi", "mici", "pc"].includes(raw.deviceType) ? raw.deviceType : null
  const recordingStorage = deviceType === "tici" && object(raw.recordingStorage) ? storageUsage(raw.recordingStorage) : null
  if (memory.totalMiB !== null && (memory.usedMiB !== null && memory.usedMiB > memory.totalMiB ||
      memory.availableMiB !== null && memory.availableMiB > memory.totalMiB)) {
    memory.usedMiB = null; memory.availableMiB = null; memory.percent = null
  }
  const v = object(raw.vitals) ? raw.vitals : {}
  const vitals = Object.fromEntries(["cpuTempC", "gpuTempC", "hotspotTempC", "gpuEdgeTempC", "memoryUsedBytes", "memoryTotalBytes",
    "onboardMaxAgeMs", "maxAgeMs", "memoryMaxAgeMs"].map((key) => [key, number(v[key])]))
  return { source: raw.source, sampledAt: raw.sampledAt, cpuPercent: percent(raw.cpuPercent), cores, memory, storage, recordingStorage, deviceType,
    batteryVoltage: number(raw.batteryVoltage, 0.01, 60),
    uptimeSeconds: number(raw.uptimeSeconds), processCount: integer(raw.processCount), processes, vitals }
}

const FEATURE_LABELS = new Map([
  ["selfdrive.controls.controlsd", "Steering and speed control"],
  ["selfdrive.modeld.modeld", "Driving model"],
  ["system.hardware.hardwared", "Power and temperature management"],
  ["system.loggerd.uploader", "Log uploads"],
  ["system.manager.manager", "Process manager"],
])
export const processFeature = (process) => process.kernel ? "" : FEATURE_LABELS.get(process.name.replace(/^openpilot\./, "")) || ""
const PROCESS_STATES = new Map([["R", "Running"], ["S", "Sleeping"], ["D", "Waiting"], ["T", "Stopped"],
  ["t", "Tracing"], ["Z", "Zombie"], ["I", "Idle"]])
export const processState = (state) => PROCESS_STATES.get(state) || state
export const displayNumber = (value, suffix = "") => value === null || value === undefined ? "—" : `${value.toFixed(1)}${suffix}`

export function vital(snapshot, field, budget, elapsedMs = 0) {
  const v = snapshot?.vitals
  if ((field === "memoryUsedBytes" || field === "memoryTotalBytes") && v?.memoryUsedBytes != null && v?.memoryTotalBytes != null &&
      v.memoryUsedBytes > v.memoryTotalBytes) return null
  return v && v[budget] > 0 && elapsedMs >= 0 && elapsedMs < v[budget] ? v[field] ?? null : null
}

export function processRows(snapshot, { query = "", scope = "comma", sort = "cpu", descending = true } = {}) {
  const q = query.trim().toLowerCase()
  const keys = new Set(["name", "pid", "cpu", "memoryMiB", "user", "state"])
  const key = keys.has(sort) ? sort : "cpu"
  return (snapshot?.processes || []).filter((p) =>
    (scope === "all" || (scope === "users" ? !p.kernel : p.user === "comma")) &&
    (!q || `${p.name} ${processFeature(p)} ${p.pid} ${p.user} ${processState(p.state)}`.toLowerCase().includes(q))
  ).slice().sort((a, b) => {
    const av = a[key], bv = b[key]
    if (av === null) return bv === null ? a.pid - b.pid : 1
    if (bv === null) return -1
    const cmp = typeof av === "number" ? av - bv : String(av).localeCompare(String(bv))
    return (descending ? -cmp : cmp) || a.pid - b.pid
  })
}
