// Canonical store metadata shared by download links and reminders.
export const APP_DOWNLOADS = Object.freeze({
  ios: Object.freeze({ href: 'https://firestar.link/ios', label: 'App Store', icon: 'bi-apple', name: 'StarPilot BT for iPhone' }),
  android: Object.freeze({ href: 'https://firestar.link/android', label: 'Google Play', icon: 'bi-google-play', name: 'Galaxy for Android' }),
})

export const AppStoreLinks = {
  data: () => ({ downloads: APP_DOWNLOADS }),
  template: `<div class="gx-actions" aria-label="Get Galaxy">
    <a v-for="(app, platform) in downloads" :key="platform" class="gx-btn" :href="app.href" target="_blank" rel="noopener"><i :class="['bi', app.icon]" aria-hidden="true"></i> {{ app.label }}</a>
    <slot />
  </div>`,
}
