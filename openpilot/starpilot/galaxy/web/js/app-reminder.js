import { APP_DOWNLOADS } from "./app-downloads.js"
export { APP_DOWNLOADS } from "./app-downloads.js"
import { reactive } from "../vendor/vue/vue.esm-browser.js"
import { GxDialog } from "./dialog.js"

export const HIDE_APP_REMINDER = "galaxy-hide-app-download-reminder"
export const DISMISSED_APP_REMINDER = "galaxy-app-download-reminder-dismissed"


export function appDownloadPlatform(target = globalThis) {
  // Native bridges exist before page scripts run on both companion apps.
  if (target.StarPilotApp || target.webkit?.messageHandlers?.galaxyDiagnostics) return null
  const nav = target.navigator || {}, agent = nav.userAgent || ""
  if (/iPhone|iPad|iPod/.test(agent) || nav.platform === "MacIntel" && nav.maxTouchPoints > 1) return "ios"
  return /Android/i.test(agent) ? "android" : null
}

export function reminderHidden(target = globalThis) {
  try { return target.localStorage?.getItem(HIDE_APP_REMINDER) === "1" } catch { return false }
}

export const appReminderPreference = reactive({
  hidden: reminderHidden(), error: "",
  set(hidden, target = globalThis) {
    try {
      target.localStorage.setItem(HIDE_APP_REMINDER, hidden ? "1" : "0")
      if (reminderHidden(target) !== hidden) throw new Error("Storage unavailable")
      this.hidden = hidden
      this.error = ""
      return true
    } catch {
      this.error = "Your browser could not save this preference. Check its storage settings and try again."
      return false
    }
  },
})

export function reminderDismissed(target = globalThis) {
  try { return target.sessionStorage?.getItem(DISMISSED_APP_REMINDER) === "1" } catch { return false }
}

export const AppDownloadReminder = {
  components: { GxDialog },
  data: () => ({ pref: appReminderPreference, download: APP_DOWNLOADS[appDownloadPlatform()] || null,
    dismissed: reminderDismissed() }),
  computed: { visible() { return this.download && !this.pref.hidden && !this.dismissed } },
  methods: {
    showLater() {
      this.dismissed = true
      try { sessionStorage.setItem(DISMISSED_APP_REMINDER, "1") } catch {}
    },
    dismiss() {
      this.dismissed = true
      this.pref.set(true)
      try { sessionStorage.setItem(DISMISSED_APP_REMINDER, "1") } catch {}
    },
  },
  template: `<GxDialog v-if="visible" labelledby="gx-app-download-title" describedby="gx-app-download-description"
    class="gx-app-download" @close="dismiss">
    <img class="gx-app-download__icon" src="./assets/galaxy-icon-180.png" width="72" height="72" alt="" />
    <h2 id="gx-app-download-title">Get the Galaxy app</h2>
    <p id="gx-app-download-description">The most seamless way to connect to your StarPilot via Bluetooth, Wi-Fi, or over the network.</p>
    <a class="gx-btn gx-app-download__get" :href="download.href" target="_blank" rel="noopener noreferrer" @click="dismiss">
      <i class="bi" :class="download.icon" aria-hidden="true"></i>{{ download.label }}</a>
    <div class="gx-app-download__actions">
      <button class="gx-btn gx-btn--tonal" type="button" @click="showLater">Not Now</button>
      <button class="gx-btn gx-btn--tonal" type="button" @click="dismiss">Don't Remind Me</button>
    </div>
  </GxDialog>`,
}

export const AppReminderSettings = {
  data: () => ({ pref: appReminderPreference }),
  template: `<section class="gx-card"><div class="gx-section__header"><i class="bi bi-phone" aria-hidden="true"></i><span class="gx-section__title">Browser preferences</span></div>
    <div class="gx-row"><div class="gx-row__info"><span class="gx-row__label">Hide app download reminder</span><small>Saved for this browser.</small></div>
      <label class="gx-switch"><input type="checkbox" role="switch" aria-label="Hide app download reminder" :checked="pref.hidden"
        @change="pref.set($event.target.checked)"><span class="gx-switch__track"></span><span class="gx-switch__thumb"></span></label></div>
    <p v-if="pref.error" class="gx-note gx-inset" role="alert">{{ pref.error }}</p></section>`,
}
