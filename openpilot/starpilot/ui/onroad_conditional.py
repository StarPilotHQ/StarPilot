"""Short, source-bound labels for accepted conditional mode receipts."""

from openpilot.starpilot.ui.raylib import rl

from openpilot.starpilot.conditional_mode.policy import ModeChoice, Reason
from openpilot.starpilot.ui.onroad_state import AlertSize
from openpilot.starpilot.ui.onroad_state import OnroadState


_CEM_HOLD_REASONS = {3: "cem_curve", 4: "cem_lead", 5: "cem_signal", 6: "cem_speed",
                     7: "cem_speed_limit", 8: "cem_stop"}
_CCM_HOLD_REASONS = {4: "ccm_lead", 6: "ccm_speed"}


_REASONS = {reason.value: (reason.name[4:] if reason.name.startswith(('CEM_', 'CCM_')) else reason.name).replace('_', ' ')
            for reason in Reason}
CURVE_SLOWING_DELTA_MPS = 1.0
_HIDDEN_REASONS = frozenset((Reason.SCENE_UNAVAILABLE.value, Reason.UNAVAILABLE.value))


def reason(state: OnroadState) -> str | None:
  """Resolve the accepted planner reason, including its authoritative held status."""
  preview = state.visual_preview
  if preview is not None and preview.cem_reason in ('CURVE', 'LEAD', 'STOP LIGHT', 'SPEED'):
    return {'CURVE': 'cem_curve', 'LEAD': 'cem_lead', 'STOP LIGHT': 'cem_stop', 'SPEED': 'cem_speed'}[preview.cem_reason]
  value = (state.conditional_effective if state.longitudinal_active else None) or getattr(state, 'conditional_perception', None)
  if value is None or value.choice not in (ModeChoice.CEM, ModeChoice.CCM) or value.reason in _HIDDEN_REASONS:
    return None
  if value.reason == 'cem_hold' and value.choice is ModeChoice.CEM:
    return _CEM_HOLD_REASONS.get(getattr(value, 'status_code', 0))
  if value.reason == 'ccm_hold' and value.choice is ModeChoice.CCM:
    return _CCM_HOLD_REASONS.get(getattr(value, 'status_code', 0))
  return value.reason


def curve_slowing(state: OnroadState) -> bool:
  """CEM Curves holding the car under the driver's cruise speed."""
  if reason(state) != 'cem_curve':
    return False
  preview = state.visual_preview
  if preview is not None and preview.cem_reason == 'CURVE':
    return state.alert.size != AlertSize.FULL
  return bool(state.alert.size == AlertSize.NONE and not state.longitudinal_overridden and state.longitudinal_active and state.speed_mps is not None and state.cruise_kph is not None and
              state.speed_mps < state.cruise_kph / 3.6 - CURVE_SLOWING_DELTA_MPS)


def stop_active(state: OnroadState) -> bool:
  """One accepted stop state for the light icon and the conditional orange border."""
  if state.alert.size == AlertSize.FULL or state.longitudinal_overridden:
    return False
  value = state.conditional_effective
  preview = state.visual_preview
  return bool(reason(state) == 'cem_stop' and
              (preview is not None and preview.cem_reason == 'STOP LIGHT' or
               state.longitudinal_active and value is not None and value.choice is ModeChoice.CEM and value.effective_experimental))


def status(state: OnroadState) -> tuple[str, str, rl.Color] | None:
  preview = state.visual_preview
  if preview is not None and preview.cem_reason in ('CURVE', 'LEAD', 'STOP LIGHT', 'SPEED'):
    if state.alert.size == AlertSize.FULL:
      return None
    if preview.layout_sample:
      # The layout editor's sample keeps the car's own reason (the gauge draws from it), not replay wording.
      detail = _REASONS.get(reason(state))
      return ('CEM', detail, rl.Color(112, 192, 216, 255)) if detail else None
    return 'CEM', f'PREVIEW {preview.cem_reason}', rl.Color(112, 192, 216, 255)
  value = (state.conditional_effective if state.longitudinal_active else None) or getattr(state, 'conditional_perception', None)
  if value is None or value.choice not in (ModeChoice.CEM, ModeChoice.CCM):
    return None
  if value.reason in _HIDDEN_REASONS or value.reason in ('no_trigger', 'manual_chill', 'manual_experimental', 'ccm_veto'):
    return None
  # The text is selected from known policy reasons, never rendered from wire text.
  detail = _REASONS.get(reason(state))
  if detail is None:
    return None
  family = 'CEM' if value.choice is ModeChoice.CEM else 'CCM'
  color = rl.Color(112, 192, 216, 255) if value.effective_experimental else rl.Color(255, 155, 63, 255)
  return family, detail, color
