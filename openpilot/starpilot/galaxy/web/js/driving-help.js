export const DrivingHelp = {
  props: {
    routeId: { type: String, default: "" },
    segment: { type: Number, default: null },
    connect: { type: String, default: "" },
    routesAvailable: Boolean,
  },
  emits: ["routes"],
  data: () => ({ copied: false }),
  computed: {
    routeDetails() {
      return this.routeId + (this.segment === null ? "" : " · Segment " + this.segment)
    },
  },
  watch: { routeDetails() { this.copied = false } },
  methods: {
    async copyDetails() {
      this.copied = false
      const details = this.routeDetails
      try {
        await navigator.clipboard.writeText(details)
        if (details === this.routeDetails) this.copied = true
      } catch {
        // Local Galaxy commonly runs on HTTP, where the Clipboard API is unavailable.
        this.$refs.details?.select()
      }
    },
  },
  template: `<div class="gx-stack">
    <p class="gx-note">Find the drive, then share the route link and approximate timestamp or segment. Describe what the car did and what you expected.</p>
    <div v-if="routeId" class="gx-actions">
      <input ref="details" class="gx-field" aria-label="Route and segment for support" :value="routeDetails" readonly @focus="$event.target.select()">
      <button type="button" class="gx-btn gx-btn--tonal" @click="copyDetails">{{ copied ? 'Copied' : 'Copy route details' }}</button>
    </div>
    <div class="gx-actions">
      <button v-if="!routeId && routesAvailable" type="button" class="gx-btn gx-btn--tonal" @click="$emit('routes')">Open Galaxy Routes</button>
      <a class="gx-btn gx-btn--tonal" :href="connect || 'https://connect.comma.ai/'" target="_blank" rel="noopener noreferrer">{{ connect ? 'Open this route online' : 'Open comma connect' }}</a>
      <a class="gx-btn gx-btn--tonal" href="https://discord.com/channels/1387432184121393333/1506463404863389806" target="_blank" rel="noopener noreferrer">Driving support on Discord</a>
    </div>
    <p v-if="!routeId && !routesAvailable" class="gx-note">Connect directly to your comma to browse saved Galaxy routes.</p>
  </div>`,
}
