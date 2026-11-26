import { requestJson } from "./startup.js"

// Read the shared FeatureRow metadata. Unknown/older servers retain the default presentation.
export const settingLevel = row => row.levels?.length
  ? row.levels.includes("simple") ? "simple" : "advanced"
  : row.level === "advanced" ? "advanced" : "simple"
export const settingVisible = (row, advanced) => Array.isArray(row.levels)
  ? row.levels.some(level => level === "simple" || advanced)
  : settingLevel(row) === "simple" || advanced

export async function saveSettingsMode(mode) {
  const result = await requestJson('./api/settings/mode', { request: {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ mode }),
  } })
  if (!['simple', 'advanced'].includes(result?.mode)) throw new Error('Settings mode could not be confirmed')
  return result.mode
}
