const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright')
const assert = require('node:assert/strict')

;(async () => {
  const browser = await chromium.launch({ headless: true })
  const page = await browser.newPage()
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  const base = process.env.GALAXY_URL || 'http://127.0.0.1:8765/'
  const CLAIM_URL = 'https://starpilot-log-relay.nathan-g-hubbard.workers.dev/report/galaxy/claim/' + 'a'.repeat(64)
  const stagedPayloads = []
  let stageState = { state: 'idle', message: '', claimUrl: '', kind: '', available: true, fork: false, noteLimit: 200, modelLimit: 80 }

  await page.route('**/data/runtime.json', route => route.fulfill({
    json: { schemaVersion: 1, monitor: 'local' },
  }))
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/auth/session') {
      return route.fulfill({ json: { state: 'configured', authenticated: true, localAccess: true } })
    }
    if (path === '/api/device/state') {
      return route.fulfill({ json: { state: 'parked', maxAgeMs: 3000 } })
    }
    if (path === '/api/logs/stage') {
      if (route.request().method() === 'POST') {
        const body = route.request().postDataJSON()
        stagedPayloads.push(body)
        stageState = {
          ...stageState,
          state: 'ready',
          claimUrl: CLAIM_URL,
          kind: body.kind,
          message: 'Logs uploaded. Continue to post your report on Discord.',
        }
        return route.fulfill({ status: 202, json: stageState })
      }
      return route.fulfill({ json: stageState })
    }
    if (path === '/api/starport/logs') {
      return route.fulfill({
        json: {
          schemaVersion: 1,
          sessions: [
            {
              name: 'session-000003.jsonl',
              size: 51712,
              modifiedAt: 1791354093,
              outcome: "projected, then failed: finding android auto: Could not find the car's Android Auto service: [Errno 112] Host is down",
              car: 'Honda T20',
              transport: 'wireless',
              trigger: 'car_connected',
            },
          ],
          others: [{ name: 'car_ui.log', size: 4096, modifiedAt: 1791354093 }],
        },
      })
    }
    return route.fulfill({ status: 503, json: { error: 'Device unavailable. Reconnect to read this tool.' } })
  })

  // 1. Themes x responsive viewports across all log reporting and entry routes
  const paths = ['/logs', '/logs/help', '/logs/help/connection', '/logs/help/controller', '/logs/starport', '/networks', '/bluetooth']
  for (const theme of ['dark', 'light']) {
    for (const width of [320, 390, 1280, 3440]) {
      await page.setViewportSize({ width, height: 900 })
      for (const path of paths) {
        await page.goto(base + '#' + path)
        await page.waitForTimeout(150)
        await page.evaluate(name => { document.documentElement.dataset.theme = name }, theme)
        await page.waitForTimeout(50)
        assert.equal(
          await page.evaluate(() => document.documentElement.scrollWidth > innerWidth),
          false,
          `${theme} ${width}px ${path}: no horizontal overflow`,
        )
      }
    }
  }

  // 2. #/logs catalog: Get help present, duplicate StarPort Logs tile removed
  await page.setViewportSize({ width: 390, height: 900 })
  await page.goto(base + '#/logs')
  await page.locator('.gx-menu-tile').first().waitFor()
  const tileTitles = await page.locator('.gx-menu-tile').evaluateAll(tiles => tiles.map(t => t.textContent))
  assert.ok(tileTitles.some(text => text.includes('Get help')), '#/logs includes Get help tile')
  assert.ok(!tileTitles.some(text => text.includes('StarPort Logs')), '#/logs has no separate StarPort Logs tile')

  // 3. #/networks and #/bluetooth Connection logs panel -> #/logs/help/connection
  for (const routePath of ['#/networks', '#/bluetooth']) {
    await page.goto(base + routePath)
    const sendBtn = page.locator('.gx-connection-logs').getByRole('button', { name: 'Send logs', exact: true })
    await sendBtn.waitFor()
    const box = await sendBtn.boundingBox()
    assert.ok(box.height >= 44, `${routePath} Send logs meets 44px touch target (${box.height}px)`)
    await sendBtn.click()
    await page.waitForFunction(() => location.hash === '#/logs/help/connection')
  }

  // 4. Desktop centering, 3 tabs, touch targets, Discord footer, and StarPort session disclosure
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.goto(base + '#/logs/help')
  await page.locator('.gx-log-help__tab').first().waitFor()
  await page.waitForFunction(() => !document.querySelector('.gx-route-enter-active, .gx-route-leave-active'))
  const desktop = await page.locator('.gx-log-help').evaluate(section => {
    const bounds = section.getBoundingClientRect()
    const content = section.closest('.gx-content').getBoundingClientRect()
    return { width: bounds.width, center: bounds.left + bounds.width / 2, contentCenter: content.left + content.width / 2 }
  })
  assert.ok(desktop.width <= 960 && Math.abs(desktop.center - desktop.contentCenter) < 1, 'LogHelp is centered and bounded <=960px on desktop')

  const tabTitles = await page.locator('.gx-log-help__tab strong').allTextContents()
  assert.deepEqual(tabTitles, ['StarPort / Car Screen', 'Phone App', 'Controller'])
  const discordHref = await page.locator('.gx-log-help__discord a').getAttribute('href')
  assert.equal(discordHref, 'https://firestar.link/discord')

  // Touch targets >= 44px on StarPort tab + disclosure
  const starportTargets = await page.locator('.gx-log-help__tab, .gx-log-help__card button, .gx-log-help__card a.gx-btn, .gx-log-help__sessions > summary, .gx-log-help__discord a').evaluateAll(
    nodes => nodes.filter(n => n.offsetParent !== null).map(n => ({ text: n.textContent.trim(), height: n.getBoundingClientRect().height })),
  )
  assert.ok(starportTargets.every(item => item.height >= 43.5), `StarPort touch targets >=44px: ${JSON.stringify(starportTargets)}`)

  await page.locator('.gx-log-help__sessions > summary').click()
  await page.locator('.gx-starport-log').first().waitFor()
  const dlBoxes = await page.locator('.gx-starport-log__download').evaluateAll(links => links.map(l => {
    const r = l.getBoundingClientRect()
    return { width: r.width, height: r.height, dl: l.getAttribute('download') }
  }))
  assert.ok(dlBoxes.every(b => b.width === 44 && b.height === 44), 'Session download links are 44x44px')
  assert.deepEqual(dlBoxes.map(b => b.dl), ['session-000003.jsonl', 'car_ui.log'])

  // 5. Switch to Phone App on 390px, check chip targets, submit, claimUrl card, and reset
  await page.setViewportSize({ width: 390, height: 900 })
  await page.getByRole('tab', { name: /Phone App/ }).click()
  const androidChip = page.locator('.gx-log-help__pills').getByRole('button', { name: 'Android', exact: true })
  await androidChip.waitFor()
  const chipHeights = await page.locator('.gx-log-help__pills button.gx-chip').evaluateAll(btns => btns.map(b => b.getBoundingClientRect().height))
  assert.ok(chipHeights.every(h => h >= 43.5), `Phone chips meet 44px touch target (${chipHeights})`)
  await androidChip.click()
  await page.locator('#gx-log-help-note').fill('App keeps spinning on Wi-Fi')

  // Block window.open popup side effect in headless run while still verifying claimUrl link renders
  await page.evaluate(() => { window.open = () => null })
  await page.getByRole('button', { name: 'Send phone app logs', exact: true }).click()
  const claimLink = page.locator('.gx-log-help__ready').getByRole('link', { name: /Continue on Discord support/ })
  await claimLink.waitFor()
  assert.equal(await claimLink.getAttribute('href'), CLAIM_URL)
  assert.deepEqual(stagedPayloads, [{ kind: 'connection', note: 'App keeps spinning on Wi-Fi', phone: 'android' }])

  await page.getByRole('button', { name: 'Send another report', exact: true }).click()
  await page.locator('#gx-log-help-note').waitFor()
  assert.equal(await page.locator('.gx-log-help__ready').count(), 0)

  assert.deepEqual(errors, [], 'LogHelp browser checks completed without runtime page errors')
  console.log('Passed LogHelp browser flow, responsiveness, touch targets, themes, and staging checks.')
  await browser.close()
})().catch(error => { console.error(error); process.exit(1) })
