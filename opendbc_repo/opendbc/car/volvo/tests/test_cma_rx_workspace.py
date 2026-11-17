"""Required RX state survives storage relocation, never a mode/reset boundary."""
import pytest

from opendbc.can import CANPacker
from opendbc.car import structs
from opendbc.car.volvo.tests.test_cma import CMA, interface, packet, sources
from opendbc.car.volvo.tests.test_cma_counter_watchdog import lca2
from opendbc.car.volvo.tests.test_cma_integrity import feed
from opendbc.safety.tests.libsafety import libsafety_py


def command_for(identity, incoming):
  _, ci = interface(identity)
  ci.update([(1_000_000_000, incoming)])
  command = structs.CarControl(latActive=True)
  _, output = ci.apply(command.as_reader(), 1_000_000_000)
  return next(msg for msg in output if msg[0] == 0x58)


def cold_mode(lib, param):
  assert lib.set_safety_hooks(structs.CarParams.SafetyModel.noOutput, 0) == 0
  assert lib.safety_test_volvo_cma_poison_workspace()
  assert lib.set_safety_hooks(structs.CarParams.SafetyModel.volvo, param) == 0
  lib.init_tests()
  if param != 2:  # C1 owns separate state and never consumes CMA caches.
    assert lib.safety_test_volvo_cma_cold_state_zeroed()
  lib.set_timer(10000)
  assert not lib.safety_config_valid()
  assert not lib.get_controls_allowed()


@pytest.mark.parametrize('identity', CMA)
@pytest.mark.parametrize('omitted', range(17))
def test_poisoned_workspace_requires_every_current_profile_source_after_init(identity, omitted):
  lib, incoming = feed(identity)
  old_command = command_for(identity, incoming)
  assert lib.safety_tx_hook(packet(old_command))
  cp, _ = interface(identity)
  cold_mode(lib, cp.safetyConfigs[0].safetyParam)
  # Neither the last active command nor exact prior stock may borrow RX leases.
  assert not lib.safety_tx_hook(packet(old_command))
  stock = next(msg for msg in incoming if msg[0] == 0x58)
  assert not lib.safety_tx_hook(packet((stock[0], stock[1], 2)))
  assert len(incoming) == 17
  for index, msg in enumerate(incoming):
    if index != omitted:
      assert lib.safety_rx_hook(packet(msg))
  assert not lib.safety_config_valid()
  assert not lib.safety_tx_hook(packet(old_command))
  assert lib.safety_rx_hook(packet(incoming[omitted]))
  assert lib.safety_config_valid() and lib.get_controls_allowed()
  assert lib.safety_tx_hook(packet(command_for(identity, incoming)))


def test_cma_spa_c1_switches_reset_identity_status_phase_and_original_stock_leases():
  lib = libsafety_py.libsafety
  previous_data = None
  previous_command = None
  previous_param = None
  for epoch, identity in enumerate(('VOLVO_XC40_RECHARGE', 'POLESTAR_2', 'VOLVO_V40',
                                    'VOLVO_S60_RECHARGE', 'VOLVO_V40', 'VOLVO_XC40_RECHARGE')):
    cp, _ = interface(identity)
    cold_mode(lib, cp.safetyConfigs[0].safetyParam)
    if previous_command is not None:
      assert not lib.safety_tx_hook(packet(previous_command))
    if identity == 'VOLVO_V40':
      packer = CANPacker('volvo_v40_2017_pt')
      incoming = [packer.make_can_msg(name, bus, values) for name, bus, values in [
        ('PSCM1', 0, {'SteeringAngleServo': 0, 'LKATorque': 10}),
        ('FSM0', 2, {'ACCStatusOnOff': 1, 'ACCStatusActive': 1}),
        ('PedalandBrake', 0, {}), ('VehicleSpeed1', 0, {'VehicleSpeed': 72}),
      ]]
      for msg in incoming:
        assert lib.safety_rx_hook(packet(msg))
      assert lib.safety_config_valid() and lib.get_controls_allowed()
      continue
    # Replaying another PT profile cannot fill this profile's required slots.
    if previous_data is not None and (previous_param & 1) != (cp.safetyConfigs[0].safetyParam & 1):
      for msg in previous_data:
        lib.safety_rx_hook(packet(msg))
      assert not lib.safety_config_valid()
      assert not lib.safety_tx_hook(packet(previous_command))
    # Each initialization accepts a different legitimate initial counter phase.
    incoming = [lca2(msg, (epoch * 3) % 16) if msg[0] == 0x69 else msg for msg in sources(identity)]
    # Clear replayed counter phase by a real repeated mode entry, never a forced flag.
    cold_mode(lib, cp.safetyConfigs[0].safetyParam)
    for msg in incoming:
      assert lib.safety_rx_hook(packet(msg))
    assert lib.safety_config_valid() and lib.get_controls_allowed()
    previous_command = command_for(identity, incoming)
    assert lib.safety_tx_hook(packet(previous_command))
    previous_data = incoming
    previous_param = cp.safetyConfigs[0].safetyParam
    # Leave invalid CRC and frozen-counter status in the live workspace.
    base = next(msg for msg in incoming if msg[0] == 0x69)
    for _ in range(7):
      lib.safety_rx_hook(packet(base))
    assert not lib.safety_config_valid()
    changed = bytearray(base[1])
    changed[6] ^= 1
    assert not lib.safety_rx_hook(packet((base[0], bytes(changed), base[2])))
    assert not lib.get_controls_allowed()
    assert not lib.safety_tx_hook(packet(previous_command))
