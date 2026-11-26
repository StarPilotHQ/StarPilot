const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright')
const assert = require('node:assert/strict')

;(async () => {
  const browser = await chromium.launch({ headless: true })
  const page = await browser.newPage()
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  await page.route('**/js/boot.js', route => route.fulfill({ body: '' }))
  await page.goto(process.env.GALAXY_URL || 'http://127.0.0.1:8765/')
  await page.evaluate(async () => {
    const { createApp } = await import('./vendor/vue/vue.esm-browser.js')
    const { GxNotice } = await import('./js/notice.js')
    const { BluetoothNotice } = await import('./js/bluetooth-notice.js')
    const { LocalAccess } = await import('./js/local-access.js')
    const LocalAccessError = { ...LocalAccess, mounted() {}, beforeUnmount() {},
      setup: () => ({ state: { error: 'Galaxy could not connect. Reconnecting automatically…' } }) }
    window.StarPilotApp = { connectionOptions() {} }
    document.body.innerHTML = '<main class="gx-content"><div id="fixture" class="gx-settings"></div></main>'
    createApp({ components: { GxNotice, BluetoothNotice, LocalAccessError }, data: () => ({ clicks: 0 }),
      template: `<GxNotice tone="danger">This page could not finish loading.<template #actions><button class="gx-btn" @click="clicks++">Reload page</button></template></GxNotice>
        <output>{{ clicks }}</output>
        <GxNotice v-for="tone in ['info', 'success', 'warn', 'danger']" :tone="tone" :title="tone"><p>A message that wraps onto multiple lines on narrow screens, with enough detail to explain what happens next.</p><p>Additional guidance.</p><template #actions><button class="gx-btn gx-btn--tonal">Choose another file with a longer name</button><a class="gx-btn" href="#fixture">Open settings</a></template></GxNotice>
        <section class="gx-card gx-tuner__section"><div class="gx-actions"><button class="gx-btn">Download tune</button></div><GxNotice tone="success">Tune backup download started.</GxNotice></section>
        <GxNotice busy>Connecting to your comma…</GxNotice>
        <GxNotice>Unable to load https://example.com/abcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyz</GxNotice>
        <BluetoothNotice feature="Replay"/>
        <section class="gx-card gx-panel"><GxNotice tone="danger">Connection failed.</GxNotice><button class="gx-btn">Direct action</button></section>
        <section class="gx-card gx-panel"><GxNotice tone="warn">Check your connection.</GxNotice><div class="gx-actions"><button class="gx-btn">Grouped action</button></div></section>
        <section class="gx-card gx-panel gx-stack"><GxNotice>Stacked message.</GxNotice><button class="gx-btn">Stacked action</button></section>
        <LocalAccessError mode="local"/>`,
    }).mount('#fixture')
  })
  for (const theme of ['dark', 'light']) {
    await page.evaluate(theme => document.documentElement.dataset.theme = theme, theme)
    for (const width of [320, 390, 627, 768, 1280, 3440]) {
      await page.setViewportSize({ width, height: 1200 })
      const failures = await page.locator('.gx-alert').evaluateAll(banners => banners.flatMap(banner => {
        const box = banner.getBoundingClientRect()
        const body = banner.querySelector('.gx-alert__body').getBoundingClientRect()
        const text = banner.querySelector('.gx-alert__text').getBoundingClientRect()
        const icon = banner.querySelector('.gx-alert__icon').getBoundingClientRect()
        const actions = banner.querySelector('.gx-alert__actions')
        const issues = []
        const style = getComputedStyle(banner)
        if ([style.paddingTop, style.paddingRight, style.paddingBottom, style.paddingLeft].some(value => parseFloat(value) < 16)) issues.push('insufficient banner padding')
        if (Math.abs((icon.top + icon.bottom) / 2 - (body.top + body.bottom) / 2) > 1) issues.push('icon vertical centering')
        if (banner.scrollWidth > banner.clientWidth || body.right > box.right) issues.push('overflow')
        if (actions) {
          const actionBox = actions.getBoundingClientRect()
          if (actionBox.top < text.bottom + 7) issues.push('actions need a separate row and gap')
          if (Math.abs(actionBox.left - text.left) > 1) issues.push('action alignment')
          if (banner.querySelector('.gx-alert__text button')) issues.push('button inside message')
        }
        // Check rendered siblings as well as slot actions: buttons may live in a
        // following component (for example ConnectionLogs inside LocalAccess).
        for (const [direction, sibling] of [['before', banner.previousElementSibling], ['after', banner.nextElementSibling]]) {
          if (!sibling || !sibling.getClientRects().length) continue
          const other = sibling.getBoundingClientRect()
          const gap = direction === 'after' ? other.top - box.bottom : box.top - other.bottom
          if (gap < 11) issues.push(`${direction} ${banner.textContent.trim()}: only ${gap}px between banner and sibling`)
        }
        return issues
      }))
      assert.deepEqual(failures, [], `${theme}/${width}`)
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
    }
  }
  await page.getByRole('button', { name: 'Reload page', exact: true }).click()
  assert.equal(await page.locator('output').textContent(), '1')
  assert.equal(await page.locator('.gx-alert--danger').first().getAttribute('role'), 'alert')
  assert.equal(await page.locator('.gx-alert--info').first().getAttribute('role'), 'status')
  assert.deepEqual(errors, [])
  await page.screenshot({ path: '/tmp/galaxy-notice-banners.png', fullPage: true })
  await browser.close()
  console.log('Notice banners: all tones, actions, loading, Bluetooth, wrapping, themes and responsive layouts passed.')
})().catch(error => { console.error(error); process.exit(1) })
