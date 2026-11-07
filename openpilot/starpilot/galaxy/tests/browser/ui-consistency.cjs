const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright')
const assert = require('node:assert/strict')
const { readFileSync } = require('node:fs')
const { resolve } = require('node:path')

;(async () => {
  const browser = await chromium.launch({ headless: true })
  const page = await browser.newPage()
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  const base = process.env.GALAXY_URL || 'http://127.0.0.1:8765/'
  const catalog = JSON.parse(readFileSync(resolve(__dirname, '../../web/data/catalog.json')))
  let local = false, delayed = false
  const groups = [['aol', 'Always On Lateral'], ['lane_change', 'Lane Changes'],
    ['torque', 'Steering and Torque'], ['lane', 'Lane Centering'],
    ['profiles','Long Planner'], ['standard','Standard Personality']]

  await page.route('**/data/runtime.json', route => route.fulfill({
    json: { schemaVersion: 1, monitor: local ? 'local' : 'sample' },
  }))
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/auth/session') return route.fulfill({
      json: { state: 'configured', authenticated: true, localAccess: true },
    })
    if (path === '/api/device/state') return route.fulfill({ json: { state: 'parked', maxAgeMs: 3000 } })
    if (path === '/api/starport/logs') return route.fulfill({ json: { schemaVersion: 1,
      sessions: [
        { name: 'session-000003.jsonl', size: 51712, modifiedAt: 1791354093, outcome: "projected, then failed: finding android auto: Could not find the car's Android Auto service: [Errno 112] Host is down", car: 'Honda T20', transport: 'wireless', trigger: 'car_connected' },
        { name: 'session-000002.jsonl', size: 716, modifiedAt: 1791340980, outcome: 'stopped at connecting_bluetooth', car: '', transport: 'wireless', trigger: 'manual' },
      ],
      others: [{ name: 'car_ui.log', size: 4096, modifiedAt: 1791354093 }],
    } })
    if (path.startsWith('/api/settings/pages/')) {
      if (delayed) await new Promise(resolve => setTimeout(resolve, 600))
      const id = path.split('/').at(-1)
      return route.fulfill({ json: { page: id, title: id, view: 'test', parked: true,
        rows: id === 'hub' ? groups.map(([page, label]) => ({ page, label, value: 'Configure ' + label, available: true })) :
          [{ label: 'Test preference', key: 'test', value: 'Off', choices: ['Off', 'On'], action: true, available: true }],
      } })
    }
    return route.fulfill({ status: 503, json: { error: 'Device unavailable. Reconnect to read this tool.' } })
  })

  const paths = [...new Set(['/', '/tools', '/settings', '/developer/connect', '/cameras/events',
    '/cameras/sentry-settings', '/cameras/pip', '/cameras/vasm', '/theme_maker/starport',
    '/logs/crashes', '/logs/starport', '/logs/monitor', '/logs/troubleshoot', '/logs/tmux', '/tuning/plots', '/tuning/flm',
    '/appearance', '/driving/conditional', '/driving/curve', '/driving/slc', '/driving/profiles', '/driving/traffic',
    ...groups.filter(([page]) => page !== "standard").map(([page]) => '/driving/' + page), ...catalog.tools.map(tool => tool.path)])]
  for (const theme of ['dark', 'light']) {
    for (const width of (process.env.GALAXY_NAV_ONLY ? [] : [320, 1280, 3440])) {
      await page.setViewportSize({ width, height: 900 })
      for (const path of paths) {
        await page.goto(base + '#' + path)
        await page.waitForTimeout(350)
        await page.evaluate(theme => document.documentElement.dataset.theme = theme, theme)
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false,
          `${theme} ${width}px ${path}: no page overflow`)
        if (path === '/navigation') {
          assert.equal(await page.locator('.gx-navigation').isVisible(), true)
          for (const tab of ['Offline Maps', 'Setup']) {
            await page.getByRole('group', { name: 'Navigation tools' }).getByRole('button', { name: tab, exact: true }).click()
            assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, `${tab}: no overflow`)
          }
        }
        const content = await page.locator('.gx-content').boundingBox()
        assert.ok(content.width <= 1200, `${path}: bounded content`)
      }
    }
  }

  local = true
  await page.reload()
  for (const width of (process.env.GALAXY_NAV_ONLY ? [] : [390, 3440])) {
    await page.setViewportSize({ width, height: 900 })
    for (const path of paths) {
      await page.goto(base + '#' + path)
      await page.waitForTimeout(350)
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, `local ${width}px ${path}: no overflow`)
    }
  }
  await page.setViewportSize({ width: 390, height: 900 })
  await page.goto(base + '#/logs/starport')
  await page.locator('.gx-starport-log').first().waitFor()
  await page.waitForFunction(() => !document.querySelector('.gx-route-enter-active, .gx-route-leave-active'))
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, 'Android Auto logs fit a phone viewport')
  const phoneCards = await page.locator('.gx-starport-log').evaluateAll(cards => cards.map(card => {
    const row = card.getBoundingClientRect(), body = card.querySelector('.gx-starport-log__body').getBoundingClientRect()
    const download = card.querySelector('.gx-starport-log__download').getBoundingClientRect()
    return { leftInset: body.left - row.left, rightInset: row.right - download.right, width: download.width, height: download.height }
  }))
  assert.ok(phoneCards.every(card => card.leftInset >= 11 && card.rightInset >= 11), 'log text and downloads are padded inside cards')
  assert.ok(phoneCards.every(card => card.width === 44 && card.height === 44), 'individual downloads remain compact touch targets')
  assert.deepEqual(await page.locator('.gx-starport-log__download').evaluateAll(links => links.map(link => link.getAttribute('download'))),
    ['session-000003.jsonl', 'session-000002.jsonl', 'car_ui.log'])
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))))
  const desktop = await page.locator('.gx-starport-logs').evaluate(section => {
    const bounds = section.getBoundingClientRect()
    const content = section.closest('.gx-content').getBoundingClientRect()
    return { width: bounds.width, center: bounds.left + bounds.width / 2, contentCenter: content.left + content.width / 2 }
  })
  assert.ok(desktop.width <= 960 && Math.abs(desktop.center - desktop.contentCenter) < 1, 'StarPort logs are centered and readable on desktop')
  await page.setViewportSize({ width: 390, height: 900 })
  await page.goto(base + '#/settings')
  const manage = page.getByRole('button', { name: 'Manage', exact: true }).first()
  await manage.waitFor()
  const widths = await page.locator('.gx-row').evaluateAll(rows => rows.map(row => row.getBoundingClientRect().width))
  assert.ok(widths.every(width => Math.abs(width - widths[0]) < 1), 'settings groups share one width')
  await manage.click()
  await page.waitForTimeout(100)
  assert.equal(await page.locator('.gx-settings-tabs').count(), 0)
  assert.equal(await page.getByRole('heading', { name: 'Toggles', exact: true }).count(), 0)
  await page.getByRole('button', { name: 'Back', exact: true }).click()
  await manage.waitFor()
  assert.ok(await page.locator('.gx-settings-tabs button').first().evaluate(element => element.offsetHeight < 32), 'Toggles section chips remain compact')
  const tabsTop = (await page.locator(".gx-settings-tabs").boundingBox()).y
  const sectionNames = await page.locator('.gx-settings-tabs button').allTextContents()
  for (const name of sectionNames) {
    await page.locator('.gx-settings-tabs').getByRole('button', { name, exact: true }).click()
    await page.waitForTimeout(100)
    assert.ok(Math.abs((await page.locator(".gx-settings-tabs").boundingBox()).y - tabsTop) < 1, `${name}: descriptions do not move tabs`)
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, `${name}: no overflow`)
  }
  await page.locator('.gx-settings-tabs button').first().click()

  const longitudinal = page.getByRole('button',{name:'Longitudinal (Speed & Following)',exact:true})
  await longitudinal.click()
  await page.waitForTimeout(150)
  assert.equal(await longitudinal.getAttribute('aria-pressed'),'true')
  assert.notEqual(await longitudinal.evaluate(e => getComputedStyle(e).backgroundColor),
    await page.locator('.gx-settings-tabs button').first().evaluate(e => getComputedStyle(e).backgroundColor), 'active tab is visibly highlighted')
  for (const back of ['page','browser']) {
    await page.locator('.gx-row').filter({hasText:'Long Planner'}).getByRole('button',{name:'Manage'}).click()
    await page.waitForTimeout(100)
    await page.getByRole('button',{name:'Back',exact:true}).click()
    await longitudinal.waitFor()
    await page.locator('.gx-row').filter({hasText:'Standard Personality'}).getByRole('button',{name:'Manage'}).click()
    await page.waitForTimeout(100)
    if(back === 'browser') await page.evaluate(() => history.back())
    else await page.getByRole('button',{name:'Back',exact:true}).click()
    await longitudinal.waitFor()
    await page.waitForFunction(() => !document.querySelector('.gx-route-leave-active, .gx-panel-leave-active'))
    assert.equal(await longitudinal.getAttribute('aria-pressed'),'true','Back returns to actual longitudinal parent')
    assert.equal(await page.locator('.gx-section__title').innerText(),'Longitudinal (Speed & Following)')
  }
  await page.locator('.gx-settings-tabs button').first().click()

  await page.goto(base + '#/tools')
  await page.locator('.gx-appbar').waitFor()
  await page.evaluate(async () => (await import('./js/router.js')).navigate('/settings'))
  await manage.click()
  await page.waitForTimeout(250)
  assert.equal(await page.locator('.gx-settings-tabs').count(),0)
  await page.evaluate(() => history.back())
  await page.locator('.gx-settings-tabs').waitFor()
  assert.equal(await page.evaluate(() => location.hash),'#/settings')
  await manage.click()
  await page.waitForTimeout(250)
  await page.getByRole('button',{name:'Back',exact:true}).click()
  await page.locator('.gx-settings-tabs').waitFor()
  assert.equal(await page.evaluate(() => location.hash),'#/settings')
  await page.evaluate(() => history.back())
  await page.waitForFunction(() => location.hash === '#/tools')
  await page.waitForTimeout(30)
  assert.equal(await page.evaluate(async () => (await import('./js/router.js')).route.direction),-1)
  await page.evaluate(async () => (await import('./js/router.js')).navigate('/settings'))
  await manage.waitFor()

  delayed = true
  await page.reload()
  const loading = page.getByRole('status').filter({ hasText: 'Reading your device' })
  await loading.waitFor()
  const bounds = await loading.boundingBox()
  assert.ok(Math.abs(bounds.x + bounds.width / 2 - 195) < 1, 'loading card is centered')
  await page.waitForTimeout(750)
  await page.goto(base + '#/starport')
  await page.waitForTimeout(300)
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
  assert.deepEqual(errors, [], 'preview routes and unavailable Android Auto setup render without exceptions')
  console.log(process.env.GALAXY_NAV_ONLY ? "Passed focused Toggles highlighting, stable tabs, nested page/browser Back, loading and setup failure checks." : `Passed ${paths.length} routes at three widths in both themes, settings navigation, loading alignment and setup failure.`)
  await browser.close()
})().catch(error => { console.error(error); process.exit(1) })
