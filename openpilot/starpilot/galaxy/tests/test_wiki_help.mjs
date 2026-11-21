import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { compile, createRenderer, nextTick } from '../web/vendor/vue/vue.esm-browser.js'
import { route } from '../web/js/route-state.js'
import { WikiHelp } from '../web/js/wiki-help.js'
import { wikiLink } from '../web/js/wiki-links.js'

assert.equal(wikiLink('/manage_models'), 'https://wiki.firestar.link/usage/model-manager/')
assert.equal(wikiLink('/navigation/maps'), 'https://wiki.firestar.link/exclusive/navigation/#offline-maps')
assert.equal(wikiLink('/settings', 'slc'), wikiLink('/driving/slc'))
assert.match(wikiLink('/settings', 'torque/developer'), /#lateral-tuning$/)
assert.match(wikiLink('/settings', 'conditional/conditions'), /#conditional-experimental-mode-recommended$/)
for (const path of ['/', '/tools', '/theme_maker', '/theme_maker/starport', '/appearance', '/logs', '/logs/help', '/networks', '/recordings', '/tuning/plots', '/driving/traffic']) {
  assert.equal(wikiLink(path), null, `${path} has no substantial feature guide`)
}
for (const page of ['visual', 'display', 'vehicle', 'data', 'traffic', 'unknown']) {
  assert.equal(wikiLink('/settings', page), null, `${page} must not fall back to the Toggles overview`)
}
assert.equal(wikiLink('/navigation-other'), null, 'Do not match partial path segments')
assert.equal(wikiLink('/undocumented'), null)

const node = (type = '') => ({ type, props: {}, children: [] })
const renderer = createRenderer({
  createElement: node, createText: () => node(), createComment: () => node(),
  setText() {}, setElementText() {},
  patchProp: (item, key, _before, value) => { item.props[key] = value },
  insert: (item, parent) => { item.parent = parent; parent.children.push(item) },
  remove: item => { item.parent.children.splice(item.parent.children.indexOf(item), 1) },
  parentNode: item => item.parent, nextSibling: () => null,
})
route.path = '/manage_models'
const host = node('host')
const app = renderer.createApp(WikiHelp)
app.mount(host)
try {
  let link = host.children.find(item => item.type === 'a')
  assert.equal(link.props.href, wikiLink('/manage_models'))
  assert.equal(link.props.target, '_blank')
  assert.equal(link.props.rel, 'noopener noreferrer')
  assert.match(link.props['aria-label'], /wiki guide.*new tab/)
  route.path = '/navigation/maps'
  await nextTick()
  assert.equal(link.props.href, wikiLink('/navigation/maps'), 'Help follows route changes')
  route.path = '/undocumented'
  await nextTick()
  assert.equal(host.children.some(item => item.type === 'a'), false, 'Hide absent guides')
} finally { app.unmount() }

route.path = '/settings'
const nestedHost = node('host')
const nestedApp = renderer.createApp({
  components: { WikiHelp }, data: () => ({ page: 'slc' }), template: '<WikiHelp :page="page" />',
})
const settings = nestedApp.mount(nestedHost)
try {
  const link = nestedHost.children.find(item => item.type === 'a')
  assert.equal(link.props.href, wikiLink('/settings', 'slc'))
  settings.page = 'aol'
  await nextTick()
  assert.equal(link.props.href, wikiLink('/settings', 'aol'), 'Nested toggles update without URL changes')
  settings.page = 'display'
  await nextTick()
  assert.equal(nestedHost.children.some(item => item.type === 'a'), false, 'Overview-only toggles hide help')
} finally { nestedApp.unmount() }

// Compile the changed page templates to catch invalid nesting or Vue directives.
for (const name of ['models', 'settings', 'navigation', 'starport', 'spotify', 'bluetooth', 'cameras',
  'home', 'system-monitor', 'sentry', 'vasm', 'pip', 'galaxy', 'networks', 'tools', 'logs', 'software-status', 'driving',
  'longitudinal-curves', 'flm', 'record-history', 'onroad-layout-template', 'settings-recovery', 'diagnostic-tools']) {
  const source = readFileSync(new URL(`../web/js/${name}.js`, import.meta.url), 'utf8')
  const templates = [...source.matchAll(/(?:template:\s*|ONROAD_LAYOUT_TEMPLATE = )`([\s\S]*?)`/g)]
  assert.ok(templates.length, `${name} has a template`)
  for (const [, template] of templates) {
    compile(template, { decodeEntities: raw => raw, onError: error => { throw new Error(`${name}: ${error.message}`) } })
  }
}
console.log('Wiki help: mapping, nested settings, reactive navigation, accessibility and page templates passed')
