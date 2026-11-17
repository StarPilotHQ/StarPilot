let deferredPrompt = null
export function rememberInstallPrompt(event) {
  event.preventDefault()
  deferredPrompt = event
}

export function isIosDevice(agent = typeof navigator === 'undefined' ? '' : navigator.userAgent) {
  return /iPhone|iPad|iPod/.test(agent)
}

export function isAndroidDevice(agent = typeof navigator === 'undefined' ? '' : navigator.userAgent) {
  return /Android/.test(agent)
}

export function installHelp({ secure = window.isSecureContext, agent = navigator.userAgent } = {}) {
  if (!secure) return 'Bookmark this local address for hotspot or Wi-Fi access without Internet. To install Galaxy as a web app (PWA), open your device-specific https://galaxy.firestar.link link from Galaxy Access & Install; this remote link needs Internet.'
  if (/iPhone|iPad|iPod/.test(agent)) return 'Open Galaxy in Safari, tap Share, then Add to Home Screen. Turn on Open as Web App if shown.'
  return 'This browser cannot install Galaxy automatically. Open the browser menu and choose Install app or Add to Home Screen. On a Mac in Safari, choose File → Add to Dock.'
}

export const InstallApp = {
  name: 'InstallApp',
  props: { url: { type: String, default: '' } },
  data: () => ({ prompt: null, installed: false, message: '' }),
  computed: { onPublicGalaxy() { return window.location?.hostname === 'galaxy.firestar.link' } },
  mounted() {
    this.prompt = deferredPrompt
    this.displayMode = window.matchMedia('(display-mode: standalone)')
    this.updateInstalled = () => { this.installed = !!window.StarPilotApp || this.displayMode.matches || navigator.standalone === true }
    this.onPrompt = (event) => { event.preventDefault(); deferredPrompt = event; this.prompt = event }
    this.onInstalled = () => { this.installed = true; this.prompt = deferredPrompt = null }
    this.updateInstalled()
    window.addEventListener('beforeinstallprompt', this.onPrompt)
    window.addEventListener('appinstalled', this.onInstalled)
    this.displayMode.addEventListener('change', this.updateInstalled)
  },
  beforeUnmount() {
    window.removeEventListener('beforeinstallprompt', this.onPrompt)
    window.removeEventListener('appinstalled', this.onInstalled)
    this.displayMode?.removeEventListener('change', this.updateInstalled)
    this.prompt = null
  },
  methods: {
    installHelp,
    async installWeb() {
      if (!this.prompt) return
      const prompt = this.prompt
      this.prompt = deferredPrompt = null
      try {
        await prompt.prompt()
        await prompt.userChoice
      } catch { this.message = installHelp() }
    },
  },
  template: `<div class="gx-install-app">
    <p v-if="installed" class="gx-note" role="status">Galaxy is installed.</p>
    <template v-else-if="onPublicGalaxy">
      <button v-if="prompt" type="button" class="gx-btn" @click="installWeb"><i class="bi bi-download" aria-hidden="true"></i> Install web app</button>
      <p v-else class="gx-note">{{ installHelp() }}</p>
      <p v-if="message" class="gx-note">{{ message }}</p>
    </template>
    <a v-else-if="url" class="gx-btn" :href="url" target="_blank" rel="noopener"><i class="bi bi-box-arrow-up-right" aria-hidden="true"></i> Install web app</a>
    <p v-else class="gx-note">Set up remote access to get your web app link.</p>
  </div>`,
}
