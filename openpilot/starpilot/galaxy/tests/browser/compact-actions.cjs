const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright')
const assert = require('node:assert/strict')

;(async () => {
  const browser = await chromium.launch({ headless: true })
  const page = await browser.newPage()
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  await page.route('**/js/boot.js', route => route.fulfill({ body: '' }))
  await page.route('**/api/**', route => route.fulfill({ status: 404, body: '' }))
  await page.goto(process.env.GALAXY_URL || 'http://127.0.0.1:8765/')
  await page.evaluate(() => { document.body.innerHTML = '<main class="gx-content"><div id="fixture"></div></main>' })

  async function mount(kind) {
    await page.evaluate(async kind => {
      const { createApp } = await import('./vendor/vue/vue.esm-browser.js')
      window.fixtureApp?.unmount()
      document.querySelector('#fixture').innerHTML = ''
      const component = kind === 'starport' ? (await import('./js/starport.js')).StarPortPage :
        (await import('./js/route-workspace.js')).RouteWorkspace
      const routeId = '2026-09-28--01-30-00'
      const segments = [0, 1, 2].map(number => ({ number, segmentName: `${routeId}--${number}`, files: { fcamera: true } }))
      const data = kind === 'starport' ? {
        setup: { enabled: false, bluetoothEnabled: true, parked: true, installReady: true, serviceReady: true,
          identity: { installed: true, message: 'Package ready' }, import: { state: 'idle' }, maxUploadBytes: 200 * 1048576 },
        packageFile: { name: 'android-auto-package.apk', size: 6 * 1048576 }, installOpen: true,
      } : { status: 'ready', route: { routeId, segments, inventoryComplete: true }, segmentNumber: 1 }
      window.uploadClicks = 0
      window.backClicks = 0
      window.fixtureApp = createApp({ ...component,
        data: () => ({ ...component.data(), ...data }),
        created() {},
        mounted() {},
        beforeUnmount() {},
        methods: { ...component.methods, upload() { window.uploadClicks++ }, videoError() {} },
      }, { mode: 'local', localAccess: true, unauthorized() {}, routeId,
        exports: { busy: false, running: false }, onBack() { window.backClicks++ } })
      window.fixtureVM = window.fixtureApp.mount('#fixture')
    }, kind)
  }

  async function checkActions(selector) {
    const overflow = await page.locator(selector).evaluateAll(elements => elements
      .filter(element => element.getClientRects().length)
      .filter(element => element.scrollWidth > element.clientWidth + 1)
      .map(element => element.className))
    assert.deepEqual(overflow, [], 'action groups need no horizontal scrolling')
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
  }

  for (const width of [320, 360, 390, 600, 1280]) {
    await page.setViewportSize({ width, height: 900 })
    await mount('starport')
    await checkActions('.gx-step-card__body, .gx-starport-sheet__toolbar')
    await page.getByRole('button', { name: 'Turn On and Install', exact: true }).click()
    assert.equal(await page.evaluate(() => window.uploadClicks), 1)
    await page.evaluate(() => { window.fixtureVM.setup.enabled = true })
    const upload = page.getByRole('button', { name: 'Install', exact: true })
    await upload.click()
    assert.equal(await page.evaluate(() => window.uploadClicks), 2, 'icon clicks reach the existing handler')
    await page.evaluate(() => { window.fixtureVM.busy = true; window.fixtureVM.uploadProgress = { loaded: 1, total: 2 } })
    assert.equal(await page.getByRole('progressbar', { name: 'Sending to your comma' }).isVisible(), true)
    assert.equal(await page.getByLabel('Android Auto APK, XAPK, APKM, or ZIP', { exact: true }).isDisabled(), true)
    await checkActions('.gx-step-card__body, .gx-starport-sheet__toolbar')

    await mount('recording')
    await checkActions('.gx-route-workspace__toolbar, .gx-route-workspace__download-actions')
    const next = page.getByRole('button', { name: 'Next segment', exact: true })
    await next.click()
    assert.equal(await next.isDisabled(), true)
    const previous = page.getByRole('button', { name: 'Previous segment', exact: true })
    await previous.click()
    await previous.click()
    assert.equal(await previous.isDisabled(), true)
    await page.getByRole('button', { name: 'Back to Routes', exact: true }).click()
    assert.equal(await page.evaluate(() => window.backClicks), 1)
  }
  assert.deepEqual(errors, [])
  console.log('StarPort and recording controls fit five widths; action names, handlers and upload states passed.')
  await browser.close()
})().catch(error => { console.error(error); process.exit(1) })
