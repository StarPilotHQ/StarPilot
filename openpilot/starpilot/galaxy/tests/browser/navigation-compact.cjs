const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright')
const { readFileSync } = require('node:fs')
const path = require('node:path')
const assert = require('node:assert/strict')
const web = path.resolve(__dirname, '../../web')
const place = { id: 'coffee', name: 'Coffee Shop', address: '123 Main Street', latitude: 40, longitude: -90 }
const status = { enabled: true, hasKey: true, isMetric: true, status: 'noDestination', revision: '1',
  destination: null, favorites: [], recents: [], location: null, route: [], instruction: null }
;(async () => {
  const browser = await chromium.launch({ headless: true,
    ...(process.env.PLAYWRIGHT_EXECUTABLE_PATH ? { executablePath: process.env.PLAYWRIGHT_EXECUTABLE_PATH } : {}) })
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 } })
    const errors = [], actions = [], searches = []
    page.on('pageerror', error => errors.push(error.message))
    await page.route('http://navigation.test/**', async route => {
      const request = route.request(), pathname = new URL(request.url()).pathname
      if (pathname === '/data/runtime.json') return route.fulfill({ json: { schemaVersion: 1, monitor: 'local' } })
      if (pathname === '/api/auth/session') return route.fulfill({ json: { state: 'configured', authenticated: true, localAccess: true } })
      if (pathname === '/api/device/state') return route.fulfill({ json: { state: 'parked', maxAgeMs: 3000 } })
      if (pathname === '/api/navigation/search') {
        const body = request.postDataJSON()
        searches.push(body)
        return route.fulfill({ json: { results: [{ ...place }] } })
      }
      if (pathname === '/api/navigation/action') {
        const body = request.postDataJSON()
        actions.push(body)
        if (body.action === 'favorite') status.favorites = [{ ...place, ...(body.label ? { label: body.label } : {}) }]
        if (body.action === 'labelFavorite') status.favorites = [{ ...place, ...(body.label ? { label: body.label } : {}) }]
        status.revision = String(Number(status.revision) + 1)
        return route.fulfill({ json: status })
      }
      if (pathname.startsWith('/api/')) return route.fulfill({ json: status })
      if (pathname === '/' && new URL(request.url()).searchParams.has('shell')) return route.fulfill({ contentType: 'text/html', body:
        `<meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/vendor/bootstrap-icons/bootstrap-icons.min.css">
        <link rel="stylesheet" href="/css/galaxy.css"><link rel="stylesheet" href="/css/setup.css"><link rel="stylesheet" href="/css/settings.css"><link rel="stylesheet" href="/css/navigation.css"><div id="galaxy-app"></div><script type="module" src="/js/app.js"></script>` })
      if (pathname === '/') return route.fulfill({ contentType: 'text/html', body: `<meta name="viewport" content="width=device-width,initial-scale=1">
        <link rel="stylesheet" href="/vendor/bootstrap-icons/bootstrap-icons.min.css"><link rel="stylesheet" href="/css/galaxy.css"><link rel="stylesheet" href="/css/setup.css"><link rel="stylesheet" href="/css/settings.css"><link rel="stylesheet" href="/css/navigation.css"><main class="gx-content"><div id="app"></div></main>
        <script type="module">import {createApp} from '/vendor/vue/vue.esm-browser.js';
        import {NavigationPage} from '/js/navigation.js';
        window.navigation = createApp(NavigationPage,{mode:'local',unauthorized:()=>{}}).mount('#app');</script>` })
      return route.fulfill({ body: readFileSync(path.join(web, pathname)),
        contentType: pathname.endsWith('.js') ? 'text/javascript' : pathname.endsWith('.css') ? 'text/css' : pathname.endsWith('.json') ? 'application/json' : pathname.endsWith('.svg') ? 'image/svg+xml' : 'application/octet-stream' })
    })
    await page.goto('http://navigation.test/')
    const search = page.getByRole('searchbox', { name: 'Search destinations' })
    await search.waitFor()
    assert.equal(await page.getByText('Back to tools', { exact: true }).count(), 0)
    await search.fill('coffee')
    await page.getByRole('button', { name: 'Save Coffee Shop', exact: true }).waitFor()
    await page.getByRole('button', { name: 'Save Coffee Shop', exact: true }).click()
    const choices = page.getByRole('group', { name: 'Save place as' })
    for (const width of [360, 390, 768, 1280]) {
      await page.setViewportSize({ width, height: 844 })
      for (const name of ['Home', 'Work', 'Other'])
        assert.equal(await choices.getByRole('button', { name, exact: true }).isVisible(), true)
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true)
      if (width < 400) {
        assert((await page.locator('.gx-navigation-map canvas').boundingBox()).height <= 300, 'phone map stays compact')
      }
      await page.screenshot({ path: '/private/tmp/navigation-compact-' + width + '.png', fullPage: true })
    }
    await choices.getByRole('button', { name: 'Home', exact: true }).click()
    await page.waitForFunction(() => window.navigation.data.favorites[0]?.label === 'home')
    assert.equal(actions.at(-1).label, 'home')
    await page.getByRole('button', { name: 'Clear search', exact: true }).click()
    await search.focus()
    await page.getByRole('button', { name: 'Save Coffee Shop as Home, Work, or Other', exact: true }).click()
    await choices.getByRole('button', { name: 'Other', exact: true }).click()
    await page.waitForFunction(() => window.navigation.data.favorites[0] && !window.navigation.data.favorites[0].label)
    assert.equal(actions.at(-1).action, 'labelFavorite')
    assert.equal(actions.at(-1).label, null)
    Object.assign(status, { network: 'online', location: { latitude: 40, longitude: -90, validForMs: 0, lastKnown: true } })
    await page.evaluate(() => window.navigation.client.load())
    await page.getByText('Using your last saved location to plan routes.', { exact: false }).waitFor()
    assert.equal(await search.isEnabled(), true, 'a saved location works online without downloaded maps')
    assert.equal(await page.getByRole('link', { name: 'download maps for offline navigation views' }).count(), 0)
    status.network = 'offline'
    await page.evaluate(() => window.navigation.client.load())
    const offlineLink = page.getByRole('link', { name: 'download maps for offline navigation views' })
    await offlineLink.waitFor()
    assert.equal(await offlineLink.getAttribute('href'), '#/navigation/maps')
    for (const width of [390, 1280]) {
      await page.setViewportSize({ width, height: 844 })
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true)
      await page.screenshot({ path: `/private/tmp/navigation-offline-${width}.png`, fullPage: true })
    }
    await offlineLink.click()
    await page.getByText('Map Overlay Roads', { exact: true }).waitFor()
    assert.equal(await page.getByRole('button', { name: 'Offline Maps', exact: true }).getAttribute('aria-pressed'), 'true')
    await page.getByRole('button', { name: 'Destination', exact: true }).click()
    status.network = 'unknown'
    await page.evaluate(() => window.navigation.client.load())
    assert.equal(await offlineLink.count(), 0, 'unknown device state must not be presented as offline')
    await page.goto('http://navigation.test/?shell=1#/navigation/maps')
    await page.getByText('Map Overlay Roads', { exact: true }).waitFor()
    assert.equal(await page.getByRole('button', { name: 'Offline Maps', exact: true }).getAttribute('aria-pressed'), 'true')
    await page.getByRole('button', { name: 'Destination', exact: true }).click()
    await page.waitForURL('**#/navigation')
    await page.goto('http://navigation.test/?shell=1#/navigation')
    await page.getByRole('searchbox', { name: 'Search destinations' }).waitFor()
    for (const theme of ['dark', 'light']) {
      await page.locator('html').evaluate((el, theme) => el.dataset.theme = theme, theme)
      for (const width of [360, 390, 768, 1280]) {
        await page.setViewportSize({ width, height: 844 })
        const header = await page.locator('.gx-appbar').boundingBox()
        const title = await page.getByRole('heading', { name: 'Navigation', exact: true }).boundingBox()
        assert(header && header.y >= 0 && header.height >= 44, 'shared header is visible')
        assert(title.y >= header.y + header.height, 'Navigation title sits below the shared header')
        // Bottom nav and the drawer menu are mutually exclusive: mobile shows the bar, desktop shows the menu.
        assert.equal(await page.getByRole('button', { name: 'Menu', exact: true }).isVisible(), width >= 768)
        assert.equal(await page.locator('.blur-nav').isVisible(), width < 768)
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true)
        for (const name of ['Destination', 'Offline Maps', 'Setup']) {
          const tab = page.getByRole('group', { name: 'Navigation tools' }).getByRole('button', { name, exact: true })
          await tab.click()
          await page.mouse.move(0, 0)
          assert.equal(await tab.getAttribute('aria-pressed'), 'true')
          const colors = await tab.evaluate(el => {
            const style = getComputedStyle(el)
            return { text: style.color, background: style.backgroundColor }
          })
          assert.notEqual(colors.text, colors.background, 'selected tab text must remain visible')
          assert.notEqual(colors.background, 'rgba(0, 0, 0, 0)', 'selected tab uses the shared selected-button style')
        }
        await page.getByRole('button', { name: 'Destination', exact: true }).click()
        await page.screenshot({ path: `/private/tmp/navigation-shell-${theme}-${width}.png`, fullPage: true })
      }
    }
    for (const pinned of [false, true]) {
      await page.locator('.gx-app').evaluate((el, pinned) => el.classList.toggle('gx-nav-pinned', pinned), pinned)
      for (const width of [768, 1024, 1280]) {
        await page.setViewportSize({ width, height: 844 })
        const pill = await page.locator('.gx-appbar__pill').boundingBox()
        const status = await page.locator('.gx-appbar__right').boundingBox()
        const searchBox = await page.locator('.gx-searchbox').boundingBox()
        assert(status.x + status.width <= pill.x + pill.width, 'status stays inside the header pill')
        assert(searchBox.x + searchBox.width <= status.x, 'search and device status do not overlap')
        await page.getByRole('button', { name: 'Menu', exact: true }).click()
        assert.equal(await page.getByRole('complementary', { name: 'Galaxy navigation' }).isVisible(), true)
        await page.locator('.gx-underlay').click({ position: { x: width - 10, y: 100 } })
      }
    }
    assert.deepEqual(errors, [])
    console.log('Navigation: 360–1280px layout, inline favorite choices, atomic Home save, Other relabel, and readable tabs with shared headers in both themes passed')
  } finally { await browser.close() }
})().catch(error => { console.error(error); process.exitCode = 1 })
