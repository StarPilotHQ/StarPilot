const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright')
const { readFileSync } = require('node:fs')
const path = require('node:path')
const assert = require('node:assert/strict')
const web = path.resolve(__dirname, '../../web')
const status = { enabled: true, hasKey: true, isMetric: true, status: 'noDestination', revision: '1',
  destination: null, favorites: [], recents: [], location: null, route: [], instruction: null }
;(async () => {
  const browser = await chromium.launch({ headless: true,
    ...(process.env.PLAYWRIGHT_EXECUTABLE_PATH ? { executablePath: process.env.PLAYWRIGHT_EXECUTABLE_PATH } : {}) })
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 } })
    const errors = [], actions = []
    page.on('pageerror', error => errors.push(error.message))
    await page.route('http://navigation.test/**', async route => {
      const request = route.request(), pathname = new URL(request.url()).pathname
      if (pathname === '/api/navigation/action') {
        const body = request.postDataJSON()
        actions.push(body)
        assert.equal(body.action, 'configure')
        assert.equal(body.revision, status.revision)
        Object.assign(status, body.patch)
        status.revision = String(Number(status.revision) + 1)
        return route.fulfill({ json: status })
      }
      if (pathname.startsWith('/api/')) return route.fulfill({ json: status })
      if (pathname === '/') return route.fulfill({ contentType: 'text/html', body: `<meta name="viewport" content="width=device-width,initial-scale=1">
        <link rel="stylesheet" href="/css/galaxy.css"><link rel="stylesheet" href="/css/setup.css"><link rel="stylesheet" href="/css/settings.css"><link rel="stylesheet" href="/css/navigation.css">
        <main class="gx-content"><div id="app"></div></main><script type="module">
        import {createApp} from '/vendor/vue/vue.esm-browser.js'; import {NavigationPage} from '/js/navigation.js';
        window.navigation = createApp(NavigationPage,{mode:'local',initialTab:'setup',unauthorized:()=>{}}).mount('#app');</script>` })
      return route.fulfill({ body: readFileSync(path.join(web, pathname)),
        contentType: pathname.endsWith('.js') ? 'text/javascript' : pathname.endsWith('.css') ? 'text/css' : 'application/octet-stream' })
    })
    await page.goto('http://navigation.test/')
    await page.waitForFunction(() => !!window.navigation?.data)
    for (const [key, label] of [['avoidTolls', 'Avoid tolls'], ['avoidHighways', 'Avoid highways'], ['avoidFerries', 'Avoid ferries']]) {
      const toggle = page.getByRole('switch', { name: label, exact: true })
      assert.equal(await toggle.isChecked(), false)
      await toggle.check({ force: true })
      await page.waitForFunction(key => window.navigation.data[key] === true && !window.navigation.busy, key)
      assert.deepEqual(actions.at(-1).patch, { [key]: true })
      assert.equal(await toggle.isChecked(), true)
    }
    await page.reload()
    await page.waitForFunction(() => window.navigation?.data?.avoidFerries === true)
    for (const theme of ['dark', 'light']) {
      await page.locator('html').evaluate((el, theme) => el.dataset.theme = theme, theme)
      for (const width of [360, 1280]) {
        await page.setViewportSize({ width, height: 844 })
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true)
        await page.screenshot({ path: `/private/tmp/navigation-preferences-${theme}-${width}.png`, fullPage: true })
      }
    }
    await page.evaluate(() => window.navigation.stale = true)
    assert.equal(await page.getByRole('switch', { name: 'Avoid tolls', exact: true }).isDisabled(), true)
    await page.evaluate(() => window.navigation.client.load())
    await page.getByRole('switch', { name: 'Avoid tolls', exact: true }).uncheck({ force: true })
    await page.waitForFunction(() => window.navigation.data.avoidTolls === false && !window.navigation.busy)
    assert.deepEqual(actions.at(-1).patch, { avoidTolls: false })
    assert.equal(status.avoidHighways, true)
    assert.equal(status.avoidFerries, true)
    assert.deepEqual(errors, [])
    console.log('Navigation preferences: independent toggles, revision-scoped saves, reload, stale-state disabling, and responsive dark/light layouts passed')
  } finally { await browser.close() }
})().catch(error => { console.error(error); process.exitCode = 1 })
