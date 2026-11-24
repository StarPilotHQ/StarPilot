"""Tesla Model 3/Y speed actions for the car's own controls (Wheel Controls).

Kept apart from runtime_app so that file stays small. The session mixes this in; every action still passes the
publisher's and card's own driving-authority checks.
"""
import time


class TeslaSpeedActions:
  def _comma_speed_actions(self):
    """The right scroll wheel's steps where the comma owns the set speed (one display unit, or to the next multiple of
    five for a fast scroll), and Resume Speed Limit + Offset, which the right stalk runs by default. Looked up apart
    from the favorites catalog, so a car button never expires while unrelated settings pages are built."""
    from openpilot.starpilot.ui.runtime_app import ui_state, _slc_action_publisher, cruise_action_authority
    from openpilot.starpilot.favorites.state import FavoriteAction
    from openpilot.starpilot.common.car_control_config import SPEED_UP, SPEED_DOWN, SPEED_RESUME
    from openpilot.starpilot.ui.slc_resume_action import resume_action

    def blocked():
      return bool(ui_state.params.get_bool('SafeMode') or getattr(ui_state, 'replay_clock', None) is not None)

    actions = {}
    drive = (ui_state.started_frame, getattr(ui_state.CP, 'carFingerprint', None))
    for key, label, sign in ((SPEED_UP, 'Increase Comma Set Speed', 1), (SPEED_DOWN, 'Decrease Comma Set Speed', -1)):
      def comma_step(fast=0., sign=sign):
        if fast not in (0, 1) or blocked():
          return False
        return self.cruise_actions.dispatch(sign > 0, ui_state.sm, ui_state.CP, _slc_action_publisher(),
                                            now_ns=time.monotonic_ns(), step_units=sign * (5 if fast else 1))
      allowed = not blocked() and cruise_action_authority(ui_state.sm, ui_state.CP, time.monotonic_ns())
      actions[key] = FavoriteAction(key, label, available=allowed, token=repr((drive, allowed)),
                                    invoke=lambda action=comma_step: action(), invoke_value=comma_step)
    actions[SPEED_RESUME] = resume_action(self, ui_state, drive, require_display=False)
    return actions

  def _tesla_set_speed(self, cp):
    """Comma can set speed on Tesla dashboard is on for this car (read at most once a second)."""
    from openpilot.starpilot.ui.runtime_app import ui_state
    from openpilot.starpilot.car.tesla.set_speed import Polled
    polled = getattr(self, '_tesla_set_speed_polled', None)
    if polled is None:
      polled = self._tesla_set_speed_polled = Polled(ui_state.params)
    return polled(cp)

  def _tesla_favorite_actions(self, actions, drive):
    """Resume Speed Limit + Offset as an assignable action on a Tesla Model 3/Y with the CAN add-on cable (the car's
    controls that can run it). Other cars keep the catalog as built."""
    from openpilot.starpilot.ui.runtime_app import ui_state
    from openpilot.starpilot.common.car_control_config import SPEED_RESUME, vehicle_supported
    if vehicle_supported(ui_state.CP):
      from openpilot.starpilot.ui.slc_resume_action import resume_action
      actions[SPEED_RESUME] = resume_action(self, ui_state, drive, require_display=True)
    return actions
