"""Release while PCM and wheel speed still report stopped; no RES injection."""
import pytest
from opendbc.car import structs
from opendbc.car.toyota import prius_advert_filter, prius_sdsu, toyotacan
from opendbc.car.toyota.values import CAR
from openpilot.common.params import Params
from openpilot.selfdrive.controls.lib.longcontrol import LongControl
from openpilot.starpilot.vehicle_preferences import VehicleStartupPreferences
from openpilot.starpilot.car.toyota.tests.test_prius_sdsu_filter import factory, original_firmware
from openpilot.starpilot.car.toyota.tests import test_rav4h_caller_native as helpers
from openpilot.starpilot.car.toyota.tests.test_toyota_cpp_transport import host_heartbeat_bridge as host_heartbeat_bridge

source_matched_native = helpers.source_matched_native
daemon_gc_scope = helpers.daemon_gc_scope


def stopped():
  cs = structs.CarState(canValid=True, standstill=True)
  cs.cruiseState.enabled = cs.cruiseState.available = cs.cruiseState.standstill = True
  return cs


def test_sng_planned_release_without_pcm_or_wheel_motion():
  control = LongControl(factory(), VehicleStartupPreferences(toyota_sng=True))
  cs = stopped()
  for _ in range(20):
    control.update(True, cs, -.5, True, (-3., 2.))
  outputs = [control.update(True, cs, .8, False, (-3., 2.)) for _ in range(100)]
  assert max(outputs) > 0, 'PRIUS_SNG_PCM_STANDSTILL_DEADLOCK'
  assert cs.standstill and cs.cruiseState.standstill and not cs.buttonEvents


@pytest.mark.parametrize('sng,active,brake,should_stop', ((False, True, False, False),
                          (True, False, False, False), (True, True, True, False), (True, True, False, True)))
def test_sng_does_not_release_default_off_inactive_brake_or_force_stop(sng, active, brake, should_stop):
  control = LongControl(factory(), VehicleStartupPreferences(toyota_sng=sng))
  cs = stopped()
  cs.brakePressed = brake
  for _ in range(100):
    assert control.update(active, cs, .8, should_stop, (-3., 2.)) <= 0.


@pytest.mark.parametrize('advert', (False, True))
def test_actual_native_drive_request_precedes_wheel_motion(monkeypatch, host_heartbeat_bridge, advert):
  tick, positive = 0, False
  was_stopped = True
  physical, inputs, put = helpers.physical, helpers.control_inputs, Params.put_bool
  accel_command = toyotacan.create_accel_command

  def saved(params, key, value, *args, **kwargs):
    result = put(params, key, value, *args, **kwargs)
    if key == 'ToyotaAutoHold':
      put(params, 'SNGHack', True, block=True)
    return result

  def frames(packer, **fields):
    nonlocal tick, was_stopped
    tick += 1
    was_stopped = not positive
    fields.update(stopped=was_stopped, brake=tick < 200)
    return physical(packer, **fields)

  def plans(*args, **kwargs):
    peers = inputs(*args, **kwargs)
    for index, peer in enumerate(peers):
      if peer.which() == 'longitudinalPlan':
        plan = peer.as_builder() if hasattr(peer, 'as_builder') else peer
        plan.longitudinalPlan.shouldStop = tick < 220
        plan.longitudinalPlan.aTarget = -.5 if tick < 220 else .8
        peers[index] = plan
    return peers

  def observe(packer, accel, cancel, *args, **kwargs):
    nonlocal positive
    if accel > 0 and not positive:
      assert was_stopped and tick >= 220 and not cancel
      positive = True
    return accel_command(packer, accel, cancel, *args, **kwargs)

  def inspect(tick, host, cs, cc, safety, native):
    assert host.CI.CC.sng_hack
    if 180 <= tick < 190:
      assert cs.brakePressed and not cc.longActive and safety.aol_get_permission_mask() & 2 == 0
    if 210 <= tick < 219:
      assert cs.standstill and cs.cruiseState.standstill and cc.actuators.accel <= 0 and not positive

  monkeypatch.setattr(Params, 'put_bool', saved)
  monkeypatch.setattr(helpers, 'physical', frames)
  monkeypatch.setattr(helpers, 'control_inputs', plans)
  monkeypatch.setattr(toyotacan, 'create_accel_command', observe)
  owner = prius_advert_filter if advert else prius_sdsu
  helpers.run_case(False, candidate=CAR.TOYOTA_PRIUS, bridge=host_heartbeat_bridge, input_clock=True,
                   firmware_override=original_firmware(), filter_advert=advert,
                   sdsu_words=(owner.LONG_WORD, owner.STOCK_WORD), on_sample=inspect)
  assert positive
