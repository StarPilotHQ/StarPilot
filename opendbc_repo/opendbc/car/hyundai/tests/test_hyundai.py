import unittest

from opendbc.can import CANPacker, CANParser
from opendbc.car import Bus, structs
from opendbc.car import gen_empty_fingerprint
from opendbc.car.structs import CarParams
from opendbc.car.fw_versions import build_fw_dict, match_fw_to_car
from opendbc.car.hyundai.carstate import CarState, ISG_RESTART_GRACE_FRAMES
from opendbc.car.hyundai.carcontroller import CarController
from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai import hyundaican
from opendbc.car.hyundai.hyundaicanfd import CanBus
from opendbc.car.hyundai.radar_interface import RADAR_START_ADDR
from opendbc.car.hyundai.values import CAR, DBC, DATE_FW_ECUS, FW_QUERY_CONFIG, CANFD_FUZZY_WHITELIST, \
                                         PLATFORM_CODE_ECUS, HYUNDAI_VERSION_REQUEST_LONG, \
                                         HyundaiFlags, get_platform_codes, HyundaiSafetyFlags
from opendbc.car.hyundai.fingerprints import FW_VERSIONS
from opendbc.testing import fuzzy_test

Ecu = CarParams.Ecu

# Some platforms have date codes in a different format we don't yet parse (or are missing).
# For now, assert list of expected missing date cars
NO_DATES_PLATFORMS = {
  # CAN FD
  CAR.KIA_SPORTAGE_5TH_GEN,
  CAR.KIA_SPORTAGE_2026,
  CAR.KIA_SPORTAGE_HEV_2026,
  CAR.HYUNDAI_SANTA_CRUZ_1ST_GEN,
  CAR.HYUNDAI_SANTA_CRUZ_2025,
  CAR.HYUNDAI_TUCSON_4TH_GEN,
  CAR.HYUNDAI_TUCSON_2025,
  CAR.HYUNDAI_TUCSON_HEV_2025,
  CAR.HYUNDAI_TUCSON_PHEV_2025,
  # CAN
  CAR.HYUNDAI_ELANTRA,
  CAR.HYUNDAI_ELANTRA_GT_I30,
  CAR.KIA_CEED,
  CAR.KIA_XCEED_PHEV,
  CAR.HYUNDAI_BAYON_1ST_GEN_NON_SCC,
  CAR.HYUNDAI_KONA_NON_SCC,
  CAR.KIA_CEED_PHEV_2022_NON_SCC,
  CAR.KIA_FORTE_2019_NON_SCC,
  CAR.KIA_FORTE_2021_NON_SCC,
  CAR.KIA_CEED_PHEV,
  CAR.KIA_FORTE,
  CAR.KIA_OPTIMA_G4,
  CAR.KIA_OPTIMA_G4_FL,
  CAR.KIA_SORENTO,
  CAR.HYUNDAI_KONA,
  CAR.HYUNDAI_KONA_EV,
  CAR.HYUNDAI_KONA_EV_2022,
  CAR.HYUNDAI_KONA_HEV,
  CAR.HYUNDAI_SONATA_LF,
  CAR.HYUNDAI_VELOSTER,
  CAR.HYUNDAI_KONA_2022,
}

CANFD_EXPECTED_ECUS = {Ecu.fwdCamera, Ecu.fwdRadar}

# CAN-only feature flags that should not appear on CAN FD platforms
CAN_FEATURE_FLAGS = (HyundaiFlags.CLUSTER_GEARS | HyundaiFlags.TCU_GEARS | HyundaiFlags.CHECKSUM_CRC8 |
                     HyundaiFlags.CHECKSUM_6B | HyundaiFlags.LEGACY | HyundaiFlags.UNSUPPORTED_LONGITUDINAL |
                     HyundaiFlags.CAMERA_SCC)


def cars_with(flags):
  return {c for c in CAR if c.config.flags & flags}


class TestHyundaiFingerprint(unittest.TestCase):

  def test_k4_shared_platform_physical_button_events(self):
    from opendbc.car.hyundai.values import Buttons
    button_type = structs.CarState.ButtonEvent.Type
    signals = (("LDA_BTN", 1, button_type.lkas), ("ADAPTIVE_CRUISE_MAIN_BTN", 1, button_type.mainCruise),
               ("CRUISE_BUTTONS", Buttons.RES_ACCEL, button_type.accelCruise),
               ("CRUISE_BUTTONS", Buttons.SET_DECEL, button_type.decelCruise),
               ("CRUISE_BUTTONS", Buttons.CANCEL, button_type.cancel))
    for alpha_long in (False, True):
      for alternate in (False, True):
        for signal, value, expected in signals:
          with self.subTest(alpha_long=alpha_long, alternate=alternate, signal=signal, value=value):
            fingerprint = gen_empty_fingerprint()
            if not alternate:
              fingerprint[0][0x1CF] = 8
            cp = CarInterface.get_params(CAR.KIA_K4_2025, fingerprint, [], alpha_long, False, False)
            self.assertEqual(cp.carFingerprint, CAR.KIA_K4_2025)
            self.assertEqual(bool(cp.flags & HyundaiFlags.CANFD_ALT_BUTTONS), alternate)
            state = CarState(cp)
            parsers = state.get_can_parsers(cp)
            packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
            state.update(parsers)
            for frame, sample in enumerate((0, value, 0), 1):
              message = packer.make_can_msg(state.cruise_btns_msg_canfd, CanBus(cp).ECAN, {signal: sample})
              parsers[Bus.pt].update([(frame * 10_000_000, [message])])
              events = state.update(parsers).buttonEvents
              actual = [(event.type, event.pressed) for event in events]
              self.assertEqual(actual, [] if frame == 1 else [(expected, frame == 2)])

  def test_eight_ordinary_ccnc_interfaces_and_display_frames(self):
    family = (
      CAR.HYUNDAI_KONA_2ND_GEN, CAR.HYUNDAI_KONA_HEV_2ND_GEN, CAR.HYUNDAI_SANTA_CRUZ_2025,
      CAR.HYUNDAI_SONATA_2024, CAR.HYUNDAI_SONATA_HEV_2024, CAR.HYUNDAI_TUCSON_2025,
      CAR.HYUNDAI_TUCSON_HEV_2025, CAR.KIA_K5_2025,
    )
    for car_model in family:
      for alpha_long in (False, True):
        with self.subTest(car_model=car_model, alpha_long=alpha_long):
          cp = CarInterface.get_params(car_model, gen_empty_fingerprint(), [], alpha_long, False, False)
          self.assertTrue(cp.flags & HyundaiFlags.CCNC)
          self.assertFalse(cp.flags & HyundaiFlags.CANFD_LKA_STEER_MSG)
          self.assertEqual(bool(cp.safetyConfigs[-1].safetyParam & HyundaiSafetyFlags.CCNC), True)
          self.assertEqual(bool(cp.safetyConfigs[-1].safetyParam & HyundaiSafetyFlags.LONG), alpha_long)
          self.assertFalse(cp.safetyConfigs[-1].safetyParam & HyundaiSafetyFlags.CAN_REFRESH_MSGS)
          state = CarState(cp)
          parsers = state.get_can_parsers(cp)
          cam = parsers[Bus.cam]
          self.assertTrue(cam.can_valid)
          packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
          can = CanBus(cp)
          output_parser = CANParser(DBC[cp.carFingerprint][Bus.pt], [("CCNC_0x161", 0), ("CCNC_0x162", 0)], can.ECAN)
          controller = CarController(DBC[cp.carFingerprint], cp)
          control = structs.CarControl()
          control.enabled = control.latActive = True
          control.longActive = alpha_long
          control.hudControl.setSpeed = 20.0
          # Establish the controller's startup baseline before camera display arrives.
          state.out = state.update(parsers)
          controller.update(control.as_reader(), state, 990_000_000)
          controller.frame = 5
          stock_161 = {"COUNTER": 7, "BACKGROUND": 3, "SETSPEED_SPEED": 83,
                       "ALERTS_2": 4, "ALERTS_3": 7, "ALERTS_5": 4, "SOUNDS_3": 5}
          stock_162 = {"COUNTER": 9, "SPEEDLIMIT": 60, "FAULT_DAS": 1, "LEAD_DISTANCE": 25}
          incoming = [packer.make_can_msg("CCNC_0x161", can.CAM, stock_161),
                      packer.make_can_msg("CCNC_0x162", can.CAM, stock_162),
                      packer.make_can_msg("FR_CMR_03_50ms", can.CAM, {"Longitudinal_Distance": 47})]
          self.assertEqual(cam.update((1_000_000_000, incoming)), {0x161, 0x162, 0x1b5})
          self.assertEqual([cam.ts_nanos[addr]["COUNTER"] for addr in (0x161, 0x162)], [1_000_000_000] * 2)
          state.out = state.update(parsers)
          state.out.vCruiseCluster = 72.0  # car/card's current driver-selected display speed
          _, sent = controller.update(control.as_reader(), state, 1_000_000_000)
          displays = [msg for msg in sent if msg[0] in (0x161, 0x162)]
          self.assertEqual([(addr, bus, len(data)) for addr, data, bus in displays],
                           [(0x161, can.ECAN, 32), (0x162, can.ECAN, 32)])
          self.assertFalse(any(addr in (0x160, 0x1E0, 0x7C4, 0xEA) and bus == can.ECAN for addr, _, bus in sent))
          output_parser.update((1_010_000_000, displays))
          self.assertEqual(output_parser.vl["CCNC_0x161"]["COUNTER"], 7)
          self.assertEqual(output_parser.vl["CCNC_0x162"]["COUNTER"], 9)
          self.assertEqual(output_parser.vl["CCNC_0x161"]["BACKGROUND"], 3)
          self.assertEqual(output_parser.vl["CCNC_0x162"]["SPEEDLIMIT"], 60)
          self.assertEqual(output_parser.vl["CCNC_0x162"]["FAULT_DAS"], 0)
          self.assertEqual(output_parser.vl["CCNC_0x162"]["LEAD_DISTANCE"], 47 if alpha_long else 25)
          for signal, expected in (("ALERTS_3", 7), ("ALERTS_5", 4), ("SOUNDS_3", 5)):
            self.assertEqual(output_parser.vl["CCNC_0x161"][signal], expected)
          self.assertEqual(output_parser.vl["CCNC_0x161"]["SETSPEED_SPEED"], 72 if alpha_long else 83)
          # No new camera pair means no second CCNC emission at the next 20 Hz slot.
          for tick in range(1, 6):
            _, held = controller.update(control.as_reader(), state, 1_000_000_000 + tick * 10_000_000)
          self.assertFalse(any(addr in (0x161, 0x162) for addr, _, _ in held))

          # A new 161 alone cannot be paired with held 162, and old 1b5
          # camera geometry/lead distance cannot overwrite a later pair.
          next_161 = packer.make_can_msg("CCNC_0x161", can.CAM, {**stock_161, "COUNTER": 8})
          cam.update((1_200_000_000, [next_161]))
          state.out = state.update(parsers)
          controller.frame = 15
          _, unpaired = controller.update(control.as_reader(), state, 1_200_000_000)
          self.assertFalse(any(addr in (0x161, 0x162) for addr, _, _ in unpaired))
          next_162 = packer.make_can_msg("CCNC_0x162", can.CAM, {**stock_162, "COUNTER": 10, "LEAD_DISTANCE": 29})
          cam.update((1_210_000_000, [next_162]))
          state.out = state.update(parsers)
          controller.frame = 20
          _, paired = controller.update(control.as_reader(), state, 1_210_000_000)
          display_pair = [msg for msg in paired if msg[0] in (0x161, 0x162)]
          self.assertEqual([msg[0] for msg in display_pair], [0x161, 0x162])
          output_parser.update((1_220_000_000, display_pair))
          self.assertEqual(output_parser.vl["CCNC_0x162"]["LEAD_DISTANCE"], 29)

          # Reversing the controller clock baselines the held pair again.
          controller.frame = 25
          _, reversed_clock = controller.update(control.as_reader(), state, 1_100_000_000)
          self.assertFalse(any(addr in (0x161, 0x162) for addr, _, _ in reversed_clock))
          fresh_pair = [packer.make_can_msg("CCNC_0x161", can.CAM, {**stock_161, "COUNTER": 9}),
                        packer.make_can_msg("CCNC_0x162", can.CAM, {**stock_162, "COUNTER": 11})]
          cam.update((1_300_000_000, fresh_pair))
          state.out = state.update(parsers)
          controller.frame = 30
          _, resumed = controller.update(control.as_reader(), state, 1_300_000_000)
          self.assertEqual([msg[0] for msg in resumed if msg[0] in (0x161, 0x162)], [0x161, 0x162])

          # Paired timestamps alone are insufficient if the control loop has
          # not emitted them until well after that display cycle.
          late_pair = [packer.make_can_msg("CCNC_0x161", can.CAM, {**stock_161, "COUNTER": 10}),
                       packer.make_can_msg("CCNC_0x162", can.CAM, {**stock_162, "COUNTER": 12})]
          cam.update((1_400_000_000, late_pair))
          state.out = state.update(parsers)
          controller.frame = 35
          _, late = controller.update(control.as_reader(), state, 2_000_000_000)
          self.assertFalse(any(msg[0] in (0x161, 0x162) for msg in late))

          control.latActive = False
          inactive_pair = [packer.make_can_msg("CCNC_0x161", can.CAM,
                                               {**stock_161, "COUNTER": 11, "LFA_ICON": 2, "CENTERLINE": 1}),
                           packer.make_can_msg("CCNC_0x162", can.CAM, {**stock_162, "COUNTER": 13})]
          cam.update((2_050_000_000, inactive_pair))
          state.out = state.update(parsers)
          controller.frame = 40
          _, inactive = controller.update(control.as_reader(), state, 2_050_000_000)
          display_pair = [msg for msg in inactive if msg[0] in (0x161, 0x162)]
          self.assertEqual(len(display_pair), 2)
          output_parser.update((2_060_000_000, display_pair))
          for signal in ("LFA_ICON", "CENTERLINE", "LANELINE_LEFT", "LANELINE_RIGHT"):
            self.assertEqual(output_parser.vl["CCNC_0x161"][signal], 0)
          self.assertEqual(output_parser.vl["CCNC_0x162"]["FAULT_DAS"], 1)

  def test_ccnc_permission_requires_observed_non_lka_camera_topology(self):
    fingerprint = gen_empty_fingerprint()
    cam_bus = CanBus(None, fingerprint).CAM
    fingerprint[cam_bus][0x50] = 16
    cp = CarInterface.get_params(CAR.HYUNDAI_KONA_2ND_GEN, fingerprint, [], False, False, False)
    self.assertTrue(cp.flags & HyundaiFlags.CANFD_LKA_STEER_MSG)
    self.assertFalse(cp.safetyConfigs[-1].safetyParam & HyundaiSafetyFlags.CCNC)
    cam = CarState(cp).get_can_parsers(cp)[Bus.cam]
    self.assertNotIn("CCNC_0x161", cam.vl)
    self.assertNotIn("CCNC_0x162", cam.vl)

  def test_elantra_hev_2024_optional_buttons_do_not_require_reception(self):
    cp = CarInterface.get_params(CAR.HYUNDAI_ELANTRA_HEV_2024, gen_empty_fingerprint(), [], False, False, False)
    pt = CarState(cp).get_can_parsers(cp)[Bus.pt]
    # Only the two optional button subscriptions exist before ordinary signals
    # are accessed. Missing either button message must not invalidate CAN health.
    for tick in range(100):
      pt.update((1_000_000_000 + tick * 10_000_000, []))
    self.assertTrue(pt.can_valid)

  def test_ioniq6_left_paddle_is_factual_button_event(self):
    for alpha_long, standard_buttons in ((False, True), (True, True), (False, False), (True, False)):
      with self.subTest(alpha_long=alpha_long, standard_buttons=standard_buttons):
        fingerprint = gen_empty_fingerprint()
        if standard_buttons:
          fingerprint[CanBus(None, fingerprint).ECAN][0x1cf] = 8
        cp = CarInterface.get_params(CAR.HYUNDAI_IONIQ_6, fingerprint, [], alpha_long, False, False)
        self.assertEqual(bool(cp.flags & HyundaiFlags.CANFD_ALT_BUTTONS), not standard_buttons)
        state = CarState(cp)
        parsers = state.get_can_parsers(cp)
        pt = parsers[Bus.pt]
        packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
        bus = CanBus(cp).ECAN

        def update(value: int, frame: int, *, packer=packer, bus=bus, pt=pt, state=state, parsers=parsers):
          msg = packer.make_can_msg("CRUISE_BUTTONS", bus, {"SET_ME_1": 1, "LEFT_PADDLE": value})
          pt.update((frame * 20_000_000, [msg]))
          return state.update(parsers)

        update(0, 1)
        pressed = update(1, 2)
        held = update(1, 3)
        released = update(0, 4)
        button = structs.CarState.ButtonEvent.Type.altButton2
        self.assertEqual([(event.type, event.pressed) for event in pressed.buttonEvents if event.type == button],
                         [(button, True)] if standard_buttons else [])
        self.assertFalse(any(event.type == button for event in held.buttonEvents))
        self.assertEqual([(event.type, event.pressed) for event in released.buttonEvents if event.type == button],
                         [(button, False)] if standard_buttons else [])

  def test_elantra_hev_2024_refresh_contract(self):
    for alpha_long in (False, True):
      with self.subTest(alpha_long=alpha_long):
        cp = CarInterface.get_params(CAR.HYUNDAI_ELANTRA_HEV_2024, gen_empty_fingerprint(), [], alpha_long, False, False)
        self.assertEqual(cp.carFingerprint, CAR.HYUNDAI_ELANTRA_HEV_2024)
        self.assertEqual(CAR.HYUNDAI_ELANTRA_HEV_2024.config.dbc_dict[Bus.pt], "hyundai_can_refresh_generated")
        self.assertEqual(cp.openpilotLongitudinalControl, alpha_long)
        self.assertAlmostEqual(cp.longitudinalActuatorDelay, 0.22)
        self.assertEqual(int(cp.safetyConfigs[0].safetyParam), int(HyundaiSafetyFlags.CAMERA_SCC | HyundaiSafetyFlags.CAN_REFRESH_MSGS |
                                                       HyundaiSafetyFlags.HYBRID_GAS | (HyundaiSafetyFlags.LONG if alpha_long else 0)))
        prior = CarInterface.get_params(CAR.HYUNDAI_ELANTRA_HEV_2021, gen_empty_fingerprint(), [], alpha_long, False, False)
        self.assertEqual(cp.mass, prior.mass)

    ecu_versions = FW_VERSIONS[CAR.HYUNDAI_ELANTRA_HEV_2024]
    self.assertEqual({ecu for ecu, _, _ in ecu_versions}, {Ecu.fwdRadar, Ecu.eps, Ecu.fwdCamera})
    self.assertEqual(sum(map(len, ecu_versions.values())), 8)

  def test_elantra_hev_2024_exact_firmware_tuple(self):
    fw = FW_VERSIONS[CAR.HYUNDAI_ELANTRA_HEV_2024]
    car_fw = [CarParams.CarFw(ecu=ecu, address=addr, subAddress=0, fwVersion=versions[-1], brand="hyundai")
              for (ecu, addr, _), versions in fw.items()]
    exact, matches = match_fw_to_car(car_fw, "", allow_exact=True, allow_fuzzy=False, log=False)
    self.assertTrue(exact)
    self.assertEqual(matches, {CAR.HYUNDAI_ELANTRA_HEV_2024})

  def test_elantra_hev_2024_parser_pulse_and_hybrid_signals(self):
    cp = CarInterface.get_params(CAR.HYUNDAI_ELANTRA_HEV_2024, gen_empty_fingerprint(), [], False, False, False)
    state = CarState(cp)
    parsers = state.get_can_parsers(cp)
    pt = parsers[Bus.pt]
    packer = CANPacker("hyundai_can_refresh_generated")
    state.update(parsers)  # subscribe to ordinary dynamically accessed signals

    pulse = [packer.make_can_msg("BCM_PO_11", 0, {"LDA_BTN": value}) for value in (1, 0)]
    signals = [packer.make_can_msg("E_EMS11", 0, {"CR_Vcu_AccPedDep_Pos": 3}),
               packer.make_can_msg("ELECT_GEAR", 0, {"Elect_Gear_Shifter": 5}),
               packer.make_can_msg("TCS13", 0, {"DriverOverride": 2})]
    pt.update((1_000_000_000, [*pulse, *signals]))
    self.assertEqual(pt.vl_all["BCM_PO_11"]["LDA_BTN"], [1, 0])
    ret = state.update(parsers)
    self.assertTrue(ret.gasPressed)
    self.assertTrue(ret.brakePressed)
    self.assertEqual(ret.gearShifter, structs.CarState.GearShifter.drive)
    self.assertTrue(any(event.type == structs.CarState.ButtonEvent.Type.lkas and event.pressed for event in ret.buttonEvents))

    cleared_signals = [packer.make_can_msg("CLU13", 0, {"CF_Clu_LdwsLkasSW": 0}),
                       packer.make_can_msg("E_EMS11", 0, {"CR_Vcu_AccPedDep_Pos": 0}),
                       packer.make_can_msg("ELECT_GEAR", 0, {"Elect_Gear_Shifter": 0}),
                       packer.make_can_msg("TCS13", 0, {"DriverOverride": 0})]
    pt.update((1_010_000_000, cleared_signals))
    released = state.update(parsers)
    self.assertTrue(any(event.type == structs.CarState.ButtonEvent.Type.lkas and not event.pressed for event in released.buttonEvents))
    self.assertFalse(released.gasPressed)
    self.assertFalse(released.brakePressed)
    self.assertEqual(released.gearShifter, structs.CarState.GearShifter.park)

  def test_elantra_hev_2024_cluster_button_short_pulse(self):
    cp = CarInterface.get_params(CAR.HYUNDAI_ELANTRA_HEV_2024, gen_empty_fingerprint(), [], False, False, False)
    state = CarState(cp)
    parsers = state.get_can_parsers(cp)
    pt = parsers[Bus.pt]
    packer = CANPacker("hyundai_can_refresh_generated")
    state.update(parsers)
    pulse = [packer.make_can_msg("CLU13", 0, {"CF_Clu_LdwsLkasSW": value}) for value in (1, 0)]
    pt.update((1_000_000_000, pulse))
    self.assertEqual(pt.vl_all["CLU13"]["CF_Clu_LdwsLkasSW"], [1, 0])
    pressed = state.update(parsers)
    self.assertTrue(any(event.type == structs.CarState.ButtonEvent.Type.lkas and event.pressed for event in pressed.buttonEvents))
    pt.update((1_010_000_000, [packer.make_can_msg("CLU13", 0, {"CF_Clu_LdwsLkasSW": 0})]))
    released = state.update(parsers)
    self.assertTrue(any(event.type == structs.CarState.ButtonEvent.Type.lkas and not event.pressed for event in released.buttonEvents))

  def test_elantra_2024_refresh_contract(self):
    for alpha_long in (False, True):
      with self.subTest(alpha_long=alpha_long):
        cp = CarInterface.get_params(CAR.HYUNDAI_ELANTRA_2024, gen_empty_fingerprint(), [], alpha_long, False, False)
        self.assertEqual(cp.carFingerprint, CAR.HYUNDAI_ELANTRA_2024)
        self.assertEqual(CAR.HYUNDAI_ELANTRA_2024.config.dbc_dict[Bus.pt], "hyundai_can_refresh_generated")
        self.assertEqual(cp.openpilotLongitudinalControl, alpha_long)
        self.assertTrue(cp.safetyConfigs[0].safetyParam & HyundaiSafetyFlags.CAMERA_SCC)
        self.assertTrue(cp.safetyConfigs[0].safetyParam & HyundaiSafetyFlags.CAN_REFRESH_MSGS)
        self.assertEqual(bool(cp.safetyConfigs[0].safetyParam & HyundaiSafetyFlags.LONG), alpha_long)

    ecu_versions = FW_VERSIONS[CAR.HYUNDAI_ELANTRA_2024]
    self.assertEqual({ecu for ecu, _, _ in ecu_versions}, {Ecu.fwdRadar, Ecu.eps, Ecu.fwdCamera, Ecu.abs})
    self.assertEqual(sum(map(len, ecu_versions.values())), 5)

  def test_elantra_2024_refresh_dbc_round_trip(self):
    packer = CANPacker("hyundai_can_refresh_generated")
    parser = CANParser("hyundai_can_refresh_generated", [("LFAHDA_MFC", 0)], 0)
    msg = packer.make_can_msg("LFAHDA_MFC", 0, {"LFA_Icon_State": 2})
    self.assertEqual((msg[0], msg[2], len(msg[1])), (0x485, 0, 8))
    self.assertEqual(parser.update((1_000_000_000, [msg])), {0x485})
    self.assertEqual(parser.vl["LFAHDA_MFC"]["LFA_Icon_State"], 2)
    old_msg = CANPacker("hyundai_can_generated").make_can_msg("LFAHDA_MFC", 0, {"LFA_Icon_State": 2})
    self.assertEqual(len(old_msg[1]), 4)
    self.assertEqual(len(hyundaican.create_lfahda_mfc(packer, True)[1]), 8)

  @staticmethod
  def _isg_steer_fault(candidate, fingerprint=None):
    fingerprint = fingerprint or gen_empty_fingerprint()
    CP = CarInterface.get_params(candidate, fingerprint, [], False, False, False)
    CS = CarState(CP)
    parsers = CS.get_can_parsers(CP)
    packer = CANPacker(DBC[CP.carFingerprint][Bus.pt])
    frame = 0

    def update(isg_state, unavail=1, fault=0, speed=0.):
      nonlocal frame
      frame += 1
      msgs = [
        packer.make_can_msg("MDPS12", 0, {"CF_Mdps_ToiUnavail": unavail, "CF_Mdps_ToiFlt": fault}),
        packer.make_can_msg("LVR12", 0, {"CF_Lvr_IsgState": isg_state}),
        packer.make_can_msg("WHL_SPD11", 0, {f"WHL_SPD_{w}": speed for w in ("FL", "FR", "RL", "RR")}),
      ]
      parsers[Bus.pt].update((frame * 10_000_000, msgs))
      return CS.update(parsers).steerFaultTemporary

    update(isg_state=0, unavail=0)  # registers the lazily parsed messages
    return update

  def test_isg_engine_stop_is_not_a_steer_fault(self):
    update = self._isg_steer_fault(CAR.KIA_STINGER_2022)
    self.assertTrue(update(isg_state=0))
    self.assertFalse(update(isg_state=1))
    self.assertTrue(update(isg_state=1, fault=1))
    self.assertTrue(update(isg_state=1, speed=20.))

  def test_isg_engine_restart_grace(self):
    update = self._isg_steer_fault(CAR.KIA_STINGER_2022)
    self.assertFalse(update(isg_state=1))
    self.assertFalse(update(isg_state=2))
    for _ in range(ISG_RESTART_GRACE_FRAMES):
      update(isg_state=0)
    self.assertTrue(update(isg_state=0))

  def test_isg_steer_fault_kept_for_steer_at_standstill(self):
    fingerprint = gen_empty_fingerprint()
    fingerprint[0][0x2AA] = 8
    update = self._isg_steer_fault(CAR.HYUNDAI_PALISADE_2023, fingerprint)
    self.assertTrue(update(isg_state=1))

  def test_feature_detection(self):
    # LKA steering
    for lka_steering in (True, False):
      fingerprint = gen_empty_fingerprint()
      if lka_steering:
        cam_can = CanBus(None, fingerprint).CAM
        fingerprint[cam_can] = [0x50, 0x110]  # LKA steering messages
      CP = CarInterface.get_params(CAR.KIA_EV6, fingerprint, [], False, False, False)
      assert bool(CP.flags & HyundaiFlags.CANFD_LKA_STEER_MSG) == lka_steering

    # radar available
    for radar in (True, False):
      fingerprint = gen_empty_fingerprint()
      if radar:
        fingerprint[1][RADAR_START_ADDR] = 8
      CP = CarInterface.get_params(CAR.HYUNDAI_SONATA, fingerprint, [], False, False, False)
      assert CP.radarUnavailable != radar

  def test_alternate_limits(self):
    # Alternate lateral control limits, for high torque cars, verify Panda safety mode flag is set
    fingerprint = gen_empty_fingerprint()
    for car_model in CAR:
      CP = CarInterface.get_params(car_model, fingerprint, [], False, False, False)
      assert bool(CP.flags & HyundaiFlags.ALT_LIMITS) == bool(CP.safetyConfigs[-1].safetyParam & HyundaiSafetyFlags.ALT_LIMITS)

  def test_can_features(self):
    for car_model in CAR:
      flags = car_model.config.flags
      # Test no EV/HEV with cluster/TCU gears (should all use ELECT_GEAR)
      if flags & (HyundaiFlags.HYBRID | HyundaiFlags.EV):
        assert not (flags & (HyundaiFlags.CLUSTER_GEARS | HyundaiFlags.TCU_GEARS))

      # Test CAN FD car not in CAN feature lists
      if flags & HyundaiFlags.CANFD:
        assert not (flags & CAN_FEATURE_FLAGS), "CAN FD car unexpectedly has a CAN feature flag"

  def test_hybrid_ev_flags(self):
    for car_model in CAR:
      flags = car_model.config.flags
      assert not (flags & HyundaiFlags.HYBRID and flags & HyundaiFlags.EV), "Shared cars between hybrid and EV"
      assert not (flags & HyundaiFlags.CANFD and flags & HyundaiFlags.HYBRID), \
        "Hard coding CAN FD cars as hybrid is no longer supported"

  def test_canfd_ecu_whitelist(self):
    # Asserts only expected Ecus can exist in database for CAN-FD cars
    for car_model, fw_versions in FW_VERSIONS.items():
      if not (car_model.config.flags & HyundaiFlags.CANFD):
        continue
      ecus = {fw[0] for fw in fw_versions.keys()}
      ecus_not_in_whitelist = ecus - CANFD_EXPECTED_ECUS
      ecu_strings = ", ".join([f"Ecu.{ecu}" for ecu in ecus_not_in_whitelist])
      assert len(ecus_not_in_whitelist) == 0, \
                       f"{car_model}: Car model has unexpected ECUs: {ecu_strings}"

  def test_blacklisted_parts(self):
    # Asserts no ECUs known to be shared across platforms exist in the database.
    # Tucson having Santa Cruz camera and EPS for example
    for car_model, ecus in FW_VERSIONS.items():
      with self.subTest(car_model=car_model.value):
        if car_model == CAR.HYUNDAI_SANTA_CRUZ_1ST_GEN:
          raise unittest.SkipTest("Skip checking Santa Cruz for its parts")

        for code, _ in get_platform_codes(ecus[(Ecu.fwdCamera, 0x7c4, None)]):
          if b"-" not in code:
            continue
          part = code.split(b"-")[1]
          assert not part.startswith(b'CW'), "Car has bad part number"

  def test_correct_ecu_response_database(self):
    """
    Assert standard responses for certain ECUs, since they can
    respond to multiple queries with different data
    """
    expected_fw_prefix = HYUNDAI_VERSION_REQUEST_LONG[1:]
    for car_model, ecus in FW_VERSIONS.items():
      with self.subTest(car_model=car_model.value):
        for ecu, fws in ecus.items():
          assert all(fw.startswith(expected_fw_prefix) for fw in fws), \
                          f"FW from unexpected request in database: {(ecu, fws)}"

  @fuzzy_test(max_examples=100)
  def test_platform_codes_fuzzy_fw(self, fuzzy):
    """Ensure function doesn't raise an exception"""
    get_platform_codes(fuzzy.list(fuzzy.binary))

  def test_expected_platform_codes(self):
    # Ensures we don't accidentally add multiple platform codes for a car unless it is intentional
    for car_model, ecus in FW_VERSIONS.items():
      with self.subTest(car_model=car_model.value):
        for ecu, fws in ecus.items():
          if ecu[0] not in PLATFORM_CODE_ECUS:
            continue

          # Third and fourth character are usually EV/hybrid identifiers
          codes = {code.split(b"-")[0][:2] for code, _ in get_platform_codes(fws)}
          if car_model in (CAR.HYUNDAI_PALISADE, CAR.HYUNDAI_PALISADE_2023):
            assert codes == {b"LX", b"ON"}, f"Car has unexpected platform codes: {car_model} {codes}"
          elif car_model == CAR.HYUNDAI_KONA_EV and ecu[0] == Ecu.fwdCamera:
            assert codes == {b"OE", b"OS"}, f"Car has unexpected platform codes: {car_model} {codes}"
          else:
            assert len(codes) == 1, f"Car has multiple platform codes: {car_model} {codes}"

  # Tests for platform codes, part numbers, and FW dates which Hyundai will use to fuzzy
  # fingerprint in the absence of full FW matches:
  def test_platform_code_ecus_available(self):
    # TODO: add queries for these non-CAN FD cars to get EPS
    no_eps_platforms = cars_with(HyundaiFlags.CANFD) | {
      CAR.KIA_RAY_EV,
      CAR.HYUNDAI_BAYON_1ST_GEN_NON_SCC, CAR.KIA_SORENTO, CAR.KIA_OPTIMA_G4, CAR.KIA_OPTIMA_G4_FL,
      CAR.KIA_OPTIMA_H, CAR.KIA_K7_2017, CAR.KIA_OPTIMA_H_G4_FL, CAR.HYUNDAI_SONATA_LF,
      CAR.HYUNDAI_TUCSON, CAR.GENESIS_G90, CAR.GENESIS_G80, CAR.HYUNDAI_ELANTRA,
    }

    # Asserts ECU keys essential for fuzzy fingerprinting are available on all platforms
    for car_model, ecus in FW_VERSIONS.items():
      with self.subTest(car_model=car_model.value):
        for platform_code_ecu in PLATFORM_CODE_ECUS:
          if platform_code_ecu in (Ecu.fwdRadar, Ecu.eps) and car_model == CAR.HYUNDAI_GENESIS:
            continue
          if platform_code_ecu == Ecu.fwdRadar and car_model.config.flags & HyundaiFlags.NON_SCC:
            continue
          if platform_code_ecu == Ecu.eps and car_model in no_eps_platforms:
            continue
          assert platform_code_ecu in [e[0] for e in ecus]

  def test_fw_format(self):
    # Asserts:
    # - every supported ECU FW version returns one platform code
    # - every supported ECU FW version has a part number
    # - expected parsing of ECU FW dates

    for car_model, ecus in FW_VERSIONS.items():
      with self.subTest(car_model=car_model.value):
        for ecu, fws in ecus.items():
          if ecu[0] not in PLATFORM_CODE_ECUS:
            continue

          codes = set()
          for fw in fws:
            result = get_platform_codes([fw])
            assert 1 == len(result), f"Unable to parse FW: {fw}"
            codes |= result
            # Exact historical camera record has no embedded ECU part number.
            # Keep every other record, including these platforms' radars, strict.
            historical_no_part = (
              car_model in (CAR.KIA_SPORTAGE_2026, CAR.KIA_SPORTAGE_HEV_2026) and
              ecu == (Ecu.fwdCamera, 0x7c4, None) and
              fw == b'\xf1\x00NQ51.011.021.012551000HKP_NQ524_50509099211P1110'
            )
            if car_model != CAR.HYUNDAI_GENESIS and not historical_no_part:
              assert all(b"-" in code for code, _ in result), f"FW does not have part number: {fw}"

          if ecu[0] not in DATE_FW_ECUS or car_model in NO_DATES_PLATFORMS:
            assert all(date is None for _, date in codes)
          else:
            assert all(date is not None for _, date in codes)

          if car_model == CAR.HYUNDAI_GENESIS:
            raise unittest.SkipTest("No part numbers for car model")

          # Hyundai places the ECU part number in their FW versions, assert all parsable
          # Some examples of valid formats: b"56310-L0010", b"56310L0010", b"56310/M6300"

  def test_platform_codes_spot_check(self):
    # Asserts basic platform code parsing behavior for a few cases
    results = get_platform_codes([b"\xf1\x00DH LKAS 1.1 -150210"])
    assert results == {(b"DH", b"150210")}

    # Some cameras and all radars do not have dates
    results = get_platform_codes([b"\xf1\x00AEhe SCC H-CUP      1.01 1.01 96400-G2000         "])
    assert results == {(b"AEhe-G2000", None)}

    results = get_platform_codes([b"\xf1\x00CV1_ RDR -----      1.00 1.01 99110-CV000         "])
    assert results == {(b"CV1-CV000", None)}

    results = get_platform_codes([
      b"\xf1\x00DH LKAS 1.1 -150210",
      b"\xf1\x00AEhe SCC H-CUP      1.01 1.01 96400-G2000         ",
      b"\xf1\x00CV1_ RDR -----      1.00 1.01 99110-CV000         ",
    ])
    assert results == {(b"DH", b"150210"), (b"AEhe-G2000", None), (b"CV1-CV000", None)}

    results = get_platform_codes([
      b"\xf1\x00LX2 MFC  AT USA LHD 1.00 1.07 99211-S8100 220222",
      b"\xf1\x00LX2 MFC  AT USA LHD 1.00 1.08 99211-S8100 211103",
      b"\xf1\x00ON  MFC  AT USA LHD 1.00 1.01 99211-S9100 190405",
      b"\xf1\x00ON  MFC  AT USA LHD 1.00 1.03 99211-S9100 190720",
    ])
    assert results == {(b"LX2-S8100", b"220222"), (b"LX2-S8100", b"211103"),
                               (b"ON-S9100", b"190405"), (b"ON-S9100", b"190720")}

  def test_fuzzy_excluded_platforms(self):
    # Asserts a list of platforms that will not fuzzy fingerprint with platform codes due to them being shared.
    # This list can be shrunk as we combine platforms and detect features
    excluded_platforms = {
      CAR.GENESIS_G70,            # shared platform code, part number, and date
      CAR.GENESIS_G70_2020,
      CAR.GENESIS_G70_2021_NON_SCC,
    }
    excluded_platforms |= cars_with(HyundaiFlags.CANFD) - cars_with(HyundaiFlags.EV) - CANFD_FUZZY_WHITELIST  # shared platform codes
    excluded_platforms |= NO_DATES_PLATFORMS  # date codes are required to match
    # Manual-only identities are absent from the FW database and cannot appear
    # in the matcher loop below.
    excluded_platforms &= FW_VERSIONS.keys()

    platforms_with_shared_codes = set()
    for platform, fw_by_addr in FW_VERSIONS.items():
      car_fw = []
      for ecu, fw_versions in fw_by_addr.items():
        ecu_name, addr, sub_addr = ecu
        for fw in fw_versions:
          car_fw.append(CarParams.CarFw(ecu=ecu_name, fwVersion=fw, address=addr,
                                        subAddress=0 if sub_addr is None else sub_addr))

      CP = CarParams(carFw=car_fw)
      matches = FW_QUERY_CONFIG.match_fw_to_car_fuzzy(build_fw_dict(CP.carFw), CP.carVin, FW_VERSIONS)
      if len(matches) == 1:
        assert list(matches)[0] == platform
      else:
        platforms_with_shared_codes.add(platform)

    assert platforms_with_shared_codes == excluded_platforms
