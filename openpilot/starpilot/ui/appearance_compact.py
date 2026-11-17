"""Compact Visuals child using the shared onroad visibility owner."""

from typing import Protocol

from openpilot.starpilot.ui.feature_settings_state import FeatureSettingsRequest, FeatureSettingsState, row_change
from openpilot.starpilot.ui.appearance_preferences import CAMERA_LABELS, LEAD_INFO_LABELS
from openpilot.starpilot.ui.pip_owner import FORMAT_PREFIX as PIP_FORMAT_PREFIX, RESET as PIP_RESET
from openpilot.system.ui.lib.application import gui_app
from openpilot.system.ui.widgets.scroller import NavScroller
from openpilot.selfdrive.ui.mici.widgets.button import BigButton, GreyBigButton
from openpilot.selfdrive.ui.mici.widgets.dialog import BigConfirmationDialog


class AppearanceSession(Protocol):
  def appearance_snapshot(self) -> FeatureSettingsState: ...
  def pip_snapshot(self) -> FeatureSettingsState: ...
  def appearance_request(self, request: FeatureSettingsRequest) -> bool: ...
  def feature_snapshot(self, page: str) -> FeatureSettingsState: ...
  def feature_request(self, request: FeatureSettingsRequest) -> bool: ...


SLC_VISUAL_LABELS = {
  "ShowSpeedLimits": "show speed limits",
  "SLCConfirmation": "confirm limits",
}


class AppearanceCompact:
  def __init__(self, session: AppearanceSession):
    self.session = session

  def entry_button(self) -> BigButton:
    button = BigButton("visuals", "onroad widgets")
    button.set_click_callback(self.open)
    return button

  def open(self, page_name: str = "appearance") -> None:
    page = NavScroller()
    self._populate(page, page_name)
    gui_app.push_widget(page)

  def _populate(self, page: NavScroller, page_name: str = "appearance") -> None:
    state = self.session.pip_snapshot() if page_name == "pip" else self.session.appearance_snapshot()
    subtitle = "onroad visuals and speed-limit choices" if page_name == "appearance" else state.subtitle.lower()
    cards = [GreyBigButton(state.title.lower(), subtitle)]
    def refresh() -> None:
      self._populate(page, page_name)
    rows = list(state.rows)
    if page_name == "appearance":
      slc = self.session.feature_snapshot("slc")
      visible = [row for row in slc.rows if row.key in SLC_VISUAL_LABELS]
      rows[-1:-1] = visible
    for row in rows:
      if row.key == "CameraView" and row.available:
        button = BigButton(row.label.lower(), row.value.lower())
        button.set_click_callback(lambda source=row: self._camera_options(source, refresh))
        cards.append(button)
        continue
      if row.key == "LeadInfo" and row.available:
        button = BigButton(row.label.lower(), row.value.lower())
        button.set_click_callback(lambda source=row: self._lead_info_options(source, refresh))
        cards.append(button)
        continue
      if row.page and row.available:
        button = BigButton(row.label.lower(), row.value.lower())
        button.set_click_callback(lambda name=row.page: self.open(name))
        cards.append(button)
        continue
      if row.key == PIP_RESET or row.key.startswith(PIP_FORMAT_PREFIX):
        button = BigButton(row.label.lower(), "requires confirmation")
        button.set_enabled(row.available)
        def reset(source=row) -> None:
          request = FeatureSettingsRequest(source.key, source.source, "confirm", confirmation=True,
                                           dependencies=source.dependencies)
          def confirmed() -> None:
            if gui_app.get_active_widget() is page and self.session.appearance_request(request):
              refresh()
          size = source.key.removeprefix(PIP_FORMAT_PREFIX).replace("x", "×")
          question = ("reset crop?" if source.key == PIP_RESET else f"{size}?")
          gui_app.push_widget(BigConfirmationDialog(
            question + "\npreview may\nresume if on",
            gui_app.texture("icons_mici/settings/device/uninstall.png", 64, 64), confirmed, red=True))
        button.set_click_callback(reset)
        cards.append(button)
        continue
      if not row.available:
        cards.append(GreyBigButton(SLC_VISUAL_LABELS.get(row.key, row.label.lower()), f"{row.value}. {row.reason}".lower()))
        continue
      label = SLC_VISUAL_LABELS.get(row.key, row.label.lower()) + (" set " + row.repair_value.lower() if row.repair_value else "")
      button = BigButton(label, row.value.lower())
      def clicked(source=row) -> None:
        request = row_change(source)
        apply = self.session.feature_request if source.key in SLC_VISUAL_LABELS else self.session.appearance_request
        if request is not None and apply(request):
          refresh()
      button.set_click_callback(clicked)
      cards.append(button)
    page._scroller.items.clear()
    page._scroller.add_widgets(cards)

  def _camera_options(self, source, refresh) -> None:
    selector = NavScroller()
    cards = [GreyBigButton("camera view", "choose the onroad camera")]
    for choice in CAMERA_LABELS:
      button = BigButton(choice.lower(), "selected" if choice == source.value else "choose")
      button.set_enabled(source.available)
      def selected(value=choice) -> None:
        if gui_app.get_active_widget() is not selector:
          return
        if value != source.value:
          self.session.appearance_request(FeatureSettingsRequest(source.key, source.source, value))
        selector.dismiss(refresh)
      button.set_click_callback(selected)
      cards.append(button)
    selector._scroller.add_widgets(cards)
    gui_app.push_widget(selector)

  def _lead_info_options(self, source, refresh) -> None:
    selector = NavScroller()
    cards = [GreyBigButton("lead info", "shown above the lead marker")]
    for choice in LEAD_INFO_LABELS:
      button = BigButton(choice.lower(), "selected" if choice == source.value else "choose")
      def selected(value=choice) -> None:
        if gui_app.get_active_widget() is not selector:
          return
        if value != source.value:
          self.session.appearance_request(FeatureSettingsRequest(source.key, source.source, value,
                                                                 related_source=source.related_source,
                                                                 dependencies=source.dependencies))
        selector.dismiss(refresh)
      button.set_click_callback(selected)
      cards.append(button)
    selector._scroller.add_widgets(cards)
    gui_app.push_widget(selector)
