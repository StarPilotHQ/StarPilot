"""Declared SDSU auto-resume must not wait for its own positive drive command."""
import pytest
from opendbc.car import structs
from opendbc.car.toyota import rav4h_filter, toyotacan
from opendbc.car.toyota.interface import CarInterface
from opendbc.car.toyota.sng import eligible
from opendbc.car.toyota.values import CAR
from openpilot.common.params import Params
from openpilot.selfdrive.controls.lib.longcontrol import LongControl
from openpilot.starpilot.car.toyota.tests import test_rav4h_caller_native as helpers
from openpilot.starpilot.car.toyota.tests.test_toyota_cpp_transport import host_heartbeat_bridge as host_heartbeat_bridge
from openpilot.starpilot.vehicle_preferences import VehicleStartupPreferences

source_matched_native = helpers.source_matched_native
daemon_gc_scope = helpers.daemon_gc_scope
AUTO_CARS = (CAR.TOYOTA_RAV4H, CAR.TOYOTA_HIGHLANDER, CAR.TOYOTA_SIENNA)


def factory(candidate, size):
  return CarInterface.get_params(candidate, {0: {0x2FF: size}, 1: {}, 2: {}}, helpers.firmware(candidate), False, False, False)


def stopped():
  state = structs.CarState(canValid=True, standstill=True)
  state.cruiseState.available = state.cruiseState.enabled = state.cruiseState.standstill = True
  return state


@pytest.mark.parametrize('candidate', AUTO_CARS)
@pytest.mark.parametrize('size', (4, 8))
def test_automatic_sdsu_releases_with_pcm_and_wheels_still_stopped(candidate, size):
  cp = factory(candidate, size)
  assert cp.autoResumeSng and cp.openpilotLongitudinalControl and not cp.alphaLongitudinalAvailable
  assert not eligible(cp)  # This automatic profile does not need the manual hack.
  control = LongControl(cp, VehicleStartupPreferences())
  state = stopped()
  for _ in range(50):
    control.update(True, state, -.5, True, (-3., 2.))
  outputs = [control.update(True, state, .8, False, (-3., 2.)) for _ in range(100)]
  assert max(outputs) > 0, 'AUTO_SDSU_PCM_STANDSTILL_DEADLOCK'
  assert state.standstill and state.cruiseState.standstill and not state.buttonEvents


@pytest.mark.parametrize('candidate', AUTO_CARS)
@pytest.mark.parametrize('size', (4, 8))
@pytest.mark.parametrize('veto', ('brake', 'inactive', 'planner_stop'))
def test_automatic_resume_preserves_driver_and_force_stop_vetoes(candidate, size, veto):
  cp = factory(candidate, size)
  control = LongControl(cp, VehicleStartupPreferences())
  state = stopped()
  state.brakePressed = veto == 'brake'
  for _ in range(100):
    assert control.update(veto != 'inactive', state, .8, veto == 'planner_stop', (-3., 2.)) <= 0.


@pytest.mark.parametrize('restriction', ('stock', 'pedal', 'unqualified', 'passive', 'dashcam', 'notcar', 'native'))
def test_auto_resume_metadata_never_grants_unqualified_longitudinal(restriction):
  cp = factory(CAR.TOYOTA_RAV4H, 8)
  if restriction == 'stock':
    assert rav4h_filter.prepare_stock(cp)
  elif restriction == 'pedal':
    cp.deprecated.enableGasInterceptor = True
  elif restriction == 'unqualified':
    cp.safetyConfigs[0].safetyParam = 73
  elif restriction == 'native':
    cp.safetyConfigs[0].safetyModel = 'noOutput'
  else:
    setattr(cp, {'passive': 'passive', 'dashcam': 'dashcamOnly', 'notcar': 'notCar'}[restriction], True)
  control = LongControl(cp, VehicleStartupPreferences())
  assert control.extension is None or not control.extension.ignore_cruise_standstill
  for _ in range(100):
    assert control.update(True, stopped(), .8, False, (-3., 2.)) <= 0.


@pytest.mark.parametrize('advert', (False, True))
def test_real_rav4h_native_positive_drive_precedes_any_wheel_motion(monkeypatch, host_heartbeat_bridge, advert):
  tick, positive = 0, False
  physical, inputs, accel_command = helpers.physical, helpers.control_inputs, toyotacan.create_accel_command

  def frames(packer, **fields):
    nonlocal tick
    tick += 1
    fields.update(stopped=not positive, brake=tick < 200)
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
      assert tick >= 220 and not cancel
      positive = True
    return accel_command(packer, accel, cancel, *args, **kwargs)

  def inspect(tick, host, state, command, safety, native):
    assert host.CP.autoResumeSng and not host.CI.CC.sng_hack
    assert not Params().get_bool('SNGHack')
    if 180 <= tick < 190:
      assert state.brakePressed and not command.longActive and safety.aol_get_permission_mask() & 2 == 0
    if 210 <= tick < 219:
      assert state.standstill and state.cruiseState.standstill and command.actuators.accel <= 0 and not positive
    if not positive:
      assert state.standstill

  monkeypatch.setattr(helpers, 'physical', frames)
  monkeypatch.setattr(helpers, 'control_inputs', plans)
  monkeypatch.setattr(toyotacan, 'create_accel_command', observe)
  words = (0x9449, 0x9649) if advert else (0x9049, 0x9249)
  helpers.run_case(False, candidate=CAR.TOYOTA_RAV4H, bridge=host_heartbeat_bridge, input_clock=True,
                   filter_advert=advert, sdsu_words=words, on_sample=inspect)
  assert positive
