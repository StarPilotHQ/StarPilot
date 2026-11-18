import { route } from './route-state.js'
import { wikiLink } from './wiki-links.js'

export const WikiHelp = {
  props: { path: { type: String, default: '' }, page: { type: String, default: '' } },
  computed: { href() { return wikiLink(this.path || route.path, this.page) } },
  template: `<a v-if="href" class="gx-icon-btn gx-wiki-help" :href="href" target="_blank" rel="noopener noreferrer"
    aria-label="Open wiki guide (opens in a new tab)" title="Open wiki guide"><i class="bi bi-book" aria-hidden="true"></i></a>`,
}
