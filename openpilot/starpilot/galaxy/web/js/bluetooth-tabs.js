export const GALAXY_TABS = [
  { id: 'access', label: 'Access & App', path: '/galaxy' },
  { id: 'networks', label: 'Networks', path: '/networks' },
  { id: 'bluetooth', label: 'Bluetooth', path: '/bluetooth' },
  { id: 'spotify', label: 'Spotify & Phone Audio', path: '/spotify' },
  { id: 'uniden', label: 'Uniden', path: '/bluetooth/uniden' },
  { id: 'controllers', label: 'Controllers', path: '/bluetooth/controllers' },
]

export function galaxyTabIndex(path) {
  if (path === '/starport/audio') return GALAXY_TABS.findIndex(tab => tab.id === 'spotify')
  return GALAXY_TABS.findIndex(tab => tab.path === path)
}

export const BluetoothTabs = {
  props: {
    active: { type: String, default: 'bluetooth' },
    go: { type: Function, default: path => { location.hash = path } },
  },
  data: () => ({ tabs: GALAXY_TABS }),
  template: `<nav class="gx-settings-tabs" aria-label="Galaxy sections">
    <button v-for="tab in tabs" :key="tab.id" type="button" class="gx-chip"
      :aria-pressed="active === tab.id" @click="go(tab.path)">{{ tab.label }}</button>
  </nav>`,
}
