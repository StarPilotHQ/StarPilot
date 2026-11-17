"""Compact Small/Chestnut model selection and catalog management."""

from collections.abc import Callable
import time
from typing import Protocol

from openpilot.starpilot.ui.feature_settings_state import FeatureSettingsState
from openpilot.starpilot.ui.models_state import (big_none_label, model_action_allowed, model_catalog_rows, model_needs_gpu, model_request,
                                                model_display_text as _display_text)
from openpilot.system.ui.lib.application import gui_app
from openpilot.system.ui.widgets.scroller import NavScroller
from openpilot.selfdrive.ui.mici.widgets.button import BigButton, GreyBigButton
from openpilot.selfdrive.ui.mici.widgets.dialog import BigConfirmationDialog, BigDialog


def _grey_card(title: str, value: str) -> GreyBigButton:
  return GreyBigButton(_display_text(title), _display_text(value))


class ModelSession(Protocol):
  def model_snapshot(self) -> FeatureSettingsState: ...
  def model_manager_snapshot(self) -> dict: ...
  def model_manager_action(self, action: str, payload: dict) -> dict: ...


class _ModelsPage(NavScroller):
  def __init__(self, owner: "ModelsCompact", kind: str = "overview", profile: str = "", model_id: str = ""):
    super().__init__()
    self.owner, self.kind, self.profile, self.model_id = owner, kind, profile, model_id
    self.current: FeatureSettingsState | None = None
    self.manager: dict | None = None
    self.next_refresh = 0.0
    self.sort_mode = "date"

  def render(self, rect=None):
    self.owner.refresh_visible(self)
    return super().render(rect)


class ModelsCompact:
  REFRESH_SECONDS = 0.5
  SORTS = {"date": "newest first", "date_oldest": "oldest first", "name": "alphabetical",
           "favorites": "your favorites", "community": "community picks"}

  def __init__(self, session: ModelSession, clock: Callable[[], float] = time.monotonic):
    self.session = session
    self.clock = clock
    self.catalog_pages: dict[str, _ModelsPage] = {}

  def entry_button(self) -> BigButton:
    button = BigButton("driving model", "small, chestnut and downloads")
    button.set_click_callback(self.open)
    return button

  def _snapshot(self) -> dict | None:
    try:
      data = self.session.model_manager_snapshot()
      if isinstance(data, dict) and data.get("schemaVersion") == 1 and isinstance(data.get("models"), list):
        return data
    except (AttributeError, OSError, ValueError):
      pass
    return None

  def open(self, kind: str = "overview", profile: str = "", model_id: str = "") -> None:
    page = self.catalog_pages.get(profile) if kind == "catalog" and profile else None
    if page is None:
      page = _ModelsPage(self, kind, profile, model_id)
      if kind == "catalog" and profile:
        self.catalog_pages[profile] = page
    state, data = self.session.model_snapshot(), self._snapshot()
    if state != page.current or data != page.manager:
      self._populate(page, state, data)
    page.next_refresh = self.clock() + self.REFRESH_SECONDS
    gui_app.push_widget(page)

  def refresh_visible(self, page: _ModelsPage) -> None:
    if gui_app.get_active_widget() is not page:
      return
    now = self.clock()
    if now < page.next_refresh:
      return
    page.next_refresh = now + self.REFRESH_SECONDS
    state, data = self.session.model_snapshot(), self._snapshot()
    if state != page.current or data != getattr(page, "manager", None):
      self._populate(page, state, data)

  @staticmethod
  def _button(title: str, value: str, callback: Callable, enabled: bool = True) -> BigButton:
    button = BigButton(_display_text(title).lower(), _display_text(value).lower(), scroll=True)
    button.set_click_callback(callback)
    button.set_enabled(enabled)
    return button

  def _populate(self, page: _ModelsPage, state: FeatureSettingsState, data: dict | None = None) -> None:
    page.current, page.manager = state, data
    if data is None:
      cards = [_grey_card(state.title.lower(), state.subtitle.lower())]
      cards.extend(_grey_card(row.label.lower(), f"{row.value}. {row.reason}".lower()) for row in state.rows)
    else:
      kind = getattr(page, "kind", "overview")
      cards = self._overview(page, state, data) if kind == "overview" else self._catalog(page, data) if kind == "catalog" else self._details(page, data)
    page._scroller.items.clear()
    page._scroller.add_widgets(cards)

  def _overview(self, page: _ModelsPage, state: FeatureSettingsState, data: dict) -> list:
    summary = data.get("summary", {})
    cards = [_grey_card("model manager", f"{summary.get('installed', 0)} installed · {summary.get('missing', 0)} missing"),
             _grey_card("model selection", "takes effect at the next start")]
    for profile, field in (("small", "activeSmallModel"), ("big", "activeBigModel")):
      mid = data.get(field, "")
      label = next((m.get("label", mid) for m in data["models"] if m["value"] == mid),
                   mid or (big_none_label(data).lower() if profile == "big" else "none — always use small"))
      cards.append(self._button(f"active {profile}", label, lambda p=profile: self.open("catalog", p),
                                data.get("capabilities", {}).get("select") is True and not data.get("randomizer")))
    cards.append(self._button("model randomizer", "on · choose at each start" if data.get("randomizer") else "off",
                              lambda: self._action(page, "randomizer"), model_action_allowed(data, "randomizer")))
    cards.append(self._button("all models", "browse, favorites and downloads", lambda: self.open("catalog")))
    action = "cancel" if data.get("downloading") else "download_all"
    cards.append(self._button("cancel download" if action == "cancel" else "download all missing", data.get("progress", "entire catalog"),
                              lambda: self._action(page, action), model_action_allowed(data, action)))
    cards.append(self._button("refresh catalog", "check for model downloads", lambda: self._action(page, "refresh_manifest"),
                              model_action_allowed(data, "refresh_manifest")))
    if data.get("progress"):
      cards.append(_grey_card("download status", data["progress"].lower()))
    cards.extend(_grey_card(row.label.lower(), f"{row.value}. {row.reason}".lower()) for row in state.rows)
    return cards

  def _catalog(self, page: _ModelsPage, data: dict) -> list:
    profile = page.profile
    cards = [_grey_card(f"active {profile}" if profile else "models", "choose a model" if profile else "browse the catalog")]
    cards.append(self._button("sort and filter", self.SORTS[page.sort_mode], lambda: self._next_sort(page)))
    jetlink = profile == "big" and data.get("jetlinkBig") is True
    if profile == "big":
      cards.append(self._button("none", big_none_label(data).lower() if jetlink else "always use active small",
                                lambda: self._action(page, "active", profile="big"), model_action_allowed(data, "active", profile="big")))
    rows = model_catalog_rows(data, page.sort_mode, profile, installed_only=bool(profile))
    for model in rows:
      mid = model["value"]
      tags = ["runs over jetlink" if jetlink else "installed" if model.get("installed") else
              model.get("unavailableReason", "download required")]
      if model.get("blacklisted"):
        tags.append("excluded from randomizer")
      tags.append("chestnut" if model.get("requiresGpu") else "small")
      if model.get("userFavorite"):
        tags.append("your favorite")
      if model.get("communityFavorite"):
        tags.append("community pick")
      callback = (lambda m=mid: self._action(page, "active", m, profile)) if profile else lambda m=mid: self.open("details", model_id=m)
      cards.append(self._button(model.get("label", mid), " · ".join(tags), callback,
                                not profile or model_action_allowed(data, "active", model, profile)))
    if not rows:
      cards.append(_grey_card("no models", "no models match this filter"))
    return cards

  def _details(self, page: _ModelsPage, data: dict) -> list:
    model = next((m for m in data["models"] if m["value"] == page.model_id), None)
    if model is None:
      return [_grey_card("model unavailable", "return to the catalog")]
    label, mid = model.get("label", page.model_id), page.model_id
    cards = [_grey_card(label.lower(), ("chestnut · external gpu" if model.get("requiresGpu") else "small · on-device")),
             _grey_card("model details", f"{mid} · version {model.get('version', '?')} · {model.get('released', '')}".lower())]
    if model.get("unavailableReason"):
      cards.append(_grey_card("availability", model["unavailableReason"].lower()))
    if model.get("communityFavorite"):
      cards.append(_grey_card("community favorite", "from the model catalog"))
    favorite = bool(model.get("userFavorite"))
    cards.append(self._button("remove favorite" if favorite else "add favorite", "your favorites", lambda: self._action(page, "preferences", mid),
                              model_action_allowed(data, "preferences", model)))
    cards.append(self._button("include in randomizer" if model.get("blacklisted") else "exclude from randomizer", "random selection only",
                              lambda: self._action(page, "exclusion", mid), model_action_allowed(data, "exclusion", model)))
    if model.get("installed"):
      for profile in ("small", "big"):
        if model_action_allowed(data, "active", model, profile):
          cards.append(self._button(f"set active {profile}", "next start", lambda p=profile: self._action(page, "active", mid, p)))
      if not model.get("builtin"):
        cards.append(self._button("delete model", "remove local files", lambda: self._action(page, "delete", mid),
                                  model_action_allowed(data, "delete", model)))
    else:
      cards.append(self._button("download model", "download does not activate", lambda: self._action(page, "download", mid),
                                model_action_allowed(data, "download", model)))
    return cards

  def _next_sort(self, page: _ModelsPage) -> None:
    modes = list(self.SORTS)
    page.sort_mode = modes[(modes.index(page.sort_mode) + 1) % len(modes)]
    self._populate(page, self.session.model_snapshot(), self._snapshot())

  def _action(self, page: _ModelsPage, action: str, model_id: str = "", profile: str = "", confirmed: bool = False) -> None:
    if gui_app.get_active_widget() is not page:
      return
    data = self._snapshot()
    model = next((m for m in data["models"] if m["value"] == model_id), None) if data else None
    needs_gpu = bool(data) and model_needs_gpu(data, action, model)
    request = model_request(data, action, model_id, profile, allow_gpu=needs_gpu and confirmed) if data else None
    if request is None:
      gui_app.push_widget(BigDialog("action unavailable", "Turn off the vehicle, then refresh the model list and try again."))
      return
    if not confirmed and (action == "delete" or needs_gpu):
      question = f"delete {model.get('label', model_id)}?" if action == "delete" and model else "download gpu models? an external gpu is needed to run them"
      gui_app.push_widget(BigConfirmationDialog(question.lower(), gui_app.texture("icons_mici/settings/device/uninstall.png", 64, 64),
                                                lambda: self._action(page, action, model_id, profile, True), red=action == "delete"))
      return
    try:
      result = self.session.model_manager_action(*request)
      self._populate(page, self.session.model_snapshot(), self._snapshot())
      if action == "active":
        gui_app.push_widget(BigDialog("selection saved", result.get("message", "Takes effect at the next start.")))
    except (OSError, ValueError) as error:
      gui_app.push_widget(BigDialog("model action failed", str(error)))
