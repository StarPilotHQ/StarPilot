const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright')
const { readFileSync } = require('node:fs')
const path = require('node:path')
const assert = require('node:assert/strict')
const web = path.resolve(__dirname, '../../web')
let config = { enabled: false, address: '', model: 'R8', auto_mute: false, hide_filtered: false, hide_below_limit: false,
  hide_q_ride: false, hide_mute_memory: false, frequency_rules: [], quiet_ride: false, quiet_margin: 0, quiet_bands: ['X', 'K'],
  auto_learn: false, learn_bands: ['X', 'K'], border_bands: ['KA', 'LASER'], border_colors: { KA: 'coral', K: 'amber', LASER: 'coral' },
  border_edge: { line: true, dashes: true, glow: false, veil: false, chevrons: true }, border_muted_percent: 50,
  timed_mute: false, timed_mute_seconds: 5, timed_mute_strength: 6, timed_mute_bands: ['X', 'K', 'KA'],
  timed_mute_settings: { X: { seconds: 5, strength: 6 }, K: { seconds: 5, strength: 6 }, KA: { seconds: 5, strength: 6 } } }
let connection = null
;(async () => {
  const browser = await chromium.launch({ headless: true,
    ...(process.env.PLAYWRIGHT_EXECUTABLE_PATH ? { executablePath: process.env.PLAYWRIGHT_EXECUTABLE_PATH } : {}) })
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 } })
    const errors = []
    page.on('pageerror', error => errors.push(error.message))
    await page.route('http://uniden.test/**', async route => {
      const pathname = new URL(route.request().url()).pathname
      if (pathname === '/api/bluetooth/uniden') {
        const body = route.request().postDataJSON()
        if (body.operation === 'configure') config = body.config
        return route.fulfill({ json: { config, connected: !!config.enabled, alerts: [], locks: [], candidates: [], learning: [], metric: false, ...(connection || {}) } })
      }
      if (pathname === '/') return route.fulfill({ contentType: 'text/html', body: `<meta name="viewport" content="width=device-width,initial-scale=1">
        <link rel="stylesheet" href="/css/galaxy.css"><link rel="stylesheet" href="/css/settings.css">
        <main class="gx-content"><div id="app"></div></main><script type="module">
        import {createApp} from '/vendor/vue/vue.esm-browser.js'; import {UnidenPanel} from '/js/uniden.js';
        window.panel = createApp({components:{UnidenPanel}, data:()=>({settings:false, parked:true,
          devices:[{name:'R8W@12',address:'AA:BB:CC:DD:EE:FF',paired:true}]}),
          template:'<UnidenPanel ref="panel" :settings="settings" :devices="devices" :parked="parked" />'}).mount('#app');</script>` })
      return route.fulfill({ body: readFileSync(path.join(web, pathname)),
        contentType: pathname.endsWith('.js') ? 'text/javascript' : pathname.endsWith('.css') ? 'text/css' : 'application/octet-stream' })
    })
    await page.goto('http://uniden.test/')
    await page.getByLabel('Uniden detector').selectOption('AA:BB:CC:DD:EE:FF')
    await page.getByRole('button', { name: 'Use this detector' }).click()
    await page.getByRole('link', { name: 'Uniden settings' }).waitFor()
    assert.equal(config.enabled, true)
    assert.equal(await page.getByRole('button', { name: 'Use this detector' }).count(), 0)
    await page.evaluate(() => window.panel.settings = true)
    await page.getByRole('switch', { name: 'Automatically mute matching frequencies' }).check()
    await page.getByRole('switch', { name: 'Hide lockouts and frequency rules' }).check()
    await page.getByRole('button', { name: 'Rules', exact: true }).click()
    await page.getByRole('button', { name: 'Add frequency' }).click()
    await page.getByLabel('Label', { exact: true }).fill('Head unit')
    await page.getByRole('button', { name: 'Save changes' }).click()
    await page.getByRole('status').filter({ hasText: /^Saved$/ }).waitFor()
    assert.equal(config.frequency_rules[0].frequency, 34.313)
    assert.equal(config.auto_mute, true)
    await page.reload()
    await page.getByRole('link', { name: 'Uniden settings' }).waitFor()
    await page.evaluate(() => window.panel.settings = true)
    await page.getByRole('button', { name: 'Rules', exact: true }).click()
    assert.equal(await page.getByLabel('Label', { exact: true }).inputValue(), 'Head unit')
    for (const theme of ['dark', 'light']) {
      await page.locator('html').evaluate((el, theme) => el.dataset.theme = theme, theme)
      for (const width of [390, 1280]) {
        await page.setViewportSize({ width, height: 844 })
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true)
        await page.screenshot({ path: `/tmp/uniden-${theme}-${width}.png`, fullPage: true })
      }
    }
    await page.getByLabel('Frequency (GHz)').fill('34.315')
    for (const error of ['org.bluez.Error.Failed', 'Waiting to reconnect detector']) {
      connection = { connected: false, connection_state: 'reconnecting', error }
      await page.evaluate(() => window.panel.$refs.panel.refresh())
      await page.getByText('Reconnecting', { exact: true }).waitFor()
      assert.equal(await page.getByText(error, { exact: true }).count(), 0)
      assert.equal(await page.getByLabel('Frequency (GHz)').inputValue(), '34.315')
    }
    connection = null
    await page.evaluate(() => window.panel.$refs.panel.refresh())
    await page.getByText('Connected', { exact: true }).waitFor()
    await page.getByRole('button', { name: 'Discard changes' }).click()
    assert.equal(await page.getByLabel('Frequency (GHz)').inputValue(), '34.313')
    await page.evaluate(() => window.panel.parked = false)
    await page.getByRole('button', { name: 'Preferences', exact: true }).click()
    assert.equal(await page.getByRole('switch', { name: 'Enable Uniden integration' }).isDisabled(), true)
    await page.evaluate(() => window.panel.devices = [])
    await page.getByRole('link', { name: 'Pair in Bluetooth' }).waitFor()
    assert.equal(await page.getByRole('button', { name: 'Add frequency' }).count(), 0)
    assert.deepEqual(errors, [])
    console.log('Uniden setup, saved state, rules, reload, discard, paired/parked gating and responsive layouts passed')
  } finally { await browser.close() }
})().catch(error => { console.error(error); process.exitCode = 1 })
