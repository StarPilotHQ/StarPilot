// Translate the independent AA placement document into the existing editor model.
const clone = (value) => JSON.parse(JSON.stringify(value))
// Text above the Android Auto lead marker, matching projection_layout.LEAD_INFO_CHOICES.
export const LEAD_INFO = ["Off", "Distance", "Speed"]

// projection_onroad draws the fixed brand at (width - 210, height - 90),
// using Sora-800.fnt at 30 * Profile.LARGE.font_scale. Reuse that atlas and
// its glyph metrics so the browser needs no substitute font or native request.
const brandGlyphs = {
  S: [331, 6, 131, 155, 43], t: [6, 175, 87, 142, 54], a: [474, 6, 120, 117, 80],
  r: [913, 6, 85, 112, 82], P: [184, 6, 135, 148, 46], i: [606, 6, 69, 157, 38],
  l: [687, 6, 65, 147, 47], o: [764, 6, 137, 119, 79],
}
export const PROJECTION_WORDMARK = {
  right: 210, bottom: 90, scale: 30 * 1.242 / 200,
  glyphs: [..."StarPilot"].map((letter, index, letters) => {
    const [x, y, width, height, offsetY] = brandGlyphs[letter]
    const offsetX = letters.slice(0, index).reduce((sum, char) => sum + brandGlyphs[char][2], 0)
    return { x, y, width, height, offsetX, offsetY }
  }),
}

export function editorSnapshot(raw) {
  if (!raw || raw.version !== 1 || !raw.screen || !raw.metadata || !raw.document || !raw.defaults)
    throw new Error(raw?.reason || "Connect Android Auto once to obtain the actual screen.")
  const colors = raw.colors
  const document = (value) => {
    if (![4, 5, 6].includes(value.version) || value.canvas?.width !== raw.metadata.width || value.canvas?.height !== raw.metadata.height)
      throw new Error("The saved StarPort layout does not match its screen. Reload before editing.")
    return { version: 8, clock24Hour: value.clock24Hour === true, largeUiGammaTrial: value.largeUiGammaTrial === true, blindSpotBorder: value.blindSpotBorder === true, signalBorder: value.signalBorder === true, blindSpotCamera: value.blindSpotCamera === true, hidePersonalityAlerts: value.hidePersonalityAlerts === true, wheelPedalFeedback: value.wheelPedalFeedback === true, leadIndicator: value.leadIndicator === true, leadInfo: LEAD_INFO.includes(value.leadInfo) ? value.leadInfo : "Off", speedSources: value.speedSources === true, speedSourcesCard: value.speedSourcesCard === true, speedAlign: ["center", "right"].includes(value.speedAlign) ? value.speedAlign : "left", ...(value.widgetOrder ? { widgetOrder: { large: clone(value.widgetOrder) } } : {}), palette: clone(colors.palette), layouts: { large: clone(value.widgets) },
      widgetColors: { large: clone(colors.widgetColors) }, roadColors: { large: clone(colors.roadColors) } }
  }
  return { ...raw, projection: true, activeProfile: "large", document: document(raw.document), defaults: document(raw.defaults),
    metadata: { projection: true, profiles: { large: clone(raw.metadata) },
      paletteFields: Object.entries(colors.palette).map(([id, value]) => ({ id, label: id, default: value })),
      roadColorFields: [{ id: "path", label: "Path", default: "#30FF9CFF" },
        { id: "pathEdge", label: "Path edges", default: "#00FF40FF" }, { id: "laneLines", label: "Lane lines", default: "#FFFFFFFF" }] } }
}

export function projectionPayload(body, metadata) {
  return { revision: body.revision, document: { version: 6,
    canvas: { width: metadata.profiles.large.width, height: metadata.profiles.large.height },
    clock24Hour: body.document.clock24Hour === true,
    largeUiGammaTrial: body.document.largeUiGammaTrial === true,
    blindSpotBorder: body.document.blindSpotBorder === true,
    signalBorder: body.document.signalBorder === true,
    blindSpotCamera: body.document.blindSpotCamera === true,
    hidePersonalityAlerts: body.document.hidePersonalityAlerts === true,
    wheelPedalFeedback: body.document.wheelPedalFeedback === true,
    leadIndicator: body.document.leadIndicator === true,
    leadInfo: LEAD_INFO.includes(body.document.leadInfo) ? body.document.leadInfo : "Off",
    speedSources: body.document.speedSources === true,
    speedSourcesCard: body.document.speedSourcesCard === true,
    speedAlign: ["center", "right"].includes(body.document.speedAlign) ? body.document.speedAlign : "left",
    ...(body.document.widgetOrder?.large ? { widgetOrder: clone(body.document.widgetOrder.large) } : {}),
    widgets: clone(body.document.layouts.large) } }
}
