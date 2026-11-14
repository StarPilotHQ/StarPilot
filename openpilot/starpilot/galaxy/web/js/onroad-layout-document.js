import { LEAD_INFO } from "./projection-layout.js"

export const PROFILES = ["large", "compact"]
const COLORS = ["cardFill", "cardBorder", "text"]
export const ROAD_COLORS = ["path", "pathEdge", "laneLines"]
// "default" is a legacy saved value that now renders as acceleration colors.
export const PATH_MODES = ["default", "color", "rainbow", "acceleration"]
export const HEX = /^#[0-9a-fA-F]{8}$/
const keysEqual = (value, keys) => value && typeof value === "object" && !Array.isArray(value) &&
  Object.keys(value).length === keys.length && keys.every((key) => Object.hasOwn(value, key))
const positive = (value) => Number.isFinite(value) && value > 0 && value <= 8192
const object = (value) => value && typeof value === "object" && !Array.isArray(value)
const frontmostOrder = (order, profile) => {
  const pinned = Object.entries(profile.widgets || {}).filter(([, widget]) => widget.frontmost === true).map(([id]) => id)
  return pinned.length <= 1 && (!pinned.length || order.at(-1) === pinned[0])
}

// Display preferences ride along in the draft but are not part of the rendered layout.
export const layoutDocument = ({ display, ...document }) => document
export const previewDocument = document => ({ ...layoutDocument(document), developerSidebar: document.display?.DeveloperSidebar === "On" })
// The device sends them beside the layout; the editor keeps them in its draft so Save, undo and reload cover them.
export const withDisplay = (data) => !object(data?.display) || !Array.isArray(data.display.rows) ? data : {
  ...data, display: data.display.rows,
  document: { ...data.document, display: { ...data.display.values } },
  defaults: { ...data.defaults, display: { ...data.display.defaults } }
}

export function orderedWidgetIds(document, metadata, profile) {
  return document.widgetOrder?.[profile] || metadata.profiles[profile].widgetOrder || Object.keys(metadata.profiles[profile].widgets)
}

export function widgetPalette(document, metadata, profile, id) {
  const colors = metadata.profiles[profile].widgets[id].colors
  return {
    ...Object.fromEntries(Object.entries(colors).map(([key, value]) => [key, value ?? document.palette[key]])),
    ...document.widgetColors[profile][id]
  }
}

export const withAlpha = (color, factor) => color.slice(0, 7) + Math.floor(parseInt(color.slice(7), 16) * factor).toString(16).padStart(2, "0")

// Steering wheels resize as a square ("size"); box widgets such as the Android Auto map take a width and height.
export function widgetSize(widget, position) {
  if (widget.iconSize && position?.display === "icons") return [widget.iconSize, widget.iconSize]
  if (widget.sizes && Array.isArray(widget.sizes[position?.size])) return [...widget.sizes[position.size]]
  if (widget.box) return [position?.width ?? widget.width, position?.height ?? widget.height]
  if (widget.resizable) return [position?.size ?? widget.width, position?.size ?? widget.height]
  return [widget.width, widget.height]
}

export function placementLimits(profile, id, layout = null) {
  const widget = profile.widgets[id], bounds = widget.bounds || profile.bounds
  const [width, height] = widgetSize(widget, layout?.[id])
  const limits = {
    minX: bounds.x, maxX: bounds.x + bounds.width - width,
    minY: bounds.y, maxY: bounds.y + bounds.height - height
  }
  const confirmation = widget.confirmation
  if (confirmation && (layout?.[id]?.enabled ?? widget.default.enabled) &&
    (layout?.speed_limit_actions?.enabled ?? profile.widgets.speed_limit_actions.default.enabled)) {
    limits.minX = Math.max(limits.minX, bounds.x - confirmation.x)
    limits.maxX = Math.min(limits.maxX, bounds.x + bounds.width - confirmation.x - confirmation.width)
    limits.minY = Math.max(limits.minY, bounds.y - confirmation.y)
    limits.maxY = Math.min(limits.maxY, bounds.y + bounds.height - confirmation.y - confirmation.height)
  }
  return limits
}

// The largest box that still fits from the widget's current corner, within its own limits.
export function clampBox(profile, id, position, width, height) {
  const widget = profile.widgets[id], box = widget.box, bounds = widget.bounds || profile.bounds
  if (!box || !Number.isFinite(width) || !Number.isFinite(height)) return null
  return {
    width: Math.round(Math.max(box.minWidth, Math.min(box.maxWidth, bounds.x + bounds.width - position.x, width))),
    height: Math.round(Math.max(box.minHeight, Math.min(box.maxHeight, bounds.y + bounds.height - position.y, height)))
  }
}

export function clampPlacement(profile, id, x, y, layout = null) {
  if (!Number.isFinite(x) || !Number.isFinite(y) || !Object.hasOwn(profile.widgets, id)) return null
  const { minX, maxX, minY, maxY } = placementLimits(profile, id, layout)
  const position = { x: Math.max(minX, Math.min(maxX, Math.round(x))), y: Math.max(minY, Math.min(maxY, Math.round(y))) }
  return overlapsReserved(profile, id, position.x, position.y, layout) ? null : position
}

export function overlapsReserved(profile, id, x, y, layout = null) {
  const widget = profile.widgets[id]
  const owner = Object.keys(profile.widgets).find(key => profile.widgets[key].confirmation)
  const positions = layout || Object.fromEntries(Object.entries(profile.widgets).map(([key, item]) => [key, item.default]))
  if (owner && positions[owner]?.enabled && positions.speed_limit_actions?.enabled) {
    const area = profile.widgets[owner].confirmation, at = positions[owner]
    const critical = owner === "speed_limit" ? ["max_speed", "torque_bar"] : ["torque_bar"]
    const overlaps = (a, b) => a.x < b.x + b.width && a.x + a.width > b.x && a.y < b.y + b.height && a.y + a.height > b.y
    const panel = { x: (id === owner ? x : at.x) + area.x, y: (id === owner ? y : at.y) + area.y, width: area.width, height: area.height }
    const ownerHeight = profile.widgets[owner].height
    if (ownerHeight > area.y + area.height) {
      panel.x = id === owner ? x : at.x
      panel.width = Math.max(area.x + area.width, profile.widgets[owner].width)
      panel.height = ownerHeight - area.y
    }
    if (critical.some(key => {
      if ((id !== owner && id !== key) || !positions[key]?.enabled) return false
      const [width, height] = widgetSize(profile.widgets[key], positions[key])
      const obstacle = profile.widgets[key].confirmationObstacle || { x: 0, y: 0, width, height }
      return overlaps(panel, { x: (id === key ? x : positions[key].x) + obstacle.x,
        y: (id === key ? y : positions[key].y) + obstacle.y, width: obstacle.width, height: obstacle.height })
    })) return true
  }
  if (widget.layer === "underlay") return false
  const [width, height] = widgetSize(widget, layout?.[id])
  const intersects = (left, top, otherWidth, otherHeight) => x < left + otherWidth && x + width > left &&
    y < top + otherHeight && y + height > top
  if (profile.reservedZones.some((zone) => intersects(zone.x, zone.y, zone.width, zone.height))) return true
  const actions = profile.widgets.speed_limit_actions
  if (!actions || profile.protectedWidget !== "speed_limit_actions") return false
  if (id === "speed_limit_actions") return Object.entries(profile.widgets).some(([key, other]) => {
    const at = positions[key]
    return key !== id && key !== "speed_limit" && key !== "stopped_timer" && other.layer !== "underlay" && at &&
      at.enabled && Number.isFinite(at.x) && Number.isFinite(at.y) &&
      intersects(at.x, at.y, ...widgetSize(other, at))
  })
  if (id === "speed_limit" || id === "stopped_timer") return false
  const at = positions.speed_limit_actions
  if (!at || !Number.isFinite(at.x) || !Number.isFinite(at.y)) return true
  return intersects(at.x, at.y, actions.width, actions.height)
}

export function previewPoint(event, rect, profile) {
  if (!rect?.width || !rect?.height) return null
  return {
    x: (event.clientX - rect.left) * profile.width / rect.width,
    y: (event.clientY - rect.top) * profile.height / rect.height
  }
}

// Legacy saved layouts retain this field even though the paired speed widget uses fixed alignment.
export const SPEED_ALIGNMENTS = ["left", "center", "right"]
// Android Auto display switches, independent of the comma screen's preferences.
export const PROJECTION_DISPLAY_SWITCHES = ["blindSpotBorder", "signalBorder", "blindSpotCamera", "hidePersonalityAlerts", "wheelPedalFeedback", "leadIndicator"]

export function validDocument(document, metadata) {
  if (!object(document)) return false
  const profiles = metadata?.projection === true ? ["large"] : PROFILES
  if (!keysEqual(document, ["version", "palette", "layouts", "widgetColors", "roadColors", ...(Object.hasOwn(document, "widgetOrder") ? ["widgetOrder"] : []), ...(Object.hasOwn(document, "speedSources") ? ["speedSources"] : []), ...(Object.hasOwn(document, "speedSourcesCard") ? ["speedSourcesCard"] : []), ...(Object.hasOwn(document, "speedAlign") ? ["speedAlign"] : []), ...(Object.hasOwn(document, "clock24Hour") ? ["clock24Hour"] : []), ...(metadata?.projection !== true && Object.hasOwn(document, "reduceMotion") ? ["reduceMotion"] : []), ...(Object.hasOwn(document, "showMaxSpeed") ? ["showMaxSpeed"] : []), ...(metadata?.projection !== true && Object.hasOwn(document, "display") ? ["display"] : []), ...(metadata?.projection === true && Object.hasOwn(document, "largeUiGammaTrial") ? ["largeUiGammaTrial"] : []), ...(metadata?.projection === true ? PROJECTION_DISPLAY_SWITCHES.filter(key => Object.hasOwn(document, key)) : []), ...(metadata?.projection === true && Object.hasOwn(document, "leadInfo") ? ["leadInfo"] : [])]) || (metadata?.projection === true ? document.version !== 8 : ![11, 12, 13, 14].includes(document.version)) ||
    (Object.hasOwn(document, "speedSources") && typeof document.speedSources !== "boolean") ||
    (Object.hasOwn(document, "speedSourcesCard") && typeof document.speedSourcesCard !== "boolean") ||
    (Object.hasOwn(document, "speedAlign") && !SPEED_ALIGNMENTS.includes(document.speedAlign)) ||
    (Object.hasOwn(document, "clock24Hour") && typeof document.clock24Hour !== "boolean") ||
    (Object.hasOwn(document, "reduceMotion") && typeof document.reduceMotion !== "boolean") ||
    (Object.hasOwn(document, "showMaxSpeed") && typeof document.showMaxSpeed !== "boolean") ||
    (Object.hasOwn(document, "display") && (!object(document.display) || !Object.values(document.display).every((value) => typeof value === "string"))) ||
    (Object.hasOwn(document, "largeUiGammaTrial") && typeof document.largeUiGammaTrial !== "boolean") ||
    PROJECTION_DISPLAY_SWITCHES.some(key => Object.hasOwn(document, key) && typeof document[key] !== "boolean") ||
    (Object.hasOwn(document, "leadInfo") && !LEAD_INFO.includes(document.leadInfo)) ||
    !keysEqual(document.palette, COLORS) || !COLORS.every((key) => HEX.test(document.palette[key])) ||
    !keysEqual(document.layouts, profiles) || !keysEqual(document.widgetColors, profiles) || !keysEqual(document.roadColors, profiles)) return false
  if (Object.hasOwn(document, "widgetOrder") && (!object(document.widgetOrder) ||
    !Object.entries(document.widgetOrder).every(([name, order]) => profiles.includes(name) && Array.isArray(order) &&
      order.length === Object.keys(metadata.profiles[name].widgets).length && new Set(order).size === order.length &&
      order.every(id => typeof id === "string" && Object.hasOwn(metadata.profiles[name].widgets, id)) &&
      frontmostOrder(order, metadata.profiles[name])))) return false
  return profiles.every((name) => {
    const profile = metadata.profiles[name], layout = document.layouts[name]
    const road = document.roadColors[name]
    if (!object(road) || !Object.entries(road).every(([key, value]) => key === "pathMode" ? PATH_MODES.includes(value) :
      ROAD_COLORS.includes(key) && typeof value === "string" && HEX.test(value))) return false
    const colors = document.widgetColors[name]
    if (!object(colors) || !Object.entries(colors).every(([id, overrides]) => {
      const fields = profile.widgets[id]?.colors
      return fields && Object.keys(fields).length > 0 && object(overrides) && Object.entries(overrides).every(([key, value]) =>
        Object.hasOwn(fields, key) && typeof value === "string" && HEX.test(value))
    })) return false
    return keysEqual(layout, Object.keys(profile.widgets)) && Object.entries(layout).every(([id, position]) => {
      const widget = profile.widgets[id], limits = placementLimits(profile, id, layout)
      const resizable = widget.resizable, box = widget.box, opacity = widget.opacity
      return keysEqual(position, ["x", "y", "enabled", ...(resizable ? ["size"] : []), ...(box ? ["width", "height"] : []),
        ...(opacity ? ["opacity"] : []), ...(widget.iconSize ? ["display"] : []), ...(widget.backgroundToggle ? ["background"] : []), ...(widget.collapseToggle ? ["collapse"] : []), ...(widget.sizes ? ["size"] : [])]) &&
        (!widget.iconSize || ["words", "icons"].includes(position.display)) &&
        (!widget.backgroundToggle || typeof position.background === "boolean") &&
        (!widget.collapseToggle || typeof position.collapse === "boolean") &&
        (!widget.sizes || Object.hasOwn(widget.sizes, position.size)) &&
        (!opacity || (Number.isInteger(position.opacity) && position.opacity >= opacity.min && position.opacity <= opacity.max)) &&
        (!resizable || (Number.isInteger(position.size) && position.size >= resizable.min && position.size <= resizable.max)) &&
        (!box || (Number.isInteger(position.width) && position.width >= box.minWidth && position.width <= box.maxWidth &&
          Number.isInteger(position.height) && position.height >= box.minHeight && position.height <= box.maxHeight)) &&
        typeof position.enabled === "boolean" && (!widget.required || position.enabled === true) &&
        Number.isFinite(position.x) && Number.isFinite(position.y) &&
        position.x >= limits.minX && position.x <= limits.maxX && position.y >= limits.minY && position.y <= limits.maxY &&
        (!position.enabled || !overlapsReserved(profile, id, position.x, position.y, layout))
    })
  })
}

export function validSnapshot(data) {
  const projection = data?.projection === true && data?.metadata?.projection === true
  if ((data?.projection === true) !== (data?.metadata?.projection === true)) return false
  const profiles = projection ? ["large"] : PROFILES
  if (!data || typeof data.revision !== "string" || !data.revision || typeof data.editable !== "boolean" ||
    typeof data.valid !== "boolean" || !keysEqual(data.metadata?.profiles, profiles) ||
    !Array.isArray(data.metadata.paletteFields) || data.metadata.paletteFields.length !== COLORS.length ||
    new Set(data.metadata.paletteFields.map((field) => field.id)).size !== COLORS.length ||
    !data.metadata.paletteFields.every((field) => COLORS.includes(field.id) && typeof field.label === "string" && HEX.test(field.default)) ||
    !Array.isArray(data.metadata.roadColorFields) || data.metadata.roadColorFields.length !== ROAD_COLORS.length ||
    new Set(data.metadata.roadColorFields.map((field) => field.id)).size !== ROAD_COLORS.length ||
    !data.metadata.roadColorFields.every((field) => ROAD_COLORS.includes(field.id) && typeof field.label === "string" && HEX.test(field.default)) ||
    (data.activeProfile != null && !profiles.includes(data.activeProfile))) return false
  for (const profile of Object.values(data.metadata.profiles)) {
    if (profile.widgetOrder != null && (!Array.isArray(profile.widgetOrder) ||
      profile.widgetOrder.length !== Object.keys(profile.widgets || {}).length ||
      new Set(profile.widgetOrder).size !== profile.widgetOrder.length ||
      !profile.widgetOrder.every(id => typeof id === "string" && Object.hasOwn(profile.widgets, id)) ||
      !frontmostOrder(profile.widgetOrder, profile))) return false
    if (profile.runtimePreviewWidgets != null && (!Array.isArray(profile.runtimePreviewWidgets) ||
      new Set(profile.runtimePreviewWidgets).size !== profile.runtimePreviewWidgets.length ||
      !profile.runtimePreviewWidgets.every(id => typeof id === "string" && Object.hasOwn(profile.widgets, id)))) return false
    if (profile.quickSelectShelves != null && (!object(profile.bounds) || !Array.isArray(profile.quickSelectShelves) || profile.quickSelectShelves.length !== 2 ||
      profile.quickSelectShelves.some(zone => !object(zone) || typeof zone.id !== 'string' || typeof zone.label !== 'string' ||
        !Number.isFinite(zone.x) || !Number.isFinite(zone.y) || !positive(zone.width) || !positive(zone.height) ||
        zone.x < profile.bounds.x || zone.y < profile.bounds.y || zone.x + zone.width > profile.bounds.x + profile.bounds.width ||
        zone.y + zone.height > profile.bounds.y + profile.bounds.height))) return false
    const bounds = profile.bounds
    if (projection && (profile.widgets?.car_exit?.kind !== "car_exit" || profile.widgets.car_exit.required !== true)) return false
    if (profile.protectedWidget != null && (profile.protectedWidget !== "speed_limit_actions" ||
      profile.widgets?.[profile.protectedWidget]?.kind !== "speed_limit_actions")) return false
    if (!positive(profile.width) || !positive(profile.height) || typeof profile.label !== "string" ||
      !bounds || !Number.isFinite(bounds.x) || !Number.isFinite(bounds.y) || bounds.x < 0 || bounds.y < 0 ||
      !positive(bounds.width) || !positive(bounds.height) || bounds.x + bounds.width > profile.width ||
      bounds.y + bounds.height > profile.height || !profile.widgets || Array.isArray(profile.widgets) ||
      Object.keys(profile.widgets).length > 32 || !Array.isArray(profile.reservedZones) || profile.reservedZones.length > 32) return false
    for (const zone of profile.reservedZones) {
      if (typeof zone.label !== "string" || !Number.isFinite(zone.x) || !Number.isFinite(zone.y) ||
        !positive(zone.width) || !positive(zone.height) || zone.x < bounds.x || zone.y < bounds.y ||
        zone.x + zone.width > bounds.x + bounds.width || zone.y + zone.height > bounds.y + bounds.height) return false
    }
    for (const widget of Object.values(profile.widgets)) {
      if (widget.hidden != null && (widget.kind !== "rear_fog" || typeof widget.hidden !== "boolean")) return false
      for (const area of [widget.confirmation, widget.confirmationObstacle]) {
        if (area != null && (!keysEqual(area, ["x", "y", "width", "height"]) ||
          !Number.isFinite(area.x) || Math.abs(area.x) > profile.width ||
          !Number.isFinite(area.y) || Math.abs(area.y) > profile.height ||
          !positive(area.width) || !positive(area.height))) return false
      }
      if (widget.available != null && (!projection || widget.kind !== "jetlink_status" || typeof widget.available !== "boolean")) return false
      const area = widget.bounds || bounds
      if (widget.visualInsetTop != null && (!Number.isFinite(widget.visualInsetTop) || widget.visualInsetTop < 0 || widget.visualInsetTop > area.height)) return false
      if (!object(widget.colors) || !Object.entries(widget.colors).every(([key, value]) =>
        COLORS.includes(key) && (value === null || (typeof value === "string" && HEX.test(value))))) return false
      if (!Number.isFinite(area.x) || !Number.isFinite(area.y) || area.x < (widget.expandable ? -300 : 0) || area.y < 0 ||
        !positive(area.width) || !positive(area.height) || area.x + area.width > profile.width ||
        area.y + area.height > profile.height || typeof widget.label !== "string" || typeof widget.kind !== "string" ||
        !positive(widget.width) || !positive(widget.height) || widget.width > area.width || widget.height > area.height) return false
      if (widget.box && (widget.kind !== "nav_map" || !object(widget.opacity) ||
        ![widget.box.minWidth, widget.box.maxWidth, widget.box.minHeight, widget.box.maxHeight].every(Number.isInteger) ||
        widget.box.minWidth < 24 || widget.box.minHeight < 24 || widget.box.minWidth > widget.box.maxWidth ||
        widget.box.minHeight > widget.box.maxHeight || widget.box.maxWidth > area.width || widget.box.maxHeight > area.height ||
        ![widget.opacity.min, widget.opacity.max, widget.opacity.default].every(Number.isInteger) ||
        widget.opacity.min < 0 || widget.opacity.max > 100 || widget.opacity.min > widget.opacity.default ||
        widget.opacity.default > widget.opacity.max)) return false
      if (widget.opacity && (!(["nav_map", "jetlink_status"].includes(widget.kind) || projection && ["nav_home", "nav_work", "bookmark", "car_exit", "cruise_limits", "now_playing"].includes(widget.kind)) ||
        !object(widget.opacity) || ![widget.opacity.min, widget.opacity.max, widget.opacity.default].every(Number.isInteger) ||
        widget.opacity.min < 0 || widget.opacity.max > 100 || widget.opacity.min > widget.opacity.default ||
        widget.opacity.default > widget.opacity.max)) return false
      if (widget.collapseToggle != null && (!projection || widget.kind !== "now_playing" || widget.collapseToggle !== true)) return false
      if (widget.sizes != null && (!projection || widget.kind !== "now_playing" || !object(widget.sizes) ||
        !Object.values(widget.sizes).every(size => Array.isArray(size) && size.length === 2 && size.every(positive) &&
          size[0] <= area.width && size[1] <= area.height))) return false
      if (widget.backgroundToggle != null && (!projection || !["nav_home", "nav_work", "bookmark", "car_exit"].includes(widget.kind) ||
        widget.backgroundToggle !== true || !widget.opacity)) return false
      if (widget.resizable && (!["steering_wheel", "driver_monitor"].includes(widget.kind) || !Number.isInteger(widget.resizable.min) ||
        !Number.isInteger(widget.resizable.default) || !Number.isInteger(widget.resizable.max) ||
        widget.resizable.default !== widget.width || widget.width !== widget.height ||
        widget.resizable.min < 24 || widget.resizable.max > area.width || widget.resizable.max > area.height ||
        widget.resizable.min > widget.resizable.default || widget.resizable.default > widget.resizable.max)) return false
      if (widget.iconSize != null && (!projection || !["nav_home", "nav_work", "bookmark"].includes(widget.kind) ||
        !Number.isInteger(widget.iconSize) || widget.iconSize < 44 || widget.iconSize > Math.min(widget.width, widget.height))) return false
      if (widget.required != null && (!projection || widget.kind !== "car_exit" || widget.required !== true)) return false
      if (widget.frontmost != null && (!projection || widget.kind !== "car_exit" || widget.frontmost !== true)) return false
      if (widget.kind === "car_exit" && (widget.required !== true || widget.frontmost !== true)) return false
    }
  }
  return validDocument(data.document, data.metadata) && validDocument(data.defaults, data.metadata)
}
