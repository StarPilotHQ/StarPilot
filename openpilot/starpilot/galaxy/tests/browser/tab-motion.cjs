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
    const json = path === '/api/auth/session' ? { state: 'configured', authenticated: true, localAccess: true } :
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

  // The old Model Laboratory link opens the merged Model Manager page at its Laboratory card.
  await page.goto(base + '#/model_laboratory')
  const laboratory = page.locator('#gx-model-laboratory')
  await laboratory.waitFor()
  await page.waitForFunction(() => {
    const box = document.querySelector('#gx-model-laboratory').getBoundingClientRect()
    return box.top >= 0 && box.top < innerHeight
  })
  assert.equal(await page.locator('.gx-route:not([inert]) h2').first().textContent(), 'Model Manager')
  for (const width of [320, 360, 390, 600, 1280]) {
    await page.setViewportSize({ width, height: 900 })
    const layout = await page.evaluate(() => {
      const title = [...document.querySelectorAll('.gx-section__title')].find(element => element.textContent === 'Models')
      const summary = title.closest('.gx-card').querySelector('.gx-summary')
      const lab = document.querySelector('#gx-model-laboratory .gx-model-choose__heading')
      return { headerHeight: title.parentElement.getBoundingClientRect().height,
        labHeaderHeight: lab.getBoundingClientRect().height,
        summaryHeight: summary.getBoundingClientRect().height,
        overflow: document.documentElement.scrollWidth > innerWidth }
    })
    assert.ok(layout.headerHeight < 100 && layout.labHeaderHeight < 260 && layout.summaryHeight < 160, 'model headers and summary stay compact')
    assert.equal(layout.overflow, false)
  }
  await page.setViewportSize({ width: 390, height: 900 })
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await nav.getByRole('button', { name: 'Home', exact: true }).click()
  await page.waitForTimeout(50)
  assert.equal(await page.locator('.gx-route-enter-active, .gx-route-leave-active').count(), 0)
  assert.equal(await page.locator('.gx-nav-indicator').evaluate(element => getComputedStyle(element).transitionDuration), '0s')
  assert.deepEqual(errors, [])
  console.log('Directional tab slides, moving glass selection, rapid navigation, merged Model Manager / Laboratory page and reduced motion passed.')
  await browser.close()
})().catch(error => { console.error(error); process.exit(1) })
