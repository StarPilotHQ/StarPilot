"""A live Toyota LAT grant survives native gas withdrawal during steering override."""
from contextlib import ExitStack
import pytest
from opendbc.car.toyota.values import CAR
from openpilot.cereal import log
from openpilot.starpilot.car.toyota.tests.test_prius_retrofit_advert_factory import factory
from openpilot.starpilot.car.toyota.tests.test_prius_retrofit_advert_fw import complete_firmware
from openpilot.starpilot.aol.tests import test_toyota_native_long_withdrawal as base
from openpilot.starpilot.car.toyota.tests import test_rav4h_caller_native as helpers
from openpilot.starpilot.car.toyota.tests.test_toyota_cpp_transport import host_heartbeat_bridge as host_heartbeat_bridge

source_matched_native = helpers.source_matched_native
daemon_gc_scope = helpers.daemon_gc_scope
IDENTITIES = (*base.IDENTITIES, CAR.TOYOTA_PRIUS_RETROFIT)


def setup(monkeypatch, cleanup, identity):
  original = base.params
  def params(candidate):
    if candidate == CAR.TOYOTA_PRIUS_RETROFIT:
      return factory(candidate)
    return original(candidate)
  original_state = base.state
  def state():
    cs = original_state()
    cs.steeringPressed = True
    cs.steeringTorque = 150.
    return cs
  monkeypatch.setattr(base, 'params', params)
  monkeypatch.setattr(base, 'state', state)
  return base.setup_callers(cleanup, identity)


@pytest.mark.parametrize('identity', IDENTITIES)
def test_real_consumers_keep_live_lateral_for_early_gas_while_steering(monkeypatch, identity):
  with ExitStack() as cleanup:
    sd, _, drive = setup(monkeypatch, cleanup, identity)
    axis, host, command = drive(10, observed_state=base.state(), steering=True, native_request_long=True,
                                long_allowed=False, panda_controls_allowed=True)
    assert log.OnroadEvent.EventName.controlsMismatch not in sd.events.names, 'TOYOTA_STEERING_GAS_FALSE_MISMATCH'
    assert host.enabled and axis.lateralActive and command.latActive
    assert not axis.longitudinalActive and not command.longActive
    for tick in range(11, 21):
      axis, host, command = drive(tick, observed_state=base.state(), steering=True, gas=True,
                                  native_request_long=True, long_allowed=False, panda_controls_allowed=True)
      assert host.enabled and command.latActive and not command.longActive
      assert log.OnroadEvent.EventName.controlsMismatch not in sd.events.names
    axis, _, command = drive(21, observed_state=base.state(), steering=True, native_request_long=True,
                              long_allowed=False, panda_controls_allowed=True)
    assert axis.lateralActive and not command.longActive
    axis, host, command = drive(22, observed_state=base.state(), steering=True, native_request_long=True,
                                long_allowed=True, panda_controls_allowed=True)
    assert host.enabled and axis.longitudinalActive and command.longActive


@pytest.mark.parametrize('fault', ('lat_denied', 'native_invalid', 'rx_fault', 'relay_fault', 'proof_missing'))
def test_steering_override_never_hides_critical_refusals(monkeypatch, fault):
  with ExitStack() as cleanup:
    sd, _, drive = setup(monkeypatch, cleanup, CAR.TOYOTA_PRIUS_RETROFIT)
    kwargs = {'observed_state': base.state(), 'steering': True, 'native_request_long': True, 'long_allowed': False, 'panda_controls_allowed': True}
    if fault == 'lat_denied':
      kwargs['allowed'] = False
    elif fault == 'native_invalid':
      kwargs['native_valid'] = False
    elif fault == 'rx_fault':
      kwargs['panda_fault'] = True
    elif fault == 'relay_fault':
      kwargs['relay_fault'] = True
    else:
      sd.aol_published_input_proof = None
    axis, host, command = drive(10, **kwargs)
    assert log.OnroadEvent.EventName.controlsMismatch in sd.events.names
    assert not host.enabled and not axis.lateralActive and not axis.longitudinalActive
    assert not command.latActive and not command.longActive


def test_actual_advert_prius_native_gas_precedes_delayed_card_while_steering(monkeypatch, host_heartbeat_bridge):
  tick = 0
  queued = None
  physical = helpers.physical
  encode = helpers.card_module.can_list_to_can_capnp

  def frames(packer, **fields):
    nonlocal tick
    tick += 1
    fields['gas'] = 301 <= tick < 331
    fields['sdsu'] = None
    frames = physical(packer, **fields)
    frames.extend(((0x2FF, bytes.fromhex('80007409'), 0), (0x2FF, bytes.fromhex('80007409'), 2)))
    steer = packer.make_can_msg('STEER_TORQUE_SENSOR', 0, {'STEER_TORQUE_DRIVER': 150 if tick >= 240 else 0})
    return [steer if f[0] == steer[0] and f[2] == 0 else f for f in frames]

  def delayed_host_frames(frames, *args, **kwargs):
    nonlocal queued
    if kwargs.get('msgtype', 'can') != 'can':
      return encode(frames, *args, **kwargs)
    prior = frames if queued is None else queued
    queued = frames
    return encode(prior, *args, **kwargs)

  def inspect(tick, host, state, command, safety, native):
    if tick == 301:
      assert state.steeringPressed and not state.gasPressed
      assert safety.aol_get_permission_mask() == 1
      assert command.latActive and not command.longActive, 'TOYOTA_STEERING_GAS_FALSE_MISMATCH'
    if 301 <= tick < 331:
      assert command.enabled and command.latActive and not command.longActive
    if tick >= 360:
      assert command.enabled and command.latActive and command.longActive

  monkeypatch.setattr(helpers.card_module, 'can_list_to_can_capnp', delayed_host_frames)
  helpers.run_case(False, scenario='gas_steering', candidate=CAR.TOYOTA_PRIUS_RETROFIT,
                   bridge=host_heartbeat_bridge, input_clock=True, filter_advert=True,
                   sdsu_words=(0xD049, 0xD249), firmware_override=complete_firmware(),
                   fingerprint_frames=[(0x2FF, bytes(4), 0)], manual_selection=True, manual_filter_length=4,
                   physical_frames=frames, on_sample=inspect)
