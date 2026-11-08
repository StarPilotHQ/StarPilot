import assert from "node:assert/strict"
import { access, readFile } from "node:fs/promises"
import { test } from "node:test"

const web = new URL("../web/", import.meta.url)
const read = (path) => readFile(new URL(path, web), "utf8")
const uncommented = (css) => css.replace(/\/\*[\s\S]*?\*\//g, "")

/** Top-level CSS blocks as { head, body }, with braces matched (so @media and @keyframes keep their insides). */
function blocks(css) {
  const out = []
  let depth = 0, start = 0, open = 0
  for (let i = 0; i < css.length; i++) {
    if (css[i] === "{" && depth++ === 0) open = i
    else if (css[i] === "}" && --depth === 0) {
      out.push({ head: css.slice(start, open).trim(), body: css.slice(open + 1, i) })
      start = i + 1
    }
  }
  return out
}

/** Split a selector list on commas that are not inside brackets, e.g. inside :has(a, b). */
function selectors(list) {
  const parts = []
  let depth = 0, start = 0
  for (let i = 0; i < list.length; i++) {
    if ("([".includes(list[i])) depth++
    else if (")]".includes(list[i])) depth--
    else if (list[i] === "," && depth === 0) { parts.push(list.slice(start, i).trim()); start = i + 1 }
  }
  return [...parts, list.slice(start).trim()]
}

test("the refined look and Deep field sky load after Galaxy's own styles and can be switched off in one place", async () => {
  const html = await read("index.html")
  assert.match(html, /<html lang="en" data-theme="dark" data-look="refined">/)
  const links = [...html.matchAll(/href="\.\/css\/([^"]+)"/g)].map((match) => match[1])
  assert.ok(links.indexOf("galaxy-look.css") > links.indexOf("galaxy.css"))
  assert.ok(links.indexOf("galaxy-look.css") > links.indexOf("menu.css"))
  assert.equal(links.indexOf("galaxy-sky.css"), links.indexOf("galaxy-look.css") + 1)
  for (const css of ["galaxy-look.css", "galaxy-sky.css"]) {
    for (const { head, body } of blocks(uncommented(await read(`css/${css}`)))) {
      const rules = head.startsWith("@media") ? blocks(body) : head.startsWith("@") ? [] : [{ head }]
      for (const rule of rules) {
        for (const part of selectors(rule.head)) {
          assert.match(part, /^(html\[data-look="refined"\]|#galaxy-bg|body:has\()/, `${css}: ${part}`)
        }
      }
    }
  }
})

test("Galaxy's purple, cyan and text colours stay as they are", async () => {
  const css = await read("css/galaxy-look.css")
  for (const token of ["--primary:", "--primary-hover:", "--secondary:", "--on-surface:", "--text-muted:", "--background:"]) {
    assert.ok(!css.includes(token), token)
  }
})

test("index.html keeps to Galaxy's content security policy (no inline scripts)", async () => {
  const html = await read("index.html")
  for (const script of html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/g)) {
    assert.match(script[1], /\bsrc="/)
    assert.equal(script[2].trim(), "")
  }
})

test("the Deep field sky is completely still", async () => {
  const html = await read("index.html")
  assert.match(html, /<div id="galaxy-bg" aria-hidden="true"><\/div>/)

  const body = uncommented(await read("css/galaxy-sky.css"))
  // Nothing moves and nothing is promoted: no animations, transitions, transforms or tall layers.
  assert.doesNotMatch(body, /@keyframes|transition|transform|will-change|animation-name|height:\s*\d{3,}%|inset:\s*-/)
  assert.deepEqual([...body.matchAll(/animation:\s*([^;]+);/g)].map((match) => match[1].trim()), ["none"])
  // Galaxy's twinkling stars hold still while the refined look is on.
  assert.match(body, /html\[data-look="refined"\] #galaxy-bg \.galaxy-hero \{ animation: none;/)

  const assets = [...body.matchAll(/url\("\.\.\/(assets\/space\/[^"]+)"\)/g)].map((match) => match[1])
  assert.deepEqual(assets.sort(), ["assets/space/stars-far.svg", "assets/space/stars-mid.svg", "assets/space/stars-near.svg"])
  for (const asset of assets) await access(new URL(asset, web))
})

test("text on the sky blurs the stars behind it without darkening, and the page fade-in lets go", async () => {
  const sky = blocks(uncommented(await read("css/galaxy-sky.css")))
  const clear = sky.find(({ body }) => /backdrop-filter/.test(body))
  for (const target of [".gx-settings__summary", ".gx-page-header", ".gx-home__hero", ".gx-settings-path", ".gx-home__section-title"]) {
    assert.ok(clear.head.includes(target), target)
  }
  assert.match(clear.head, /^html\[data-look="refined"\]:not\(\[data-theme="light"\]\) /)
  assert.match(clear.head, /:not\(\.gx-card, \.gx-card \*\)$/, "panels are left alone")
  assert.match(clear.body, /-webkit-backdrop-filter: blur\(\d+px\);\s*backdrop-filter: blur\(\d+px\);/)
  assert.doesNotMatch(clear.body, /background|box-shadow|opacity/, "no dark patch: only the blur")
  // Only sideways padding, cancelled by an equal negative margin, so no text moves.
  assert.match(clear.body, /margin-inline: -(\d+)px;\s*padding-inline: \1px;/)
  assert.doesNotMatch(clear.body, /margin(-block|-top|-bottom)?:|padding(-block|-top|-bottom)?:/)
  // A held (filled) fade-in would cut the page off from the sky, so the blur would see nothing.
  assert.match(sky.find(({ head }) => head.endsWith(".gx-app")).body, /animation-fill-mode: backwards;/)
})
