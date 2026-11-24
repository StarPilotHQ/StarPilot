export const BluetoothTabs = {
  props: {
    active: { type: String, default: 'devices' },
    go: { type: Function, default: path => { location.hash = path } },
  },
  data: () => ({ tabs: [
    { id: 'devices', label: 'Devices', path: '/bluetooth' },
    { id: 'audio', label: 'Phones & Audio', path: '/spotify' },
    { id: 'starport', label: 'StarPort', path: '/starport' },
    { id: 'uniden', label: 'Uniden', path: '/bluetooth/uniden' },
    { id: 'controllers', label: 'Controllers', path: '/bluetooth/controllers' },
  ] }),
  template: `<nav class="gx-settings-tabs" aria-label="Bluetooth sections">
    <button v-for="tab in tabs" :key="tab.id" type="button" class="gx-chip"
      :aria-pressed="active === tab.id" @click="go(tab.path)">{{ tab.label }}</button>
  </nav>`,
}
