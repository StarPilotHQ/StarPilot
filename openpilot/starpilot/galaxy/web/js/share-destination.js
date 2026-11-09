// Destination coordinates take precedence over Google's viewport (@lat,lng).
export function parseSharedDestination(input) {
  let text = String(input || '').slice(0, 8192).trim()
  try { text = decodeURIComponent(text.replace(/\+/g, ' ')) } catch { /* Keep malformed shares searchable. */ }
  const number = '(-?\\d+(?:\\.\\d+)?)'
  const patterns = [new RegExp(`!3d${number}!4d${number}`),
    new RegExp(`(?:[?&:](?:q|query|daddr|destination)=)${number},\\s*${number}`),
    new RegExp(`geo:${number},\\s*${number}`), new RegExp(`@${number},\\s*${number}`)]
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

export const SHORT_MAP_LINK = /https:\/\/(?:maps\.app\.goo\.gl|goo\.gl\/maps)\//

// A pasted or shared map link opens its place; anything else is a search.
export function isMapLink(input) {
  const text = String(input || '')
  return SHORT_MAP_LINK.test(text) || Boolean(parseSharedDestination(text).destination)
}
