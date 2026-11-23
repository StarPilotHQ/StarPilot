import assert from "node:assert/strict"
import { translateText, localizeDocument, startLocalization, LANGUAGE_RECHECK_MS, LANGUAGE_RETRY_MS } from "../web/js/localization.js"

assert.equal(translateText("  Home\n", { Home: "ホーム" }), "  ホーム\n")
assert.equal(translateText("SpeedLimitController", {}), "SpeedLimitController")
assert.equal(translateText("Save", { Save: "$&<text>" }), "$&<text>", "translations remain literal text")

const doc = { documentElement: {} }
class Element {
  constructor(tag, attrs = {}, children = []) {
    this.nodeType = 1; this.tag = tag; this.attrs = { ...attrs }; this.childNodes = children; this.ownerDocument = doc
    for (const child of children) child.parentElement = this
  }
  matches(selector) {
    return selector.split(",").some(value => value === "select[hidden]" ? this.tag === "select" && this.hasAttribute("hidden") : value === "option:not([value])" ? this.tag === "option" && !this.hasAttribute("value") : value.startsWith("[") ?
      value === "[translate=no]" ? this.attrs.translate === "no" : Object.hasOwn(this.attrs, value.slice(1, -1)) : this.tag === value)
  }
  closest(selector) { return this.matches(selector) || this.parentElement?.closest(selector) }
  hasAttribute(key) { return Object.hasOwn(this.attrs, key) }
  getAttribute(key) { return this.attrs[key] }
  setAttribute(key, value) { this.attrs[key] = value }
}
const text = value => ({ nodeType: 3, nodeValue: value })
const label = text("Home"), dynamic = text("Save"), technical = text("Save")
const input = new Element("input", { placeholder: "Search", value: "Home" })
const root = new Element("main", {}, [new Element("button", { title: "Home" }, [label]),
  new Element("span", {}, [dynamic]), new Element("code", {}, [technical]), input,
  new Element("span", { translate: "no" }, [text("Save")])])
const records = new WeakMap()
localizeDocument(root, { language: "ja", messages: { Home: "ホーム", Save: "保存", Search: "検索" } }, records)
assert.equal(label.nodeValue, "ホーム")
assert.equal(input.attrs.placeholder, "検索")
assert.equal(input.attrs.value, "Home")
assert.equal(technical.nodeValue, "Save")
assert.equal(doc.documentElement.lang, "ja")
dynamic.nodeValue = "Home" // Vue changed the source while another language was active.
localizeDocument(root, { language: "fr", messages: { Home: "Accueil", Save: "Enregistrer", Search: "Rechercher" } }, records)
assert.equal(label.nodeValue, "Accueil")
assert.equal(dynamic.nodeValue, "Accueil")
localizeDocument(root, { language: "en", messages: {} }, records)
assert.equal(label.nodeValue, "Home")
assert.equal(dynamic.nodeValue, "Home")
assert.equal(input.attrs.placeholder, "Search")
assert.equal(root.childNodes[0].attrs.title, "Home")

const dynamicMessages = { "{} drives": "{} trayectos", "Saved {count} items for {name}": "Para {name}: {count} elementos guardados",
  "Preparing {} ({}%)": "Preparando {} ({}%)" }
assert.equal(translateText("3 drives", dynamicMessages), "3 trayectos")
assert.equal(translateText("Saved 12 items for Dom", dynamicMessages), "Para Dom: 12 elementos guardados")
assert.equal(translateText("Preparing Alpha (23%)", dynamicMessages), "Preparando Alpha (23%)")
assert.equal(translateText("Some unknown diagnostic", dynamicMessages), "Some unknown diagnostic")

assert.equal(translateText('Expires in 1 day', {'{} day':'{}일','Expires in {} day{}':'{}일{} 후 만료'}), '1일 후 만료')
assert.equal(translateText('1 minute Left', {'{} minute{} Left':'{}분{} 남음'}), '1분 남음')

const implicitText = text("Save"), explicitText = text("Save")
const selectRoot = new Element("select", {}, [new Element("option", {}, [implicitText]),
  new Element("option", {value:"saved-id"}, [explicitText])])
localizeDocument(selectRoot, {language:"ja",messages:{Save:"保存"}})
assert.equal(implicitText.nodeValue,"Save","implicit option values must remain unchanged")
assert.equal(explicitText.nodeValue,"保存")
assert.equal(selectRoot.childNodes[1].attrs.value,"saved-id")
assert.equal(translateText("Switch to light mode", {"{} to {}":"{}から{}へ"}), "Switch to light mode")

const hiddenText = text("Save")
const hiddenNative = new Element("select", {hidden:""}, [new Element("option", {value:"saved-id"}, [hiddenText])])
localizeDocument(hiddenNative, {language:"ja",messages:{Save:"保存"}})
assert.equal(hiddenText.nodeValue,"Save","GalaxySelect must retain canonical hidden option labels for sync()")
assert.equal(translateText('Reset Language to default', {Language:'Sprache','Reset {label} to default':'{label} zurücksetzen'}),'Sprache zurücksetzen')

{
  // Poll the language once a minute after a confirmed load; retry quickly only while it cannot be read.
  const delays = [], pending = [], listeners = {}
  const realSetTimeout = globalThis.setTimeout, realClearTimeout = globalThis.clearTimeout
  globalThis.MutationObserver = class { observe() {} disconnect() {} }
  globalThis.setTimeout = (fn, ms) => { if (ms === LANGUAGE_RETRY_MS || ms === LANGUAGE_RECHECK_MS) { delays.push(ms); pending.push(fn); return pending.length } return realSetTimeout(fn, ms) }
  globalThis.clearTimeout = id => { if (typeof id !== "number" || id > 1000) realClearTimeout(id) }
  const visibility = { visibilityState: "visible", addEventListener: (name, fn) => { listeners[name] = fn }, removeEventListener: name => { delete listeners[name] } }
  globalThis.document = visibility
  const responses = [true, true, false]
  let calls = 0
  const fetcher = async () => {
    calls++
    if (!responses.shift()) throw new Error("offline")
    return new Response(JSON.stringify({ language: "en", messages: {} }), { status: 200, headers: { "Content-Type": "application/json" } })
  }
  const root = new Element("main", {}, [text("Home")])
  const settle = () => new Promise(resolve => realSetTimeout(resolve, 20))
  const stop = startLocalization(root, fetcher)
  await settle()
  assert.deepEqual(delays, [LANGUAGE_RECHECK_MS], "a confirmed language is rechecked once a minute, not every 2 s")
  listeners.visibilitychange()
  await settle()
  assert.equal(calls, 2, "showing the tab again rechecks at once")
  assert.equal(delays.at(-1), LANGUAGE_RECHECK_MS)
  pending.at(-1)()
  await settle()
  assert.equal(calls, 3)
  assert.equal(delays.at(-1), LANGUAGE_RETRY_MS, "an unreadable language retries quickly")
  stop()
  assert.equal(listeners.visibilitychange, undefined)
  globalThis.setTimeout = realSetTimeout; globalThis.clearTimeout = realClearTimeout
  delete globalThis.document; delete globalThis.MutationObserver
}
