import { WikiHelp } from './wiki-help.js'
import { AppStoreLinks } from './app-downloads.js'
import { InstallApp } from './install-app.js'
import { RemoteAccess } from './remote-access.js'
import { CompanionUpdates } from './companion-updates.js'

export const GalaxyPage = {
  name: 'GalaxyPage',
  components: { WikiHelp, AppStoreLinks, InstallApp, RemoteAccess, CompanionUpdates },
  props: { mode: { type: String, required: true }, unauthorized: { type: Function, required: true } },
  data: () => ({ url: '' }),
  template: `<div class="gx-settings gx-tunnel">
    <header class="gx-settings__header gx-page-header"><div><h2>Galaxy Access & Install</h2>
      <p>Set up your link, then choose how to open Galaxy.</p></div><WikiHelp /></header>
    <ol class="gx-steps">
      <RemoteAccess title="Set up remote access" :mode="mode" :unauthorized="unauthorized" @link="url = $event" />
      <li class="gx-card gx-step" aria-label="Get the app">
        <div class="gx-step__head"><span class="gx-step__num" aria-hidden="true">2</span>
          <div><h3>Get the app</h3><p>Choose the phone app or install Galaxy in your browser.</p></div></div>
        <div class="gx-step__body">
          <AppStoreLinks><InstallApp :url="url" /></AppStoreLinks>
          <p>Connect through your Firestar Link, nearby Wi-Fi, or Bluetooth.</p>
          <CompanionUpdates :mode="mode" :unauthorized="unauthorized" />
        </div>
      </li>
    </ol>
    <section class="gx-card gx-panel gx-stack"><div class="gx-section__header"><i class="bi bi-wifi" aria-hidden="true"></i><span class="gx-section__title">Galaxy Wi-Fi Hotspot</span></div>
      <p>The hotspot, preferred Wi-Fi networks and cellular data are on the Networks page.</p>
      <div class="gx-actions"><a class="gx-btn gx-btn--tonal" href="#/networks">Open Networks</a></div></section>
  </div>`,
}
