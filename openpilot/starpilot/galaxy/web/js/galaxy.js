import { MenuTile } from './menu-tile.js'
import { WikiHelp } from './wiki-help.js'
import { AppStoreLinks } from './app-downloads.js'
import { InstallApp } from './install-app.js'
import { RemoteAccess } from './remote-access.js'
import { CompanionUpdates } from './companion-updates.js'

export const GalaxyPage = {
  name: 'GalaxyPage',
  components: { WikiHelp, AppStoreLinks, InstallApp, RemoteAccess, CompanionUpdates, MenuTile },
  props: {
    mode: { type: String, required: true }, unauthorized: { type: Function, required: true },
    tools: { type: Array, default: () => [] }, go: { type: Function, default: path => { location.hash = path } },
  },
  data: () => ({ url: '' }),
  template: `<div class="gx-settings gx-tunnel">
    <header class="gx-settings__header gx-page-header">
      <div><h2>Galaxy Access &amp; Connections</h2></div>
      <div class="gx-actions">
        <button type="button" class="gx-btn gx-btn--tonal" @click="go('/logs/help')">Get help</button>
        <WikiHelp />
      </div>
    </header>
    <div v-if="tools.length" class="gx-grid">
      <MenuTile v-for="tool in tools" :key="tool.path" :icon="tool.icon" :title="tool.name"
        :description="tool.description" :disabled="mode !== 'local'" :availability="mode !== 'local' ? 'Connect to your comma' : ''"
        @select="go(tool.path)" />
    </div>
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
  </div>`,
}
