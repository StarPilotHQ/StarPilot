// Only map pages with substantial, relevant guidance. Omit overview-only destinations.
// Paths are relative to the wiki root; unmapped pages hide the help link.
const settings = 'usage/settings/'
const galaxy = 'usage/galaxy/'
const cameras = 'exclusive/cameras-and-monitoring/'
const starport = 'exclusive/starport/'

export const WIKI_LINKS = Object.freeze({
  '/settings': settings,
  '/manage_models': 'usage/model-manager/',
  '/model_laboratory': 'usage/model-manager/#model-pair',
  '/navigation': 'exclusive/navigation/',
  '/navigation/maps': 'exclusive/navigation/#offline-maps',
  '/navigation/setup': 'exclusive/navigation/#set-up',
  '/cameras': cameras,
  '/cameras/pip': cameras + '#pip-blind-spot-camera-preview',
  '/cameras/vasm': cameras + '#v-asm-adjacent-spot-monitoring',
  '/cameras/events': 'exclusive/sentry-mode/',
  '/cameras/sentry-settings': 'exclusive/sentry-mode/#settings',
  '/bluetooth': 'exclusive/bluetooth-controls/',
  '/starport': starport,
  '/starport/audio': starport + '#other-phones-and-audio',
  '/spotify': starport + '#set-up-spotify-connect',
  '/galaxy': galaxy + '#access-galaxy',
  '/system': 'software/starpilot/#changing-branches',
  '/logs/starport': starport + '#connection-logs',
  '/driving': settings + '#driving-controls',
  '/tuning': settings + '#lateral-tuning',
  '/tuning/flm': settings + '#lateral-tuning',
})

// Settings navigate internally without changing the URL. These keys follow the API's page IDs.
export const SETTINGS_WIKI_LINKS = Object.freeze({
  hub: settings,
  lateral: settings + '#steering',
  longitudinal: settings + '#driving-controls',
  personalities: settings + '#customize-driving-personalities',
  starport_options: starport,
  aol: settings + '#always-on-lateral',
  conditional: settings + '#conditional-experimental-mode-recommended',
  curve: 'usage/operation/#curve-speed-controller',
  slc: 'exclusive/speed-limit-controller/#vision-speed-limits',
  torque: settings + '#lateral-tuning',
  profiles: settings + '#customize-driving-personalities',
  aggressive: settings + '#customize-driving-personalities',
  standard: settings + '#customize-driving-personalities',
  relaxed: settings + '#customize-driving-personalities',
  lane: 'exclusive/lane-centering/',
  lane_change: settings + '#lane-changes',
  wheel: settings + '#wheel-controls',
  sounds: settings + '#alerts-and-sounds',
  sentry: 'exclusive/sentry-mode/#settings',
  pip: cameras + '#pip-blind-spot-camera-preview',
})

function lookup(links, key) {
  // Inherit only at slash boundaries, so /navigation/setup matches /navigation, not /nav.
  while (key) {
    if (Object.hasOwn(links, key)) return links[key]
    key = key.slice(0, key.lastIndexOf('/') < 0 ? 0 : key.lastIndexOf('/'))
  }
  return undefined
}

export function wikiLink(path, page = '') {
  const settingsPage = page || (path.startsWith('/driving/') ? path.slice('/driving/'.length) : '')
  // A settings page must have its own guide; never fall back to the generic Toggles guide.
  const target = settingsPage ? lookup(SETTINGS_WIKI_LINKS, settingsPage) : WIKI_LINKS[path]
  return target === undefined ? null : 'https://wiki.firestar.link/' + target
}
