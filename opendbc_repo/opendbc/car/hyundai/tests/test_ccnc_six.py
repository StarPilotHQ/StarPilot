import unittest

from opendbc.can import CANPacker
from opendbc.car import Bus, gen_empty_fingerprint, structs
from opendbc.car.fw_versions import match_fw_to_car
from opendbc.car.hyundai.fingerprints import FW_VERSIONS
from opendbc.car.hyundai.hyundaicanfd import CanBus, create_carnival_alt_resume, hkg_can_fd_checksum
from opendbc.car.hyundai.carcontroller import CarController
from opendbc.car.hyundai.carstate import CarState
from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai.values import CAR, DBC, HyundaiFlags, HyundaiSafetyFlags
from opendbc.car.structs import CarParams
from opendbc.safety.tests.libsafety import libsafety_py


SIX = (CAR.HYUNDAI_IONIQ_5_N, CAR.HYUNDAI_TUCSON_PHEV_2025, CAR.KIA_K4_2025,
       CAR.KIA_SORENTO_2024, CAR.KIA_CARNIVAL_2025, CAR.KIA_CARNIVAL_HEV_4TH_GEN)
CARNIVALS = SIX[-2:]


def params(car, hda2=False, alt_buttons=False, alpha=False, adas=False, alt_lka=False):
  fp = gen_empty_fingerprint()
  if hda2:
    fp[2][0x110 if alt_lka else 0x50] = 32 if alt_lka else 16
  if alt_buttons:
    fp[1 if hda2 else 0][0x1aa] = 16
  else:
    fp[1 if hda2 else 0][0x1cf] = 8
  fw = [CarParams.CarFw(ecu=CarParams.Ecu.adas)] if adas else []
  return CarInterface.get_params(car, fp, fw, alpha, False, False)


class TestCcncSix(unittest.TestCase):
  def test_exact_firmware_requires_both_distinguishing_ecus(self):
    for car in SIX:
      with self.subTest(car=car):
        fw = [CarParams.CarFw(ecu=ecu, address=addr, subAddress=sub or 0, fwVersion=versions[0], brand="hyundai")
              for (ecu, addr, sub), versions in FW_VERSIONS[car].items()]
        exact, matches = match_fw_to_car(fw, "", log=False)
        self.assertTrue(exact)
        self.assertEqual(matches, {car})
        for item in fw:
          _, partial = match_fw_to_car([item], "", log=False)
          self.assertNotIn(car, partial)

    for sibling in (CAR.HYUNDAI_TUCSON_HEV_2025, CAR.HYUNDAI_TUCSON_2025, CAR.KIA_CARNIVAL_4TH_GEN,
                    CAR.KIA_SORENTO_4TH_GEN, CAR.HYUNDAI_IONIQ_5, CAR.KIA_EV6):
      if sibling not in FW_VERSIONS:
        continue
      fw = [CarParams.CarFw(ecu=ecu, address=addr, subAddress=sub or 0, fwVersion=versions[0], brand="hyundai")
            for (ecu, addr, sub), versions in FW_VERSIONS[sibling].items()]
      _, matches = match_fw_to_car(fw, "", log=False)
      self.assertFalse(set(SIX) & matches, sibling)

    radar_key = (CarParams.Ecu.fwdRadar, 0x7d0, None)
    camera_key = (CarParams.Ecu.fwdCamera, 0x7c4, None)
    for car, sibling in ((CAR.HYUNDAI_IONIQ_5_N, CAR.HYUNDAI_IONIQ_5),
                         (CAR.HYUNDAI_TUCSON_PHEV_2025, CAR.HYUNDAI_TUCSON_HEV_2025),
                         (CAR.KIA_SORENTO_2024, CAR.KIA_SORENTO_4TH_GEN),
                         (CAR.KIA_CARNIVAL_2025, CAR.KIA_CARNIVAL_4TH_GEN)):
      mixed = [CarParams.CarFw(ecu=key[0], address=key[1], fwVersion=FW_VERSIONS[owner][key][0], brand="hyundai")
               for key, owner in ((camera_key, car), (radar_key, sibling))]
      _, matches = match_fw_to_car(mixed, "", log=False)
      self.assertNotIn(car, matches)

  def test_six_topology_and_stock_ownership(self):
    for car in SIX:
      for hda2 in (False, True):
        with self.subTest(car=car, hda2=hda2):
          cp = params(car, hda2=hda2)
          self.assertEqual(bool(cp.flags & HyundaiFlags.CANFD_LKA_STEER_MSG), hda2)
          self.assertTrue(cp.pcmCruise)
          self.assertFalse(cp.openpilotLongitudinalControl)
          self.assertEqual(bool(cp.safetyConfigs[-1].safetyParam & HyundaiSafetyFlags.CCNC), not hda2)
          unavailable = params(car, hda2=hda2, alpha=True)
          self.assertEqual(unavailable.alphaLongitudinalAvailable, not hda2)
          self.assertEqual(unavailable.openpilotLongitudinalControl, not hda2)
          eligible = params(car, hda2=hda2, alpha=True, adas=True)
          self.assertTrue(eligible.alphaLongitudinalAvailable)
          self.assertTrue(eligible.openpilotLongitudinalControl)
          self.assertTrue(eligible.safetyConfigs[-1].safetyParam & HyundaiSafetyFlags.LONG)

  def test_carnival_alt_resume_only_with_observed_source_and_stock_long(self):
    for car in CARNIVALS:
      for hda2 in (False, True):
        cp = params(car, hda2=hda2, alt_buttons=True)
        self.assertTrue(cp.flags & HyundaiFlags.CANFD_ALT_BUTTONS)
        self.assertTrue(cp.safetyConfigs[-1].safetyParam & HyundaiSafetyFlags.CARNIVAL_ALT_RESUME)
        cp2 = params(car, hda2=hda2, alt_buttons=False)
        self.assertFalse(cp2.safetyConfigs[-1].safetyParam & HyundaiSafetyFlags.CARNIVAL_ALT_RESUME)
        if not hda2:
          cp_long = params(car, alt_buttons=True, alpha=True)
          self.assertTrue(cp_long.openpilotLongitudinalControl)
          self.assertFalse(cp_long.safetyConfigs[-1].safetyParam & HyundaiSafetyFlags.CARNIVAL_ALT_RESUME)

  def test_parser_and_controller_require_fresh_idle_carnival_button_source(self):
    for hda2 in (False, True):
      cp = params(CAR.KIA_CARNIVAL_2025, hda2=hda2, alt_buttons=True)
      state = CarState(cp)
      parsers = state.get_can_parsers(cp)
      bus = CanBus(cp)
      packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
      source = packer.make_can_msg("CRUISE_BUTTONS_ALT", bus.ECAN,
                                   {"COUNTER": 23, "SET_ME_1": 1, "CRUISE_BUTTONS": 0})
      parsers[Bus.pt].update((1_000_000_000, [source]))
      state.out = state.update(parsers)
      self.assertEqual(state.cruise_buttons_alt_msg["COUNTER"], 23)
      self.assertEqual(state.cruise_buttons_alt_ts_ns, 1_000_000_000)
      control = structs.CarControl()
      control.cruiseControl.resume = True
      controller = CarController(DBC[cp.carFingerprint], cp)
      controller.frame = 26
      _, sent = controller.update(control.as_reader(), state, 1_050_000_000)
      self.assertEqual(len([msg for msg in sent if msg[0] == 0x1aa]), 1)
      controller.frame = 52
      _, sent = controller.update(control.as_reader(), state, 1_101_000_000)
      self.assertFalse(any(msg[0] == 0x1aa for msg in sent))
      state.cruise_buttons_alt_msg["ADAPTIVE_CRUISE_MAIN_BTN"] = 1
      controller.frame = 78
      _, sent = controller.update(control.as_reader(), state, 1_050_000_000)
      self.assertFalse(any(msg[0] == 0x1aa for msg in sent))

  def test_packed_carnival_resume_native_counter_crc_and_replay(self):
    self._check_packed_carnival_resume(libsafety_py.libsafety)

  def _check_packed_carnival_resume(self, safety):
    packer = CANPacker(DBC[CAR.KIA_CARNIVAL_2025][Bus.pt])
    for hda2 in (False, True):
      cp = params(CAR.KIA_CARNIVAL_2025, hda2=hda2, alt_buttons=True)
      can = CanBus(cp)
      safety.set_safety_hooks(CarParams.SafetyModel.hyundaiCanfd, cp.safetyConfigs[-1].safetyParam)
      safety.init_tests()
      bus = can.ECAN
      source_values: dict[str, float] = {"COUNTER": 17, "SET_ME_1": 1, "CRUISE_BUTTONS": 0, "BYTE6": 0x55}
      addr, raw, _ = packer.make_can_msg("CRUISE_BUTTONS_ALT", bus, source_values)
      data = bytearray(raw)
      data[0:2] = hkg_can_fd_checksum(addr, None, data).to_bytes(2, "little")
      source = dict(source_values)
      resume = create_carnival_alt_resume(packer, cp, can, source)
      self.assertEqual(resume[2], bus if hda2 else can.CAM)
      tx = libsafety_py.make_CANPacket(resume[0], resume[2], resume[1])
      self.assertFalse(safety.safety_tx_hook(tx))
      self.assertTrue(safety.safety_rx_hook(libsafety_py.make_CANPacket(addr, bus, bytes(data))))
      safety.set_controls_allowed(True)
      self.assertTrue(safety.safety_tx_hook(tx))
      self.assertFalse(safety.safety_tx_hook(tx))
      self.assertTrue(safety.safety_rx_hook(libsafety_py.make_CANPacket(addr, bus, bytes(data))))
      self.assertFalse(safety.safety_tx_hook(tx))  # replayed stock counter cannot reopen the gate

      source["COUNTER"] = 18
      source_values["COUNTER"] = 18
      _, raw, _ = packer.make_can_msg("CRUISE_BUTTONS_ALT", bus, source_values)
      data = bytearray(raw)
      data[0:2] = hkg_can_fd_checksum(addr, None, data).to_bytes(2, "little")
      resume = create_carnival_alt_resume(packer, cp, can, source)
      tx = libsafety_py.make_CANPacket(resume[0], resume[2], resume[1])
      bad_source = bytearray(data)
      bad_source[0] ^= 1
      self.assertTrue(safety.safety_rx_hook(libsafety_py.make_CANPacket(addr, bus, bad_source)))
      self.assertFalse(safety.safety_tx_hook(tx))
      active_source = bytearray(data)
      active_source[4] |= 0x20  # driver SET button
      active_source[0:2] = hkg_can_fd_checksum(addr, None, active_source).to_bytes(2, "little")
      self.assertTrue(safety.safety_rx_hook(libsafety_py.make_CANPacket(addr, bus, active_source)))
      self.assertFalse(safety.safety_tx_hook(tx))
      source["COUNTER"] = source_values["COUNTER"] = 19
      _, raw, _ = packer.make_can_msg("CRUISE_BUTTONS_ALT", bus, source_values)
      data = bytearray(raw)
      data[0:2] = hkg_can_fd_checksum(addr, None, data).to_bytes(2, "little")
      resume = create_carnival_alt_resume(packer, cp, can, source)
      tx = libsafety_py.make_CANPacket(resume[0], resume[2], resume[1])
      self.assertTrue(safety.safety_rx_hook(libsafety_py.make_CANPacket(addr, bus, bytes(data))))
      wrong_counter = bytearray(resume[1])
      wrong_counter[2] += 1
      wrong_counter[0:2] = hkg_can_fd_checksum(addr, None, wrong_counter).to_bytes(2, "little")
      self.assertFalse(safety.safety_tx_hook(libsafety_py.make_CANPacket(addr, resume[2], wrong_counter)))
      wrong_field = bytearray(resume[1])
      wrong_field[6] ^= 1
      wrong_field[0:2] = hkg_can_fd_checksum(addr, None, wrong_field).to_bytes(2, "little")
      self.assertFalse(safety.safety_tx_hook(libsafety_py.make_CANPacket(addr, resume[2], wrong_field)))
      safety.set_timer(100_001)
      self.assertFalse(safety.safety_tx_hook(tx))
      safety.set_timer(0)
      self.assertTrue(safety.safety_tx_hook(tx))

      source["COUNTER"] = 20
      source_values["COUNTER"] = 20
      source_values["ADAPTIVE_CRUISE_MAIN_BTN"] = 1
      _, raw, _ = packer.make_can_msg("CRUISE_BUTTONS_ALT", bus, source_values)
      data = bytearray(raw)
      data[0:2] = hkg_can_fd_checksum(addr, None, data).to_bytes(2, "little")
      self.assertTrue(safety.safety_rx_hook(libsafety_py.make_CANPacket(addr, bus, data)))
      source["ADAPTIVE_CRUISE_MAIN_BTN"] = 1
      active_resume = create_carnival_alt_resume(packer, cp, can, source)
      self.assertFalse(safety.safety_tx_hook(libsafety_py.make_CANPacket(active_resume[0], active_resume[2], active_resume[1])))

      source_values["ADAPTIVE_CRUISE_MAIN_BTN"] = 0
      source_values["CRUISE_BUTTONS"] = 4  # driver cancel
      _, raw, _ = packer.make_can_msg("CRUISE_BUTTONS_ALT", bus, source_values)
      data = bytearray(raw)
      data[0:2] = hkg_can_fd_checksum(addr, None, data).to_bytes(2, "little")
      self.assertTrue(safety.safety_rx_hook(libsafety_py.make_CANPacket(addr, bus, data)))
      source["ADAPTIVE_CRUISE_MAIN_BTN"] = 0
      source["CRUISE_BUTTONS"] = 4
      active_resume = create_carnival_alt_resume(packer, cp, can, source)
      self.assertFalse(safety.safety_tx_hook(libsafety_py.make_CANPacket(active_resume[0], active_resume[2], active_resume[1])))

      source_values["CRUISE_BUTTONS"] = 0
      _, raw, _ = packer.make_can_msg("CRUISE_BUTTONS_ALT", bus, source_values)
      data = bytearray(raw)
      data[0:2] = hkg_can_fd_checksum(addr, None, data).to_bytes(2, "little")
      self.assertTrue(safety.safety_rx_hook(libsafety_py.make_CANPacket(addr, bus, data)))
      idle_resume = create_carnival_alt_resume(packer, cp, can, {**source, "CRUISE_BUTTONS": 0})
      safety.set_timer(2_000_000)
      safety.safety_tick()
      self.assertFalse(safety.safety_tx_hook(libsafety_py.make_CANPacket(idle_resume[0], idle_resume[2], idle_resume[1])))

      safety.set_safety_hooks(CarParams.SafetyModel.hyundaiCanfd,
                              cp.safetyConfigs[-1].safetyParam & ~HyundaiSafetyFlags.CARNIVAL_ALT_RESUME)
      safety.init_tests()
      safety.set_controls_allowed(True)
      self.assertFalse(safety.safety_tx_hook(tx))

      safety.set_safety_hooks(CarParams.SafetyModel.hyundaiCanfd, cp.safetyConfigs[-1].safetyParam)
      safety.init_tests()
      source_values["COUNTER"] = 255
      _, raw, _ = packer.make_can_msg("CRUISE_BUTTONS_ALT", bus, source_values)
      data = bytearray(raw)
      data[0:2] = hkg_can_fd_checksum(addr, None, data).to_bytes(2, "little")
      self.assertTrue(safety.safety_rx_hook(libsafety_py.make_CANPacket(addr, bus, data)))
      safety.set_controls_allowed(True)
      wrap_resume = create_carnival_alt_resume(packer, cp, can, {**source, "COUNTER": 255, "CRUISE_BUTTONS": 0})
      self.assertEqual(wrap_resume[1][2], 0)
      self.assertTrue(safety.safety_tx_hook(libsafety_py.make_CANPacket(wrap_resume[0], wrap_resume[2], wrap_resume[1])))

  def test_ccnc_owned_warnings_preserve_critical_and_obstruction_fields(self):
    from opendbc.can import CANParser
    from opendbc.car import structs
    from opendbc.car.hyundai.hyundaicanfd import create_ccnc

    packer = CANPacker('hyundai_canfd_generated')
    cp = params(CAR.KIA_CARNIVAL_HEV_4TH_GEN, alt_buttons=True)
    critical = ('FAULT_FSS', 'FAULT_FCA', 'FAULT_SCC', 'FAULT_ESS')
    owned = ('FAULT_LSS', 'FAULT_HDA', 'FAULT_DAS', 'FAULT_LFA', 'FAULT_DAW')
    for active in (False, True):
      for fault_value in (1, 2, 3):
        for alert in (5, 7, 8, 9, 10, 14, 21):
          with self.subTest(active=active, fault_value=fault_value, alert=alert):
            first = {name: 0 for name in packer.dbc.name_to_msg['CCNC_0x161'].sigs}
            second = {name: 0 for name in packer.dbc.name_to_msg['CCNC_0x162'].sigs}
            first.update(COUNTER=17, ALERTS_2=alert, SOUNDS_2=3, DAW_ICON=1, ALERTS_3=17, ALERTS_5=5)
            second.update({name: fault_value for name in (*critical, *owned)})
            second['COUNTER'] = 23
            hud = structs.CarControl.HUDControl()
            state = structs.CarState(vEgo=20., steeringAngleDeg=0.)
            frames = create_ccnc(packer, CanBus(cp), False, active, hud, False, False,
                                 first, second, None, True, state, True, active)
            parser = CANParser('hyundai_canfd_generated', [('CCNC_0x161', 0), ('CCNC_0x162', 0)], CanBus(cp).ECAN)
            parser.update((1_000_000_000, frames))
            a, b = parser.vl['CCNC_0x161'], parser.vl['CCNC_0x162']
            self.assertEqual(a['ALERTS_2'], 0 if active and alert == 5 else alert)
            self.assertEqual(a['SOUNDS_2'], 0 if active and alert == 5 else 3)
            self.assertEqual(a['DAW_ICON'], 0 if active else 1)
            self.assertEqual((a['COUNTER'], b['COUNTER']), (17, 23))
            for name in critical:
              self.assertEqual(b[name], fault_value)
            for name in owned:
              self.assertEqual(b[name], 0 if active and fault_value == 1 else fault_value)
