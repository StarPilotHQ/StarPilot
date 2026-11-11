const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright')
const { execFileSync } = require('node:child_process')
const { readFileSync, mkdirSync } = require('node:fs')
const path = require('node:path')
const assert = require('node:assert/strict')

const web = path.resolve(__dirname, '../../web')
const fixtures = JSON.parse(execFileSync(process.env.PYTHON || 'python3', ['-c', `
import json
from openpilot.starpilot.ui.onroad_customization import default_document, customization_metadata
from openpilot.starpilot.system.starport.projection_layout import default_layout_for_viewport, layout_metadata_for_viewport
doc=default_document()
for layout in doc['layouts'].values():
  for widget in layout.values(): widget['enabled']=True
native=dict(document=doc,defaults=doc,metadata=customization_metadata(),revision='scenes',editable=True,valid=True,activeProfile='large',supportedProfiles=['large','compact'])
layout=default_layout_for_viewport((2880,1080))
for widget in layout['widgets'].values(): widget['enabled']=True
projection=dict(version=1,document=layout,defaults=layout,metadata=layout_metadata_for_viewport((2880,1080)),screen=dict(width=1280,height=720,margin_width=0,margin_height=240),revision='aa-scenes',editable=True,valid=True,colors=dict(palette=doc['palette'],widgetColors=doc['widgetColors']['large'],roadColors={}))
print(json.dumps(dict(native=native,projection=projection)))
`], { encoding: 'utf8' }))

;(async () => {
  const browser = await chromium.launch({ headless: true, ...(process.env.PLAYWRIGHT_EXECUTABLE_PATH ? { executablePath: process.env.PLAYWRIGHT_EXECUTABLE_PATH } : {}) })
  const output = process.env.SCENE_SCREENSHOTS
  const nativeArt = process.env.NATIVE_SCENE_ART
  if (output) mkdirSync(output, { recursive: true })
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 1000 } })
    const errors = []
    page.on('pageerror', error => errors.push(error.message))
    await page.route('http://scenes.test/**', async route => {
      const pathname = new URL(route.request().url()).pathname
      if (pathname === '/api/ui/layout/preview') {
        if (!nativeArt) return route.fulfill({ status: 503, json: { error: 'Scene sketch test' } })
        const { profile, scene, art } = route.request().postDataJSON()
        return route.fulfill({ contentType: 'image/png', body: readFileSync(path.join(nativeArt, `${profile}-${scene}-${art === true ? 'art' : 'backdrop'}.png`)) })
      }
      if (pathname.startsWith('/api/')) return route.fulfill({ json: pathname === '/api/starport/layout' ? fixtures.projection : fixtures.native })
      if (pathname === '/') return route.fulfill({ contentType: 'text/html', body: `
        <meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/css/galaxy.css"><link rel="stylesheet" href="/css/setup.css"><link rel="stylesheet" href="/css/settings.css">
        <main class="gx-content"><div id="app"></div></main><script type="module">
        import {createApp} from '/vendor/vue/vue.esm-browser.js'; import {OnroadLayoutPage} from '/js/onroad-layout.js';
        window.editor=createApp(OnroadLayoutPage,{mode:'local',projection:location.search.includes('projection'),unauthorized(){}}).mount('#app');</script>` })
      return route.fulfill({ body: readFileSync(path.join(web, pathname)), contentType: pathname.endsWith('.js') ? 'text/javascript' : pathname.endsWith('.css') ? 'text/css' : 'application/octet-stream' })
    })
    for (const target of ['large', 'compact', 'projection']) {
      await page.goto('http://scenes.test/' + (target === 'projection' ? '?projection' : ''))
      await page.locator('.gx-layout__layer').first().waitFor()
      if (target === 'compact') await page.getByRole('button', { name: /^Small/ }).click()
      const scenes = await page.evaluate(() => window.editor.availableScenes)
      const original = await page.evaluate(() => JSON.stringify(window.editor.state.draft))
      for (const scene of scenes) {
        // Choose scenes through the real accessible dropdown.
        await page.getByRole('combobox', { name: 'Preview scene', exact: true }).click()
        await page.getByRole('option', { name: scene.label, exact: true }).click()
        // After the first render, a scene switch keeps native artwork up instead of falling back to outlines.
        if (nativeArt && scene !== scenes[0]) assert.equal(await page.locator('[data-preview-source="placeholder"]').count(), 0, `${target}/${scene.id}: no outlines while loading`)
        if (nativeArt) await page.waitForFunction(() => window.editor.state.backdrop.scene === window.editor.state.scene &&
          window.editor.state.backdrop.status === 'ready' && window.editor.state.art.scene === window.editor.state.scene && window.editor.state.art.status === 'ready')
        const state = await page.evaluate(() => ({ scene: window.editor.state.scene, border: window.editor.sceneBorder,
          visible: window.editor.renderWidgets.map(widget => widget.id), draft: JSON.stringify(window.editor.state.draft),
          general: window.editor.layerWidgets.map(widget => widget.id), hidden: window.editor.layerWidgets.filter(widget => window.editor.sceneHidden(widget.id)).map(widget => widget.id) }))
        assert.equal(state.scene, scene.id)
        assert.equal(state.draft, original, `${target}/${scene.id}: scene changes never edit the saved layout`)
        if (nativeArt) assert.equal(await page.locator('[data-preview-source="device-backdrop"]').count(), 1)
        else assert.equal(await page.locator('[data-preview-source="scene-border"]').getAttribute('stroke'), state.border)
        if (scene.id === 'general') assert.deepEqual([...state.visible].sort(), [...state.general].sort())
        for (const id of state.hidden) {
          assert(!state.visible.includes(id), `${target}/${scene.id}/${id}: hidden widgets have no hit box`)
          const name = await page.evaluate(id => window.editor.profile.widgets[id].label, id)
          assert.equal(await page.getByRole('button', { name: `${name} is hidden in this scene. Choose General edit to arrange it.`, exact: true }).isDisabled(), true)
        }
        if (target !== 'large') assert.equal(state.visible.includes('stopped_timer'), ['general', 'stopped'].includes(scene.id))
        if (scene.id !== 'general') {
          assert.equal(state.visible.includes('speed_limit_actions'), false)
        }
        if (output) await page.locator('svg.gx-layout__preview').screenshot({ path: path.join(output, `${target}-${scene.id}.png`) })
      }
      await page.evaluate(() => { window.editor.state.scene = 'general'; window.editor.togglePreviewVisibility('driver_monitor'); window.editor.state.scene = 'engaged' })
      assert.equal(await page.evaluate(() => window.editor.state.hiddenPreviewWidgets[window.editor.state.profile].driver_monitor), true)
      assert.equal(await page.evaluate(() => window.editor.sceneHidden('driver_monitor') || !window.editor.previewHidden('driver_monitor')), true)
      for (const width of [390, 1280]) {
        await page.setViewportSize({ width, height: 900 })
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true)
      }
    }
    assert.deepEqual(errors, [])
    console.log('All three layout scene menus, border colors, automatic eyes, timer/CEM/actions, scene isolation, draft preservation and mobile sizing passed.')
  } finally { await browser.close() }
})().catch(error => { console.error(error); process.exit(1) })
