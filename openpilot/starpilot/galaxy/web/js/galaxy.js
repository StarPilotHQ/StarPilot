import { AppStoreLinks } from './app-downloads.js'
import { InstallApp } from './install-app.js'
import { RemoteAccess } from './remote-access.js'
import { CompanionUpdates } from './companion-updates.js'
import { GalaxyHotspot } from './hotspot.js'

export const GalaxyPage = {
  name: 'GalaxyPage',
  components: { AppStoreLinks, InstallApp, RemoteAccess, CompanionUpdates, GalaxyHotspot },
  props: { mode: { type: String, required: true }, unauthorized: { type: Function, required: true } },
  data: () => ({ url: '' }),
  template: `<div class="gx-settings gx-tunnel">
    <header class="gx-settings__header gx-page-header"><div><h2>Galaxy Access & Install</h2>
      <p>Set up your link, then choose how to open Galaxy.</p></div></header>
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
    <GalaxyHotspot :mode="mode" :unauthorized="unauthorized" />
  </div>`,
}
