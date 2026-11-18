"""Real model frames through producer, wire, and both passive display paths."""
from dataclasses import replace
from unittest.mock import Mock, patch

from openpilot.starpilot.ui.onroad_compact_widgets import MiciSidebarWidgets
from openpilot.starpilot.ui.raylib import rl

import pytest

from openpilot.cereal import messaging
from openpilot.starpilot.conditional_mode.host import ConditionalModeHost
from openpilot.starpilot.conditional_mode.policy import ManualIntent, ModeChoice, ModeSettings
from openpilot.starpilot.conditional_mode.preferences import manual_for_drive, selection_for_drive
from openpilot.starpilot.conditional_mode.projection import ObservedBool
from openpilot.starpilot.conditional_mode.runtime_settings import SettingsSnapshot, DocumentState, SafeModeState
from openpilot.starpilot.conditional_mode.status import StatusPublisher
from openpilot.starpilot.conditional_mode.tests.test_host import CP, set_axes
from openpilot.starpilot.conditional_mode.tests.test_projection import MONO, BOOT, FakeSubMaster, serialized_scene, owner_context
from openpilot.starpilot.ui.conditional_status import ConditionalPerceptionProjector
from openpilot.starpilot.ui.onroad_conditional import stop_active, stop_observed
from openpilot.starpilot.ui.onroad_cem_gauge import gauge_data, IndicatorType
from openpilot.starpilot.ui.tests.test_conditional_perception_render import aol_light


@pytest.mark.parametrize('aol', [True, False])
@pytest.mark.parametrize('lead_present', [True, False])
def test_real_passive_scene_survives_control_reset(aol, lead_present):
  host = ConditionalModeHost()
  sm = FakeSubMaster(serialized_scene(), MONO - 5_000_000)
  selection = selection_for_drive(ModeChoice.CEM, ModeSettings(), MONO)
  manual = manual_for_drive(ManualIntent.FORCE_CHILL, MONO, MONO)
  for tick in range(50):
    stamp = MONO + tick * 50_000_000
    sm.stamp(stamp)
    sm.payloads.update(serialized_scene(stamp=BOOT + tick * 50_000_000, horizon=30., lead_present=lead_present))
    set_axes(sm, long_active=not aol, lat_active=True, enabled=not aol)
    proposal = host.sample(sm, CP, now_mono_ns=stamp+1_000_000, now_boot_ns=BOOT+tick*50_000_000+1_000_000,
                           sample_skew_ns=1000, drive_id=MONO, selection=selection, manual=manual,
                           safe_mode=ObservedBool(False, stamp), owner_context=owner_context(stamp),
                           selected_t_follow_s=1.45, selected_t_follow_observed_mono_ns=stamp)
  # Existing stop commitment precedence is unchanged; AOL never requests control.
  assert proposal.decision.reason.value == ('cem_stop' if not aol and not lead_present else 'manual_chill')
  assert proposal.override_experimental is (None if aol else not lead_present)
  assert proposal.projected.perceived_stop is (not lead_present)
  if aol:
    assert not host.projector.stop_detector.committed
    assert host.projector.stop_detector.light_filter.x == 0
  snapshot = SettingsSnapshot('test', 1, stamp, stamp, DocumentState.ABSENT, SafeModeState.ABSENT_FALSE, None, None, None)
  event = StatusPublisher().attach(None, proposal, snapshot, now_ns=stamp+1_000_000, drive_id=MONO, model_ns=stamp, car_state_ns=stamp)
  received = messaging.log_from_bytes(event.to_bytes())
  args = {'now_ns': stamp+20_000_000, 'event_ns': stamp+1_000_000, 'drive_id': MONO, 'choice': ModeChoice.CEM,
          'lateral_active': True, 'selfdrive_enabled': not aol, 'car_valid': True, 'system_long': True}
  value = ConditionalPerceptionProjector().project(received.slcState, **args)
  assert value is not None and value.perceived_stop is (not lead_present)
  state = replace(aol_light(), conditional_perception=value, longitudinal_active=not aol)
  assert stop_observed(state) is (not lead_present)
  assert not stop_active(state)
  compact = MiciSidebarWidgets(Mock())
  with patch.object(compact, '_stop_icon') as light, patch.object(compact, '_chill_icon'):
    compact._conditional(rl.Rectangle(0, 0, 60, 80), state)
  assert light.call_count == (0 if lead_present else 1)
  data = gauge_data(state)
  if not lead_present:
    assert data.indicator_type is IndicatorType.STOP_LIGHT
  else:
    assert data is None
  assert ConditionalPerceptionProjector().project(received.slcState, **(args | {'now_ns': stamp+250_000_000})) is None
