import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { compile } from "../web/vendor/vue/vue.esm-browser.js"
import { Home, recordArt } from "../web/js/home.js"
import { DriveStatePanel } from "../web/js/drive-state.js"
import { GalaxyPage } from "../web/js/galaxy.js"
const ids = ["longestDrive", "mostEngagedDay", "bestWeek", "highestStreak", "longestUndistractedDrive", "cleanDriveStreak"]
assert.equal(new Set(ids.map(id => JSON.stringify(recordArt(id)))).size, ids.length)
assert.ok(ids.every(id => recordArt(id).every(path => typeof path === "string" && path.length > 0)))
assert.ok(recordArt("futureRecord").length)
assert.ok(Home.template.includes('recordArt(record.id)'))
assert.ok(Home.template.includes('aria-hidden="true"><svg'))
assert.ok(GalaxyPage.template.includes('>Galaxy Access & Install</h2>'))
assert.ok(!GalaxyPage.template.includes('Galaxy & App Install'))
assert.ok(GalaxyPage.template.includes('Your remote link needs Internet and an active tunnel.'))
assert.ok(GalaxyPage.template.includes('join the Android beta on Google Groups'))
assert.ok(GalaxyPage.template.includes('Android offline app version:'))
assert.ok(GalaxyPage.template.includes('<GalaxyHotspot'))
assert.ok(GalaxyPage.template.includes('<GalaxyCompanion v-if="!ios"'), 'The Android companion section is not mounted on iOS')
assert.ok(GalaxyPage.template.indexOf('<h3>Remote pairing</h3>') < GalaxyPage.template.indexOf('<InstallApp'))
assert.ok(GalaxyPage.template.includes('class="gx-starport__steps"'))
assert.ok(GalaxyPage.template.includes('<InstallApp :url="url"'))
assert.ok(GalaxyPage.template.indexOf('<InstallApp') < GalaxyPage.template.indexOf('<GalaxyHotspot'))
assert.ok(GalaxyPage.template.includes('<LocalAccess v-if="!ios" :mode="mode" :on-unauthorized="unauthorized" />'))
assert.ok(!GalaxyPage.template.includes('PWA'))
assert.ok(DriveStatePanel.template.includes('gx-card gx-force-drive'))
const css = readFileSync(new URL('../web/css/galaxy.css', import.meta.url), 'utf8')
assert.match(DriveStatePanel.template, /<div v-if="!offroadOnly" class="gx-section__header">.*Force Drive State/)
assert.match(css, /\.gx-panel \{ padding: var\(--panel-padding\);/)
for (const component of [Home, DriveStatePanel, GalaxyPage])
  compile(component.template, { decodeEntities: value => value.replaceAll('&amp;', '&') })
console.log('Home polish: six distinct lightweight record motifs, accessible text, Galaxy access and install copy, shared Force Drive card padding and actual Vue templates passed')
