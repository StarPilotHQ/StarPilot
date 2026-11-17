import { requestJson } from "./startup.js"
import { ref } from "../vendor/vue/vue.esm-browser.js"

const language = ref(undefined)
const setDisplayLanguage = value => { language.value = value }
export const displayLocale = () => ({"zh-CHS":"zh-Hans","zh-CHT":"zh-Hant"}[language.value] || language.value)

// Translate presentation only: Vue's values, requests and technical identifiers stay in English.
const ATTRIBUTES = ["title", "aria-label", "placeholder", "alt"]
const PROTECTED = "script,style,code,pre,textarea,input,select[hidden],option:not([value]),[contenteditable],[translate=no]"

const compiled = new WeakMap()
const escapeRegex = text => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
const tokens = text => [...text.matchAll(/\{([^{}]*)\}/g)]

function templates(messages) {
  if (compiled.has(messages)) return compiled.get(messages)
  const result = []
  for (const [source, translated] of Object.entries(messages)) {
    const fields = tokens(source), target = tokens(translated)
    if (!fields.length || fields.length !== target.length) continue
    let offset = 0, pattern = "^"
    const keys = fields.map((field, index) => field[1].split(/[!:]/, 1)[0] || String(index))
    for (const field of fields) {
      pattern += escapeRegex(source.slice(offset, field.index)) + "([^\\n]*?)"
      offset = field.index + field[0].length
    }
    pattern += escapeRegex(source.slice(offset)) + "$"
    const literal = source.replace(/\{[^{}]*\}/g, "").toLowerCase()
    const stops = new Set(["to", "of", "in", "on", "at", "by", "for", "or", "and", "a", "the"])
    const generic = !(literal.match(/[a-z]+/g) || []).some(word => !stops.has(word))
    result.push({ generic, specificity: source.length - fields.reduce((sum, field) => sum + field[0].length, 0),
      prefix: source.slice(0, fields[0].index), pattern: new RegExp(pattern), keys, translated })
  }
  result.sort((a, b) => b.specificity - a.specificity || a.keys.length - b.keys.length)
  compiled.set(messages, result)
  return result
}

export function translateText(source, messages) {
  const text = source.trim()
  let translated = Object.hasOwn(messages, text) ? messages[text] : text
  if (translated === text && text.length <= 1024) {
    for (const template of templates(messages)) {
      if (!text.startsWith(template.prefix)) continue
      const matched = template.pattern.exec(text)
      if (!matched || template.generic && !matched.slice(1).some(value => /\d/.test(value))) continue
      const values = Object.fromEntries(template.keys.map((key, index) => [key, matched[index + 1]]))
      if (Object.hasOwn(values, "label") && Object.hasOwn(messages, values.label)) values.label = messages[values.label]
      let auto = 0
      translated = template.translated.replace(/\{([^{}]*)\}/g, (token, field) => {
        const key = field.split(/[!:]/, 1)[0] || String(auto++)
        return Object.hasOwn(values, key) ? values[key] : token
      })
      break
    }
  }
  if (translated === text && /\n|\s+·\s+/.test(text)) {
    translated = text.split(/(\n|\s+·\s+)/).map((part, index) => index % 2 === 0 ? translateText(part, messages) : part).join("")
  }
  return translated !== text ? source.replace(text, () => translated) : source
}

export function localizeDocument(root, { language, messages }, records = new WeakMap()) {
  function translate(node, key, source, write) {
    const saved = records.get(node) || {}
    const prior = saved[key]
    const original = prior && prior.output === source ? prior.source : source
    const output = translateText(original, messages)
    saved[key] = { source: original, output }
    records.set(node, saved)
    if (output !== source) write(output)
  }
  function visit(node) {
    if (node.nodeType === 3) {
      if (!node.parentElement?.closest(PROTECTED)) translate(node, "text", node.nodeValue, value => { node.nodeValue = value })
      return
    }
    if (node.nodeType !== 1 || node.matches("script,style,code,pre,[translate=no],[contenteditable]")) return
    for (const key of ATTRIBUTES) {
      if (node.hasAttribute(key)) translate(node, key, node.getAttribute(key), value => node.setAttribute(key, value))
    }
    if (!node.matches(PROTECTED)) for (const child of node.childNodes) visit(child)
  }
  visit(root)
  root.ownerDocument.documentElement.lang = language
  setDisplayLanguage(language)
  return records
}

export function startLocalization(root = document.body, fetcher = fetch) {
  let state = { language: "en", messages: {} }, stopped = false, timer, scheduled = false, generation = 0, request
  const records = new WeakMap()
  const dirty = new Set()
  const observer = new MutationObserver(mutations => {
    for (const mutation of mutations) dirty.add(mutation.target)
    if (scheduled) return
    scheduled = true
    queueMicrotask(() => {
      scheduled = false
      const changed = [...dirty].filter(node => node.isConnected)
      dirty.clear()
      if (!stopped) apply(changed)
    })
  })
  function apply(nodes = [root]) {
    observer.disconnect()
    for (const node of nodes) localizeDocument(node, state, records)
    observer.observe(root, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ATTRIBUTES })
  }
  async function refresh() {
    clearTimeout(timer)
    request?.abort()
    const current = ++generation
    request = new AbortController()
    try {
      const next = await requestJson(`./api/localization?language=${encodeURIComponent(state.language)}`,
        { fetcher, timeout: 4000, request: { signal: request.signal } })
      if (!stopped && current === generation && typeof next.language === "string" && next.messages && typeof next.messages === "object") {
        state = next
        apply()
      }
    } catch { /* Keep the last confirmed language during connection loss. */ }
    if (!stopped && current === generation) timer = setTimeout(refresh, 2000)
  }
  apply()
  globalThis.addEventListener?.("galaxy-language-changed", refresh)
  refresh()
  return () => {
    stopped = true; generation++; request?.abort(); clearTimeout(timer); observer.disconnect()
    globalThis.removeEventListener?.("galaxy-language-changed", refresh)
  }
}
