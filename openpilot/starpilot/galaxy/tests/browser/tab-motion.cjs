const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright')
const assert = require('node:assert/strict')

;(async () => {
  const browser = await chromium.launch({ headless: true })
  const page = await browser.newPage({ viewport: { width: 390, height: 900 } })
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  await page.route('**/data/runtime.json', route => route.fulfill({ json: { schemaVersion: 1, monitor: 'local' } }))
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname
    const json = path === '/api/models/manager' ? { schemaVersion: 1, isOnroad: false, capabilities: {},
      models: [{ value: 'test-model', label: 'Test model', requiresGpu: false, installed: true, selectable: true }] } :
      path === '/api/models/laboratory' ? { schemaVersion: 1, isOnroad: false, chestnutReady: false, runtimeSupported: false,
        configuration: { enabled: false, lateralModel: '', longitudinalModel: '' }, capabilities: {}, models: [],
        download: { downloading: false }, runtime: { active: false, requested: false } } :
      path === '/api/auth/session' ? { state: 'configured', authenticated: true, localAccess: true } :
      path === '/api/device/state' ? { state: 'parked', maxAgeMs: 3000 } :
        path.startsWith('/api/settings/pages/') ? { page: 'hub', view: 'test', parked: true, rows: [] } : null
    return route.fulfill({ status: json ? 200 : 503, json: json || { error: 'Unavailable in this test' } })
  })
  const base = process.env.GALAXY_URL || 'http://127.0.0.1:8765/'
  await page.goto(base)
  const nav = page.getByRole('navigation', { name: 'Primary navigation' })
  await nav.getByRole('button', { name: 'Home', exact: true }).waitFor()

  async function checkSlide(name, direction) {
    await nav.getByRole('button', { name, exact: true }).click()
    await page.waitForFunction(() => !!document.querySelector('.gx-route-enter-active'))
    const motion = await page.locator('.gx-route-enter-active').evaluate(element => {
      const matrix = new DOMMatrix(getComputedStyle(element).transform)
      return { x: matrix.m41, y: matrix.m42 }
    })
    assert.ok(motion.x * direction > 0, `${name} enters from the correct horizontal side`)
    assert.equal(motion.y, 0)
    await page.waitForTimeout(500)
    const geometry = await nav.evaluate(element => {
      const pill = element.querySelector('.gx-nav-indicator').getBoundingClientRect()
      const button = element.querySelector('[aria-current="page"]').getBoundingClientRect()
      return { delta: Math.abs(pill.x - button.x), widthDelta: Math.abs(pill.width - button.width) }
    })
    assert.ok(geometry.delta < 2 && geometry.widthDelta < 2, 'glass indicator settles over the selected tab')
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
  }
  await checkSlide('Tools', 1)
  await checkSlide('Home', -1)
  await checkSlide('Routes', 1)
  await checkSlide('Toggles', -1)
  // Fast changes must leave only one interactive page and the correct selected tab.
  await nav.getByRole('button', { name: 'Tools', exact: true }).click()
  await nav.getByRole('button', { name: 'Home', exact: true }).click()
  await page.waitForTimeout(600)
  assert.equal(await page.locator('.gx-route:not([inert])').count(), 1)
  assert.equal(await nav.getByRole('button', { name: 'Home', exact: true }).getAttribute('aria-current'), 'page')

  // The old Model Laboratory link opens the merged Models page at its Laboratory pair section.
  await page.goto(base + '#/model_laboratory')
  const laboratory = page.locator('#gx-model-laboratory')
  await laboratory.waitFor()
  await page.waitForFunction(() => {
    const box = document.querySelector('#gx-model-laboratory').getBoundingClientRect()
    return box.top >= 0 && box.top < innerHeight
  })
  assert.equal(await page.locator('.gx-route:not([inert]) h2').first().textContent(), 'Models')
  for (const width of [320, 360, 390, 600, 1280]) {
    await page.setViewportSize({ width, height: 900 })
    const layout = await page.evaluate(() => {
      const header = document.querySelector('.gx-page-header')
      const modeBar = document.querySelector('.gx-model-mode')
      const list = document.querySelector('.gx-model-list')
      return { headerHeight: header.getBoundingClientRect().height,
        modeBarHeight: modeBar.getBoundingClientRect().height,
        hasRows: list && list.children.length > 0,
        overflow: document.documentElement.scrollWidth > innerWidth }
    })
    assert.ok(layout.headerHeight < 120 && layout.modeBarHeight < 80 && layout.hasRows, 'model headers and row list stay compact')
    assert.equal(layout.overflow, false)
  }
  await page.setViewportSize({ width: 390, height: 900 })
  for (const [name, direction] of [['Single model', -1], ['Model pair', 1]]) {
    await page.getByRole('button', { name, exact: true }).click()
    const entering = page.locator('.gx-model-driving .gx-panel-enter-active')
    await entering.waitFor()
    const x = await entering.evaluate(element => new DOMMatrix(getComputedStyle(element).transform).m41)
    assert.ok(x * direction > 0, `${name} enters from the correct side`)
    assert.equal(await page.locator('.gx-model-driving .gx-panel-leave-active').evaluate(element => element.inert), true)
    await page.waitForFunction(() => !document.querySelector('.gx-panel-enter-active, .gx-panel-leave-active'))
    assert.equal(await page.locator('.gx-model-driving .gx-panel-stage > :not([inert])').count(), 1)
  }
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await page.getByRole('button', { name: 'Single model', exact: true }).click()
  await page.waitForTimeout(50)
  assert.equal(await page.locator('.gx-panel-enter-active, .gx-panel-leave-active').count(), 0)
  await nav.getByRole('button', { name: 'Home', exact: true }).click()
  await page.waitForTimeout(50)
  assert.equal(await page.locator('.gx-route-enter-active, .gx-route-leave-active').count(), 0)
  assert.equal(await page.locator('.gx-nav-indicator').evaluate(element => getComputedStyle(element).transitionDuration), '0s')
  assert.deepEqual(errors, [])
  console.log('Directional tab slides, moving glass selection, rapid navigation, compact model summary and reduced motion passed.')
  await browser.close()
})().catch(error => { console.error(error); process.exit(1) })
