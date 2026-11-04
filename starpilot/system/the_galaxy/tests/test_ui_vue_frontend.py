import json
import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[4]
UI_ROOT = REPO_ROOT / "starpilot/system/the_galaxy/assets/mobile"
GALAXY_PY = REPO_ROOT / "starpilot/system/the_galaxy/the_galaxy.py"


def _read(rel):
  return (UI_ROOT / rel).read_text(encoding="utf-8")


def test_ui_app_shell_files_exist():
  required = [
    "index.html",
    "manifest.json",
    "css/material.css",
    "js/app.js",
    "js/store.js",
    "js/api.js",
    "js/params.js",
    "js/i18n.js",
    "js/components/AppShell.js",
    "js/components/DevicePicker.js",
    "js/components/GalaxyModal.js",
    "js/components/GalaxySheet.js",
    "js/components/GalaxySection.js",
    "js/components/GalaxyEmbed.js",
    "js/components/GalaxyToggleCard.js",
    "js/components/SettingTree.js",
    "js/components/ParamSections.js",
    "js/components/ManeuverCard.js",
    "js/components/WheelControls.js",
    "js/components/BluetoothPanel.js",
    "js/components/DevModeBanner.js",
    "js/components/LanguageSelector.js",
    "js/composables.js",
    "js/views/Home.js",
    "js/views/Settings.js",
    "js/views/Tools.js",
    "js/views/Recordings.js",
    "js/views/Logs.js",
    "js/views/Tuning.js",
    "js/views/Navigation.js",
    "js/views/Vehicle.js",
    "js/views/Bluetooth.js",
    "js/views/SystemTools.js",
  ]
  for rel in required:
    assert (UI_ROOT / rel).is_file(), f"missing new-UI file: {rel}"


def test_ui_index_wires_vue_and_mount_point():
  index = _read("index.html")
  assert 'id="galaxy-app"' in index
  assert 'src="/assets/mobile/js/app.js"' in index
  assert '"vue": "/assets/vendor/vue/vue.esm-browser.js"' in index
  assert '<title>Galaxy</title>' in index
  assert 'apple-mobile-web-app-title" content="Galaxy"' in index


def test_ui_device_picker_uses_gateway_directory_and_preserves_local_galaxy():
  picker = _read("js/components/DevicePicker.js")
  shell = _read("js/components/AppShell.js")
  css = _read("css/material.css")
  param_keys = (REPO_ROOT / "common/params_keys.h").read_text(encoding="utf-8")

  assert 'fetch("/_gateway/devices"' in picker
  assert 'method: "PUT"' in picker
  assert "/name`" in picker
  assert "Rename comma" in picker
  assert "hasMultipleDevices" in picker
  assert "window.location.assign(device.path)" in picker
  assert shell.index('<DevicePicker />') > shell.index('v-for="(links, section) in NAV"')
  assert '"GalaxyDeviceName", {PERSISTENT | DONT_LOG, STRING' in param_keys
  assert "localStorage" not in picker
  assert ".gx-drawer.open ~ .blur-nav" in css
  assert "Local Galaxy instances do not have the gateway directory endpoint." in picker


def test_ui_uses_same_backend_endpoints():
  settings = _read("js/views/Settings.js")
  params = _read("js/params.js")
  api = _read("js/api.js")

  # Settings fetches the exact same layout JSON + params API the original UI used.
  assert '/assets/components/tools/device_settings_layout.json?v=settings-tier-1' in settings or \
    '/assets/components/tools/device_settings_layout.json?v=settings-tier-1' in api
  assert '"/api/params/all"' in api
  assert '"/api/params"' in api
  assert '"/api/params/defaults"' in api


def test_ui_language_selector_uses_shared_device_language_setting():
  i18n = _read("js/i18n.js")
  selector = _read("js/components/LanguageSelector.js")
  settings = _read("js/views/Settings.js")

  for code in ["en", "es", "fr", "ko", "zh-CHS", "vi"]:
    assert f'value: "{code}"' in i18n
  assert "localStorage" in i18n
  assert "LanguageSetting" in selector
  assert "main_${next}" in selector
  assert "<LanguageSelector" in settings


def test_ui_ports_developer_mode_gating():
  params = _read("js/params.js")
  assert "countAdvancedHiddenByDeveloperMode" in params
  assert "isSettingVisible" in params
  assert "isAdvancedHiddenByDeveloperMode" in params


def test_ui_exposes_gm_auto_hold_to_buick():
  params = _read("js/params.js")
  device_settings = (REPO_ROOT / "starpilot/system/the_galaxy/assets/components/tools/device_settings.js").read_text(encoding="utf-8")

  assert 'GMAutoHold: ["Buick", "Chevrolet", "Holden"]' in params
  assert 'GMAutoHold: ["Buick", "Chevrolet", "Holden"]' in device_settings


def test_ui_restores_hierarchical_sub_toggle_rendering():
  # Children must nest under parents via the recursive SettingTree, gated on
  # the parent being enabled AND expanded (classic renderSettingTree contract).
  params = _read("js/params.js")
  settings = _read("js/views/Settings.js")
  tree = _read("js/components/SettingTree.js")

  assert "buildRenderTree" in params
  assert "isParamEnabledForChildren" in params
  assert "hasChildParams" in params

  assert "SettingTree" in settings
  assert '<SettingTree :params="ordinaryParams(activeSection)"' in settings
  assert '<LongitudinalMode v-if="modeSection(activeSection)"' in settings
  assert 's.params.filter(p => !this.isModeParam(p))' in settings

  # SettingTree recursively reveals children; subpanels are collapsed by default
  # (classic Galaxy behavior) and expand only when the user taps Manage/Close.
  assert "showChildren(p)" in tree
  assert "enabledForChildren(p) && this.isExpanded(p)" in tree
  assert "gx-tree-node--child" in tree
  assert "gx-collapse" in tree
  # It self-references so arbitrary nesting depth (grandchildren, etc.) works.
  assert "<SettingTree" in tree


def test_ui_ports_all_tool_views():
  # Endpoint usage may live in a view or in a reusable component it composes.
  checks = {
        "js/views/Recordings.js": ["/api/routes", "getRoutesStream", "getRouteLogs"],
    "js/views/Logs.js": ["getErrorLogs", "tmuxSnapshot"],
    "js/components/TroubleshootPanel.js": ["getTroubleshoot", "resetTroubleshootSection", "GalaxyConfirm"],
    "js/views/Tuning.js": ["LateralTuningPanel"],
    "js/views/Navigation.js": ["NavigationDestinationPanel", "MapsPanel", "NavigationKeysPanel"],
    "js/views/ToolEmbed.js": ["/manage_maps", "/manage_navigation_keys"],
    "js/views/SystemTools.js": [
      "backupToggles", "restoreToggles", "getToggleProfiles", "saveToggleProfile", "loadToggleProfile", "getUpdateBranches", "factoryReset",
    ],
    "js/components/WheelControls.js": ["getWheelControlsStatus"],
    "js/components/BluetoothPanel.js": ["getBluetoothStatus"],
  }
  for rel, endpoints in checks.items():
    src = _read(rel)
    assert src, f"missing file: {rel}"
    for ep in endpoints:
      assert ep in src, f"{rel} should use api.{ep}"
  vehicle = _read("js/views/Vehicle.js")
  bluetooth = _read("js/views/Bluetooth.js")
  assert "WheelControls" not in vehicle and "BluetoothPanel" not in vehicle and "carFeaturesCheck" in vehicle
  assert "BluetoothPanel" in bluetooth and "WheelControls" in bluetooth


def test_ui_routes_ported_views_natively_no_classic_fallback():
  app = _read("js/app.js")
  shell = _read("js/components/AppShell.js")
  tools = _read("js/views/Tools.js")

  for view in ["Recordings", "Logs", "Tuning", "Navigation", "Vehicle", "Bluetooth", "SystemTools"]:
    assert view in app, f"app.js should register {view}"

  # Ported routes must resolve natively in the Vue app (zero /classic redirect).
  for route in ["/recordings", "/logs", "/tuning", "/navigation", "/vehicle", "/bluetooth", "/system"]:
    assert route in shell, f"AppShell should route {route} natively"
    assert route in app, f"app.js should resolve {route} natively"
  # Tools grid routes the native categories (Recordings lives in the bottom nav
  # and is intentionally absent from the Tools page).
  for tool in ["/tuning", "/logs", "/navigation", "/vehicle", "/bluetooth", "/system"]:
    assert tool in tools, f"Tools grid should route {tool} natively"
  assert "/cameras" in tools, "Tools grid should route the camera hub natively"
  assert "/manage_v_asm" not in tools and "/manage_pip_sidecam" not in tools
  # Unmigrated tools are embedded (ToolEmbed), never a full-page redirect.
  assert "return ToolEmbed" in app
  assert "ToolEmbed" in app
  home = _read("js/views/Home.js")
  assert "GalaxyEmbed" not in home and 'src="/classic"' not in home
  assert "api.getStats()" in home and "keepRefreshing" in home and "usePolling" in home
  for section in ["Last drive", "This week", "Recent drives", "Personal records",
                  "Most used models", "Storage", "Vitals", "Software", "Your driving", "Your device"]:
    assert section in home, f"Home dashboard should include {section!r}"
  assert "setDriveStats" in _read("js/api.js")
  assert "fetch(" not in home.replace("api.", ""), "Home should not use raw fetch()"
  # Neither the shell, tools grid, nor home tiles ever redirect out of the UI.
  for src in [shell, tools, home, _read("js/views/ToolEmbed.js"), _read("js/store.js")]:
    assert "window.location.href" not in src


def test_ui_embeds_unmigrated_tools_inapp():
  # All page-in-page embeds go through the single shared GalaxyEmbed component.
  embed_comp = _read("js/components/GalaxyEmbed.js")
  assert "iframe" in embed_comp and "gx-embed__frame" in embed_comp
  tool = _read("js/views/ToolEmbed.js")
  assert "GalaxyEmbed" in tool and "forward-nav" in tool
  store = _read("js/store.js")
  assert "toolHref" in store and "/embed?src=" in store
  shell = _read("js/components/AppShell.js")
  assert "toolHref" in shell
  # Embed mode tags the iframe src so the classic SPA hides its sidebar/menu.
  assert "embedded=1" in embed_comp
  classic_index = (REPO_ROOT / "starpilot/system/the_galaxy/templates/index.html").read_text(encoding="utf-8")
  assert 'has("embedded")' in classic_index and 'classList.add("embedded")' in classic_index
  assert "window.self !== window.top" in classic_index


def test_ui_numeric_toggles_are_sliders_with_default():
  card = _read("js/components/GalaxyToggleCard.js")
  # All numeric params render as a slider (no more +/- stepper or bare 0 button).
  assert "isSlider() { return this.isNumeric }" in card
  assert 'type="range"' in card
  assert "onSliderInput" in card and "onSliderCommit" in card
  # A Default (reset-to-stock) button is present for numerics.
  assert "resetToDefault" in card
  # The stepper/number-input UI is gone.
  assert 'type="number"' not in card
  assert "−{{ stepLabel() }}" not in card
  assert 'title="Set to zero"' not in card


def test_ui_speed_units_follow_the_vehicle():
  params = _read("js/params.js")
  card = _read("js/components/GalaxyToggleCard.js")
  tree = _read("js/components/SettingTree.js")
  settings = _read("js/views/Settings.js")

  assert "resolveVehicleUnitParam" in params
  assert "formatNumericParamValue" in params
  assert "unit_search_terms" in params and "unit_search_terms" in settings
  assert "IsMetric" in params
  assert ':values="values"' in tree
  assert "displayParam" in card and "formatNumericParamValue" in card
  assert "sliderStepDisplay" in card and "Step:" in card
  assert ':values="values"' in settings
  assert "gx-unit-note" not in settings


def test_ui_centralizes_api_and_uses_composables():
  api = _read("js/api.js")
  composables = _read("js/composables.js")
  for view in ["Recordings", "Logs", "Tuning", "Navigation", "Vehicle", "SystemTools"]:
    src = _read(f"js/views/{view}.js")
    # Views must not issue raw fetch() / hand-rolled polling / SSE.
    assert "fetch(" not in src.replace("api.", ""), f"{view} should not use raw fetch()"

  # Shared polling + log streaming live in composables, not duplicated in views.
  assert "usePolling" in composables
  assert "useLogStream" in composables
  assert "usePolling" in _read("js/components/ManeuverCard.js")
  assert "usePolling" in _read("js/components/WheelControls.js")
  assert "useLogStream" in _read("js/views/Logs.js")


def test_ui_schema_driven_param_engine_reused():
  tuning = _read("js/views/Tuning.js")
  assert "GalaxyEmbed" not in tuning and 'src="/tuning"' not in tuning, "Tuning must be native, not a classic embed"
  assert "LateralTuningPanel" in tuning and "LongitudinalManeuvers" not in tuning
  vehicle = _read("js/views/Vehicle.js")
  bluetooth = _read("js/views/Bluetooth.js")
  assert "ParamSections" not in vehicle, "Vehicle must not render redundant toggles"
  assert "WheelControls" not in vehicle and "BluetoothPanel" not in vehicle
  assert "GalaxySection" in vehicle
  assert "WheelControls" in bluetooth and "BluetoothPanel" in bluetooth
  assert bluetooth.index('bluetooth: "Bluetooth"') < bluetooth.index('controllers: "Controllers"')
  assert 'useTabRouting("/bluetooth"' in bluetooth
  engine = _read("js/components/ParamSections.js")
  assert "SettingTree" in engine
  assert "isSettingVisible" in engine


def test_ui_eliminates_slider_toggle_flicker():
  card = _read("js/components/GalaxyToggleCard.js")
  css = _read("css/material.css")

  # Optimistic local preview + interacting guard. The value only commits when
  # the drag/keyboard interaction is RELEASED, so holding never drops it.
  assert "preview" in card
  assert "interacting" in card
  assert "onSliderCommit" in card
  assert "flushSlider" in card
  # No mid-drag auto-commit timer: holding still must NOT release/lock or
  # commit. A hold may only switch the slider into fine scrubbing.
  assert "commitTimer" not in card
  hold = card[card.index("startHoldTimer"):card.index("activateFineScrub")]
  assert "flushSlider" not in hold and "commit" not in hold
  # Release (change) and blur (keyboard) both flush the commit.
  assert "interacting = false" in card
  assert "onSliderBlur" in card
  assert "@blur=\"onSliderBlur\"" in card
  assert "currentValue() { return this.preview !== undefined ? this.preview : this.value }" in card

  # Manage/Close affordance for parent toggles with nested children.
  assert "manageable" in card
  assert "manageOpen" in card
  assert "gx-manage-btn" in card

  # Nested indentation / expand animation styling present.
  assert ".gx-tree-node--child" in css
  assert ".gx-manage-btn" in css
  assert ".gx-tree-children" in css


def test_ui_developer_mode_banner_offers_unlock():
  banner = _read("js/components/DevModeBanner.js")
  assert "Go to Developer Tab" in banner
  assert 'navigate("/settings/developer")' in banner
  assert "advanced setting" in banner


def test_ui_has_bottom_navigation_and_drawer():
  shell = _read("js/components/AppShell.js")
  assert "blur-nav" in shell
  assert "nav-item" in shell
  assert "gx-appbar__menu" in shell
  assert "gx-back-btn" in shell
  assert "goBack" in shell or "back()" in shell
  assert "gx-drawer" in shell
  assert "gx-appbar" in shell
  assert "Search toggles" in shell
  assert ">Galaxy</span>" in shell
  assert "gx-appbar__home" in shell
  assert "goHome" in shell and 'navigate("/")' in shell


def test_ui_search_visible_on_mobile_and_content_full_width():
  css = _read("css/material.css")
  # Search must NOT be hidden on mobile (regression: it was display:none <600px).
  assert ".gx-appbar__search" in css
  # A single breakpoint picks mobile (bottom nav + back) vs desktop (drawer).
  assert ".blur-nav { display: flex; }" in css
  assert ".gx-appbar__menu { display: inline-flex; }" in css
  assert ".gx-back-btn { display: inline-flex; }" in css
  # Content + embedded tools fill the available width (no 760px cap).
  assert "max-width: none" in css
  # Blur nav styling is present.
  assert "--glass-bg" in css
  assert "backdrop-filter" in css


def test_ui_blur_nav_single_breakpoint_no_dual_nav():
  css = _read("css/material.css")
  assert ".blur-nav" in css
  assert "@media (min-width: 768px)" in css
  assert ".blur-nav { display: none; }" in css
  assert ".gx-back-btn { display: none; }" in css
  assert ".gx-appbar__menu { display: inline-flex; }" in css


def test_ui_settings_deep_links_and_dev_mode_updates():
  settings = _read("js/views/Settings.js")
  # Route is reactive via a computed (not the broken this.store watch), so a
  # Developer-Mode navigation updates the page without a refresh.
  assert "route() { return store.route }" in settings
  assert "route() { this.applyRouteSection() }" in settings
  # Selecting a section + opening a subpanel update the URL (deep-linkable).
  assert 'navigate("/settings/" + slug)' in settings
  assert "?open=" in settings
  # Turning Developer Mode on reloads data so newly-visible sections (e.g.
  # Favorites) render immediately.
  assert "devModeOn() { this.load() }" in settings


def test_ui_galaxy_background_is_css_only_and_lightweight():
  index = _read("index.html")
  css = _read("css/material.css")
  # Background is a single fixed layer, pure CSS (no canvas/WebGL/images).
  assert 'id="galaxy-bg"' in index
  assert "#galaxy-bg" in css
  assert "position: fixed" in css
  assert "radial-gradient" in css
  # Cheap GPU-friendly transforms + stars; disabled for reduced-motion.
  assert "--gx-para-x" in css
  assert "prefers-reduced-motion" in css
  # App content sits above the background layer.
  assert ".gx-app" in css and "z-index: 1" in css


def test_galaxy_py_serves_classic_at_root_and_new_ui_at_mobile():
  source = GALAXY_PY.read_text(encoding="utf-8")
  assert '@app.route("/", methods=["GET"])' in source
  assert 'render_template("index.html")' in source
  assert 'params.get_bool("GalaxyMobileDefault")' in source
  # Classic also stays reachable at /classic (page-in-page embed target).
  assert '@app.route("/classic", methods=["GET"])' in source
  # The modern Vue UI is served at /mobile, not the root.
  assert '@app.route("/mobile", methods=["GET"])' in source
  assert 'Path(app.static_folder) / "mobile" / "index.html"' in source
  assert '@app.route("/ui", methods=["GET"])' not in source


def test_ui_manifest_is_valid_pwa_manifest():
  manifest = json.loads((UI_ROOT / "manifest.json").read_text(encoding="utf-8"))
  assert manifest["display"] == "standalone"
  assert manifest["name"] == "Galaxy"
  assert manifest["short_name"] == "Galaxy"
  assert manifest["icons"]
  assert "start_url" not in manifest


def test_ui_ported_classic_tools_native_no_embed():
  app = _read("js/app.js")
  store = _read("js/store.js")
  api = _read("js/api.js")

  assert "Doors" in app and "Galaxy" in app and "Tsk" in app
  for route in ["/manage_doors", "/galaxy", "/manage_tsk"]:
    assert route in app, f"app.js should register {route}"
    assert route in store, f"store NATIVE_ROOTS should include {route}"

  doors = _read("js/views/Doors.js")
  galaxy = _read("js/views/Galaxy.js")
  tsk = _read("js/views/Tsk.js")
  assert "GalaxyEmbed" not in doors and "fetch(" not in doors
  assert "api.postAction" in doors and "showSnackbar" in doors
  assert "GalaxyEmbed" not in galaxy and "fetch(" not in galaxy
  assert "api.getGalaxyStatus" in galaxy and "api.galaxyPair" in galaxy and "api.galaxyUnpair" in galaxy
  assert "GalaxyConfirm" in galaxy  # destructive unpair is confirmed
  assert "GalaxyEmbed" not in tsk and "fetch(" not in tsk
  assert "api.getTskKeys" in tsk and "api.saveTskKeys" in tsk and "api.deleteTskKey" in tsk and "api.tskKeySet" in tsk

  # New shared API surface added for the ported pages.
  for method in ["getGalaxyStatus", "galaxyPair", "galaxyUnpair",
                 "getSpeedLimitsStatus", "processSpeedLimits",
                 "getTskKeys", "saveTskKeys", "deleteTskKey", "tskKeySet",
                 "resetTroubleshootSection"]:
    assert method in api, f"api.js should expose {method}"

  # Troubleshoot is now a native panel inside the Logs view (no embed).
  logs = _read("js/views/Logs.js")
  assert "GalaxyEmbed" not in logs
  assert "TroubleshootPanel" in logs
  panel = _read("js/components/TroubleshootPanel.js")
  assert "getTroubleshoot" in panel and "resetTroubleshootSection" in panel
  assert "fetch(" not in panel

  # Speed limits panel is native and reused by the Navigation speeds tab.
  nav = _read("js/views/Navigation.js")
  assert "SpeedLimitsPanel" in nav
  nav_speed = nav
  assert 'src="/download_speed_limits"' not in nav_speed
  panel = _read("js/components/SpeedLimitsPanel.js")
  assert "getSpeedLimitsStatus" in panel and "processSpeedLimits" in panel
  assert "usePolling" in panel
  assert "fetch(" not in panel


def test_ui_all_remaining_classic_tools_native_no_embed():
  app = _read("js/app.js")
  store = _read("js/store.js")
  api = _read("js/api.js")

  # Standalone native views + their routes.
  native = {
    "/manage_models": "ModelManager",
    "/plots": "Plots",
    "/testing_ground": "TestingGround",
    "/theme_maker": "ThemeMaker",
  }
  for route, view in native.items():
    assert view in app, f"app.js should register {view}"
    assert route in app, f"app.js should resolve {route}"
    assert route in store, f"store NATIVE_ROOTS should include {route}"
    src = _read(f"js/views/{view}.js")
    assert src, f"missing view: {view}"
    assert "GalaxyEmbed" not in src and "fetch(" not in src, f"{view} should be native with no raw fetch"

  assert '"/sentry": Cameras' in app
  sentry = _read("js/views/Sentry.js")
  assert "GalaxyEmbed" not in sentry and "fetch(" not in sentry
  assert "selectedImage" in sentry and "openImage" in sentry and "closeImage" in sentry
  assert "GalaxySheet" in sentry
  assert 'scrim-class="gx-scrim--image-viewer"' in sentry
  assert 'target="_blank"' not in sentry

  # Navigation maps + App Keys and Tuning lateral are native tabs now.
  nav = _read("js/views/Navigation.js")
  assert "GalaxyEmbed" not in nav and "MapsPanel" in nav and "NavigationKeysPanel" in nav
  tuning = _read("js/views/Tuning.js")
  assert "GalaxyEmbed" not in tuning and "LateralTuningPanel" in tuning
  assert _read("js/components/MapsPanel.js") and _read("js/components/NavigationKeysPanel.js")
  destination = _read("js/components/NavigationDestinationPanel.js")
  assert '"./views/Navigation.js?v=nav-destination-6"' in _read("js/app.js")
  assert '"../components/NavigationDestinationPanel.js?v=nav-destination-6"' in _read("js/views/Navigation.js")
  assert "mapboxSuggest" in destination and "mapboxRetrieve" in destination
  assert "mapboxGeocode" in destination and "mapboxDirections" in destination
  assert "ref=\"map\"" in destination and "setNavigation(this.destination)" in destination
  assert destination.count("methods: {") == 1 and "secondaryLabel," in destination
  assert _read("js/components/LateralTuningPanel.js")
  # Shared API surface added for the second batch of ported pages.
  for method in ["selectTestingGround",
                 "getSentryStatus", "getSentryEvents", "deleteSentryEvent", "sentryPushSubscribe",
                 "getModelStatus", "setActiveModel", "startModelDownload", "downloadAllModels", "deleteModel", "saveModelPreferences",
                 "getPlotsLive",
                 "getGalaxySession", "deleteNavigationKey",
                 "getThemeList", "saveTheme", "applyTheme", "deleteTheme", "downloadTheme",
                 "getFlmStatus", "flmAnalyze", "flmApplyTrial", "flmRevertTrial", "flmSaveFeedback",
                 "flmSaveTune", "flmApplySavedTune", "flmRenameSavedTune", "flmDeleteSavedTune", "flmSubmitTune"]:
    assert method in api, f"api.js should expose {method}"

  # Panels used by the native tabs expose no raw fetch.
  for rel in ["js/components/MapsPanel.js", "js/components/NavigationKeysPanel.js",
              "js/components/LateralTuningPanel.js"]:
    assert "fetch(" not in _read(rel), f"{rel} should not use raw fetch()"

  lateral = _read("js/components/LateralTuningPanel.js")
  modal = _read("js/components/GalaxyModal.js")
  assert "MAX_SEGMENTS = 5" in lateral
  assert "segmentRanges" in lateral and "selectedSegmentRanges" in lateral
  assert "flmAnalyze(this.selectedRoutes, this.selectedSegmentRanges())" in lateral
  assert "routeSelectedSegmentCount" in lateral
  assert "GalaxyPrompt" in lateral
  assert "renameSavedTune(tune)" in lateral
  assert "initialValue: tune.name" in lateral
  assert "export function GalaxyPrompt" in modal
  assert "inputRequired" in modal
  assert lateral.index('>Workspace status</span>') < lateral.index('>Saved Tunes</span>')
  assert lateral.index('>Saved Tunes</span>') < lateral.index('>Local Routes</span>')


def test_navigation_requires_secret_key_before_starting_on_device_route():
  destination = _read("js/components/NavigationDestinationPanel.js")
  classic_destination = (REPO_ROOT / "starpilot/system/the_galaxy/assets/components/navigation/navigation_destination.js").read_text(encoding="utf-8")

  assert 'mapboxSecret: ""' in destination
  assert "hasRoutingKey()" in destination
  assert "!query.trim() || !hasRoutingKey" in destination
  assert "loadingRoute || !hasRoutingKey" in destination
  assert "required for the comma to calculate the on-device route" in destination
  assert "secret key lets your comma calculate the on-device route" in classic_destination
  assert "if (!response.ok)" in classic_destination
  assert 'result.message || "Failed to start navigation."' in classic_destination


def test_ui_cameras_hub_vasm_and_pip_native_no_embed():
  app = _read("js/app.js")
  store = _read("js/store.js")
  api = _read("js/api.js")

  cameras = _read("js/views/Cameras.js")
  assert cameras
  assert "/cameras" in app and "Cameras" in app, "app.js should register the camera hub"
  assert "/cameras" in store, "store NATIVE_ROOTS should include /cameras"
  assert "GalaxyEmbed" not in cameras and "fetch(" not in cameras
  assert "Sentry" in cameras and "Vasm" in cameras and "Pip" in cameras, "camera hub should embed Sentry, V-ASM, and PiP"
  assert "GalaxyTabs" in cameras
  assert cameras.index('sentry: "Sentry Mode"') < cameras.index('vasm: "V-ASM Spot Monitor"')

  # Removed standalone pages are no longer routed or listed as native roots.
  for route in ["/manage_v_asm", "/manage_pip_sidecam"]:
    assert route not in app, f"{route} standalone page should be removed from app.js"
    assert route not in store, f"{route} should be removed from NATIVE_ROOTS"

  vasm = _read("js/views/Vasm.js")
  assert "api.getVasmSnapshotBlob" in vasm and "api.deleteVasmConfig" in vasm and "api.getMemoryParam" in vasm
  assert "GalaxyConfirm" in vasm  # destructive delete is confirmed
  assert "GalaxyEmbed" not in vasm and "fetch(" not in vasm
  pip = _read("js/views/Pip.js")
  assert "api.pipSnapshotSource" in pip and "api.deletePipConfig" in pip and "api.setPipConfig" in pip
  assert "GalaxyEmbed" not in pip and "fetch(" not in pip

  for method in ["getVasmSnapshotBlob", "deleteVasmConfig", "getMemoryParam",
                 "pipSnapshotSource", "deletePipConfig"]:
    assert method in api, f"api.js should expose {method}"


def test_ui_mobile_polish_regressions():
  system = _read("js/views/SystemTools.js")
  css = _read("css/material.css")
  assert 'button v-if="updateAvailable"' in system
  assert "checkedForUpdates && !!this.fastStatus?.updateAvailable" in system
  assert "gx-update-progress__fill" in system
  assert "linear-gradient(90deg, #5ec8c8 0%, #8b6cc5 100%)" in css
  assert "Automatically Install Updates" in system
  assert 'key: "AutomaticUpdates"' in system
  assert "!!fastStatus?.automaticUpdates" in system
  assert "isOnroad || autoUpdateBusy || updateInProgress" in system
  assert "getGatewayDevices" in _read("js/api.js")
  assert "rebootStorageScope" in system
  assert "writeRebootMarker(this.rebootStorageScope" in system
  assert "clearRebootMarker(this.rebootStorageScope" in system

  bluetooth = _read("js/components/BluetoothPanel.js")
  assert "methods: {\n    address," in bluetooth

  logs = _read("js/views/Logs.js")
  assert logs.index('troubleshoot: "Troubleshoot"') < logs.index('errors: "Error Logs"') < logs.index('tmux: "Tmux Live Log"')
  troubleshoot = _read("js/components/TroubleshootPanel.js")
  assert "gx-diagnostic-row--changed" in troubleshoot and ".gx-row.gx-diagnostic-row--changed" in css

  recordings = _read("js/views/Recordings.js")
  galaxy = _read("js/views/Galaxy.js")
  assert "bandwidth reasons" in recordings and "status?.lanIp" in recordings
  assert "status?.lanIp" in galaxy
  assert ':href="localUrl"' in recordings and ':href="localUrl"' in galaxy
  assert 'localDeviceUrl(status?.lanIp, "/recordings")' in recordings
  assert 'localDeviceUrl(status?.lanIp, "/galaxy")' in galaxy
  assert "gx-btn gx-btn--tonal" in recordings and "Open Recordings Locally" in recordings
  assert "screenDisplayName(rec) {\n      if (!rec) return" in recordings
  assert "gx-btn gx-btn--tonal" in galaxy and "Open Galaxy Locally" in galaxy

  home = _read("js/views/Home.js")
  home_css = _read("css/home.css")
  assert "backgroundImage: modelView.style" in home
  assert "display: flex" in home_css and "flex-direction: column" in home_css

  tuning = _read("js/views/Tuning.js")
  classic_sidebar = (REPO_ROOT / "starpilot/system/the_galaxy/assets/components/sidebar.js").read_text(encoding="utf-8")
  c4_developer = (REPO_ROOT / "selfdrive/ui/layouts/settings/developer.py").read_text(encoding="utf-8")
  assert "LongitudinalManeuvers" not in tuning and "Long Maneuvers" not in classic_sidebar
  assert 'tr("Longitudinal Maneuver Mode")' not in c4_developer


@pytest.mark.parametrize("native_scrollend", [False, True])
def test_scroll_coordinator_gesture_lifecycle(native_scrollend):
  node = _node_exe()
  if node is None:
    pytest.skip("no node.js runtime available")
  script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const native = process.argv[2] === 'true';
const source = fs.readFileSync(process.argv[1], 'utf8');
const handlers = { document: {}, window: {} };
const classes = new Set();
const timers = new Map();
let nextTimer = 0;
const window = {
  scrollY: 0,
  addEventListener: (name, fn) => { handlers.window[name] = fn; },
};
class Element {
  constructor(modal = false) { this.modal = modal; }
  closest() { return this.modal ? this : null; }
}
const document = {
  body: { classList: {
    contains: k => classes.has(k), add: k => classes.add(k), remove: k => classes.delete(k),
  } },
  addEventListener: (name, fn) => { handlers.document[name] = fn; },
};
if (native) document.onscrollend = null;
vm.runInNewContext(source.slice(source.indexOf('// Disable card blur'), source.indexOf('// Layer 2')), {
  document, Element,
  window,
  setTimeout: fn => { timers.set(++nextTimer, fn); return nextTimer; },
  clearTimeout: id => timers.delete(id),
});
const fire = (scope, name, ids = [], modal = false) => handlers[scope][name]?.({
  target: new Element(modal), changedTouches: ids.map(identifier => ({ identifier })),
});
const tick = () => { const pending = [...timers.values()]; timers.clear(); pending.forEach(fn => fn()); };
const active = () => classes.has('is-scrolling');
// Inertial movement can continue between delivered scroll events after release.
fire('window', 'touchstart', [10]);
fire('document', 'scroll');
fire('window', 'touchend', [10]);
if (native) fire('document', 'scrollend');
window.scrollY = 100;
tick();
assert.equal(active(), true, 'finger release must not end momentum scrolling');
window.scrollY = 160;
tick();
assert.equal(active(), true, 'continued movement must keep blur disabled');
tick();
assert.equal(active(), false, 'restore after completion and a stable position');
fire('document', 'scrollend');
fire('window', 'wheel');
assert.equal(active(), false, 'wheel without document movement');
fire('window', 'touchstart', [1, 2]);
fire('document', 'scroll');
fire('window', 'touchend', [1]);
fire('window', 'touchstart', [3], true);
fire('window', 'touchend', [3], true);
tick();
assert.equal(active(), true, 'remaining page finger holds blur disabled');
fire('window', 'touchcancel', [2]);
tick();
assert.equal(active(), native, 'native completion must be authoritative');
fire('document', 'scrollend');
tick();
assert.equal(active(), false);
// More scroll events after a completion signal invalidate its pending restore.
fire('document', 'scroll');
fire('document', 'scrollend');
fire('document', 'scroll');
tick();
assert.equal(active(), native, 'new scrolling cancels the previous native completion');
fire('document', 'scrollend');
tick();
assert.equal(active(), false);
fire('window', 'touchstart', [4]);
fire('document', 'scroll');
fire('document', 'scrollend');
assert.equal(active(), true, 'hold survives completion');
fire('window', 'touchend', [4]);
tick();
assert.equal(active(), false, 'release after completion cannot leave state stuck');
fire('document', 'scroll');
fire('window', 'hashchange');
tick();
assert.equal(active(), false, 'navigation clears pending gesture');
fire('document', 'scroll');
tick();
assert.equal(active(), native, 'fallback only on unsupported browsers');
fire('document', 'scrollend');
tick();
assert.equal(active(), false);
"""
  result = subprocess.run(
    [node, "-e", script, str(UI_ROOT / "js/app.js"), str(native_scrollend).lower()],
    capture_output=True, text=True,
  )
  assert result.returncode == 0, result.stdout + result.stderr


def _node_exe():
  candidates = [
    shutil.which("node"),
    "/mnt/c/Program Files/nodejs/node.exe",
  ]
  for path in candidates:
    if path and Path(path).exists():
      return path
  return None


@pytest.mark.skipif(_node_exe() is None, reason="no node.js runtime available")
@pytest.mark.parametrize("language", ["vi", "en"])
def test_confirmation_localizes_destructive_warning_and_preserves_actions(tmp_path, language):
  # Exercise the actual dictionary and dialog renderer; only the DOM mount is stubbed.
  vue_src = (UI_ROOT.parent / "vendor/vue/vue.esm-browser.js").read_text(encoding="utf-8")
  (tmp_path / "vue.mjs").write_text(vue_src, encoding="utf-8")
  (tmp_path / "i18n.mjs").write_text(
    _read("js/i18n.js").replace('from "vue"', 'from "./vue.mjs"'), encoding="utf-8",
  )
  (tmp_path / "modal.mjs").write_text(
    _read("js/components/GalaxyModal.js").replace('from "../i18n.js"', 'from "./i18n.mjs"'), encoding="utf-8",
  )
  script = tmp_path / "confirmation.mjs"
  script.write_text(
    r"""
import assert from "node:assert/strict"
import { h } from "./vue.mjs"
import { setLanguage, t } from "./i18n.mjs"
import { GalaxyConfirm } from "./modal.mjs"

let rendered
let unmounts = 0
const hosts = new Set()
globalThis.document = {
  documentElement: {},
  getElementById: () => null,
  createElement: () => ({ remove() { hosts.delete(this) } }),
  body: { appendChild(host) { hosts.add(host) } },
}
globalThis.window = { __galaxyVue: {
  h,
  createApp: component => ({
    mount() { rendered = component.render() },
    unmount() { unmounts++ },
  }),
} }

const language = process.argv[2]
setLanguage(language)
const pending = GalaxyConfirm({
  title: "Factory reset (SAVE ME)?",
  message: "This wipes params, backups, themes, models, maps, and route data, then reboots. This cannot be undone.",
  confirmLabel: "Factory Reset",
  danger: true,
})
const props = rendered.props
assert.equal(props.title, language === "vi" ? "Khôi phục cài đặt gốc (SAVE ME)?" : "Factory reset (SAVE ME)?")
assert.equal(props.message, language === "vi"
  ? "Thao tác này sẽ xóa tham số, bản sao lưu, giao diện, mô hình, bản đồ và dữ liệu hành trình, sau đó khởi động lại thiết bị. Không thể hoàn tác."
  : "This wipes params, backups, themes, models, maps, and route data, then reboots. This cannot be undone.")
assert.equal(props.confirmLabel, language === "vi" ? "Khôi phục cài đặt gốc" : "Factory Reset")
assert.equal(props.cancelLabel, language === "vi" ? "Hủy" : "Cancel")
assert.equal(props.danger, true)
props.onConfirm()
props["onUpdate:modelValue"](false)
assert.equal(await pending, true)
assert.equal(hosts.size, 0)
assert.equal(unmounts, 1)

// Callers such as Home can already pass translated labels.
const cancelled = GalaxyConfirm({ title: t("Ignore this drive's statistics?"), confirmLabel: t("Ignore"), cancelLabel: t("Cancel") })
assert.equal(rendered.props.title, language === "vi" ? "Bỏ chuyến này khỏi thống kê?" : "Ignore this drive's statistics?")
assert.equal(rendered.props.confirmLabel, language === "vi" ? "Bỏ qua" : "Ignore")
assert.equal(rendered.props.cancelLabel, language === "vi" ? "Hủy" : "Cancel")
rendered.props.onCancel()
assert.equal(await cancelled, false)
assert.equal(hosts.size, 0)

const defaults = GalaxyConfirm({ title: "Custom untranslated heading" })
assert.equal(rendered.props.title, "Custom untranslated heading")
assert.equal(rendered.props.confirmLabel, language === "vi" ? "Xác nhận" : "Confirm")
rendered.props["onUpdate:modelValue"](false)
assert.equal(await defaults, false)
assert.equal(hosts.size, 0)

const empty = GalaxyConfirm()
assert.equal(rendered.props.title, undefined, "preserve GalaxyModal's default title")
assert.equal(rendered.props.message, "")
rendered.props.onCancel()
assert.equal(await empty, false)
assert.equal(hosts.size, 0)
""",
    encoding="utf-8",
  )
  result = subprocess.run([_node_exe(), str(script), language], capture_output=True, text=True)
  assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(_node_exe() is None, reason="no node.js runtime available")
def test_ui_ported_param_logic_runs_and_passes(tmp_path):
  node = _node_exe()
  params_src = _read("js/params.js")
  params_dst = tmp_path / "params.mjs"
  params_dst.write_text(params_src, encoding="utf-8")

  script = tmp_path / "pcheck.mjs"
  script.write_text(
    """
import * as P from "./params.mjs"
const sec = { name: "Lateral (Steering)", params: [
  { key: "AlwaysOnLateral", settings_tier: "simple", data_type: "bool" },
  { key: "VASMEnabled", settings_tier: "advanced", data_type: "bool" },
]}
const assert = (cond, msg) => { if (!cond) throw new Error("FAIL: " + msg) }
assert(P.isSettingVisible(sec, sec.params[0], {}) === true, "simple visible (off)")
assert(P.isSettingVisible(sec, sec.params[1], {}) === false, "advanced hidden (off)")
assert(P.isSettingVisible(sec, sec.params[1], { GalaxyDeveloperMode: true }) === true, "advanced visible (on)")
assert(P.countAdvancedHiddenByDeveloperMode([sec], {}) === 1, "count hidden (off)")
assert(P.countAdvancedHiddenByDeveloperMode([sec], { GalaxyDeveloperMode: true }) === 0, "count hidden (on)")
const developer = { name: "Developer", params: [] }
const clusterOffset = { key: "ClusterOffset", parent_key: "GalaxyDeveloperMode", settings_tier: "advanced", data_type: "float" }
assert(P.isVehicleSettingVisible(developer, clusterOffset, { CarMake: "gm" }) === true, "cluster offset has no vehicle filter")
assert(P.isSettingVisible(developer, clusterOffset, { CarMake: "gm" }) === false, "cluster offset hidden without developer mode")
assert(P.isSettingVisible(developer, clusterOffset, { CarMake: "gm", GalaxyDeveloperMode: true }) === true, "cluster offset visible in developer mode")
const slider = { key: "DeviceShutdown", data_type: "int", min: 1, max: 30, step: 1 }
assert(P.snapNumericToBoundsAndStep(17.9, P.numericBounds(slider, {}), 0) === 18, "snap")
assert(P.formatSliderValue(6, "1", 0, "DeviceShutdown") === "6 hours", "format")
const speed = {
  key: "Offset2", data_type: "float", unit_type: "vehicle_speed",
  min: -99, max: 99, step: 1, precision: 0,
  metric_min: -150, metric_max: 150, unit_range_index: 1,
}
const imperialSpeed = P.resolveVehicleUnitParam(speed, { IsMetric: false })
assert(imperialSpeed.unit === " mph", "imperial unit")
assert(imperialSpeed.label === "Speed Offset (25–34 mph)", "imperial offset band")
assert(P.formatNumericParamValue(speed, 3, { IsMetric: false }) === "3 mph", "imperial value")
const metricSpeed = P.resolveVehicleUnitParam(speed, { IsMetric: true })
assert(metricSpeed.unit === " km/h", "metric unit")
assert(metricSpeed.label === "Speed Offset (30–49 km/h)", "metric offset band")
assert(metricSpeed.unit_search_terms.includes("metric"), "metric settings are searchable")
assert(P.numericBounds(speed, { IsMetric: true }).max === 150, "metric bounds")
assert(P.numericBounds(speed, { IsMetric: true }).step === 1, "one km/h per step")
assert(P.formatNumericParamValue(speed, 3, { IsMetric: true }) === "3 km/h", "metric value")
assert(P.usesMetricUnits({ IsMetric: "1" }) === true, "serialized metric bool")
const laneOffset = { key: "LaneCenterOffset", data_type: "float", min: 0, max: 0.3, step: 0.01 }
const laneBounds = P.numericBounds(laneOffset, {})
assert(laneBounds.min === -0.3, "lane offset keeps signed lower bound")
assert(P.snapNumericToBoundsAndStep(-0.01, laneBounds, 2) === -0.01, "lane offset snaps below zero")
console.log("params.js logic OK")
""",
    encoding="utf-8",
  )

  result = subprocess.run([node, str(script)], capture_output=True, text=True)
  assert result.returncode == 0, f"node failed:\n{result.stdout}\n{result.stderr}"


@pytest.mark.skipif(_node_exe() is None, reason="no node.js runtime available")
def test_ui_hierarchical_tree_logic_runs_and_passes(tmp_path):
  node = _node_exe()
  params_src = _read("js/params.js")
  (tmp_path / "params.mjs").write_text(params_src, encoding="utf-8")

  script = tmp_path / "treecheck.mjs"
  script.write_text(
    """
import * as P from "./params.mjs"
const params = [
  { key: "Parent", ui_type: "toggle" },
  { key: "Child", parent_key: "Parent", ui_type: "toggle" },
  { key: "GrandChild", parent_key: "Child", ui_type: "toggle" },
  { key: "Sibling", ui_type: "toggle" },
]
const assert = (cond, msg) => { if (!cond) throw new Error("FAIL: " + msg) }

// Parent off -> no children rendered.
let tree = P.buildRenderTree(params, {}, {})
assert(tree.length === 2, "parent off shows only roots, got " + tree.length)
assert(tree.map(t => t.param.key).join(",") === "Parent,Sibling", "parent off keys")

// Parent on but collapsed -> still no children.
tree = P.buildRenderTree(params, { Parent: true }, {})
assert(tree.length === 2, "parent on but collapsed hides children")

// Parent on + expanded -> children, but grandchild hidden until Child expanded.
tree = P.buildRenderTree(params, { Parent: true }, { Parent: true })
assert(tree.map(t => t.param.key).join(",") === "Parent,Child,Sibling", "one level deep")
assert(tree[1].depth === 1, "child depth is 1")

// Full expansion -> grandchild at depth 2.
tree = P.buildRenderTree(params, { Parent: true, Child: true }, { Parent: true, Child: true })
const gc = tree.find(t => t.param.key === "GrandChild")
assert(gc && gc.depth === 2, "grandchild nested at depth 2")
assert(tree[0].depth === 0, "root depth is 0")

assert(P.isParamEnabledForChildren({ key: "X" }, { X: true }) === true, "enabled when true")
assert(P.isParamEnabledForChildren({ key: "X" }, { X: false }) === false, "disabled when false")
assert(P.isParamEnabledForChildren({ ui_type: "group" }, {}) === true, "group always enabled")
assert(P.hasChildParams(params, "Parent") === true, "hasChildParams true")
assert(P.hasChildParams(params, "Sibling") === false, "hasChildParams false")
console.log("hierarchy logic OK")
""",
    encoding="utf-8",
  )

  result = subprocess.run([node, str(script)], capture_output=True, text=True)
  assert result.returncode == 0, f"node failed:\n{result.stdout}\n{result.stderr}"


def test_ui_navigation_map_first_layout_regressions():
  nav = _read("js/views/Navigation.js")
  css = _read("css/material.css")
  destination = _read("js/components/NavigationDestinationPanel.js")

  # Destination tab is a map-first screen: the map fills the content area and
  # runs behind the top appbar and the tab switcher floats above the bottom nav.
  assert 'class="gx-navigation-view"' in nav and "gx-navigation-tabs" in nav
  assert ".gx-content:has(.gx-navigation-view)" in css
  assert "--bottomnav-clearance:" in css
  assert "margin-top: calc(-1 * (var(--appbar-height) + env(safe-area-inset-top, 0px) + var(--sp-1)))" in css
  assert "height: 100dvh" in css
  assert "padding-top: calc(var(--appbar-height) + env(safe-area-inset-top, 0px) + var(--sp-4))" in css
  assert ".gx-navigation-tabs {" in css
  assert "bottom: var(--bottomnav-clearance)" in css
  assert "padding-bottom: calc(var(--bottomnav-clearance) + 56px)" in css

  # Route metrics use Bootstrap Icons consistently, not emoji.
  assert "bi-signpost-2" in destination and "bi-clock-history" in destination
  for emoji in ("🛣️", "⌛", "🕗"):
    assert emoji not in destination

  # The route summary stays compact so the map remains visible: metrics are a
  # horizontal strip and route options are compact chips, not full-width rows.
  assert 'class="gx-navigation-metrics"' in destination
  assert destination.count('class="gx-navigation-metric"') == 3
  assert "gx-navigation-summary__rows" not in destination and "gx-navigation-summary__rows" not in css
  assert "grid-template-columns: repeat(3, minmax(0, 1fr))" in css
