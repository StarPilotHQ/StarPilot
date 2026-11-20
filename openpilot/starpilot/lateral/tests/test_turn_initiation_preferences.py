from pathlib import Path

from openpilot.common.params import Params
from openpilot.starpilot.lateral.lane_change_preferences import KEY as LANE_KEY, LaneChangePolicy, read_saved, to_value
from openpilot.starpilot.lateral.turn_initiation_preferences import KEY, REFRESH_NS, TurnInitiationPreference


def test_never_inherits_legacy_guidance_and_does_not_change_document(tmp_path):
  params = Params(str(tmp_path))
  assert params.get_default_value(KEY) is False
  params.put(LANE_KEY, to_value(LaneChangePolicy(turn_signal_guidance=True, auto_lane_change=True)), block=True)
  before = read_saved(params)
  assert not TurnInitiationPreference(params).sample(1)
  for enabled in (True, False):
    params.put_bool(KEY, enabled, block=True)
    assert TurnInitiationPreference(params).sample(1) is enabled
    assert read_saved(params) == before
  params.remove(KEY)
  assert not TurnInitiationPreference(params).sample(1)


def test_enable_at_neutral_boundary_and_disable_on_refresh(tmp_path):
  params = Params(str(tmp_path))
  pref = TurnInitiationPreference(params)
  params.put_bool(KEY, True, block=True)
  assert not pref.sample(1, signaling=True)
  assert not pref.sample(2, maneuver_active=True)
  assert pref.sample(3)
  params.put_bool(KEY, False, block=True)
  assert pref.sample(REFRESH_NS, signaling=True)
  assert not pref.sample(REFRESH_NS + 1, signaling=True)
  assert not pref.sample(REFRESH_NS - 1)
  params.put_bool(KEY, True, block=True)
  assert not pref.sample(REFRESH_NS + 2, signaling=True)
  assert pref.sample(REFRESH_NS + 3)


def test_invalid_exact_value_fails_closed_even_on_active_signal(tmp_path):
  params = Params(str(tmp_path))
  pref = TurnInitiationPreference(params)
  params.put_bool(KEY, True, block=True)
  assert pref.sample(1)
  Path(params.get_param_path(KEY)).write_bytes(b"1\n")
  assert not pref.sample(REFRESH_NS + 1, signaling=True)
