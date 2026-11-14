export const ONROAD_LAYOUT_TEMPLATE = `
    <section class="gx-settings gx-layout" aria-label="Colors and layout" :aria-busy="busy || undefined">
      <div class="gx-settings__header gx-page-header"><div><h2>{{ projection ? 'StarPort Layout' : 'Colors & Layout' }}</h2></div>
        <button class="gx-icon-btn gx-layout__info" type="button" :aria-expanded="state.aboutOpen" aria-controls="gx-layout-about" aria-label="About this editor" title="About this editor" @click="state.aboutOpen = !state.aboutOpen"><i class="bi bi-info-circle" aria-hidden="true"></i></button>
      </div>
      <GxNotice v-if="mode === 'local' && state.data && !state.data.editable && !(projection && state.data.reason)" tone="warn">Park the vehicle and reload to edit. If it stays unavailable, reload saved settings.</GxNotice>
      <div class="gx-layout__targets">
        <nav class="gx-layout__tabs gx-layout__segment" aria-label="Layout target">
          <button class="gx-btn gx-btn--tonal" :aria-pressed="!projection" :disabled="!!state.drag" @click="$emit('target', 'device')">Comma</button>
          <button class="gx-btn gx-btn--tonal" :aria-pressed="projection" :disabled="!!state.drag" @click="$emit('target', 'projection')">StarPort</button>
        </nav>
        <div v-if="mode === 'local' && state.data && state.draft && !projection && availableProfiles.length" class="gx-layout__tabs gx-layout__segment" role="group" aria-label="Layout size">
          <button v-for="tab in [{id:'large',label:'Big'}, {id:'compact',label:'Small'}].filter(tab => availableProfiles.includes(tab.id))" :key="tab.id" class="gx-btn gx-btn--tonal" type="button"
            :aria-pressed="state.profile === tab.id" :disabled="!!state.drag" @click="selectProfile(tab.id)">{{ tab.label }}<span v-if="state.data.activeProfile === tab.id" class="gx-layout__active" title="Active UI on the comma"><span class="gx-sr-only"> (active UI)</span></span></button>
        </div>
      </div>
      <div v-show="state.aboutOpen" id="gx-layout-about" class="gx-layout__about">
        <p v-if="!projection" class="gx-note">Choose a widget to move it or change its colors. Edit the layout available on this comma.</p><p v-else class="gx-note">Move widgets for the last connected StarPort screen. The comma layout stays separate. Colors follow the comma theme. Changes apply on the next connection.</p>
        <p v-if="projection && state.data?.screen" class="gx-note">Last usable screen: {{ state.data.screen.width - state.data.screen.margin_width }} × {{ state.data.screen.height - state.data.screen.margin_height }} pixels.</p>
      </div>
      <p v-if="projection && state.data?.reason" class="gx-note">{{ state.data.reason }}</p>
      <GxState v-if="mode !== 'local'">Connect to local Galaxy to edit this device’s colors and layouts.</GxState>
      <template v-else>
        <div ref="leaveBar" class="gx-layout__savebar" :class="{'gx-layout__savebar--leave': inlineLeavePrompt, 'is-stuck': state.stuck.leaveBar}" :inert="busy">
          <div v-if="snapInBar && !inlineLeavePrompt" class="gx-layout__savebar-snap" aria-hidden="true"><i class="bi bi-magnet" aria-hidden="true"></i>{{ snapSummary }}</div>
          <span role="status" :class="{'is-dirty': dirty && !inlineLeavePrompt && state.status !== 'saving'}">{{ inlineLeavePrompt ? (pendingLeave ? 'Leave without saving your changes?' : 'Discard your edits and reload the saved colors and layouts?') : state.status === 'saving' ? 'Saving…' : state.status === 'loading' ? 'Loading…' : !state.data ? 'No saved layout loaded' : dirty ? 'Unsaved' : state.notice || 'Saved' }}</span>
          <template v-if="inlineLeavePrompt">
            <button class="gx-btn gx-btn--tonal" type="button" aria-label="Keep editing" title="Keep editing" @click="cancelLeave"><svg class="gx-layout__action-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 12a8 8 0 1 0 3-6M4 4v6h6"/></svg><span class="gx-layout__action-label">Keep editing</span></button>
            <button class="gx-btn gx-btn--danger" type="button" :aria-label="pendingLeave ? 'Discard and leave' : 'Discard and reload'" :title="pendingLeave ? 'Discard and leave' : 'Discard and reload'" @click="leave(state.discard)"><svg class="gx-layout__action-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 7h16M9 7V4h6v3m-8 0 1 13h8l1-13"/></svg><span class="gx-layout__action-label">{{ pendingLeave ? 'Discard and leave' : 'Discard and reload' }}</span></button>
            <button v-if="pendingLeave" class="gx-btn" type="button" :disabled="!saveAvailable" aria-label="Save changes" title="Save changes" @click="saveAndLeave"><svg class="gx-layout__action-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 3h12l4 4v14H3V3h2Zm2 0v6h10V3M7 21v-8h10v8"/></svg><span class="gx-layout__action-label">{{ state.status === 'saving' ? 'Saving…' : 'Save changes' }}</span></button>
          </template>
          <template v-else>
            <button class="gx-btn gx-btn--tonal" type="button" :disabled="!editAvailable || !canUndo || !!state.drag || !!state.layerDrag" aria-label="Undo" title="Undo" @click="undo"><svg class="gx-layout__action-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 5 4 10l5 5M4 10h10a6 6 0 0 1 0 12"/></svg><span class="gx-layout__action-label">Undo</span></button>
            <button class="gx-btn gx-btn--tonal" type="button" :disabled="!editAvailable || !canRedo || !!state.drag || !!state.layerDrag" aria-label="Redo" title="Redo" @click="redo"><svg class="gx-layout__action-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m15 5 5 5-5 5m5-5H10a6 6 0 0 0 0 12"/></svg><span class="gx-layout__action-label">Redo</span></button>
            <div ref="moreMenu" class="gx-layout__more" @keydown.esc="closeFloating('moreOpen')">
              <button class="gx-btn gx-btn--tonal" type="button" :aria-expanded="state.moreOpen" aria-haspopup="menu" aria-label="More actions" title="More actions" @click="toggleFloating('moreOpen', 'moreMenu')"><svg class="gx-layout__action-icon" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><circle cx="5" cy="12" r="1.8"/><circle cx="12" cy="12" r="1.8"/><circle cx="19" cy="12" r="1.8"/></svg><span class="gx-layout__action-label">More</span></button>
              <template v-if="state.moreOpen">
                <div class="gx-layout__menu" role="menu" aria-label="More actions">
                  <button class="gx-layout__menu-item" type="button" role="menuitem" :disabled="!!state.drag || !!state.layerDrag" @click="closeFloating('moreOpen'); requestLeave('reload')"><svg class="gx-layout__action-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 7v5h-5M20 12a8 8 0 1 0-2 5"/></svg>Reload saved</button>
                  <button class="gx-layout__menu-item" type="button" role="menuitem" :disabled="!editAvailable || !stockChanged || !!state.drag || !!state.layerDrag" @click="closeFloating('moreOpen'); resetToStock()"><svg class="gx-layout__action-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m3 11 9-8 9 8M5 10v11h14V10M9 21v-7h6v7"/></svg>Reset to stock StarPilot</button>
                </div>
              </template>
            </div>
            <button class="gx-btn gx-layout__save" type="button" :disabled="!editAvailable || !dirty || !!state.drag || !!state.layerDrag" aria-label="Save changes" title="Save changes" @click="save"><svg class="gx-layout__action-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 3h12l4 4v14H3V3h2Zm2 0v6h10V3M7 21v-8h10v8"/></svg><span class="gx-layout__action-label gx-layout__save-label">Save<span class="gx-layout__long"> changes</span></span></button>
          </template>
        </div>
        <GxNotice tone="danger" v-if="state.error">{{ state.error }}</GxNotice>
        <template v-if="state.data && state.draft">
          <p v-if="!state.data.valid" class="gx-note" role="status">{{ projection ? 'Default StarPort positions are shown. Save to keep your separate layout.' : 'The saved customization is invalid. Default colors and positions are shown; save your edits to replace it.' }}</p>
          <GalaxySettingRow feedback="draft" v-if="!projection && state.profile === 'large'" :row="switchRow('Reduce Quick Select motion', state.draft.reduceMotion === true)" :index="0"
            :disabled="!editAvailable || !!state.drag || !!state.layerDrag" :save-value="(index, value) => setReduceMotion(value === 'On')" />
          <p v-if="quickSelectBlocked" class="gx-note" role="status">Quick Select needs space. Move a speed widget clear of either shelf area. Show tap areas to see them.</p>
          <div class="gx-layout__workspace" :inert="busy">
            <section ref="pinned" class="gx-card gx-layout__preview-card" :class="{'is-stuck': state.stuck.pinned}" aria-label="Driving screen preview" :style="{'--layout-ratio': profile.width / profile.height}">
              <div class="gx-layout__subhead"><strong>Layout preview</strong><span>{{ state.layerDrag ? "Adjusting " + selectedWidget.label : state.art.status === 'updating' || state.backdrop.status === 'updating' ? 'Updating preview…' : profile.width + " × " + profile.height }}</span></div>
              <div class="gx-layout__preview-bar">
                <GalaxySelect class="gx-field gx-field--full" aria-label="Preview scene" :value="state.scene" :disabled="!!state.drag || !!state.layerDrag" @change="state.scene = $event.target.value">
                  <optgroup v-for="group in sceneGroups" :key="group.label" :label="group.label">
                    <option v-for="scene in group.scenes" :key="scene.id" :value="scene.id">{{ scene.label }}</option>
                  </optgroup>
                </GalaxySelect>
                <button class="gx-icon-btn gx-layout__bar-toggle" type="button" :aria-pressed="state.snapEnabled" :disabled="!!state.drag || !!state.layerDrag" aria-label="Snap to edges and centers" title="Snap to edges and centers" @click="toggleSnap"><i class="bi bi-magnet" aria-hidden="true"></i></button>
                <button class="gx-icon-btn gx-layout__bar-toggle" type="button" :aria-pressed="state.showEditingAids" aria-label="Show editing aids" title="Show editing aids" @click="toggleEditingAids"><i class="bi bi-bounding-box" aria-hidden="true"></i></button>
                <button v-if="!projection && profile.inputZones?.length" class="gx-icon-btn gx-layout__bar-toggle" type="button" :aria-pressed="state.showFavoriteZones" aria-label="Show Favorite tap areas" title="Show Favorite tap areas" @click="state.showFavoriteZones = !state.showFavoriteZones"><i class="bi bi-grid-3x3-gap" aria-hidden="true"></i></button>
                <button class="gx-icon-btn gx-layout__bar-toggle" type="button" :aria-expanded="state.tipsOpen" aria-controls="gx-layout-tips" aria-label="Preview tips and display behavior" title="Preview tips and display behavior" @click="state.tipsOpen = !state.tipsOpen"><i class="bi bi-question-circle" aria-hidden="true"></i></button>
              </div>
              <p class="gx-note">{{ state.scene === 'general' ? 'Arrange all enabled widgets with sample readings. Use a driving scene to check when they appear.' : 'Eyes follow this driving scene. Choose General edit to arrange widgets hidden here.' }}</p>
              <div class="gx-layout__stage">
              <span v-if="state.backdrop.status === 'unavailable'" class="gx-layout__stage-chip gx-layout__stage-chip--bottom" role="status">{{ nativeBackdropReady ? 'The comma screen could not refresh. Showing the last one.' : 'The comma screen is unavailable. Showing a sketch.' }}</span>
              <span v-if="state.art.status === 'unavailable'" class="gx-layout__stage-chip gx-layout__stage-chip--bottom" :class="{'gx-layout__stage-chip--second': state.backdrop.status === 'unavailable'}" role="status">{{ state.art.url ? 'Widget preview could not refresh. Showing the last preview.' : 'Widget preview unavailable. Placement controls remain available.' }}</span>
              <span v-if="state.showEditingAids && state.snapGuides.length && !snapInBar" class="gx-layout__stage-chip gx-layout__stage-chip--start" :class="{'gx-layout__stage-chip--with-tap-areas': !projection && state.showFavoriteZones && state.profile !== 'compact'}" aria-hidden="true">{{ snapSummary }}</span>
              <span v-if="!projection && state.showFavoriteZones && state.showEditingAids && state.profile !== 'compact'" class="gx-layout__stage-chip" aria-hidden="true"><i class="bi bi-grid-3x3-gap"></i>Tap areas</span>
              <svg ref="preview" class="gx-layout__preview" :style="{'--layout-glow': state.profile === 'compact' ? '4px' : '14px'}" :viewBox="'0 0 ' + profile.width + ' ' + profile.height" :width="profile.width" :height="profile.height"
                @pointermove="moveDrag" @pointerup="endDrag" @pointercancel="cancelDrag" @lostpointercapture="cancelDrag"
                role="group" :aria-label="projection ? 'StarPort layout preview' : (state.profile === 'large' ? 'Big' : 'Small') + ' layout preview'">
                <image v-if="nativeBackdropReady" :href="state.backdrop.url" :width="profile.width" :height="profile.height" preserveAspectRatio="none" data-preview-source="device-backdrop" />
                <template v-else>
                <rect width="100%" height="100%" fill="#252b32" />
                <polygon :points="roadPoints([[0,1],[.4,.45],[.55,.45],[.83,1]])" fill="#303943" />
                <defs><linearGradient id="layout-road-gradient" gradientUnits="userSpaceOnUse" x1="0" :y1="profile.bounds.y" x2="0" :y2="profile.bounds.y + profile.bounds.height">
                  <stop v-for="stop in roadGradient" :key="stop.offset" :offset="stop.offset" :stop-color="stop.color" :stop-opacity="stop.alpha" />
                </linearGradient></defs>
                <defs><linearGradient id="layout-edge-gradient" gradientUnits="userSpaceOnUse" x1="0" :y1="profile.bounds.y" x2="0" :y2="profile.bounds.y + profile.bounds.height">
                  <stop offset="0" :stop-color="roadColors.pathEdge.slice(0,7)" stop-opacity="0" />
                  <stop offset=".5" :stop-color="roadColors.pathEdge.slice(0,7)" :stop-opacity="Math.floor(alpha(roadColors.pathEdge) * .35) / 255" />
                  <stop offset="1" :stop-color="roadColors.pathEdge.slice(0,7)" :stop-opacity="Math.floor(alpha(roadColors.pathEdge) * .4) / 255" />
                </linearGradient></defs>
                <polygon v-if="state.profile === 'large' && roadStyle.pathEdge" v-for="strip in [[[.18,1],[.44,.45],[.447,.45],[.231,1]], [[.639,1],[.503,.45],[.51,.45],[.69,1]]]"
                  :points="roadPoints(strip)" fill="url(#layout-edge-gradient)" />
                <polygon :points="roadPoints(state.profile === 'large' && roadStyle.pathEdge ? [[.231,1],[.447,.45],[.503,.45],[.639,1]] : [[.18,1],[.44,.45],[.51,.45],[.69,1]])" fill="url(#layout-road-gradient)" />
                <path v-for="(line,index) in [[[.16,1],[.43,.45]],[[.71,1],[.52,.45]],[[.02,1],[.38,.45]],[[.85,1],[.57,.45]]]" :key="'road-line-'+index"
                  :d="'M'+roadPoints(line).replace(' ', ' L')"
                  :stroke="state.profile === 'compact' && index < 2 ? (roadStyle.pathEdge || '#00ff40ff') : roadColors.laneLines"
                  opacity=".7" :stroke-width="state.profile === 'compact' ? 2 : 5" />
                <rect v-if="!projection && !expandedRoad" :x="state.profile === 'large' ? 1860 : 476" y="0" :width="state.profile === 'large' ? 300 : 60" :height="profile.height" fill="#090c10" />
                <g v-if="!projection && state.profile === 'large' && !expandedRoad" aria-hidden="true" pointer-events="none">
                  <g v-for="(sample, index) in [{label:'ACCEL',value:'1.08 ft/s²',accent:'#ffffff'}, {label:'STEER DELAY',value:'0.15000',accent:'#3b82f6'}, {label:'FRICTION',value:'0.12000',accent:'#22c55e'}, {label:'LAT ACCEL',value:'2.50000',accent:'#3b82f6'}, {label:'LATERAL %',value:'78.00%',accent:'#ffffff'}, {label:'TORQUE %',value:'42%',accent:'#ffffff'}, {label:'CHESTNUT',value:'',accent:'#ffffff'}]" :key="sample.label" :transform="'translate(1873 ' + (24 + index * 150) + ')'">
                    <rect width="275" height="126" rx="16" fill="#000000" stroke="#ffffff55" stroke-width="2" />
                    <rect x="253" y="4" width="18" height="118" rx="4" :fill="sample.accent" />
                    <text x="137" :y="sample.value ? 51 : 75" text-anchor="middle" :font-size="sample.label === 'STEER DELAY' ? 26 : 30" font-weight="600" fill="#ffffff">{{ sample.label }}</text>
                    <text v-if="sample.value" x="137" y="91" text-anchor="middle" font-size="30" font-weight="600" fill="#ffffff">{{ sample.value }}</text>
                  </g>
                </g>
                </template>
                <rect v-if="!nativeBackdropReady" data-preview-source="scene-border"
                  :x="state.profile === 'compact' ? 4 : profile.bounds.x" :y="state.profile === 'compact' ? 4 : profile.bounds.y"
                  :width="state.profile === 'compact' ? 468 : roadBounds.width" :height="state.profile === 'compact' ? 232 : profile.bounds.height"
                  :rx="state.profile === 'compact' ? 14 : 61" fill="none" :stroke="sceneBorder" :stroke-width="state.profile === 'compact' ? 8 : 30" />
                <rect v-if="state.showEditingAids" :x="roadBounds.x" :y="roadBounds.y" :width="roadBounds.width" :height="roadBounds.height" fill="none" stroke="#8495a855" stroke-width="2" stroke-dasharray="8 8" />
                <g v-if="!projection && state.showFavoriteZones && state.showEditingAids" aria-hidden="true" pointer-events="none">
                  <g v-if="state.profile === 'large'">
                    <g v-for="zone in quickSelectShelves" :key="zone.id">
                      <rect :x="zone.x" :y="zone.y" :width="zone.width" :height="zone.height" rx="28" fill="#b799ff0a" :stroke="zone.conflicts.length ? '#e2b886' : '#b799ff88'" stroke-width="2" stroke-dasharray="8 6" />
                      <text :x="zone.x + 16" :y="zone.y + 28" font-size="20" :fill="zone.conflicts.length ? '#e2b886' : '#d8c8ff'">{{ zone.label }}{{ zone.conflicts.length ? ' — speed widget overlap' : '' }}</text>
                    </g>
                    <g v-for="zone in favoriteZones" :key="zone.id">
                      <defs><mask :id="'favorite-area-' + zone.id" maskUnits="userSpaceOnUse">
                        <rect :x="zone.x" :y="zone.y" :width="zone.width" :height="zone.height" fill="white" />
                        <template v-for="widget in renderWidgets" :key="widget.id">
                          <rect v-if="zone.excludes?.includes(widget.id)" :x="previewX(widget.id)" :y="layout[widget.id].y" :width="widget.width" :height="widget.height" fill="black" />
                        </template>
                      </mask></defs>
                      <g :mask="'url(#favorite-area-' + zone.id + ')'">
                        <rect :x="zone.x" :y="zone.y" :width="zone.width" :height="zone.height" fill="#b799ff16" stroke="#b799ff88" :stroke-width="state.profile === 'large' ? 3 : 1" stroke-dasharray="5 5" />
                        <text :x="zone.x + zone.width / 2" :y="zone.y + zone.height - 8" text-anchor="middle" :font-size="state.profile === 'large' ? 20 : 10" fill="#d8c8ff">{{ zone.label }}</text>
                      </g>
                    </g>
                  </g>
                  <g v-else v-for="zone in profile.inputZones || []" :key="zone.id">
                    <rect :x="zone.x" :y="zone.y" :width="zone.width" :height="zone.height" fill="#b799ff16" stroke="#b799ff88" :stroke-width="state.profile === 'large' ? 3 : 1" stroke-dasharray="5 5" />
                    <text :x="zone.x + zone.width / 2" :y="zone.y + zone.height - 8" text-anchor="middle" :font-size="state.profile === 'large' ? 20 : 10" fill="#d8c8ff">{{ zone.label }}</text>
                  </g>
                </g>
                <g v-for="widget in renderWidgets" :key="widget.id" class="gx-layout__widget" :class="{'is-selected': state.drag?.id === widget.id, 'is-editing': state.showEditingAids && state.inspectorPanel === 'edit' && state.selected === widget.id}"
                  :transform="'translate(' + previewX(widget.id) + ' ' + layout[widget.id].y + ')'" tabindex="0" role="button"
                  :aria-label="widget.label + ', x ' + previewX(widget.id) + ', y ' + layout[widget.id].y +
                    (widget.frontmost ? '. Arrow keys move; Shift moves ten pixels; required and always frontmost widget.' :
                      widget.required ? '. Arrow keys move; Shift moves ten pixels; required widget.' : '. Arrow keys move; Shift moves ten pixels; Delete removes.')"
                  :aria-pressed="state.selected === widget.id" @focus="state.selected = widget.id" @keydown="onKey(widget.id, $event)"
                  @pointerdown="startDrag(widget.id, $event)">
                  <rect class="gx-layout__hit" :y="-(widget.visualInsetTop || 0)" :width="widget.width" :height="widget.height + (widget.visualInsetTop || 0) + (widget.visualInsetBottom || 0)" fill="transparent" />
                  <LayoutWidgetPreview :widget="widget" :palette="widgetColors(widget.id)" :profile="state.profile" :scene="state.scene" :clock24-hour="!!state.draft.clock24Hour" :native="runtimeWidget(widget.id)" :art="state.art" />
                  <rect class="gx-layout__selection" :y="-(widget.visualInsetTop || 0)" :width="widget.width" :height="widget.height + (widget.visualInsetTop || 0) + (widget.visualInsetBottom || 0)" fill="none" stroke="#b799ff" :stroke-width="state.profile === 'large' ? 5 : 1.5" stroke-dasharray="6 4" />
                  <g v-if="widget.box && state.selected === widget.id && editable" class="gx-layout__resize" role="button" :aria-label="'Resize ' + widget.label"
                    :transform="'translate(' + widget.width + ' ' + widget.height + ')'" @pointerdown="startResize(widget.id, $event)">
                    <circle r="34" fill="transparent" /><circle r="16" fill="#b799ff" stroke="#16121f" stroke-width="4" />
                    <path d="M-6 6 L6 -6 M0 6 L6 0" stroke="#16121f" stroke-width="3" stroke-linecap="round" />
                  </g>
                </g>
                <g v-if="projection && !nativeBackdropReady" data-preview-source="fixed-wordmark" role="img" aria-label="StarPilot, fixed logo" pointer-events="none"
                  :transform="'translate(' + (profile.width - projectionWordmark.right) + ' ' + (profile.height - projectionWordmark.bottom) + ') scale(' + projectionWordmark.scale + ')'">
                  <svg v-for="(glyph, index) in projectionWordmark.glyphs" :key="index" :x="glyph.offsetX" :y="glyph.offsetY"
                    :width="glyph.width" :height="glyph.height" :viewBox="[glyph.x, glyph.y, glyph.width, glyph.height].join(' ')" aria-hidden="true">
                    <image href="./assets/starpilot-wordmark.png" width="1024" height="512" />
                  </svg>
                </g>
                <g v-if="state.showEditingAids && state.snapGuides.length" class="gx-layout__guides" aria-hidden="true" pointer-events="none">
                  <line v-for="guide in state.snapGuides" :key="guide.axis + guide.value" :class="'gx-layout__guide gx-layout__guide--' + guide.kind"
                    :x1="guide.axis === 'x' ? guide.value + previewShift(state.drag?.id) : 0" :x2="guide.axis === 'x' ? guide.value + previewShift(state.drag?.id) : profile.width"
                    :y1="guide.axis === 'y' ? guide.value : 0" :y2="guide.axis === 'y' ? guide.value : profile.height" vector-effect="non-scaling-stroke" />
                </g>
                <rect v-if="state.showEditingAids && selectionBox" class="gx-layout__outline" :x="selectionBox.x" :y="selectionBox.y" :width="selectionBox.width" :height="selectionBox.height" rx="4" fill="none" stroke="#d4baff" stroke-width="2" vector-effect="non-scaling-stroke" />
              </svg>
              </div>
              <!-- The Small screen is too short to carry the chip over the layout; it sits underneath instead. -->
              <span v-if="!projection && state.showFavoriteZones && state.showEditingAids && state.profile === 'compact'" class="gx-layout__stage-chip gx-layout__stage-chip--below" aria-hidden="true"><i class="bi bi-grid-3x3-gap"></i>Tap areas</span>
              <div v-show="state.tipsOpen" id="gx-layout-tips" class="gx-layout__tips">
                <p class="gx-note">Drag widgets to place them. Behind them is the comma's own screen for the chosen scene, including its border, road and alerts. Readings are samples.</p>
                <p class="gx-note">Widgets snap to the screen edges, the center and other widgets. Turn snapping off with the magnet button, or hold Option (Alt) while dragging.</p>
              <p class="gx-note">General edit shows samples of all enabled widgets together. Driving scenes hide widgets that would be absent, and the eyes follow the scene. Eye changes stay with that scene; your General edit eyes are remembered.</p>
              <p v-if="selectedWidget?.kind === 'weather'" class="gx-note">Weather data by <a href="https://open-meteo.com/" target="_blank" rel="noopener noreferrer">Open-Meteo</a>. Uses your GPS area when enabled; updates in the background about every 10 minutes.</p>
              <p class="gx-note">The stopped timer appears in General edit and Stopped timer. Turn off editing aids to see the screen without outlines and guides.</p>
              <p class="gx-note">The steering wheel and driver monitoring icon can be resized in each layout.{{ profile.widgets.nav_map ? ' The map overlay has adjustable size and opacity.' : '' }} Existing display preferences and driving state still control when widgets appear.</p>
              <p v-if="!projection" class="gx-note">Small speed-limit signs can overlap confirmation actions. Live confirmations hide an overlapping sign until the decision ends. Select either widget in the list to edit it.</p>
              <p v-if="!projection && state.showFavoriteZones" class="gx-note">Favorite tap areas apply to enabled slots. Speed-limit controls and the steering wheel take priority when the drawer is closed.</p>
              </div>
              <GxNotice v-if="state.placementError" tone="warn">{{ state.placementError }}</GxNotice>
            </section>
            <section class="gx-card gx-layout__inspector" aria-label="Widget placement" @focusin="revealField"> <!-- codespell:ignore focusin -->
              <nav ref="sections" class="gx-layout__switcher gx-layout__sections gx-settings-tabs" :class="{'is-stuck': state.stuck.sections}" aria-label="Editor sections">
                <button class="gx-btn gx-btn--tonal" :aria-pressed="state.inspectorPanel === 'widgets'" :disabled="!!state.drag || !!state.layerDrag" @click="state.inspectorPanel = 'widgets'">Widgets</button>
                <button class="gx-btn gx-btn--tonal" :aria-pressed="state.inspectorPanel === 'edit'" :disabled="!!state.drag || !!state.layerDrag" @click="state.inspectorPanel = 'edit'"><span>Edit<span class="gx-layout__long"> widget</span></span></button>
                <button v-if="!projection" class="gx-btn gx-btn--tonal" :aria-pressed="state.inspectorPanel === 'road'" :disabled="!!state.drag || !!state.layerDrag" @click="state.inspectorPanel = 'road'"><span>Road<span class="gx-layout__long"> colors</span></span></button>
                <button v-if="projection || displayGroups.length" class="gx-btn gx-btn--tonal" :aria-pressed="state.inspectorPanel === 'display'" :disabled="!!state.drag || !!state.layerDrag" @click="state.inspectorPanel = 'display'">Options</button>
              </nav>
              <div v-show="state.inspectorPanel === 'widgets'" class="gx-layout__panel">
              <div ref="widgetsHelp" class="gx-layout__subhead gx-layout__help-anchor"><h3>Widgets</h3><span>{{ activeWidgets.length }} active</span>
                <button class="gx-icon-btn gx-layout__info" type="button" :aria-expanded="state.widgetsHelpOpen" aria-controls="gx-layout-widgets-help" aria-label="How the widget list works" title="How the widget list works" @click="toggleFloating('widgetsHelpOpen', 'widgetsHelp')"><i class="bi bi-info-circle" aria-hidden="true"></i></button>
                <div v-show="state.widgetsHelpOpen" id="gx-layout-widgets-help" class="gx-layout__popover" @click="closeFloating('widgetsHelpOpen')" @keydown.esc="closeFloating('widgetsHelpOpen')">
                  <p>Tap a widget to edit it.</p><p>Drag ⠿ to reorder. The top row is in front, and alerts stay above all widgets.</p><p>The eyes follow the chosen scene. Choose General edit to arrange every enabled widget. Hiding a widget with the eye affects only this scene's preview.</p>
                </div>
              </div>
              <div class="gx-layout__widget-list"><div v-for="widget in listWidgets" :key="widget.id" class="gx-layout__layer" :data-layer-id="widget.id"
                :class="{'is-dragging': state.layerDrag?.id === widget.id && state.layerDrag?.moved, 'is-drop-target': state.layerDrag?.target === widget.id && state.layerDrag?.id !== widget.id, 'is-drop-after': state.layerDrag?.after}">
                <button class="gx-layout__row-grip gx-icon-btn" type="button"
                  :aria-label="widget.frontmost ? widget.label + ' is pinned above all widgets' : 'Reorder ' + widget.label"
                  :disabled="!editAvailable || !!state.drag || widget.frontmost"
                  :title="widget.frontmost ? 'Always above other widgets' : 'Drag to reorder. Up and down keys reorder.'"
                  @pointerdown="startLayerDrag($event, widget.id)" @pointermove="moveLayerDrag($event)"
                  @pointerup="endLayerDrag($event)" @pointercancel="endLayerDrag($event, true)" @lostpointercapture="endLayerDrag($event, true)"
                  @keydown="layerKey($event, widget.id)" @click="layerClick($event, widget.id)"><span aria-hidden="true">{{ widget.frontmost ? '↑' : '⠿' }}</span></button>
                <button class="gx-layout__layer-name" type="button" :aria-pressed="state.selected === widget.id" :disabled="!!state.drag || !!state.layerDrag"
                  @click="selectWidget(widget.id)">{{ widget.label }}</button>
                <button v-if="!projection || widget.id !== 'car_exit'" class="gx-layout__visibility gx-icon-btn" type="button" :disabled="!!state.drag || !!state.layerDrag || sceneHidden(widget.id)"
                  :aria-label="visibilityTitle(widget.id, widget.label)"
                  :title="visibilityTitle(widget.id, widget.label)"
                  :aria-pressed="!previewHidden(widget.id)" @click="togglePreviewVisibility(widget.id)">
                  <i class="bi" :class="previewHidden(widget.id) ? 'bi-eye-slash' : 'bi-eye'" aria-hidden="true"></i>
                </button>
                <button v-if="!widget.required" class="gx-layout__remove gx-icon-btn" type="button" :aria-label="'Remove ' + widget.label" :disabled="!editAvailable || !!state.drag || !!state.layerDrag"
                  @click="requestRemove(widget.id)"><span aria-hidden="true">−</span></button>
                <span v-else class="gx-note gx-layout__required" title="This control cannot be removed or placed below another widget.">Required</span>
              </div></div>
              <Teleport to="body"><div v-if="state.layerDrag?.moved" class="gx-layout__drag-ghost" aria-hidden="true"
                :style="{left: state.layerDrag.x + 'px', top: state.layerDrag.y + 'px', width: state.layerDrag.width + 'px'}">
                <span aria-hidden="true">⠿</span><strong>{{ selectedWidget.label }}</strong><span>↕</span>
              </div></Teleport>
              <div class="gx-layout__tray" role="group" aria-labelledby="gx-layout-tray-title">
                <div class="gx-layout__subhead"><h4 id="gx-layout-tray-title">Inactive widgets</h4><span>{{ inactiveWidgets.length }}</span></div>
                <p v-if="inactiveWidgets.length" class="gx-note">Drag a widget onto the preview, or tap + to add it.</p>
                <div v-if="inactiveWidgets.length" class="gx-layout__chips">
                  <div v-for="widget in inactiveWidgets" :key="widget.id" class="gx-layout__tray-item">
                    <button class="gx-layout__drag-handle" type="button" :disabled="!editAvailable" :aria-label="'Drag ' + widget.label + ' onto preview'"
                      @pointerdown="startDrag(widget.id, $event, true)"><i class="bi bi-grip-vertical" aria-hidden="true"></i></button><span class="gx-layout__inactive-name">{{ widget.label }}</span>
                    <button class="gx-icon-btn gx-layout__chip-add" type="button" :aria-label="'Add ' + widget.label" :title="'Add ' + widget.label" :disabled="!editAvailable || !!state.drag" @click="add(widget.id)"><i class="bi bi-plus-lg" aria-hidden="true"></i></button>
                  </div>
                </div>
                <p v-else class="gx-note">All available widgets are on this layout.</p>
              </div>
              <button class="gx-btn gx-btn--tonal gx-layout__reset" type="button" :disabled="!editAvailable || !!state.drag" @click="resetLayout">Reset this layout</button>
              </div>
              <div v-show="state.inspectorPanel === 'edit'" class="gx-layout__panel">
                <label class="gx-layout__widget-picker"><span class="gx-layout__picker-head">Selected widget<span v-if="selectedWidget && selectedPosition.enabled" class="gx-note">{{ selectedSize[0] }} × {{ selectedSize[1] + (selectedWidget.box ? 0 : selectedWidget.visualInsetTop || 0) }} px</span></span><GalaxySelect class="gx-field gx-field--full" :value="state.selected" :disabled="!!state.drag" @change="state.selected = $event.target.value">
                  <option v-for="widget in widgets" :key="widget.id" :value="widget.id">{{ widget.label }}{{ layout[widget.id].enabled ? '' : ' · Inactive' }}</option>
                </GalaxySelect></label>
                <div v-if="selectedWidget && !selectedPosition.enabled" class="gx-layout__panel"><p class="gx-note">Add this widget to edit its position and colors.</p><button class="gx-btn" :disabled="!editAvailable" @click="add(state.selected)">Add to layout</button></div>
              <p v-if="selectedWidget && selectedPosition.enabled && sceneHidden(state.selected)" class="gx-note">This widget is hidden in this driving scene. <button class="gx-btn gx-btn--tonal" type="button" :disabled="!!state.drag || !!state.layerDrag" @click="state.scene = 'general'">Edit in General edit</button></p>
              <div v-if="selectedWidget && selectedPosition.enabled" class="gx-layout__position">
                <label v-if="selectedWidget.resizable">Size
                  <input class="gx-field" type="number" step="1" :min="selectedWidget.resizable.min" :max="selectedWidget.resizable.max"
                    :value="selectedPosition.size" :disabled="!editAvailable || !!state.drag" @change="resizeSelected($event)"></label>
                <input v-if="selectedWidget.resizable" class="gx-slider" type="range" step="1" aria-label="Size" :min="selectedWidget.resizable.min" :max="selectedWidget.resizable.max"
                  :value="selectedPosition.size" :disabled="!editAvailable || !!state.drag || !!state.layerDrag" @input="sizeSlider($event)" @change="finishColorEdit" @blur="finishColorEdit">
                <template v-if="selectedWidget.opacity">
                  <label for="layout-widget-opacity">{{ selectedWidget.box ? 'Map opacity' : ['cruise_limits', 'jetlink_status'].includes(selectedWidget.kind) ? 'Background opacity' : 'Opacity' }} · {{ selectedPosition.opacity }}%</label>
                  <input id="layout-widget-opacity" class="gx-slider" type="range" step="1" :min="selectedWidget.opacity.min" :max="selectedWidget.opacity.max"
                    :value="selectedPosition.opacity" :disabled="!editAvailable || !!state.drag || !!state.layerDrag" @input="opacityInput($event)" @change="finishColorEdit" @blur="finishColorEdit">
                </template>
                <GalaxySettingRow feedback="draft" v-if="selectedWidget.backgroundToggle" :row="switchRow('Show background', selectedPosition.background === true)" :index="0"
                  :disabled="!editAvailable || !!state.drag || !!state.layerDrag" :save-value="(index, value) => setWidgetBackground(value)" />
                <div v-if="projection && selectedWidget.sizes" class="gx-layout__favorite-display">
                  <span>Size</span>
                  <div class="gx-segment" role="group" :aria-label="selectedWidget.label + ' size'">
                    <button v-for="size in ['big', 'small']" :key="size" class="gx-btn" type="button"
                      :class="selectedPosition.size === size ? 'gx-btn--primary' : 'gx-btn--tonal'"
                      :aria-pressed="selectedPosition.size === size" :disabled="!editAvailable || !!state.drag || !!state.layerDrag"
                      @click="setWidgetSize(size)">{{ size === 'big' ? 'Big' : 'Small' }}</button>
                  </div>
                </div>
                <GalaxySettingRow v-if="selectedWidget.collapseToggle" :row="switchRow('Collapse when paused', selectedPosition.collapse === true)" :index="0"
                  :disabled="!editAvailable || !!state.drag || !!state.layerDrag" :save-value="(index, value) => setWidgetCollapse(value)" />
                <p v-if="selectedWidget.defaultAnchor === 'driver_side'" class="gx-note">The default position follows the driver’s side. A moved position stays where you place it.</p>
                <p v-if="selectedWidget.note" class="gx-note">{{ selectedWidget.note }}</p>
                <div v-if="selectedWidget.kind === 'clock'" class="gx-row"><div class="gx-row__info"><span class="gx-row__label">Use 24-hour time</span></div>
                  <label class="gx-switch"><input type="checkbox" role="switch" aria-label="Use 24-hour time" :checked="!!state.draft.clock24Hour" :disabled="!editAvailable || !!state.drag || !!state.layerDrag" @change="setClock24Hour"><span class="gx-switch__track"></span><span class="gx-switch__thumb"></span></label></div>
                <GalaxySettingRow feedback="draft" v-if="selectedWidget.kind === 'cruise_limits'" :row="switchRow('Show Speed-Limit Sources', state.draft.speedSources === true)" :index="0"
                  :disabled="!editAvailable || !!state.drag || !!state.layerDrag" :save-value="(index, value) => setSpeedSources('speedSources', value === 'On')" />
                <GalaxySettingRow feedback="draft" v-if="selectedWidget.kind === 'cruise_limits' && state.draft.speedSources === true" :row="switchRow('Show as Separate Card', state.draft.speedSourcesCard === true)" :index="1"
                  :disabled="!editAvailable || !!state.drag || !!state.layerDrag" :save-value="(index, value) => setSpeedSources('speedSourcesCard', value === 'On')" />
                <div v-if="!projection && selectedWidget.kind === 'cruise_limits'" class="gx-row"><div class="gx-row__info"><span class="gx-row__label">Show set speed</span></div>
                  <label class="gx-switch"><input type="checkbox" role="switch" aria-label="Show set speed" :checked="state.draft.showMaxSpeed !== false" :disabled="!editAvailable || !!state.drag || !!state.layerDrag" @change="setShowMaxSpeed"><span class="gx-switch__track"></span><span class="gx-switch__thumb"></span></label></div>
                <GalaxySettingRow feedback="draft" v-if="projection && selectedWidget.kind === 'steering_wheel'" :row="switchRow('Brake and Acceleration Colors', state.draft.wheelPedalFeedback === true)" :index="0"
                  :disabled="!editAvailable || !!state.drag || !!state.layerDrag" :save-value="(index, value) => setProjectionDisplay('wheelPedalFeedback', value)" />
                <GalaxySettingRow feedback="draft" v-if="!projection && selectedWidget.kind === 'steering_wheel' && displayRow('ShowBrakeStatus')" :row="switchRow('Brake and Acceleration Colors', displayValue('ShowBrakeStatus') === 'On')" :index="0"
                  :disabled="!editAvailable || !!state.drag || !!state.layerDrag || !displayAvailable(displayRow('ShowBrakeStatus'))" :save-value="(index, value) => setDisplay('ShowBrakeStatus', value)" />
                <div v-if="!projection && selectedWidget.kind === 'model_confidence' && displayRow('StockConfidenceBallWidget')" class="gx-layout__favorite-display">
                  <span>Style</span>
                  <div class="gx-segment" role="group" aria-label="Model confidence style">
                    <button v-for="choice in [['On', 'StarPilot widgets'], ['Off', 'Stock confidence ball']]" :key="choice[0]" class="gx-btn" type="button"
                      :class="displayValue('StockConfidenceBallWidget') === choice[0] ? 'gx-btn--primary' : 'gx-btn--tonal'"
                      :aria-pressed="displayValue('StockConfidenceBallWidget') === choice[0]"
                      :disabled="!editAvailable || !!state.drag || !!state.layerDrag || !displayAvailable(displayRow('StockConfidenceBallWidget'))"
                      @click="setDisplay('StockConfidenceBallWidget', choice[0])">{{ choice[1] }}</button>
                  </div>
                </div>
                <p v-if="!projection && stockConfidence && selectedWidget.kind === 'conditional_mode'" class="gx-note">Hidden while Model confidence uses the stock confidence ball.</p>
                <div v-if="projection && selectedWidget.iconSize" class="gx-layout__favorite-display">
                  <span>Show as</span>
                  <div class="gx-segment" role="group" :aria-label="selectedWidget.label + ' display'">
                    <button v-for="display in ['icons', 'words']" :key="display" class="gx-btn" type="button"
                      :class="selectedPosition.display === display ? 'gx-btn--primary' : 'gx-btn--tonal'"
                      :aria-pressed="selectedPosition.display === display" :disabled="!editAvailable || !!state.drag || !!state.layerDrag"
                      @click="setFavoriteDisplay(display)">{{ display === 'icons' ? 'Icons' : 'Words' }}</button>
                  </div>
                </div>
                <div class="gx-layout__coordinates"><label>X<input class="gx-field" type="number" step="1" :min="limits.minX" :max="limits.maxX" :value="selectedPosition.x" :disabled="!editAvailable || !!state.drag" @change="positionInput('x', $event)"></label>
                  <label>Y<input class="gx-field" type="number" step="1" :min="limits.minY" :max="limits.maxY" :value="selectedPosition.y" :disabled="!editAvailable || !!state.drag" @change="positionInput('y', $event)"></label><template v-if="selectedWidget.box">
                  <label>Width<input class="gx-field" type="number" step="10" :min="selectedWidget.box.minWidth" :max="selectedWidget.box.maxWidth" :value="selectedPosition.width" :disabled="!editAvailable || !!state.drag" @change="boxInput('width', $event)"></label>
                  <label>Height<input class="gx-field" type="number" step="10" :min="selectedWidget.box.minHeight" :max="selectedWidget.box.maxHeight" :value="selectedPosition.height" :disabled="!editAvailable || !!state.drag" @change="boxInput('height', $event)"></label></template></div>
                <template v-if="selectedWidget.box">
                  <label :for="'layout-box-width'">Width · {{ selectedPosition.width }}</label>
                  <input id="layout-box-width" class="gx-slider" type="range" step="10" :min="selectedWidget.box.minWidth" :max="selectedWidget.box.maxWidth"
                    :value="selectedPosition.width" :disabled="!editAvailable || !!state.drag || !!state.layerDrag" @input="boxSlider('width', $event)" @change="finishColorEdit" @blur="finishColorEdit">
                  <label :for="'layout-box-height'">Height · {{ selectedPosition.height }}</label>
                  <input id="layout-box-height" class="gx-slider" type="range" step="10" :min="selectedWidget.box.minHeight" :max="selectedWidget.box.maxHeight"
                    :value="selectedPosition.height" :disabled="!editAvailable || !!state.drag || !!state.layerDrag" @input="boxSlider('height', $event)" @change="finishColorEdit" @blur="finishColorEdit">
                  <span class="gx-note">Smaller sizes let more of the road show through.</span>
                </template>
                <span class="gx-note gx-layout__keys-note">Use arrow keys on a preview widget to move 1 pixel, or Shift + arrow for 10.</span>
                <section v-if="!projection && colorFields.length" class="gx-layout__colors" :aria-label="selectedWidget.label + ' colors'">
                  <div class="gx-layout__subhead"><h4>{{ selectedWidget.label }} colors</h4><button class="gx-btn gx-btn--tonal" type="button" :disabled="!editAvailable" @click="resetColors" aria-label="Reset widget colors" title="Reset widget colors">Reset</button></div>
                  <p class="gx-note">Changes this widget in the {{ state.profile === 'large' ? 'Big' : 'Small' }} layout only. Status indicators keep their meaning.</p>
                  <div class="gx-layout__palette"><div v-for="field in colorFields" :key="field.id" class="gx-layout__color" :class="{'is-open': state.openColor === 'widget:' + field.id}">
                    <button class="gx-layout__color-row" type="button" :aria-expanded="state.openColor === 'widget:' + field.id" :aria-controls="'layout-color-panel-' + field.id" @click="toggleColor('widget:' + field.id)">
                      <span class="gx-layout__swatch" :style="{'--swatch': selectedColors[field.id]}" aria-hidden="true"></span>
                      <span class="gx-layout__color-name">{{ field.label }}</span>
                      <span class="gx-layout__color-value">{{ selectedColors[field.id].slice(0,7) }}</span>
                      <span class="gx-layout__color-alpha">{{ Math.round(alpha(selectedColors[field.id]) / 255 * 100) }}%</span>
                      <i class="bi bi-chevron-down" aria-hidden="true"></i>
                    </button>
                    <div v-if="state.openColor === 'widget:' + field.id" :id="'layout-color-panel-' + field.id" class="gx-layout__color-editor">
                    <label class="gx-sr-only" :for="'layout-color-' + field.id">{{ field.label }} hex</label><div class="gx-layout__color-values">
                      <input type="color" :aria-label="field.label + ' color'" :value="selectedColors[field.id].slice(0,7)" :disabled="!editAvailable || !!state.drag" @input="colorRgb(field.id, $event)" @change="finishColorEdit" @blur="finishColorEdit">
                      <input :id="'layout-color-' + field.id" class="gx-field" type="text" spellcheck="false" maxlength="9" :value="selectedColors[field.id]" :disabled="!editAvailable || !!state.drag" @change="colorText(field.id, $event)"></div>
                    <label :for="'layout-alpha-' + field.id">Opacity · {{ Math.round(alpha(selectedColors[field.id]) / 255 * 100) }}%</label>
                    <input :id="'layout-alpha-' + field.id" class="gx-slider" type="range" min="0" max="255" step="1" :value="alpha(selectedColors[field.id])" :disabled="!editAvailable || !!state.drag" @input="colorAlpha(field.id, $event)" @change="finishColorEdit" @blur="finishColorEdit">
                    <p v-if="!alpha(selectedColors[field.id])" class="gx-note">Hidden at 0%. Picking a color makes it visible.</p>
                    </div>
                  </div></div>
                  <GxNotice tone="danger" v-if="state.colorError">{{ state.colorError }}</GxNotice>
                </section>
                <p v-if="!projection && !colorFields.length" class="gx-note">This widget uses its original status colors.</p>
              </div>
              </div>
              <section v-if="!projection" v-show="state.inspectorPanel === 'road'" class="gx-layout__colors" aria-label="Path and lane colors">
                <div class="gx-layout__subhead"><h4>Path &amp; Lane Colors</h4><button class="gx-btn gx-btn--tonal" type="button" :disabled="!editAvailable || !!state.drag" @click="resetRoad" aria-label="Reset road colors" title="Reset road colors">Reset</button></div>
                <label>Path style<GalaxySelect class="gx-field gx-field--full" :value="roadMode" :disabled="!editAvailable || !!state.drag" @change="setRoadMode($event.target.value)">
                  <option value="acceleration">Acceleration colors</option>
                  <option value="color">Custom color</option><option value="rainbow">Rainbow Road</option>
                </GalaxySelect></label>
                <p class="gx-note">These colors apply to this layout only. The preview uses a sample road.</p>
                <details class="gx-layout__help"><summary>About path &amp; lane colors</summary><div>
                <p class="gx-note">Choose how the path is colored. Acceleration colors change with acceleration and braking. Custom color stays one color. Rainbow Road shows a moving rainbow.</p>
                <p class="gx-note">{{ state.profile === 'large' ? 'Path border colors the strips along the path. Lane lines keep their own color.' : 'Lane marking colors apply to the lines beside your lane and the outer lines separately.' }} Blue lane-centering and orange steering-effort indicators stay blue and orange.</p>
                </div></details>
                <div class="gx-layout__palette"><div v-for="field in state.data.metadata.roadColorFields" :key="field.id" class="gx-layout__color" :class="{'is-open': state.openColor === 'road:' + field.id}">
                  <button class="gx-layout__color-row" type="button" :aria-expanded="state.openColor === 'road:' + field.id" :aria-controls="'road-color-panel-' + field.id" @click="toggleColor('road:' + field.id)">
                    <span class="gx-layout__swatch" :style="{'--swatch': roadColors[field.id]}" aria-hidden="true"></span>
                    <span class="gx-layout__color-name">{{ roadLabel(field.id) }}</span>
                    <span class="gx-layout__color-value">{{ roadColors[field.id].slice(0,7) }}</span>
                    <span class="gx-layout__color-alpha">{{ Math.round(alpha(roadColors[field.id]) / 255 * 100) }}%</span>
                    <i class="bi bi-chevron-down" aria-hidden="true"></i>
                  </button>
                  <div v-if="state.openColor === 'road:' + field.id" :id="'road-color-panel-' + field.id" class="gx-layout__color-editor">
                  <label class="gx-sr-only" :for="'road-color-' + field.id">{{ roadLabel(field.id) }} hex</label><div class="gx-layout__color-values">
                    <input type="color" :aria-label="roadLabel(field.id) + ' color'" :value="roadColors[field.id].slice(0,7)" :disabled="!editAvailable || !!state.drag" @input="roadRgb(field.id, $event)" @change="finishColorEdit" @blur="finishColorEdit">
                    <input :id="'road-color-' + field.id" class="gx-field" type="text" spellcheck="false" maxlength="9" :value="roadColors[field.id]" :disabled="!editAvailable || !!state.drag" @change="roadColor(field.id, $event.target.value.trim())"></div>
                  <label :for="'road-alpha-' + field.id">Opacity · {{ Math.round(alpha(roadColors[field.id]) / 255 * 100) }}%</label>
                  <input :id="'road-alpha-' + field.id" class="gx-slider" type="range" min="0" max="255" step="1" :value="alpha(roadColors[field.id])" :disabled="!editAvailable || !!state.drag" @input="roadAlpha(field.id, $event)" @change="finishColorEdit" @blur="finishColorEdit">
                  </div>
                </div></div>
                <GxNotice tone="danger" v-if="state.colorError">{{ state.colorError }}</GxNotice>
              </section>
            <section v-if="projection" v-show="state.inspectorPanel === 'display'" class="gx-layout__colors gx-layout__display" aria-label="StarPort screen options">
              <div class="gx-layout__subhead"><h4>StarPort screen options</h4></div>
              <p class="gx-note">Only the StarPort screen uses these. The comma screen has its own options on the Comma tab. Save to apply.</p>
              <h5 class="gx-layout__display-group">Alerts and borders</h5>
              <p class="gx-note">Personality alerts follow the comma screen setting.</p>
              <GalaxySettingRow feedback="draft" :row="switchRow('Red Blind-Spot Border', state.draft.blindSpotBorder === true)" :index="1"
                :disabled="!editable || !!state.drag || !!state.layerDrag" :save-value="(index, value) => setProjectionDisplay('blindSpotBorder', value)" />
              <GalaxySettingRow feedback="draft" :row="switchRow('Amber Signal Border', state.draft.signalBorder === true)" :index="2"
                :disabled="!editable || !!state.drag || !!state.layerDrag" :save-value="(index, value) => setProjectionDisplay('signalBorder', value)" />
              <h5 class="gx-layout__display-group">Lead car</h5>
              <GalaxySettingRow feedback="draft" :row="switchRow('Lead Indicator', state.draft.leadIndicator === true)" :index="0"
                :disabled="!editable || !!state.drag || !!state.layerDrag" :save-value="(index, value) => setProjectionDisplay('leadIndicator', value)" />
              <GalaxySettingRow feedback="draft" :row="projectionLeadInfoRow()" :index="1"
                :disabled="!editable || !!state.drag || !!state.layerDrag || state.draft.leadIndicator !== true" :save-value="(index, value) => setProjectionLeadInfo(value)" />
              <h5 class="gx-layout__display-group">Camera</h5>
              <GalaxySettingRow feedback="draft" :row="switchRow('Show Camera on Blind-Spot Warning', state.draft.blindSpotCamera === true)" :index="0"
                :disabled="!editable || !!state.drag || !!state.layerDrag || !projectionHasPipWidget" :save-value="(index, value) => setProjectionDisplay('blindSpotCamera', value)" />
              <p v-if="!projectionHasPipWidget" class="gx-note">Add a left or right blind-spot camera widget to enable this option.</p>
              <GalaxySettingRow feedback="draft" :row="switchRow('Large UI Gamma Effect', state.draft.largeUiGammaTrial)" :index="1"
                :disabled="!editable || !!state.drag || !!state.layerDrag" :save-value="(index, value) => setLargeUiGammaTrial(value)" />
              <p class="gx-note">The large UI road-camera color effect on StarPort (not shown in the preview).</p>
            </section>
              <section v-if="!projection && displayGroups.length" v-show="state.inspectorPanel === 'display'" class="gx-layout__colors gx-layout__display" aria-label="Comma screen options">
                <div class="gx-layout__subhead"><h4>Comma screen options</h4></div>
                <p class="gx-note">Only the comma screen uses these, in both the Big and Small layouts unless marked. StarPort has its own options on the StarPort tab. Save to apply.</p>
                <template v-for="group in displayGroups" :key="group.name">
                  <h5 class="gx-layout__display-group">{{ group.name }}</h5>
                  <GalaxySettingRow feedback="draft" v-for="(row, index) in group.rows" :key="row.key" :row="displaySettingRow(row)" :index="index"
                    :disabled="!editable || !!state.drag || !!state.layerDrag || !displayAvailable(row)" :save-value="(_, value) => setDisplay(row.key, value)" />
                </template>
              </section>
            </section>
          </div>
        </template>
      </template>
      <GxDialog v-if="state.removeConfirm && profile" labelledby="gx-layout-remove-title" describedby="gx-layout-remove-body" alert @close="cancelRemove">
        <div><h3 id="gx-layout-remove-title">Remove {{ profile.widgets[state.removeConfirm]?.label }}?</h3>
          <p id="gx-layout-remove-body">Remove this widget from the {{ projection ? 'StarPort' : state.profile === 'large' ? 'Big' : 'Small' }} layout? You can add it again from Inactive Widgets.</p></div>
        <div class="gx-settings__controls gx-actions">
          <button type="button" class="gx-btn gx-btn--tonal" @click="cancelRemove">Cancel</button>
          <button type="button" class="gx-btn gx-btn--danger" @click="confirmRemove">Remove widget</button>
        </div>
      </GxDialog>
      <GxDialog v-if="modalLeavePrompt" class="gx-layout__leave-dialog" :inert="busy" labelledby="gx-layout-leave-title" describedby="gx-layout-leave-body" alert @close="cancelLeave">
        <div class="gx-layout__leave-copy"><h3 id="gx-layout-leave-title">Leave without saving your changes?</h3>
          <p id="gx-layout-leave-body">Save your {{ projection ? 'StarPort' : 'comma' }} layout before leaving, keep editing, or discard this draft.</p></div>
        <GxNotice v-if="state.error" tone="danger">{{ state.error }}</GxNotice>
        <div class="gx-layout__leave-actions">
          <button type="button" class="gx-btn gx-btn--tonal" @click="cancelLeave">Keep editing</button>
          <button type="button" class="gx-btn gx-btn--danger" @click="leave(state.discard)">Discard and leave</button>
          <button type="button" class="gx-btn" :disabled="!saveAvailable" @click="saveAndLeave">{{ state.status === 'saving' ? 'Saving…' : 'Save changes' }}</button>
        </div>
      </GxDialog>
    </section>`
