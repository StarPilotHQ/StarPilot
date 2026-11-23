import unittest
from unittest.mock import patch

from opendbc.can import CANPacker, CANParser
from opendbc.car import Bus, gen_empty_fingerprint, structs
from opendbc.car.hyundai.hyundaican import hyundai_checksum
from opendbc.car.hyundai.hyundaicanfd import CanBus
from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai.values import CAR, DBC, CarControllerParams, HyundaiFlags


def params(topology='hdai', *, adas=False, alpha=False, release=False):
  fp = gen_empty_fingerprint()
  if topology == 'hdaii':
    fp[2][0x50] = 16
  elif topology == '110':
    fp[2][0x110] = 32
  fw = []
  if adas:
    item = structs.CarParams.CarFw()
    item.ecu = structs.CarParams.Ecu.adas
    fw.append(item)
  return CarInterface.get_params(CAR.HYUNDAI_PALISADE_2023, fp, fw, alpha, release, False)


def feed(ci, tick, *, main=True, cruise=1, display=0, gas=False, brake=False, torque=0, camera=True):
  packer = CANPacker(DBC[ci.CP.carFingerprint][Bus.pt])
  messages = []
  for parser in ci.can_parsers.values():
    for address in parser.addresses:
      if address == 0x2A4 and camera is False:
        continue
      message = parser.dbc.addr_to_msg[address]
      values = {'COUNTER': tick % 16} if 'COUNTER' in message.sigs else {}
      if address == 0x2A4 and type(camera) is int:
        values['COUNTER'] = camera
      if message.name == 'SCC12':
        values.update(MainMode_ACC=int(main), ACCMode=cruise, SCCInfoDisplay=display, VSetDis=90)
      elif message.name == 'EMS16':
        values['CF_Ems_AclAct'] = int(gas)
      elif message.name == 'TCS13':
        values['DriverOverride'] = 2 if brake else 0
      elif message.name == 'MDPS12':
        values['CR_Mdps_StrColTq'] = torque
      elif message.name == 'CGW1':
        values['CF_Gway_DrvSeatBeltSw'] = 1
      elif message.name == 'ALERTS_364':
        values.update(BYTE2=0x52, BYTE3=0x31, BYTE5=0x75, BYTE6=0xA6, BYTE7=0x17, DAW_Warning=1)
      messages.append(packer.make_can_msg(message.name, parser.bus, values))
  return ci.update([(1_000_000_000 + tick * 10_000_000, messages)])


class TestPalisade2023(unittest.TestCase):
  def test_exact_topology_stock_cp_and_original_driver_limits(self):
    for topology, adas, hdaii in [('hdai', False, False), ('110', False, False), ('hdaii', False, True), ('hdai', True, True)]:
      for alpha in (False, True):
        for release in (False, True):
          cp = params(topology, adas=adas, alpha=alpha, release=release)
          self.assertEqual(cp.safetyConfigs[-1].safetyParam, 0x2010 if hdaii else 0x2000)
          self.assertEqual(cp.safetyConfigs[-1].safetyModel, structs.CarParams.SafetyModel.hyundai)
          self.assertEqual(cp.steerControlType, structs.CarParams.SteerControlType.torque)
          self.assertEqual(cp.alphaLongitudinalAvailable, not release)
          self.assertFalse(cp.openpilotLongitudinalControl or cp.dashcamOnly)
          self.assertTrue(cp.pcmCruise)
          self.assertAlmostEqual(cp.stopAccel, -.85, places=6)
          self.assertEqual((CanBus(cp).ECAN, CanBus(cp).ACAN, CanBus(cp).CAM), (1, 0, 2) if hdaii else (0, 1, 2))
          limits = CarControllerParams(cp)
          self.assertEqual((limits.STEER_MAX, limits.STEER_DELTA_UP, limits.STEER_DELTA_DOWN, limits.STEER_DRIVER_ALLOWANCE),
                           (384, 3, 7, 50) if hdaii else (404, 2, 3, 50))
          ci = CarInterface(cp)
          with patch('opendbc.car.hyundai.interface.disable_ecu') as disable:
            ci.init(cp, list, lambda _: None)
          disable.assert_not_called()
          parser = ci.can_parsers[Bus.pt]
          self.assertEqual(parser.bus, int(hdaii))
          self.assertEqual(parser.message_states[0x421].frequency, 50)
          if hdaii:
            self.assertEqual(ci.can_parsers[Bus.cam].message_states[0x2A4].frequency, 20)

  def test_actual_parser_stock_cruise_gas_brake_and_mixed_camera_sources(self):
    for topology in ('hdai', 'hdaii'):
      ci = CarInterface(params(topology))
      for tick in range(12):
        result = feed(ci, tick, main=True, cruise=1, display=4, gas=True, brake=True, torque=175)
      self.assertTrue(result.canValid)
      self.assertTrue(result.cruiseState.available and result.cruiseState.enabled and result.cruiseState.standstill)
      self.assertAlmostEqual(result.cruiseState.speed, 25., places=6)
      self.assertTrue(result.gasPressed and result.brakePressed and result.steeringPressed)
      self.assertFalse(result.stockAeb or result.stockFcw)
      result = feed(ci, 12, main=False, cruise=0, display=2)
      self.assertFalse(result.cruiseState.available or result.cruiseState.enabled or result.cruiseState.standstill)
      self.assertTrue(result.cruiseState.nonAdaptive)
      self.assertFalse(result.gasPressed or result.brakePressed)
      self.assertEqual(bool(ci.CS.lkas11), topology == 'hdai')

  def test_actual_controller_stock_frames_crc_opaque_alerts_and_no_longitudinal_tx(self):
    for topology in ('hdai', 'hdaii'):
      ci = CarInterface(params(topology))
      for tick in range(12):
        feed(ci, tick)
      command = structs.CarControl()
      command.enabled = command.latActive = True
      command.actuators.torque = 1.
      command.hudControl.setSpeed = 25.
      previous = 0
      for tick in range(20):
        feed(ci, tick + 12, camera=12 + (tick + 3) // 5 if tick == 0 or tick % 5 == 2 else False)
        actuators, messages = ci.apply(command.as_reader(), 1_200_000_000 + tick * 10_000_000)
        self.assertEqual(actuators.torqueOutputCan - previous, 3 if topology == 'hdaii' else 2)
        previous = actuators.torqueOutputCan
        addresses = {a for a, _, _ in messages}
        self.assertFalse(addresses & {0x420, 0x421, 0x389, 0x7D0, 0x730})
        if topology == 'hdaii':
          self.assertEqual(addresses, {0x50, 0x2A4} if tick in (0, 5, 10, 15) else {0x50})
          self.assertTrue(all(bus == 0 for _, _, bus in messages))
        else:
          self.assertEqual(addresses, {0x340, 0x364})
          for _, data, bus in messages:
            self.assertEqual(bus, 0)
            self.assertEqual(data[0], hyundai_checksum(data[1:8]))
          alert = next(m for m in messages if m[0] == 0x364)
          decoder = CANParser(DBC[ci.CP.carFingerprint][Bus.pt], [('ALERTS_364', 0)], 0)
          decoder.update([(1_200_000_000 + tick * 10_000_000, [alert])])
          self.assertEqual([decoder.vl['ALERTS_364'][k] for k in ('BYTE2', 'BYTE3', 'BYTE5', 'BYTE6', 'BYTE7')],
                           [0x52, 0x31, 0x75, 0xA6, 0x17])
          self.assertEqual(decoder.vl['ALERTS_364']['DAW_Warning'], 0)
      command.latActive = False
      actuators, _ = ci.apply(command.as_reader(), 1_500_000_000)
      self.assertEqual(actuators.torqueOutputCan, 0)

  def test_hda2_camera_copy_follows_new_input_instead_of_free_running(self):
    ci = CarInterface(params('hdaii'))
    for tick in range(12):
      feed(ci, tick)
    command = structs.CarControl(enabled=True, latActive=True)
    copied = []
    for frame in range(20):
      tick = frame + 12
      feed(ci, tick, camera={0: 12, 6: 13, 11: 14, 16: 15}.get(frame, False))
      _, messages = ci.apply(command.as_reader(), 1_000_000_000 + tick * 10_000_000)
      copied.extend((frame, data[2]) for address, data, _ in messages if address == 0x2A4)
    self.assertEqual(copied, [(0, 12), (6, 13), (11, 14), (16, 15)])

  def test_hda2_alpha_long_keeps_primary_steering_payload(self):
    from opendbc.car.hyundai.blended_longitudinal import candidate_from_stock

    stock = params('hdaii')
    alpha = candidate_from_stock(stock, alpha_requested=True, native_qualified=True)
    self.assertTrue(alpha.openpilotLongitudinalControl)
    interfaces = [CarInterface(cp) for cp in (stock, alpha)]
    command = structs.CarControl(enabled=True, latActive=True)
    command.actuators.torque = .25
    for tick in range(12):
      steering = []
      for ci in interfaces:
        feed(ci, tick)
        _, messages = ci.apply(command.as_reader(), 1_000_000_000 + tick * 10_000_000)
        steering.append(next((data, bus) for address, data, bus in messages if address == 0x50))
      self.assertEqual(steering[0], steering[1])
      self.assertEqual(steering[0][0][8], 0)

  def test_cancel_resume_route_to_exact_pt_bus_and_sibling_params_unchanged(self):
    for topology in ('hdai', 'hdaii'):
      ci = CarInterface(params(topology))
      for tick in range(12):
        feed(ci, tick)
      command = structs.CarControl()
      command.cruiseControl.cancel = True
      for tick in range(11):
        _, messages = ci.apply(command.as_reader(), 1_200_000_000 + tick * 10_000_000)
      buttons = [m for m in messages if m[0] == 0x4F1]
      self.assertEqual(len(buttons), 1)
      self.assertEqual(buttons[0][2], int(topology == 'hdaii'))
      command.cruiseControl.cancel = False
      command.cruiseControl.resume = True
      ci.CC.frame = 20
      _, messages = ci.apply(command.as_reader(), 1_500_000_000)
      buttons = [m for m in messages if m[0] == 0x4F1]
      self.assertEqual(len(buttons), 25)
      self.assertTrue(all(m[2] == int(topology == 'hdaii') for m in buttons))
    cp = CarInterface.get_params(CAR.HYUNDAI_PALISADE, gen_empty_fingerprint(), [], False, False, False)
    self.assertEqual(cp.safetyConfigs[-1].safetyParam, 0)
    self.assertFalse(cp.flags & HyundaiFlags.CANFD_LKA_STEER_MSG)
    self.assertEqual(DBC[cp.carFingerprint][Bus.pt], 'hyundai_can_generated')

  def test_required_source_loss_wrong_bus_and_optional_blended_lfa_payload(self):
    for topology in ('hdai', 'hdaii'):
      cp = params(topology)
      cp.flags |= HyundaiFlags.SEND_LFA.value
      ci = CarInterface(cp)
      for tick in range(12):
        result = feed(ci, tick)
      self.assertTrue(result.canValid)
      command = structs.CarControl()
      command.latActive = True
      command.hudControl.setSpeed = 25.
      _, messages = ci.apply(command.as_reader(), 1_200_000_000)
      lfa = next(m for m in messages if m[0] == 0x485)
      self.assertEqual(lfa[2], int(topology == 'hdaii'))
      self.assertEqual(lfa[1][0], hyundai_checksum(lfa[1][1:8]))
      decoder = CANParser(DBC[cp.carFingerprint][Bus.pt], [('LFAHDA_MFC', 0)], lfa[2])
      decoder.update([(1_200_000_000, [lfa])])
      self.assertEqual(decoder.vl['LFAHDA_MFC']['LFA_Icon_State'], 2)
      pt = ci.can_parsers[Bus.pt]
      old = dict(pt.vl['SCC12'])
      bad_bus = 0 if topology == 'hdaii' else 1
      frame = CANPacker(DBC[cp.carFingerprint][Bus.pt]).make_can_msg('SCC12', bad_bus, {'MainMode_ACC': 0})
      # Required sources have timed out; parser validity debounces five invalid updates.
      for invalid_tick in range(5):
        result = ci.update([(2_000_000_000 + invalid_tick * 10_000_000, [frame])])
        self.assertEqual(result.canValid, invalid_tick < 4)
      self.assertFalse(result.canValid)
      self.assertEqual(dict(pt.vl['SCC12']), old)

  def test_actual_mixed_topology_metadata_correct_bus_and_wrong_bus(self):
    for hdaii in (False, True):
      for observation_bus in (0, 1, 2):
        with self.subTest(hdaii=hdaii, observation_bus=observation_bus):
          fingerprint = gen_empty_fingerprint()
          if hdaii:
            fingerprint[2][0x50] = 16
          fingerprint[observation_bus][0x38D] = 8
          fingerprint[observation_bus][0x58B] = 8
          stock = CarInterface.get_params(CAR.HYUNDAI_PALISADE_2023, fingerprint, [], False, False, False)
          expected_fca = observation_bus in (int(hdaii), 2)
          self.assertEqual(bool(stock.flags & HyundaiFlags.USE_FCA), expected_fca)
          self.assertEqual(stock.deprecated.enableBsm, observation_bus == int(hdaii))

  def test_mixed_standstill_metadata_retains_original_bus_zero(self):
    for hdaii in (False, True):
      for observation_bus in (0, 1):
        with self.subTest(hdaii=hdaii, observation_bus=observation_bus):
          fingerprint = gen_empty_fingerprint()
          if hdaii:
            fingerprint[2][0x50] = 16
          fingerprint[observation_bus][0x2AA] = 8
          stock = CarInterface.get_params(CAR.HYUNDAI_PALISADE_2023, fingerprint, [], False, False, False)
          self.assertEqual(stock.steerAtStandstill, observation_bus == 0)
          # Exercise the reached flag clear with a real mutable CP, not a fake field bag.
          stock.flags |= HyundaiFlags.MIN_STEER_32_MPH.value
          stock.minSteerSpeed = 10.0
          CarInterface._get_params(stock, CAR.HYUNDAI_PALISADE_2023, fingerprint, [], False, False, False)
          self.assertEqual(bool(stock.flags & HyundaiFlags.MIN_STEER_32_MPH), observation_bus != 0)
          self.assertEqual(stock.minSteerSpeed, 0.0 if observation_bus == 0 else 10.0)
