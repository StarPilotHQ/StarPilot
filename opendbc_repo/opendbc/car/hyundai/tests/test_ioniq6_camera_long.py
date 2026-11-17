import os
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from opendbc.car import Bus, gen_empty_fingerprint, structs
from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai.carcontroller import CarController
from opendbc.car.hyundai.canfd_stock_aol import qualified, qualified_long
from opendbc.car.hyundai.hyundaicanfd import CanBus
from opendbc.car.hyundai.tests.test_ioniq6_handoff import ioniq6_profiles
from opendbc.car.hyundai.values import CAR, DBC
from opendbc.can import CANPacker
from opendbc.safety.tests.libsafety import libsafety_py
from opendbc.safety.tests import test_hyundai_gv60_hda1_long_aol as native_fixture
from openpilot.common.params import Params
from openpilot.starpilot.car.hyundai.aol import policy_for, native_accepts_cp
with patch.dict(os.environ, {"SCALE": "1"}):
  from openpilot.selfdrive.ui.layouts.settings import toggles


def camera_params(offset=0, alpha=False):
  fp = gen_empty_fingerprint()
  fp[offset].update({0x1CF: 8, 0x35: 32, 0x175: 24, 0xA0: 24, 0xEA: 24, 0x1BA: 24, 0x1E5: 16})
  fp[offset + 2].update({0x12A: 16, 0x1A0: 32})
  fp[offset + 1].update({0x110: 32, 0x100: 24})
  return CarInterface.get_params(CAR.HYUNDAI_IONIQ_6, fp, [], alpha, False, False)


@pytest.mark.parametrize('offset', (0, 4))
def test_camera_scc_factory_offers_long_without_hda2_takeover(offset):
  stock = camera_params(offset)
  cp = camera_params(offset, True)
  assert stock.alphaLongitudinalAvailable, 'HDA1_ALPHA_CAPABILITY_MISSING'
  assert not stock.openpilotLongitudinalControl and stock.pcmCruise
  assert cp.alphaLongitudinalAvailable and cp.openpilotLongitudinalControl and not cp.pcmCruise
  assert cp.steerControlType == structs.CarParams.SteerControlType.torque
  assert cp.safetyConfigs[-1].safetyParam == 0x0D
  assert CanBus(cp).ECAN == offset
  assert CarController(DBC[cp.carFingerprint], cp).ioniq6_longitudinal is None
  with patch('opendbc.car.hyundai.interface.disable_ecu') as disable:
    CarInterface.init(cp, Mock(), Mock())
  disable.assert_not_called()
  for value in (stock, cp):
    policy = policy_for(value)
    assert policy.runtime_supported and policy.explicit_latch
    value.safetyConfigs[-1].safetyParam |= policy.safety_param_addition
    value.alternativeExperience |= policy.alternative_experience_addition
    word = 0x80D if value.openpilotLongitudinalControl else 0x809
    assert value.safetyConfigs[-1].safetyParam == word
    assert native_accepts_cp(value, int(structs.CarParams.SafetyModel.hyundaiCanfd), word)
    assert not native_accepts_cp(value, int(structs.CarParams.SafetyModel.hyundai), word)
    assert not native_accepts_cp(value, int(structs.CarParams.SafetyModel.hyundaiCanfd), word ^ 16)


@pytest.mark.parametrize('offset', (0, 4))
def test_external_hda2_settings_explain_opt_in_without_claiming_stock_acc_authority(offset):
  _, stock, _ = ioniq6_profiles(bus_offset=offset, alternate=True)
  with TemporaryDirectory() as root:
    params = Params(root)
    params.put_bool('ExperimentalMode', True, block=True)
    class Toggle:
      def __init__(self):
        self.action_item = self
        self.description = ''
      def set_enabled(self, enabled):
        self.enabled = enabled
      def set_description(self, description):
        self.description = description
      def set_state(self, state):
        self.state = state
    panel = toggles.TogglesLayout.__new__(toggles.TogglesLayout)
    panel._params = params
    panel._is_release = False
    panel._initial_cruise_speed = Mock()
    panel._long_personality_setting = SimpleNamespace(action_item=Toggle())
    panel._toggles = {'ExperimentalMode': Toggle(), 'IsRhdDetected': Toggle()}
    panel._toggle_defs = {}
    panel._locked_toggles = set()
    panel._update_experimental_mode_icon = Mock()
    panel._slc_offsets = SimpleNamespace(_raw=lambda key: None)
    panel._localization = SimpleNamespace(snapshot=lambda: SimpleNamespace(rows=(None, SimpleNamespace(source=None, available=True))))
    ui = SimpleNamespace(CP=stock, has_longitudinal_control=False, update_params=lambda: None)
    with patch.object(toggles, 'ui_state', ui):
      panel._update_toggles()
    assert 'Enable the openpilot longitudinal control (alpha)' in panel._toggles['ExperimentalMode'].description
    assert not panel._toggles['ExperimentalMode'].enabled
    assert params.get_bool('ExperimentalMode')


@pytest.mark.parametrize('offset', (0, 4))
def test_camera_long_actual_controller_packets_use_existing_native_contract(offset):
  cp = camera_params(offset, True)
  assert cp.openpilotLongitudinalControl
  cp.safetyConfigs[-1].safetyParam |= 0x800
  cp.alternativeExperience = 32
  ci = CarInterface(cp)
  packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
  bus = CanBus(cp)
  frames = [packer.make_can_msg(name, bus.ECAN, values) for name, values in (
    ('ACCELERATOR', {'GEAR': 5}), ('TCS', {'ACCEnable': 0}), ('WHEEL_SPEEDS', {}), ('MDPS', {}),
    ('CRUISE_BUTTONS', {'COUNTER': 1}), ('DOORS_SEATBELTS', {'DRIVER_SEATBELT': 1}), ('STEERING_SENSORS', {}))]
  ci.update([(1_000_000_000, frames)])
  case = native_fixture.TestGv60Hda1LongAol(methodName='runTest')
  case.setUp()
  try:
    case.mode(0x80D)
    if case.release:
      assert not case.safety.safety_config_valid()
      return
    case.healthy(0x80D, omit='CRUISE_BUTTONS')
    case.safety.set_aol_test_heartbeat(True)
    case.safety.aol_set_host_request(3)
    for tick, counter, button in ((0, 13, 0), (1, 14, 2), (2, 0, 0)):
      case.safety.set_timer(1_000_000 + tick * 20_000)
      assert case.safety.safety_rx_hook(case.packet(packer.make_can_msg('CRUISE_BUTTONS', 0, {'COUNTER': counter, 'CRUISE_BUTTONS': button})))
    case.safety.aol_set_host_request(3)
    assert case.safety.aol_get_permission_mask() == 3
    cc = structs.CarControl(enabled=True, latActive=True, longActive=True)
    cc.actuators.torque = .01
    cc.actuators.accel = .1
    cc.actuators.longControlState = structs.CarControl.Actuators.LongControlState.pid
    controller = CarController(DBC[cp.carFingerprint], cp)
    assert controller.ioniq6_longitudinal is None
    _, sent = controller.update(cc.as_reader(), ci.CS, 1_000_000_000)
    assert any(frame[0] == 0x1A0 for frame in sent)
    assert not any(frame[0] == 0x730 for frame in sent)
    for frame in sent:
      assert frame[2] in (offset, offset + 1, offset + 2)
      packet = libsafety_py.make_CANPacket(frame[0], frame[2] - offset, frame[1])
      assert case.safety.safety_tx_hook(packet), (hex(frame[0]), frame[2])
  finally:
    case.tearDown()


@pytest.mark.parametrize('mutation', ('aux_word', 'wrong_model', 'angle', 'lka', 'passive'))
def test_camera_admission_never_borrows_other_topologies(mutation):
  cp = camera_params(4, True)
  cp.safetyConfigs[-1].safetyParam |= 0x800
  cp.alternativeExperience = 32
  if mutation == 'aux_word':
    cp.safetyConfigs[0].safetyParam = 1
  elif mutation == 'wrong_model':
    cp.safetyConfigs[-1].safetyModel = structs.CarParams.SafetyModel.hyundai
  elif mutation == 'angle':
    cp.steerControlType = structs.CarParams.SteerControlType.angle
  elif mutation == 'lka':
    cp.flags |= 1
  else:
    cp.passive = True
  assert not qualified(cp) and not qualified_long(cp)
  assert not policy_for(cp).runtime_supported


@pytest.mark.parametrize('offset', (0, 4))
def test_galaxy_exposes_external_stock_alpha_request_and_preserves_choice(offset):
  from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner
  _, stock, _ = ioniq6_profiles(bus_offset=offset, alternate=True)
  with TemporaryDirectory() as root:
    params = Params(root)
    params.put_bool('IsReleaseBranch', False, block=True)
    params.put_bool('AlphaLongitudinalEnabled', True, block=True)
    stock.radarUnavailable = True
    owner = FeatureSettingsOwner(params, lambda scope: True,
      vehicle_params=lambda: stock, vehicle_fingerprint=lambda: str(stock.carFingerprint), galaxy=lambda: True)
    assert owner._alpha_long_capability() is not None
    rows = owner._alpha_long_rows(True)
    assert len(rows) == 1 and rows[0].key == 'AlphaLongitudinalEnabled'
    assert params.get_bool('AlphaLongitudinalEnabled')
    stock.passive = True
    assert owner._alpha_long_capability() is None
    assert params.get_bool('AlphaLongitudinalEnabled')


@pytest.mark.parametrize('offset', (0, 4))
def test_cached_radar_discovery_can_save_request_but_cannot_grant_long_or_aol(offset):
  from opendbc.car.hyundai.ioniq6_handoff import build_ioniq6_hda2_long_candidate
  from openpilot.starpilot.car.hyundai.aol import ioniq6_alpha_long_available, ioniq6_settings_capable
  fingerprint, stock, _ = ioniq6_profiles(bus_offset=offset, alternate=True)
  stock.radarUnavailable = True
  assert ioniq6_alpha_long_available(stock), 'CACHED_EXTERNAL_RADAR_BLOCKS_ALPHA_REQUEST'
  assert not ioniq6_settings_capable(stock)
  assert not policy_for(stock).runtime_supported
  assert build_ioniq6_hda2_long_candidate(stock, fingerprint) is None
  assert not stock.openpilotLongitudinalControl and stock.pcmCruise
  stock.safetyConfigs[-1].safetyParam ^= 16
  assert not ioniq6_alpha_long_available(stock)
