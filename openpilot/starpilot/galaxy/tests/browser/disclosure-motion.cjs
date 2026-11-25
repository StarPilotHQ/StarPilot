const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright')
const { readFileSync } = require('node:fs')
const path = require('node:path')
const assert = require('node:assert/strict')
const web = path.resolve(__dirname, '../../web')
const reviewMarkup = readFileSync(path.join(web, 'js/flm.js'), 'utf8').match(/<div class="gx-tuner__drive-mark"[^]*?<\/div>/)[0]
const reviewIcons = [...reviewMarkup.matchAll(/'(bi-[\w-]+)'/g)].map(match => match[1])
const iconCss = readFileSync(path.join(web, 'vendor/bootstrap-icons/bootstrap-icons.min.css'), 'utf8')
for (const icon of reviewIcons) assert.ok(iconCss.includes('.' + icon + '::before'), `${icon} must exist in the bundled font`)

;(async () => {
  const browser = await chromium.launch({ headless: true,
    ...(process.env.PLAYWRIGHT_EXECUTABLE_PATH ? { executablePath: process.env.PLAYWRIGHT_EXECUTABLE_PATH } : {}) })
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 } })
    const errors = []
    page.on('pageerror', error => errors.push(error.message))
    await page.route('http://motion.test/**', route => {
      const pathname = new URL(route.request().url()).pathname
      if (pathname === '/') return route.fulfill({ contentType: 'text/html', body: `<!doctype html><html><head>
        <link rel="stylesheet" href="/css/galaxy.css"><link rel="stylesheet" href="/css/galaxy-components.css"><link rel="stylesheet" href="/css/settings.css"><link rel="stylesheet" href="/css/flm.css">
        <link rel="stylesheet" href="/vendor/bootstrap-icons/bootstrap-icons.min.css"></head>
        <body><i id="review-icon" class="bi ${reviewIcons.at(-1)}" aria-hidden="true"></i><main id="fixture" style="padding:24px"></main><script type="module">
          import {createApp} from '/vendor/vue/vue.esm-browser.js'
          import {GalaxySelect} from '/js/galaxy-select.js'
          import {installDisclosureMotion, GxCollapse} from '/js/disclosure.js'
          installDisclosureMotion()
          import {RouteCleanup} from '/js/route-cleanup.js'
          createApp({components:{GalaxySelect,RouteCleanup,GxCollapse},data:()=>({choice:'one',expanded:false}),template:
            '<GalaxySelect v-model="choice" aria-label="Example"><option value="one">One</option><option value="two">Two</option></GalaxySelect><div class="gx-tuner"><details id="example" class="gx-card gx-tuner__section gx-tuner__disclosure"><summary>Diagnostics &amp; source details</summary><p style="height:120px">Details content</p><details><summary>Nested</summary><p>Nested content</p></details></details></div><button id="panel-toggle" @click="expanded=!expanded">Panel</button><GxCollapse><div v-show="expanded" id="panel" class="gx-card gx-panel"><p style="height:100px">Custom panel</p></div></GxCollapse><RouteCleanup :unauthorized="()=>{}" />'
          }).mount('#fixture')
        </script></body></html>` })
      const file = path.join(web, pathname)
      const ext = path.extname(file)
      return route.fulfill({ body: readFileSync(file), contentType: ({ '.js': 'text/javascript', '.css': 'text/css', '.woff2': 'font/woff2' })[ext] || 'application/octet-stream' })
    })
    await page.goto('http://motion.test/')
        await page.locator('details#example > summary').waitFor()
    assert.notEqual(await page.locator('#review-icon').evaluate(node => getComputedStyle(node, '::before').content), 'none')
    // All native disclosures use the same CSS, including late-rendered content.
    const details = page.locator('#example')
    const closedHeight = await details.evaluate(node => node.getBoundingClientRect().height)
    await details.locator(':scope > summary').click()
    await page.waitForTimeout(30)
    assert.ok(await details.evaluate(node => node.getAnimations().length > 0), 'Outer card animates open')
    const openingHeight = await details.evaluate(node => node.getBoundingClientRect().height)
    await page.waitForTimeout(280)
    const openHeight = await details.evaluate(node => node.getBoundingClientRect().height)
    assert.ok(openHeight > closedHeight + 100)
    assert.ok(openingHeight > closedHeight && openingHeight < openHeight, 'Border height expands gradually')
    await details.locator(':scope > summary').click()
    await page.waitForTimeout(30)
    assert.ok(await details.evaluate(node => node.getAnimations().length > 0), 'Outer card animates closed')
    const closingHeight = await details.evaluate(node => node.getBoundingClientRect().height)
    assert.ok(closingHeight > closedHeight && closingHeight < openHeight, 'Border height collapses gradually')
    await page.waitForTimeout(280)
    assert.ok(Math.abs(await details.evaluate(node => node.getBoundingClientRect().height) - closedHeight) < 1)
    // Real shared select keeps keyboard selection and focus restoration.
    const button = page.locator('button[aria-haspopup="listbox"]')
    await button.click()
    const menu = page.locator('.gx-select-menu')
    assert.ok(await menu.evaluate(node => node.getAnimations().length > 0), 'Menu animates open')
    await page.waitForTimeout(280)
    await page.getByRole('option', { name: 'Two' }).click()
    assert.ok(await menu.evaluate(node => node.getAnimations().length > 0), 'Menu animates closed')
    await page.waitForTimeout(280)
    assert.equal(await button.getAttribute('aria-expanded'), 'false')
    assert.equal(await button.evaluate(node => node === document.activeElement), true)
    assert.match(await button.textContent(), /Two/)
    // Reversing an in-flight transition must settle in the final native state.
    await details.locator(':scope > summary').click()
    await details.locator(':scope > summary').click()
    await page.waitForTimeout(280)
    assert.equal(await details.getAttribute('open'), null)
    await page.locator('#panel-toggle').click()
    assert.ok(await page.locator('#panel').evaluate(node => node.getAnimations().length > 0), 'Vue panels share the height animation')
    await page.waitForTimeout(280)
    await page.locator('#panel-toggle').click()
    assert.ok(await page.locator('#panel').evaluate(node => node.getAnimations().length > 0), 'Vue panels animate closed')
    await page.waitForTimeout(280)
    assert.equal(await page.locator('#panel').evaluate(node => getComputedStyle(node).display), 'none')
    // No per-page registration is needed for disclosures rendered after startup.
    await page.evaluate(() => {
      const dynamic = document.createElement('details')
      dynamic.id = 'dynamic'
      dynamic.innerHTML = '<summary>Later disclosure</summary><p style="height:80px">Dynamic content</p>'
      document.querySelector('main').append(dynamic)
    })
    await page.locator('#dynamic > summary').click()
    assert.ok(await page.locator('#dynamic').evaluate(node => node.getAnimations().length > 0))
    await page.waitForTimeout(280)
    const danger = page.getByRole('button', { name: 'Delete all driving routes' })
    assert.equal(await danger.evaluate(node => getComputedStyle(node).backgroundColor), await danger.evaluate(node => {
      const probe = document.createElement('span'); probe.style.color = 'var(--error)'; node.append(probe)
      const color = getComputedStyle(probe).color; probe.remove(); return color
    }))
    await page.emulateMedia({ reducedMotion: 'reduce' })
    await details.locator(':scope > summary').click()
    assert.equal(await details.evaluate(node => node.getAnimations().length), 0)
    await button.click()
    assert.equal(await menu.evaluate(node => node.getAnimations().length), 0)
    await page.keyboard.press('Escape')
    assert.equal(await button.evaluate(node => node === document.activeElement), true)
    assert.deepEqual(errors, [])
    console.log('Shared disclosure/menu motion, reversal, reduced motion, keyboard focus and danger contrast passed')
  } finally { await browser.close() }
})().catch(error => { console.error(error); process.exitCode = 1 })
