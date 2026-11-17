// Destination coordinates take precedence over Google's viewport (@lat,lng).
const GEOHASH = '0123456789bcdefghjkmnpqrstuvwxyz'
const WAZE_SHORT = /https:\/\/(?:www\.)?waze\.com\/ul\/h([0-9b-hjkmnp-z]{4,12})(?![0-9a-z])/i

function wazeGeohash(code) {
  const latitude = [-90, 90], longitude = [-180, 180]
  let even = true
  for (const char of code.toLowerCase()) {
    const bits = GEOHASH.indexOf(char)
    for (let shift = 4; shift >= 0; shift--) {
      const span = even ? longitude : latitude
      span[bits >> shift & 1 ? 0 : 1] = (span[0] + span[1]) / 2
      even = !even
    }
  }
  return [(latitude[0] + latitude[1]) / 2, (longitude[0] + longitude[1]) / 2].map((value) => Math.round(value * 1e6) / 1e6)
}

export function parseSharedDestination(input) {
  let text = String(input || '').slice(0, 8192).trim()
  try { text = decodeURIComponent(text.replace(/\+/g, ' ')) } catch { /* Keep malformed shares searchable. */ }
  const waze = text.match(WAZE_SHORT)
  if (waze) { const [latitude, longitude] = wazeGeohash(waze[1]); return { destination: { name: 'Shared destination', latitude, longitude } } }
  const number = '(-?\\d+(?:\\.\\d+)?)'
  const patterns = [new RegExp(`!3d${number}!4d${number}`),
    new RegExp(`(?:[?&:](?:q|query|daddr|destination)=)${number},\\s*${number}`),
    new RegExp(`geo:${number},\\s*${number}`), new RegExp(`to=ll\\.${number},\\s*${number}`), new RegExp(`@${number},\\s*${number}`)]
  for (const pattern of patterns) {
    const match = text.match(pattern)
    if (!match) continue
    const latitude = Number(match[1]), longitude = Number(match[2])
    if (Math.abs(latitude) > 90 || Math.abs(longitude) > 180) continue
    // geo:0,0?q=an address is a search, not a destination in the ocean.
    if (match[0].startsWith('geo:0,0') && /[?&:]q=/.test(text)) continue
    const label = text.match(/\(([^()]{1,120})\)/)?.[1] || text.match(/\/place\/([^/]+)/)?.[1]
    return { destination: { name: label || 'Shared destination', latitude, longitude } }
  }
  const query = text.match(/[?&:](?:q|query|daddr|destination)=([^&#]+)/)?.[1] || text
  return { query: query.slice(0, 200) }
}

export const SHORT_MAP_LINK = /https:\/\/(?:maps\.app\.goo\.gl|goo\.gl\/maps|(?:[a-z0-9-]+\.)?waze\.com\/ul)/

// A pasted or shared map link opens its place; anything else is a search.
export function isMapLink(input) {
  const text = String(input || '')
  return SHORT_MAP_LINK.test(text) || Boolean(parseSharedDestination(text).destination)
}
