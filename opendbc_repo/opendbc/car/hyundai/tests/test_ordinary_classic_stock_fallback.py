from types import SimpleNamespace
from unittest.mock import patch
import os

import pytest

from opendbc.can import CANPacker
from opendbc.car import car_helpers, gen_empty_fingerprint, structs
from opendbc.car.can_definitions import CanData
from opendbc.car.hyundai.classic_scc_aol import qualified as stock_qualified
from opendbc.car.hyundai.ecu_startup import Outcome
from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai.ioniq6_handoff import TimestampedCanPacket
from opendbc.car.hyundai.ordinary_classic_startup import CARS, OrdinaryClassicStartup
from opendbc.car.hyundai.values import CAR, HyundaiFlags
from opendbc.safety.tests.test_hyundai import checksum
from opendbc.safety.tests.libsafety import libsafety_py
from openpilot.cereal import messaging
from openpilot.common.params import Params
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.selfdrive.car import card as card_module
from openpilot.starpilot.aol.vehicle import policy_for


def stock_native_frames(ci, tick, *, main=0, lkas=0, button=0, engaged=False):

  assert stock_qualified(ci.CP)
  packer = CANPacker('hyundai_can_generated')

  gas_name = 'E_EMS11' if ci.CP.flags & (HyundaiFlags.HYBRID | HyundaiFlags.EV) else 'EMS16'
  hz = {gas_name: 100, 'ELECT_GEAR': 20, 'WHL_SPD11': 100, 'TCS13': 100, 'MDPS12': 50, 'CLU11': 50, 'BCM_PO_11': 50, 'LKAS11': 100}
  scheduled = {(packer.dbc.name_to_msg[name].address, 0): name for name in hz if name != 'LKAS11'}
  for parser in ci.can_parsers.values():
    for address in parser.addresses:
      scheduled[(address, parser.bus)] = parser.dbc.addr_to_msg[address].name
  drive_gear = next(code for code, label in ci.CS.shifter_values.items() if ci.CS.parse_gear_shifter(label) == structs.CarState.GearShifter.drive)
  frames = []
  for (address, bus), name in sorted(scheduled.items()):
    parser = next((p for p in ci.can_parsers.values() if p.bus == bus), None)
    declared = parser.message_states[address].frequency if parser is not None and address in parser.addresses else 0
    frequency = hz.get(name, min(declared, 100) if 0 < declared <= 100 else 100)
    period = max(1, round(100 / frequency))
    if tick % period:
      continue
    counter = tick // period
    values = {}
    if name == 'CLU11':
      values.update(CF_Clu_AliveCnt1=counter % 16, CF_Clu_CruiseSwState=button, CF_Clu_CruiseSwMain=main)
    elif name == 'EMS16':
      values.update(AliveCounter=counter % 4, CF_Ems_AclAct=0)
    elif name == 'E_EMS11':
      values['CR_Vcu_AccPedDep_Pos'] = 0
    elif name == 'ELECT_GEAR':
      values['Elect_Gear_Shifter'] = drive_gear
    elif name == 'WHL_SPD11':
      values.update(
        WHL_SPD_AliveCounter_LSB=counter % 4, WHL_SPD_AliveCounter_MSB=(counter // 4) % 4, WHL_SPD_FL=72, WHL_SPD_FR=72, WHL_SPD_RL=72, WHL_SPD_RR=72
      )
    elif name == 'TCS13':
      values.update(AliveCounterTCS=counter % 8, ACCEnable=0, ACC_REQ=int(engaged), DriverOverride=0)
    elif name == 'MDPS12':
      values.update(CR_Mdps_StrColTq=0, CR_Mdps_OutTq=0)
    elif name == 'BCM_PO_11':
      values['LDA_BTN'] = lkas
    elif name == 'CGW1':
      values['CF_Gway_DrvSeatBeltSw'] = 1
    elif name == 'LVR12':
      values['CF_Lvr_Gear'] = drive_gear
    elif name == 'TCU12':
      values['CUR_GR'] = drive_gear
    elif name == 'SAS11':
      values['SAS_Angle'] = 1.0
    elif name == 'CLU15':
      values.update(CF_Clu_VehicleSpeed=72, CF_Clu_Gear=drive_gear)
    if name == 'SCC12':
      values.update(ACCMode=0, CR_VSM_Alive=counter % 16)
    frame = packer.make_can_msg(name, bus, values)
    if name in ('EMS16', 'WHL_SPD11', 'TCS13', 'SCC12'):
      address, data, bus = checksum(frame)
      frame = (address, bytes(data), bus)
    frames.append(frame)
  assert all(address != 0x340 or bus == 2 for address, _, bus in frames), 'Never echo outgoing LKAS on protected RX bus'
  return frames


@pytest.mark.parametrize('identity', sorted(CARS, key=str))
@pytest.mark.parametrize('lda', [False, True])
@pytest.mark.parametrize('aol', [False, True])
def test_actual_factory_rejection_restores_and_warms_exact_stock_before_publication(identity, lda, aol):
  native = libsafety_py.libsafety
  now = [10_000_000_000]
  owners, requests, warm_sources = [], [], []
  card = None
  with OpenpilotPrefix(), patch.dict(os.environ, {'SIMULATION': '0', 'AOL_REPLAY_RUNTIME': '1'}):
    saved = Params()
    for key, value in [
      ('OpenpilotEnabledToggle', True),
      ('AlphaLongitudinalEnabled', True),
      ('IsReleaseBranch', False),
      ('IsOffroad', False),
      ('AlwaysOnLateral', aol),
      ('ExperimentalMode', False),
    ]:
      saved.put_bool(key, value, block=True)
    saved.put('LKASButtonControl', 9, block=True)
    saved.put('MainCruiseButtonControl', 0, block=True)
    keys = ['AlphaLongitudinalEnabled', 'AlwaysOnLateral', 'LKASButtonControl', 'MainCruiseButtonControl', 'ExperimentalMode']
    settings = {key: saved.get(key) for key in keys}
    fingerprint = gen_empty_fingerprint()
    if lda:
      fingerprint[0][0x391] = 8
    desired = CarInterface.get_params(identity, fingerprint, [], True, False, False)
    expected = CarInterface.get_params(identity, fingerprint, [], False, False, False)
    expected.carVin = '0' * 17
    expected.carFw = []
    expected.fingerprintSource = desired.fingerprintSource
    expected.fuzzyFingerprint = False
    if identity == CAR.HYUNDAI_I30_2026:
      assert desired.dashcamOnly and expected.dashcamOnly
      assert not desired.openpilotLongitudinalControl and not expected.openpilotLongitudinalControl
      assert desired.safetyConfigs[0].safetyModel == expected.safetyConfigs[0].safetyModel == structs.CarParams.SafetyModel.noOutput
      return
    assert desired.openpilotLongitudinalControl and not expected.openpilotLongitudinalControl
    identified = (identity, fingerprint, '0' * 17, [], desired.fingerprintSource, True)
    native.set_alternative_experience(0)
    assert native.set_safety_hooks(structs.CarParams.SafetyModel.elm327, 1) == 0
    native.init_tests()
    factory = CarInterface.startup_owner

    def send(frames):
      assert saved.get('CarParams') is None
      assert native.get_current_safety_mode() == int(structs.CarParams.SafetyModel.elm327)
      assert not native.get_controls_allowed() and native.aol_get_permission_mask() == 0
      for address, data, bus in frames:
        assert native.safety_tx_hook(libsafety_py.make_CANPacket(address, bus, data))

    def receive(*args, **kwargs):
      if not owners or owners[0].ci is None:
        return []
      owner = owners[0]
      assert owner.restored and not owner.published and saved.get('CarParams') is None
      assert not owner.ci.CP.openpilotLongitudinalControl and owner.ci.CP.pcmCruise
      now[0] += 10_000_000
      frames = []
      for parser in owner.ci.can_parsers.values():
        packer = CANPacker(parser.dbc.name)
        for address in parser.addresses:
          name = parser.dbc.addr_to_msg[address].name
          frame = CanData(*packer.make_can_msg(name, parser.bus, {}))
          assert native.safety_rx_hook(libsafety_py.make_CANPacket(frame.address, frame.src, frame.dat))
          frames.append(frame)
      warm_sources.append(now[0])
      return [TimestampedCanPacket(frames, now[0])]

    def query(sender, receiver, bus, addresses, commands, responses):
      command = commands[0]
      requests.append(command)
      assert bus == 0 and addresses == [(0x7D0, None)]
      assert command in (b'\x10\x03', b'\x28\x83\x01', b'\x28\x00\x01', b'\x10\x01')

      def data(timeout):
        sender([(0x7D0, bytes([len(command)]) + command + bytes(7 - len(command)), 0)])
        return {(0x7D0, None): b'\x7f\x28\x22' if command == b'\x28\x83\x01' else b''}

      return SimpleNamespace(get_data=data)

    def traced_factory(cp, callbacks, *, requested):
      owner = factory(cp, callbacks, requested=requested)
      assert isinstance(owner, OrdinaryClassicStartup)
      # This fixture exercises the explicit eager startup/fallback API.
      # Default deferred takeover is covered by test_classic_deferred_takeover.
      owner.prepare_when_controls_ready = owner.prepare
      owner._query = query
      owners.append(owner)
      return owner

    def panda_update(sm, *args, **kwargs):
      if 'pandaStates' not in sm.services:
        return
      message = messaging.new_message('pandaStates', 1, valid=True, logMonoTime=now[0])
      panda = message.pandaStates[0]
      panda.safetyModel, panda.safetyParam = native.get_current_safety_mode(), native.get_current_safety_param()
      panda.alternativeExperience, panda.controlsAllowed = 0, native.get_controls_allowed()
      panda.safetyRxChecksInvalid = native.safety_test_toyota_rx_summary_invalid()
      panda.ignitionLine = True
      sm.update_msgs(now[0] / 1e9, [message.as_reader()])

    def elapsed_sleep(seconds):
      now[0] += round(seconds * 1e9)

    initial = messaging.new_message('can', 1, valid=True, logMonoTime=now[0])
    initial.can[0].address, initial.can[0].src, initial.can[0].dat = 0x391, 0, bytes(8)
    try:
      with (
        patch.object(car_helpers, 'fingerprint', return_value=identified),
        patch.object(CarInterface, 'startup_owner', side_effect=traced_factory),
        patch.object(card_module, 'can_comm_callbacks', return_value=(receive, send)),
        patch.object(card_module.messaging, 'recv_one_retry', return_value=initial.as_reader()),
        patch.object(messaging.SubMaster, 'update', panda_update),
        patch('time.clock_gettime_ns', side_effect=lambda clock: now[0]),
        patch('time.monotonic_ns', side_effect=lambda: now[0]),
        patch('time.monotonic', side_effect=lambda: now[0] / 1e9),
        patch('time.sleep', side_effect=elapsed_sleep),
      ):
        card = card_module.Car()
      assert len(owners) == 1
      owner = owners[0]
      assert owner.outcome is Outcome.STOCK_RESTORED and owner.restored and owner.ready and owner.published
      assert owner.keeper is None and owner.prepared_for(card.CP)
      assert warm_sources and all(stamp > owner.floor_ns for stamp in warm_sources)
      assert requests == [b'\x10\x03', b'\x28\x83\x01', b'\x10\x03', b'\x28\x00\x01', b'\x10\x01']
      policy = policy_for(expected)
      if aol:
        expected.safetyConfigs[0].safetyParam |= policy.safety_param_addition
        expected.alternativeExperience |= policy.alternative_experience_addition
      assert card.CP.to_dict() == expected.to_dict()
      assert stock_qualified(card.CP, marked_only=aol)
      assert not card.CP.openpilotLongitudinalControl and card.CP.pcmCruise
      assert not int(card.CP.safetyConfigs[0].safetyParam) & 4
      assert not saved.get_bool('ControlsReady')
      assert {key: saved.get(key) for key in keys} == settings
      with structs.CarParams.from_bytes(saved.get('CarParams')) as published:
        assert published.to_dict() == expected.to_dict()
      native.set_alternative_experience(card.CP.alternativeExperience)
      config = card.CP.safetyConfigs[0]
      assert native.set_safety_hooks(int(config.safetyModel.raw), int(config.safetyParam)) == 0
      native.init_tests()
      native.set_alternative_experience(card.CP.alternativeExperience)
      assert native.get_current_safety_mode() == int(config.safetyModel.raw)
      assert native.get_current_safety_param() == int(config.safetyParam)
      for tick in range(1, 121):
        now[0] += 10_000_000
        native.set_timer(now[0] // 1000)
        for address, payload, bus in stock_native_frames(card.CI, tick):
          assert native.safety_rx_hook(libsafety_py.make_CANPacket(address, bus, payload)), (identity, hex(address), bus)
        if tick % 100 == 0:
          native.safety_tick()
        assert not native.get_relay_malfunction()
      assert native.safety_config_valid()
      assert not native.safety_test_toyota_rx_summary_invalid()
      assert not native.get_controls_allowed() and native.aol_get_permission_mask() == 0
    finally:
      if card is not None:
        card.vehicle_startup.close()
      elif owners:
        owners[0].close()
