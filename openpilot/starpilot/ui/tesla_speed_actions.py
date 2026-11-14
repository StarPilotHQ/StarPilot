"""Tesla Model 3/Y speed actions for the comma's own screen and Wheel Controls.

Kept apart from runtime_app so that file stays small. The session mixes this in; every action still passes the
publisher's and card's own driving-authority checks.
"""
import math
import time


class TeslaSpeedActions:
  def _comma_speed_actions(self, *, require_display=False):
    """Increase and Decrease Comma Set Speed: a scroll moves the comma speed by the Short scroll step, a fast scroll by
    the Fast scroll step. Hardware inputs must not expire while unrelated settings pages are built. Also Resume Speed
    Limit + Offset (slc_resume_action), which the right stalk runs by default."""
    from openpilot.starpilot.ui.runtime_app import ui_state, _slc_action_publisher, cruise_action_authority
    from openpilot.starpilot.favorites.state import FavoriteAction
    from openpilot.starpilot.common.car_control_config import SPEED_UP, SPEED_DOWN, match_speed_owner, software_cruise_requested
    from openpilot.starpilot.longitudinal.cruise_intervals import read_cruise_intervals, step_units
    actions = {}
    drive = (ui_state.started_frame, getattr(ui_state.CP, 'carFingerprint', None))
    tesla_controls = software_cruise_requested(ui_state.params, ui_state.CP)
    match_owner = match_speed_owner(ui_state.params, ui_state.CP)
    for key, label, sign in ((SPEED_UP, 'Increase Comma Set Speed', 1), (SPEED_DOWN, 'Decrease Comma Set Speed', -1)):
      def comma_step(fast=0., sign=sign):
        if (fast not in (0, 1) or ui_state.params.get_bool('SafeMode') or getattr(ui_state, 'replay_clock', None) is not None or
            require_display and not self._favorite_authority()):
          return False
        intervals = read_cruise_intervals(ui_state.params, ui_state.CP)
        return self.cruise_actions.dispatch(sign > 0, ui_state.sm, ui_state.CP, _slc_action_publisher(),
                                            now_ns=time.monotonic_ns(), step_units=sign * step_units(intervals, bool(fast)),
                                            tesla_controls=software_cruise_requested(ui_state.params, ui_state.CP),
                                            match_owner=match_speed_owner(ui_state.params, ui_state.CP), intervals=intervals)
      allowed = ((not require_display or self._favorite_authority()) and getattr(ui_state, 'replay_clock', None) is None and
                 not ui_state.params.get_bool('SafeMode') and
                 cruise_action_authority(ui_state.sm, ui_state.CP, time.monotonic_ns(), tesla_controls=tesla_controls,
                                         match_owner=match_owner))
      actions[key] = FavoriteAction(key, label, available=allowed, token=repr((drive, allowed)),
                                    invoke=lambda action=comma_step: action(), invoke_value=comma_step)
    from openpilot.starpilot.common.car_control_config import SPEED_RESUME
    from openpilot.starpilot.ui.slc_resume_action import resume_action
    actions[SPEED_RESUME] = resume_action(self, ui_state, drive, require_display=require_display)
    return actions

  def _tesla_favorite_actions(self, actions, drive):
    """On a Tesla Model 3/Y the comma's own speed actions work through Tesla controls (pcmCruise), plus Resume Speed
    Limit + Offset. Other cars keep the actions built before this."""
    from openpilot.starpilot.ui.runtime_app import ui_state, _slc_action_publisher, cruise_action_authority
    from openpilot.starpilot.favorites.actions import INCREASE_SPEED, DECREASE_SPEED, SET_SPEED
    from openpilot.starpilot.favorites.state import FavoriteAction
    from openpilot.starpilot.saved_source import read_saved
    from openpilot.starpilot.common.car_control_config import match_speed_owner, software_cruise_requested
    cp = ui_state.CP
    if cp is not None and cp.pcmCruise and cp.carFingerprint in ('TESLA_MODEL_3', 'TESLA_MODEL_Y'):
      owner = {'tesla_controls': software_cruise_requested(ui_state.params, cp), 'match_owner': match_speed_owner(ui_state.params, cp)}
      current = self._favorite_authority() and cruise_action_authority(ui_state.sm, cp, time.monotonic_ns(), **owner)
      safe = not ui_state.params.get_bool("SafeMode")

      def change_speed(increase):
        return bool(not ui_state.params.get_bool("SafeMode") and self.cruise_actions.dispatch(
          increase, ui_state.sm, ui_state.CP, _slc_action_publisher(), now_ns=time.monotonic_ns(),
          tesla_controls=software_cruise_requested(ui_state.params, ui_state.CP),
          match_owner=match_speed_owner(ui_state.params, ui_state.CP)))
      for key, label, increase in ((INCREASE_SPEED, "Increase Speed", True), (DECREASE_SPEED, "Decrease Speed", False)):
        actions[key] = FavoriteAction(key, label, available=current and safe, reason="Active software cruise control required",
                                      token=repr((drive, current)), invoke=lambda increase=increase: change_speed(increase))

      def set_speed(value):
        if not self._favorite_authority() or type(value) not in (int, float) or not math.isfinite(value):
          return False
        metric_raw, readable = read_saved(ui_state.params, "IsMetric", 8)
        if not readable or metric_raw not in (None, b"0", b"1"):
          return False
        metric = metric_raw == b"1"
        if not (8 <= value <= 145 if metric else 5 <= value <= 90):
          return False
        return bool(not ui_state.params.get_bool("SafeMode") and self.cruise_actions.dispatch(
          True, ui_state.sm, ui_state.CP, _slc_action_publisher(), now_ns=time.monotonic_ns(),
          target_mps=float(value) * (1 / 3.6 if metric else 0.44704),
          tesla_controls=software_cruise_requested(ui_state.params, ui_state.CP),
          match_owner=match_speed_owner(ui_state.params, ui_state.CP)))
      actions[SET_SPEED] = FavoriteAction(SET_SPEED, "Set Speed To", available=current and safe,
        reason="Active software cruise required; speed uses device units", token=repr((drive, current)), invoke_value=set_speed)
    # Increase / Decrease Comma Set Speed and Resume Speed Limit + Offset, with the screen's authority.
    actions.update(self._comma_speed_actions(require_display=True))
    return actions
