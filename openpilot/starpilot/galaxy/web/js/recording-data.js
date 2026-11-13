// Local recording inventory and closed camera playback.
export const LOCAL = /^(?:[a-f0-9]{8}--[a-f0-9]{10}|[0-9]{4}-[0-9]{2}-[0-9]{2}--[0-9]{2}-[0-9]{2}-[0-9]{2}|[a-f0-9]{16}\|(?:[a-f0-9]{8}--[a-f0-9]{10}|[0-9]{4}-[0-9]{2}-[0-9]{2}--[0-9]{2}-[0-9]{2}-[0-9]{2}))$/
export const SEGMENT = /^(?:[a-f0-9]{8}--[a-f0-9]{10}|[0-9]{4}-[0-9]{2}-[0-9]{2}--[0-9]{2}-[0-9]{2}-[0-9]{2}|[a-f0-9]{16}[|_](?:[a-f0-9]{8}--[a-f0-9]{10}|[0-9]{4}-[0-9]{2}-[0-9]{2}--[0-9]{2}-[0-9]{2}-[0-9]{2}))--[0-9]{1,6}$/
const FILES = ["rlog", "qlog", "fcamera", "dcamera", "ecamera", "qcamera"]
export const LABELS = { rlog: "Full log", qlog: "Quick log", fcamera: "Road video", dcamera: "Driver video",
  ecamera: "Wide video", qcamera: "Quick video" }

export function validLocalHistory(value, complete = false) {
  if (value?.schemaVersion !== 1 || value.source !== "local" || (!complete && value.partialHistory !== true) ||
      typeof value.scanIncomplete !== "boolean" || !Array.isArray(value.routes) || value.routes.length > (complete ? 1 : 100)) return false
  let count = 0
  const routes = new Set()
  return value.routes.every((route) => {
    if (typeof route?.routeId !== "string" || !LOCAL.test(route.routeId) || routes.has(route.routeId) ||
        !Array.isArray(route.segments) || route.segments.length > 512 ||
        !Number.isSafeInteger(route.segmentCount) || route.segmentCount !== route.segments.length) return false
    routes.add(route.routeId)
    const numbers = new Set()
    count += route.segmentCount
    return count <= 512 && route.segments.every((segment) => {
      if (!Number.isSafeInteger(segment?.number) || segment.number < 0 || segment.number > 999999 ||
          typeof segment.segmentName !== "string" || !SEGMENT.test(segment.segmentName) ||
          (!segment.segmentName.startsWith(route.routeId.replace('|', '_') + '--') &&
           !segment.segmentName.startsWith(route.routeId + '--')) ||
          Number(segment.segmentName.slice(segment.segmentName.lastIndexOf('--') + 2)) !== segment.number ||
          numbers.has(segment.number) || segment.files === null || typeof segment.files !== "object" ||
          Object.keys(segment.files).length !== FILES.length ||
          !FILES.every((key) => typeof segment.files[key] === "boolean") ||
          (!complete && !FILES.some((key) => segment.files[key]))) return false
      numbers.add(segment.number)
      return true
    })
  })
}

export const VIDEOS = ["fcamera", "dcamera", "ecamera", "qcamera"]
export const hasVideo = (files) => VIDEOS.some((key) => files?.[key])
export const availableFiles = (files) => FILES.filter((key) => files?.[key]).map((key) => LABELS[key])
export const routeFiles = (route) => availableFiles(Object.fromEntries(FILES.map((key) =>
  [key, route.segments.some((segment) => segment.files[key])])))
export const firstQuickVideo = (route) => route.segments.find((segment) => segment.files.qcamera) || null
export function routeDate(route, locale = undefined, timeZone = undefined) {
  const validTime = (value) => typeof value === "number" && Number.isFinite(value) && value > 0 && Number.isFinite(new Date(value * 1000).getTime())
  const captured = validTime(route.startTime)
  const value = captured ? route.startTime : validTime(route.fileTime) ? route.fileTime : null
  if (value === null) return { label: "Date unavailable", source: "", datetime: null }
  const date = new Date(value * 1000)
  return { label: new Intl.DateTimeFormat(locale, { year: "numeric", month: "short", day: "numeric",
    hour: "numeric", minute: "2-digit", ...(timeZone ? { timeZone } : {}) }).format(date),
    source: captured ? "" : "File date", datetime: date.toISOString() }
}
export function connectRouteUrl(route) {
  if (typeof route?.routeId !== "string" || !LOCAL.test(route.routeId) || typeof route.connectUrl !== "string") return null
  const provider = route.provider ?? "comma"
  if (!["comma", "konik", "custom"].includes(provider)) return null
  let parsed
  try {
    parsed = new URL(route.connectUrl)
  } catch {
    return null
  }
  if (parsed.protocol !== "https:" || parsed.username || parsed.password || parsed.search || parsed.hash) return null
  const expectedHost = provider === "konik" ? "stable.konik.ai" : provider === "comma" ? "connect.comma.ai" : null
  if (expectedHost !== null && (parsed.hostname !== expectedHost || parsed.port !== "")) return null
  const match = /\/([a-f0-9]{16})\/([^/?#]+)$/.exec(parsed.pathname)
  if (!match || (expectedHost !== null && parsed.pathname !== `/${match[1]}/${match[2]}`)) return null
  const [device, identifier] = route.routeId.includes('|') ? route.routeId.split('|') : [null, route.routeId]
  return match[2] === identifier && (device === null || match[1] === device) ? route.connectUrl : null
}
export const recordingUrl = (segmentName, camera = "qcamera") => SEGMENT.test(segmentName) &&
  ["qcamera", "fcamera", "dcamera", "ecamera"].includes(camera) ?
  `./api/recordings/media/${encodeURIComponent(segmentName)}${camera === "qcamera" ? "" : "/" + camera}` : null
export const quickRoadUrl = (segmentName) => recordingUrl(segmentName)
export const segmentSummaryUrl = (segmentName) => SEGMENT.test(segmentName) ?
  `./api/recordings/segment-summary?segmentName=${encodeURIComponent(segmentName)}` : null

export function validSegmentSummary(value, segmentName) {
  const metric = (v) => v === null || (typeof v === "number" && Number.isFinite(v) && v >= 0)
  return value?.schemaVersion === 1 && value.source === "closed_local_rlog" &&
    value.segmentName === segmentName && typeof value.sourceSha256 === "string" &&
    /^[a-f0-9]{64}$/.test(value.sourceSha256) &&
    [value.observedCarSpanSeconds, value.estimatedDistanceMeters,
      value.observedLatActiveSeconds, value.observedLongActiveSeconds].every(metric) &&
    value.gaps && Number.isSafeInteger(value.gaps.carState) && value.gaps.carState >= 0 &&
    Number.isSafeInteger(value.gaps.carControl) && value.gaps.carControl >= 0 &&
    typeof value.sampleCoverageComplete === "boolean"
}

export const detailMetric = (seconds, unit = "s") => seconds === null ? "Unavailable" :
  `${seconds.toFixed(unit === "m" ? 1 : 2)} ${unit}`



const searchText = value => String(value || "").normalize("NFKD").replace(/\p{M}/gu, "")
  .toLocaleLowerCase().replace(/(\d+)(?:st|nd|rd|th)\b/g, "$1").replace(/[^\p{L}\p{N}]+/gu, " ").trim()

export function routeMatchesSearch(route, query) {
  const date = routeDate(route)
  const index = searchText(`${route.displayName || ""} ${route.routeId} ${date.label} ${date.datetime || ""}`)
  return searchText(query).split(" ").every(word => index.includes(word))
}

export const driveVideoUrl = (routeId, camera) => LOCAL.test(routeId) && VIDEOS.includes(camera) ?
  `./api/recordings/route/${encodeURIComponent(routeId)}/${camera}` : null
export const logUrl = (segmentName, filename) => SEGMENT.test(segmentName) && /^(rlog|qlog)\.(zst|bz2)$/.test(filename) ?
  `./api/recordings/files/${encodeURIComponent(segmentName)}/${filename}` : null
export const logArchiveUrl = routeId => LOCAL.test(routeId) ? `./api/recordings/logs/${encodeURIComponent(routeId)}` : null
export const cameraLabel = camera => LABELS[camera]?.replace(" video", "")
export const formatDuration = minutes => minutes >= 60 ? `${Math.floor(minutes / 60)}h ${minutes % 60}m` : `${minutes} min`
export const formatBytes = bytes => bytes >= 1e9 ? `${(bytes / 1e9).toFixed(2)} GB` : `${((bytes || 0) / 1e6).toFixed(1)} MB`
export function validRouteDetail(value, routeId) {
  return value?.schemaVersion === 1 && value.source === "local" && value.route?.routeId === routeId &&
    typeof value.route.inventoryComplete === "boolean" && validLocalHistory({ schemaVersion: 1, source: "local",
      scanIncomplete: !value.route.inventoryComplete, routes: [value.route] }, true)
}
