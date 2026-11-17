"""Actual controller frames stay on the selected Panda and retain native limits."""
from pathlib import Path
from tempfile import TemporaryDirectory
import shutil

import pytest

from opendbc.can import CANPacker
from opendbc.car import Bus, structs
from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai.carcontroller import CarController
from opendbc.car.hyundai.hyundaicanfd import CanBus
from opendbc.car.hyundai.tests.test_ioniq6_handoff import ioniq6_profiles
from opendbc.car.hyundai.values import DBC
from opendbc.safety.tests.libsafety import libsafety_py


@pytest.mark.parametrize('offset', (0, 4))
@pytest.mark.parametrize('alternate', (False, True))
@pytest.mark.parametrize('lateral,longitudinal', ((False, False), (True, False), (False, True), (True, True)))
def test_actual_controller_transport_and_native_axis_authority(offset, alternate, lateral, longitudinal):
  _, _, cp = ioniq6_profiles(bus_offset=offset, alternate=alternate)
  cp.safetyConfigs[-1].safetyParam |= 0x800
  cp.alternativeExperience = 32
  ci = CarInterface(cp)
  packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
  bus = CanBus(cp)
  physical = [packer.make_can_msg(name, bus.ECAN, values) for name, values in (
    ('ACCELERATOR', {'GEAR': 5}), ('TCS', {'ACCEnable': 0, 'ACC_REQ': 1}),
    ('WHEEL_SPEEDS', {}), ('MDPS', {}), ('CRUISE_BUTTONS', {'COUNTER': 1}),
    ('DOORS_SEATBELTS', {'DRIVER_SEATBELT': 1}), ('STEERING_SENSORS', {}))]
  physical.append(packer.make_can_msg('CAM_0x362' if alternate else 'CAM_0x2a4', bus.CAM, {}))
  ci.update([(1_000_000_000, physical)])
  safety = libsafety_py.libsafety
  def packet(frame):
    return libsafety_py.make_CANPacket(frame[0], frame[2] - offset, frame[1])
  release = safety.set_safety_hooks(structs.CarParams.SafetyModel.allOutput, 0) != 0
  assert safety.set_safety_hooks(structs.CarParams.SafetyModel.hyundaiCanfd, cp.safetyConfigs[-1].safetyParam) == 0
  safety.init_tests()
  safety.set_timer(1_000_000)
  safety.set_aol_test_heartbeat(True)
  for frame in physical[:5]:
    assert safety.safety_rx_hook(packet(frame))
  if lateral:
    for counter, pressed in ((2, 1), (3, 0)):
      assert safety.safety_rx_hook(packet(packer.make_can_msg('CRUISE_BUTTONS', bus.ECAN, {'COUNTER': counter, 'LDA_BTN': pressed})))
  if longitudinal:
    for counter, button in ((4, 2), (5, 0)):
      assert safety.safety_rx_hook(packet(packer.make_can_msg('CRUISE_BUTTONS', bus.ECAN, {'COUNTER': counter, 'CRUISE_BUTTONS': button})))
  mask = int(lateral) | (int(longitudinal) << 1)
  safety.aol_set_host_request(mask)
  assert safety.aol_get_permission_mask() == (0 if release else mask)
  cc = structs.CarControl()
  cc.enabled = cc.longActive = longitudinal
  cc.latActive = lateral
  cc.actuators.torque = 0.01
  cc.actuators.accel = 0.1
  cc.actuators.longControlState = structs.CarControl.Actuators.LongControlState.starting
  _, frames = CarController(DBC[cp.carFingerprint], cp).update(cc.as_reader(), ci.CS, 1_000_000_000)
  assert frames and {frame[2] for frame in frames} <= {offset, offset + 1, offset + 2}
  assert next(frame for frame in frames if frame[0] == 0x100).src == offset
  assert all(frame[2] == offset + 1 for frame in frames if frame[0] in (0x1BA, 0x1E5, 0x1A0))
  for frame in frames:
    accepted = safety.safety_tx_hook(packet(frame))
    assert bool(accepted) == (not release), (hex(frame[0]), frame[2])
  if offset:
    with TemporaryDirectory() as directory:
      # A separate loaded library gives the passive internal Panda independent native state.
      library = Path(directory) / 'internal.so'
      shutil.copyfile(libsafety_py.libpath, library)
      aux = libsafety_py.ffi.dlopen(str(library))
      assert aux.set_safety_hooks(structs.CarParams.SafetyModel.noOutput, 0) == 0
      aux.init_tests()
      for frame in frames:
        assert not aux.safety_tx_hook(packet(frame))
