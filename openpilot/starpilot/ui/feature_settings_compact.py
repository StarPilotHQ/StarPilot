"""Compact native card/page adapter for the shared saved-feature owner."""

from __future__ import annotations

from openpilot.starpilot.ui.lane_change_feature import confirm_question as lane_change_question

from collections.abc import Callable
from dataclasses import replace
from typing import Protocol

from openpilot.starpilot.ui.vehicle_bool import VEHICLE_BOOL_KEYS, confirmation_question as vehicle_question
from openpilot.starpilot.ui.feature_settings_state import (
  FeaturePage, FeatureRow, FeatureSettingsRequest, FeatureSettingsState,
  FEATURE_CONFIRM_ACTIONS, CONDITIONAL_CONFIRM_ACTIONS, is_long_confirm_action, row_change, value_text, value_reason,
)
from openpilot.starpilot.ui.lane_change_feature import KEYS as LANE_CHANGE_KEYS, RESET as LANE_CHANGE_RESET
from openpilot.starpilot.ui.long_profile_feature import long_confirm_question
from openpilot.starpilot.ui.conditional_feature import confirmation_question as conditional_question
from openpilot.starpilot.ui.controller_feature import SETUP_ACTION, SETUP_QUESTION
from openpilot.system.ui.lib.application import gui_app
from openpilot.system.ui.widgets.scroller import NavScroller
from openpilot.selfdrive.ui.mici.widgets.button import BigButton, GreyBigButton
from openpilot.selfdrive.ui.mici.widgets.dialog import BigConfirmationDialog, BigConfirmationCircleButton, SettingDescriptionDialog

class FeatureSession(Protocol):
  def feature_snapshot(self, page: str) -> FeatureSettingsState: ...
  def feature_request(self, request: FeatureSettingsRequest) -> bool: ...


class FeatureSettingsCompact:
  def __init__(self, session: FeatureSession):
    self.session = session

  def entry_button(self) -> BigButton:
    button = BigButton("driving controls", "saved feature settings")
    button.set_click_callback(lambda: self.open(FeaturePage.HUB))
    return button

  def open(self, page_name: str) -> None:
    page = NavScroller()
    self._populate(page, page_name)
    gui_app.push_widget(page)

  def _populate(self, page: NavScroller, page_name: str) -> None:
    state = self.session.feature_snapshot(page_name)
    def refresh() -> None:
      self._populate(page, page_name)
    cards = [GreyBigButton(state.title.lower(), state.subtitle.lower())]
    for row in state.rows:
      if row.page and row.available:
        button = BigButton(row.label.lower(), row.value.lower())
        button.set_click_callback(lambda name=row.page: self.open(name))
        cards.append(button)
      elif (row.key in FEATURE_CONFIRM_ACTIONS or
            is_long_confirm_action(row.key)):
        button = BigButton("reset invalid profiles" if row.key == "reset_profiles" else row.label.lower(),
                           "")
        button.set_enabled(row.available)
        button.set_click_callback(lambda selected=row: self._confirm_reset(selected, refresh, page))
        cards.append(button)
      elif row.available:
        cards.extend(self._editable(row, refresh, page))
      else:
        detail = value_text(row) if row.key == "LaneCenteringStrength" else (row.value + " " + row.unit).strip()
        if value_reason(row):
          detail += " · " + value_reason(row)
        cards.append(GreyBigButton(row.label.lower(), detail.lower()))
    page._scroller.items.clear()
    page._scroller.add_widgets(cards)

  def _editable(self, row: FeatureRow, refresh: Callable[[], None], page: NavScroller) -> list[BigButton]:
    result: list[BigButton] = []
    directions = (1,) if row.repair_value else (-1, 1) if row.step else (1,)
    for direction in directions:
      label = row.label.lower() + (" set " + row.repair_value.lower() if row.repair_value else
                                   " −" if direction < 0 else " +" if row.step else "")
      detail = value_text(row) if row.key == "LaneCenteringStrength" else (row.value + " " + row.unit).strip()
      if value_reason(row):
        detail += " · " + value_reason(row)
      button = BigButton(label, detail)

      def clicked(source=row, direction=direction) -> None:
        request = row_change(source, direction)
        if request is None:
          return
        if request.key in VEHICLE_BOOL_KEYS:
          self._confirm(request, vehicle_question(request), refresh, page)
        elif request.key in LANE_CHANGE_KEYS:
          self._confirm(request, lane_change_question(request), refresh, page)
        elif self.session.feature_request(request):
          refresh()

      button.set_click_callback(clicked)
      result.append(button)
    return result

  def _confirm_reset(self, row: FeatureRow, refresh: Callable[[], None], page: NavScroller | None = None) -> None:
    request = FeatureSettingsRequest(row.key, row.source, "confirm", confirmation=True,
                                     related_source=row.related_source,
                                     vehicle_fingerprint=row.vehicle_fingerprint,
                                     capability=row.capability, dependencies=row.dependencies)
    if row.key == LANE_CHANGE_RESET:
      if page is not None:
        self._confirm(request, lane_change_question(request), refresh, page)
      return
    if row.key in CONDITIONAL_CONFIRM_ACTIONS:
      if page is not None:
        self._confirm(request, conditional_question(request), refresh, page)
      return
    def confirmed() -> None:
      if self.session.feature_request(request):
        refresh()
    question = {"torque_adopt": "use new torque editor for this vehicle model? old values remain saved but inactive",
                SETUP_ACTION: SETUP_QUESTION,
                "torque_reset": "reset invalid torque profiles? old values remain saved but inactive",
                "torque_reset_profile": "reset this vehicle model's custom torque profile?",
                "torque_gain_rebase": "Apply the saved steering response to the selected controller and current vehicle tune? " +
                                     "Friction and other models stay unchanged.",
                "torque_rebase": f"reapply saved torque values ({row.value}) with the current vehicle tune?",
                "slc_reset": f"reset speed-limit offsets to zero? {row.value}. " +
                             "saved control may resume if its switch is on; its switch will not change",
                "curve_reset": "Erase learned curve data? Curve Speed Controller keeps its current On or Off choice.",
                "reset_profiles": "reset invalid profiles"}.get(row.key, long_confirm_question(row))
    gui_app.push_widget(BigConfirmationDialog(question,
                                               gui_app.texture("icons_mici/settings/device/uninstall.png", 64, 64),
                                               confirmed, red=True))

  def _confirm(self, request: FeatureSettingsRequest, question: str, refresh: Callable[[], None], page: NavScroller) -> None:
    if gui_app.get_active_widget() is not page:
      return
    def confirmed() -> None:
      if (gui_app.get_active_widget() is page and
          self.session.feature_request(replace(request, confirmation=True))):
        refresh()
    dialog = SettingDescriptionDialog("save change", question)
    dialog._scroller.add_widgets([BigConfirmationCircleButton(
      "save change", gui_app.texture("icons_mici/setup/driver_monitoring/dm_check.png", 64, 64),
      lambda: dialog.dismiss(confirmed))])
    gui_app.push_widget(dialog)
