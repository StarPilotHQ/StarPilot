const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright')
const { readFileSync } = require('node:fs')
const path = require('node:path')
const assert = require('node:assert/strict')
const web = path.resolve(__dirname, '../../web')
const detector = { name: 'Uniden R8', address: 'AA:BB:CC:DD:EE:FF', paired: true, trusted: true, connected: true }
const status = { version: 1, available: true, parked: true, powered: true, discovering: false, errorCode: null, pairing: null,
  devices: [detector, { name: 'Steering remote', address: '11:22:33:44:55:66', paired: true, trusted: true, connected: false }] }
let config = { enabled: false, address: '', model: 'R8', transparency: 8, hide_when_no_alert: true,
  auto_mute: false, hide_filtered: false, frequency_rules: [] }
const controller = { version: 1, available: true, editable: true, enabled: false, valid: true,
  revision: 'a'.repeat(64), devices: [], slots: Array.from({ length: 13 }, (_, index) => ({ index,
    label: index < 3 ? `Quick Select ${index + 1}` : `Button ${index - 2}`, key: null, available: index < 3 })),
  options: [], bindings: [], learning: null, testing: false, lastPress: null }
;(async () => {
  const browser = await chromium.launch({ headless: true,
    ...(process.env.PLAYWRIGHT_EXECUTABLE_PATH ? { executablePath: process.env.PLAYWRIGHT_EXECUTABLE_PATH } : {}) })
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 } })
    const errors = [], requests = [], actions = []
    page.on('pageerror', error => errors.push(error.message))
    await page.route('http://bluetooth.test/**', async route => {
      const pathname = new URL(route.request().url()).pathname
      requests.push(pathname)
      if (pathname === '/api/bluetooth/action') {
        const body = route.request().postDataJSON(); actions.push(body)
        const device = status.devices.find(device => device.address === body.address)
        if (device && ['connect', 'disconnect'].includes(body.operation)) device.connected = body.operation === 'connect'
        if (body.operation === 'forget') status.devices = status.devices.filter(device => device.address !== body.address)
        if (body.operation === 'scan') status.discovering = true
        if (body.operation === 'stop_scan') status.discovering = false
        return route.fulfill({ json: status })
      }
      if (pathname === '/api/bluetooth/status') return route.fulfill({ json: status })
      if (pathname === '/api/controllers/status') return route.fulfill({ json: controller })
      if (pathname === '/api/bluetooth/uniden') {
        const body = route.request().postDataJSON()
        if (body.operation === 'configure') config = body.config
        return route.fulfill({ json: { config, connected: config.enabled, alerts: [] } })
      }
      if (pathname === '/') return route.fulfill({ contentType: 'text/html', body: `<meta name="viewport" content="width=device-width,initial-scale=1">
        <link rel="stylesheet" href="/vendor/bootstrap-icons/bootstrap-icons.min.css"><link rel="stylesheet" href="/css/galaxy.css"><link rel="stylesheet" href="/css/settings.css">
        <main class="gx-content"><div id="app"></div></main><script type="module">
        import {createApp} from '/vendor/vue/vue.esm-browser.js'; import {BluetoothPage} from '/js/bluetooth.js';
        import {ControllersPage} from '/js/controllers.js';
        window.app = createApp({ components:{BluetoothPage,ControllersPage}, data:()=>({path:'/bluetooth', mode:'local'}),
          methods:{go(path){this.path=path}},
          template:\`<ControllersPage v-if="path === '/bluetooth/controllers'" :mode="mode" :go="go" :unauthorized="()=>{}" />
          <BluetoothPage v-else ref="bluetooth" :key="path" :mode="mode" :uniden-settings="path === '/bluetooth/uniden'" :go="go" :unauthorized="()=>{}" />\` }).mount('#app');</script>` })
      return route.fulfill({ body: readFileSync(path.join(web, pathname)),
        contentType: pathname.endsWith('.js') ? 'text/javascript' : pathname.endsWith('.css') ? 'text/css' : 'application/octet-stream' })
    })
    await page.goto('http://bluetooth.test/')
    await page.getByRole('button', { name: 'Add a device', exact: true }).waitFor()
    assert.equal(requests.includes('/api/controllers/status'), false)
    assert.equal(requests.includes('/api/bluetooth/uniden'), false)
    assert.equal(await page.getByRole('button', { name: 'Pair phone', exact: true }).count(), 0)
    assert.equal(await page.getByRole('button', { name: 'Learn Button' }).count(), 0)
    assert.equal(await page.getByText(detector.address, { exact: true }).isVisible(), false)
    for (const theme of ['dark', 'light']) {
      await page.locator('html').evaluate((el, theme) => el.dataset.theme = theme, theme)
      for (const width of [390, 1280]) {
        await page.setViewportSize({ width, height: 844 })
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true)
        await page.screenshot({ path: `/tmp/bluetooth-${theme}-${width}.png`, fullPage: true })
      }
    }
    await page.getByRole('button', { name: 'Add a device', exact: true }).click()
    await page.getByRole('button', { name: 'Pair phone', exact: true }).waitFor()
    await page.getByRole('button', { name: 'Search', exact: true }).click()
    await page.getByRole('button', { name: 'Stop search', exact: true }).waitFor()
    await page.getByRole('button', { name: 'Done', exact: true }).click()
    await page.getByRole('button', { name: 'Add a device', exact: true }).waitFor()
    assert.deepEqual(actions.map(action => action.operation), ['scan', 'stop_scan'])
    await page.getByRole('button', { name: /^StarPort/ }).click()
    assert.equal(await page.evaluate(() => window.app.path), '/starport')
    assert.equal(actions.length, 2)
    await page.evaluate(() => window.app.go('/bluetooth'))
    await page.getByRole('button', { name: /^Uniden/ }).click()
    await page.getByLabel('Uniden detector').selectOption(detector.address)
    await page.getByRole('button', { name: 'Use this detector' }).click()
    await page.getByRole('switch', { name: 'Automatically mute matching frequencies' }).waitFor()
    assert.equal(await page.getByRole('button', { name: 'Pair phone' }).count(), 0)
    assert.equal(await page.getByRole('button', { name: 'Search', exact: true }).count(), 0)
    await page.evaluate(() => window.app.go('/bluetooth'))
    await page.getByRole('button', { name: /^Controllers/ }).click()
    await page.getByRole('heading', { name: 'Controllers', exact: true }).waitFor()
    await page.getByRole('link', { name: 'pair in Bluetooth', exact: true }).waitFor()
    assert.equal(await page.getByRole('button', { name: 'Pair phone' }).count(), 0)
    assert.equal(await page.getByRole('button', { name: 'Search', exact: true }).count(), 0)
    await page.evaluate(() => window.app.go('/bluetooth'))
    await page.getByRole('button', { name: 'Add a device', exact: true }).waitFor()
    const detectorRow = page.locator('.gx-bluetooth__device').filter({ hasText: 'Uniden R8' })
    assert.equal(await detectorRow.locator('summary').count(), 0)
    await detectorRow.getByRole('button', { name: 'Disconnect', exact: true }).click()
    await detectorRow.getByRole('button', { name: 'Connect', exact: true }).click()
    await detectorRow.getByRole('button', { name: 'Disconnect', exact: true }).waitFor()
    const remoteRow = page.locator('.gx-bluetooth__device').filter({ hasText: 'Steering remote' })
    await remoteRow.getByRole('button', { name: 'Forget', exact: true }).click()
    const dialog = page.getByRole('alertdialog')
    await dialog.getByRole('button', { name: 'Cancel', exact: true }).click()
    assert.equal(actions.some(action => action.operation === 'forget'), false)
    await remoteRow.getByRole('button', { name: 'Forget', exact: true }).click()
    await dialog.getByRole('button', { name: 'Forget', exact: true }).click()
    await dialog.getByRole('button', { name: 'Done', exact: true }).click()
    assert.equal(await remoteRow.count(), 0)
    assert.deepEqual(actions.slice(-3), [
      { operation: 'disconnect', address: detector.address },
      { operation: 'connect', address: detector.address },
      { operation: 'forget', address: '11:22:33:44:55:66' },
    ])
    status.parked = false
    await page.evaluate(() => window.app.$refs.bluetooth.refresh())
    assert.equal(await page.getByRole('button', { name: 'Add a device', exact: true }).isDisabled(), true)
    assert.equal(await detectorRow.getByRole('button', { name: 'Disconnect', exact: true }).isDisabled(), true)
    assert.equal(await detectorRow.getByRole('button', { name: 'Forget', exact: true }).isDisabled(), true)
    assert.deepEqual(errors, [])
    console.log('Bluetooth tabs, shared pairing, device rows, StarPort link, Uniden selection and standalone controllers passed')
  } finally { await browser.close() }
})().catch(error => { console.error(error); process.exitCode = 1 })
