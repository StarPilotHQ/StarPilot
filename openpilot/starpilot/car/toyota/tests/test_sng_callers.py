"""Saved SNG opt-in retains the physical/native stop-engagement contract."""
import pytest
from opendbc.car.toyota.sng import eligible
from openpilot.common.params import Params
from openpilot.starpilot.vehicle_preferences import VehicleStartupPreferences
from openpilot.starpilot.car.toyota.tests import test_prius_stop_engagement_callers as helpers
from openpilot.starpilot.car.toyota.tests.test_toyota_cpp_transport import host_heartbeat_bridge as host_heartbeat_bridge

source_matched_native = helpers.source_matched_native
daemon_gc_scope = helpers.daemon_gc_scope


@pytest.mark.parametrize('advert', (False, True))
@pytest.mark.parametrize('stock', (False, True))
def test_saved_sng_reaches_real_native_stop_callers(monkeypatch, host_heartbeat_bridge, advert, stock):
  put, configure = Params.put_bool, VehicleStartupPreferences.configure_controller
  bound = []

  def saved(params, key, value, *args, **kwargs):
    result = put(params, key, value, *args, **kwargs)
    if key == 'ToyotaAutoHold':
      put(params, 'SNGHack', True, block=True)
    return result

  def bind(prefs, ci):
    configure(prefs, ci)
    assert ci.CC.sng_hack == eligible(ci.CP)
    bound.append(ci.CC.sng_hack)

  monkeypatch.setattr(Params, 'put_bool', saved)
  monkeypatch.setattr(VehicleStartupPreferences, 'configure_controller', bind)
  helpers.test_prius_accepts_stop_engagement_before_brake_release(monkeypatch, host_heartbeat_bridge, advert, stock)
  assert bound and bound[-1] == (not stock)
