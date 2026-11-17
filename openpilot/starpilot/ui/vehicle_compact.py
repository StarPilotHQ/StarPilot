"""C4 vehicle identity and one saved Auto/manual selection flow."""

from collections import defaultdict

from openpilot.selfdrive.ui.mici.widgets.button import BigButton, GreyBigButton
from openpilot.selfdrive.ui.mici.widgets.dialog import BigDialog
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.starpilot.vehicle_selection import SelectionSnapshot, VehicleChoice, VehicleSelectionOwner
from openpilot.system.ui.lib.application import gui_app
from openpilot.system.ui.widgets.scroller import NavScroller


def vehicle_identity(cp: object | None) -> str:
  if cp is None:
    return "not reported"
  fingerprint = str(getattr(cp, "carFingerprint", "") or "").strip()
  if not fingerprint or fingerprint.upper() == "MOCK" or bool(getattr(cp, "notCar", False)):
    return "not reported"
  if fingerprint == "HYUNDAI_ELANTRA_HEV_2024":
    return "hyundai elantra hybrid 2024-2026"
  return fingerprint.replace("_", " ").lower()


def selection_label(snapshot: SelectionSnapshot, choices: tuple[VehicleChoice, ...]) -> str:
  if not snapshot.readable:
    return "unavailable"
  if not snapshot.valid:
    return "needs review"
  if snapshot.platform is None:
    return "auto detection"
  choice = next((item for item in choices if item.platform == snapshot.platform), None)
  return choice.label.lower() if choice is not None else "unavailable"


class VehicleCompact(NavScroller):
  def __init__(self, feature_session=None):
    super().__init__()
    self.feature_session = feature_session
    self.owner = VehicleSelectionOwner(ui_state.params, ui_state.is_offroad)
    self.choices = self.owner.choices()
    self.selection = self.owner.snapshot()
    self._populate()

  def show_event(self):
    super().show_event()
    self.selection = self.owner.snapshot()
    self._populate()

  def _populate(self):
    identity = vehicle_identity(ui_state.CP)
    choice = selection_label(self.selection, self.choices)
    cards = [GreyBigButton("reported vehicle", identity), GreyBigButton("saved selection", choice),
             GreyBigButton("takes effect", "after next start")]
    if self.selection.readable:
      button = BigButton("change vehicle", "auto or manual")
      button.set_click_callback(self._open_makes)
      button.set_enabled(self.owner.authorized)
      cards.append(button)
    if self.feature_session is not None:
      from openpilot.starpilot.ui.feature_settings_compact import FeatureSettingsCompact
      from openpilot.starpilot.ui.feature_settings_state import FeaturePage
      adapter = FeatureSettingsCompact(self.feature_session)
      features = self.feature_session.feature_snapshot(FeaturePage.VEHICLE)
      for row in features.rows:
        if row.available:
          cards.extend(adapter._editable(row, self._populate, self))
        else:
          cards.append(GreyBigButton(row.label.lower(), (row.value + " " + row.reason).strip().lower()))
    self._scroller.items.clear()
    self._scroller.add_widgets(cards)

  def _open_makes(self):
    groups: dict[str, list[VehicleChoice]] = defaultdict(list)
    for choice in self.choices:
      groups[str(choice.make).lower()].append(choice)
    page = NavScroller()
    auto = BigButton("auto detection", "detect car")
    auto.set_click_callback(lambda: self._choose(None))
    auto.set_enabled(self.owner.authorized)
    cards = [auto]
    for make, models in groups.items():
      button = BigButton(make, f"{len(models)} models")
      button.set_click_callback(lambda name=make, items=tuple(models): self._open_models(name, items))
      cards.append(button)
    page._scroller.add_widgets(cards)
    gui_app.push_widget(page)

  def _open_models(self, make: str, models: tuple[VehicleChoice, ...]):
    page = NavScroller()
    cards = [GreyBigButton(make, "select car model")]
    for model in models:
      button = BigButton(model.label.lower(), scroll=True)
      button.set_click_callback(lambda platform=model.platform: self._choose(platform))
      button.set_enabled(self.owner.authorized)
      cards.append(button)
    page._scroller.add_widgets(cards)
    gui_app.push_widget(page)

  def _choose(self, platform: str | None):
    result = self.owner.choose(self.selection.raw, platform)
    self.selection = self.owner.snapshot()
    self._populate()
    if result.verified:
      gui_app.pop_widgets_to(self)
    else:
      gui_app.push_widget(BigDialog("selection not verified", "Reopen vehicle settings while offroad, then try again."))
