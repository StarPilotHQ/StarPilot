import unittest

from opendbc.car import structs
from opendbc.can.dbc import DBC
from opendbc.safety.tests.common import CANPackerSafety
from opendbc.safety.tests.libsafety import libsafety_py


CASES = (
  ("classic-alpha", 20, 2082, 1, "honda_civic_hatchback_ex_2017_can_generated", False, "GEARBOX_AUTO", 4),
  ("classic-alpha-alt", 20, 2083, 1, "acura_rdx_2020_can_generated", False, "GEARBOX_AUTO", 4),
  ("classic-alpha-rdx", 20, 2211, 1, "acura_rdx_2020_can_generated", False, "GEARBOX_AUTO", 4),
  ("nidec-pedal", 1, 544, 0, "honda_civic_touring_2016_can_generated", False, "GEARBOX_CVT", 4),
  ("nidec-pedal-alt", 1, 548, 0, "acura_ilx_2016_can_generated", True, "GEARBOX_AUTO", 4),
  ("nidec-pedal-twn", 1, 804, 0, "honda_odyssey_twn_2018_generated", True, "GEARBOX_AUTO", 4),
  ("accord-mvl-stock", 20, 80, 0, "honda_common_canfd_generated", False, "GEARBOX_AUTO", 4),
  ("accord-mvl-stock-alt-brake", 20, 81, 0, "honda_common_canfd_generated", False, "GEARBOX_AUTO", 4),
  ("alt-radar-alpha", 20, 1058, 1, "honda_civic_hatchback_ex_2017_can_generated", False, "GEARBOX_AUTO", 4),
  ("alt-radar-alpha-alt-brake", 20, 1059, 1, "acura_rdx_2020_can_generated", False, "GEARBOX_AUTO", 4),
  ("mdx-tja-alpha", 20, 1570, 1, "honda_common_canfd_generated", False, "GEARBOX_AUTO", 4),
  ("mdx-tja-alpha-alt-brake", 20, 1571, 1, "honda_common_canfd_generated", False, "GEARBOX_AUTO", 4),
  ("alt-radar-stock", 20, 0, 1, "honda_civic_hatchback_ex_2017_can_generated", False, "GEARBOX_AUTO", 4),
  ("alt-radar-stock-alt-brake", 20, 1, 1, "acura_rdx_2020_can_generated", False, "GEARBOX_AUTO", 4),
  ("mdx-tja-stock", 20, 512, 1, "honda_common_canfd_generated", False, "GEARBOX_AUTO", 4),
  ("mdx-tja-stock-alt-brake", 20, 513, 1, "honda_common_canfd_generated", False, "GEARBOX_AUTO", 4),
  ("canfd-stock", 20, 16, 0, "honda_common_canfd_generated", False, "GEARBOX_AUTO", 4),
  ("canfd-stock-alt-brake", 20, 17, 0, "honda_common_canfd_generated", False, "GEARBOX_AUTO", 4),
  ("classic", 20, 0, 1, "honda_civic_hatchback_ex_2017_can_generated", False, "GEARBOX_AUTO", 4),
  ("classic-alt-brake", 20, 1, 1, "acura_rdx_2020_can_generated", False, "GEARBOX_AUTO", 4),
  ("nidec", 1, 0, 0, "honda_civic_touring_2016_can_generated", False, "GEARBOX_CVT", 4),
  ("nidec-alt-six-byte-eps", 1, 4, 0, "acura_rdx_2018_can_generated", True, "GEARBOX_AUTO", 8),
  ("nidec-alt-other-eps", 1, 4, 0, "honda_accord_2017_can_ext_generated", True, "GEARBOX_AUTO", 8),
  ("twn", 1, 260, 0, "honda_odyssey_twn_2018_generated", True, "GEARBOX_AUTO", 4),
  ("radarless", 20, 8, 0, "honda_bosch_radarless_generated", False, "GEARBOX_AUTO", 4),
  ("radarless-alt-brake", 20, 9, 0, "honda_bosch_radarless_generated", False, "GEARBOX_AUTO", 4),
  ("radarless-long", 20, 10, 0, "honda_bosch_radarless_generated", False, "GEARBOX_AUTO", 4),
  ("radarless-long-alt-brake", 20, 11, 0, "honda_bosch_radarless_generated", False, "GEARBOX_AUTO", 4),
)


class TestHondaStockAol(unittest.TestCase):
  def tearDown(self):
    # Later safety suites configure hooks before init_tests resets shared experience.
    libsafety_py.libsafety.set_alternative_experience(0)

  def configure(self, case, experience=32):
    self.case = case
    _, model, word, self.pt, dbc, self.alt, self.gear_name, self.drive_value = case
    self.safety = libsafety_py.libsafety
    self.debug = self.safety.set_safety_hooks(structs.CarParams.SafetyModel.allOutput, 0) == 0
    self.safety.set_safety_hooks(model, word)
    self.safety.init_tests()
    self.safety.set_alternative_experience(experience)
    self.safety.set_safety_hooks(model, word)
    self.safety.set_timer(1_000_000)
    self.safety.set_aol_test_heartbeat(True)
    self.packer = CANPackerSafety(dbc)
    self.dbc = DBC(dbc)
    self.pedal = model == 1 and word in (544, 548, 804)
    self.scoped = experience == 32 and (word not in (10, 11, 1058, 1059, 1570, 1571, 2082, 2083, 2211) or self.debug)

  def packet(self, name, values, bus=None):
    expected = self.dbc.name_to_msg[name].sigs
    self.assertFalse(set(values) - set(expected), (name, set(values) - set(expected)))
    return self.packer.make_can_msg_safety(name, self.pt if bus is None else bus, values)

  def stream(self, *, main=True, lkas=0, eps=0, gear=None, cruise=0, brake=0, gas=0, omit=None, request=True):
    scm = {"CRUISE_BUTTONS": 0, "CRUISE_SETTING": lkas}
    if self.alt:
      scm["MAIN_ON"] = int(main)
    frames = [
      ("SCM_BUTTONS", self.packet("SCM_BUTTONS", scm)),
      ("ENGINE_DATA", self.packet("ENGINE_DATA", {"XMISSION_SPEED": 30})),
      ("POWERTRAIN_DATA", self.packet("POWERTRAIN_DATA", {"ACC_STATUS": cruise, "PEDAL_GAS": gas, "BRAKE_PRESSED": brake})),
      ("STEER_STATUS", self.packet("STEER_STATUS", {"STEER_STATUS": eps, "STEER_TORQUE_SENSOR": 0})),
      (self.gear_name, self.packet(self.gear_name, {"GEAR_SHIFTER": self.drive_value if gear is None else gear})),
    ]
    if not self.alt:
      frames.insert(0, ("SCM_FEEDBACK", self.packet("SCM_FEEDBACK", {"MAIN_ON": int(main)})))
    if self.case[1] == 1:
      frames.append(("BRAKE_COMMAND", self.packet("BRAKE_COMMAND", {}, 2)))
    elif self.case[2] & 1:
      frames.append(("BRAKE_MODULE", self.packet("BRAKE_MODULE", {"BRAKE_PRESSED": brake})))
    if self.pedal:
      frames.append(("GAS_SENSOR", self.packet("GAS_SENSOR", {"INTERCEPTOR_GAS": 0, "INTERCEPTOR_GAS2": 0, "STATE": 0})))
    for name, frame in frames:
      if name != omit:
        self.assertTrue(self.safety.safety_rx_hook(frame), name)
    if request:
      self.safety.aol_set_host_request(1)

  def normal_engage(self, *, button=3, gas=0, brake=0):
    if self.pedal or (self.debug and self.case[2] in (10, 11, 1058, 1059, 1570, 1571, 2082, 2083, 2211)):
      for value in (button, 0):
        scm = {"CRUISE_BUTTONS": value, "CRUISE_SETTING": 0}
        if self.alt:
          scm["MAIN_ON"] = 1
        self.assertTrue(self.safety.safety_rx_hook(self.packet("SCM_BUTTONS", scm)))
    else:
      self.assertTrue(self.safety.safety_rx_hook(self.packet("POWERTRAIN_DATA", {
        "ACC_STATUS": 1, "PEDAL_GAS": gas, "BRAKE_PRESSED": brake,
      })))

  def press_brake(self):
    alternate = self.case[1] == 20 and bool(self.case[2] & 1)
    name = "BRAKE_MODULE" if alternate else "POWERTRAIN_DATA"
    values = {"BRAKE_PRESSED": 1}
    if not alternate:
      values.update(ACC_STATUS=1, PEDAL_GAS=0)
    self.assertTrue(self.safety.safety_rx_hook(self.packet(name, values)))

  def test_cancel_preserves_only_existing_lateral_without_renewing_request(self):
    for case in CASES:
      with self.subTest(case=case[0]):
        self.configure(case)
        if not self.scoped:
          continue
        self.stream()
        self.normal_engage()
        self.safety.aol_set_host_request(3)
        self.assertEqual(self.safety.aol_get_permission_mask(), 3)
        self.safety.set_timer(1_010_000)
        values = {"CRUISE_BUTTONS": 2, "CRUISE_SETTING": 0}
        if self.alt:
          values["MAIN_ON"] = 1
        self.assertTrue(self.safety.safety_rx_hook(self.packet("SCM_BUTTONS", values)))
        self.assertFalse(self.safety.get_controls_allowed())
        self.assertEqual(self.safety.aol_get_request_mask(), 1)
        self.assertEqual(self.safety.aol_get_permission_mask(), 1)
        # Expire the original 1s request, not the later Cancel receipt.
        self.safety.set_timer(1_299_999)
        self.stream(request=False)
        self.safety.set_timer(1_300_001)
        self.assertEqual(self.safety.aol_get_permission_mask(), 0)

  def test_cancel_never_creates_lateral_and_main_still_withdraws_all(self):
    for case in CASES:
      for experience in (0, 32):
        for request in (0, 2, 3):
          with self.subTest(case=case[0], experience=experience, request=request):
            self.configure(case, experience=experience)
            self.stream()
            self.safety.aol_set_host_request(request)
            values = {"CRUISE_BUTTONS": 2, "CRUISE_SETTING": 0}
            if self.alt:
              values["MAIN_ON"] = 1
            self.assertTrue(self.safety.safety_rx_hook(self.packet("SCM_BUTTONS", values)))
            self.assertFalse(self.safety.get_controls_allowed())
            self.assertEqual(self.safety.aol_get_permission_mask(), 0)
            if experience == 0 or request in (0, 2):
              self.assertEqual(self.safety.aol_get_request_mask(), 0)
            self.stream(main=False)
            self.assertEqual(self.safety.aol_get_permission_mask(), 0)

  def test_normal_engagement_then_brake_without_main_or_lkas_gesture(self):
    for case in CASES:
      for button in ((3, 4) if case[2] in (10, 11, 544, 548, 804, 1058, 1059, 1570, 1571, 2082, 2083, 2211) else (3,)):
        for gas in (0, 1):
          with self.subTest(case=case[0], button=button, gas=gas):
            self.configure(case)
            self.stream(gas=gas)
            self.assertEqual(self.safety.aol_get_permission_mask(), 0)
            self.normal_engage(button=button, gas=gas)
            self.assertTrue(self.safety.get_controls_allowed())
            self.safety.aol_set_host_request(3)
            self.assertEqual(self.safety.aol_get_permission_mask(), 3 if self.scoped else 0)
            self.press_brake()
            self.assertFalse(self.safety.get_controls_allowed())
            self.assertEqual(self.safety.aol_get_permission_mask(), 1 if self.scoped else 0)
            self.safety.aol_set_host_request(0)
            self.assertEqual(self.safety.aol_get_permission_mask(), 0)
            self.safety.aol_set_host_request(3)
            self.assertEqual(self.safety.aol_get_permission_mask(), 1 if self.scoped else 0)
            self.stream(main=False)
            self.assertEqual(self.safety.aol_get_permission_mask(), 0)

  def test_blocked_normal_edge_is_not_replayed_after_sources_recover(self):
    for case in CASES:
      for blocked in ("eps", "reverse", "brake", "missing", "expired", "request_expired"):
        with self.subTest(case=case[0], blocked=blocked):
          self.configure(case)
          self.stream(eps=7 if blocked == "eps" else 0, gear=2 if blocked == "reverse" else None,
                      brake=int(blocked == "brake"), omit="STEER_STATUS" if blocked == "missing" else None)
          self.assertEqual(self.safety.aol_get_permission_mask(), 0)
          if blocked in ("expired", "request_expired"):
            self.safety.set_timer(1_300_001)
            if blocked == "expired":
              self.safety.aol_set_host_request(1)
            else:
              self.stream(request=False)
          self.normal_engage(brake=int(blocked == "brake"))
          self.safety.set_aol_test_heartbeat(True)
          self.stream(cruise=1)
          self.press_brake()
          self.safety.aol_set_host_request(3)
          self.assertFalse(self.safety.get_controls_allowed())
          self.assertEqual(self.safety.aol_get_permission_mask(), 0)

  def test_normal_engagement_in_temporary_eps_restores_only_after_recovery(self):
    for case in CASES:
      for eps in (2, 6):
        with self.subTest(case=case[0], eps=eps):
          self.configure(case)
          self.stream(eps=eps)
          self.normal_engage()
          self.assertTrue(self.safety.get_controls_allowed())
          self.assertEqual(self.safety.aol_get_permission_mask(), 0)
          self.press_brake()
          self.stream(cruise=1)
          self.assertFalse(self.safety.get_controls_allowed())
          self.assertEqual(self.safety.aol_get_permission_mask(), 1 if self.scoped else 0)

  def test_temporary_eps_during_engagement_keeps_longitudinal(self):
    # Hard driver override: EPS reports 2/6 while engaged; only lateral may drop.
    for case in CASES:
      for eps in (2, 6):
        with self.subTest(case=case[0], eps=eps):
          self.configure(case)
          self.stream()
          self.normal_engage()
          self.safety.aol_set_host_request(3)
          self.assertEqual(self.safety.aol_get_permission_mask(), 3 if self.scoped else 0)
          self.stream(eps=eps, cruise=1, request=False)
          self.assertTrue(self.safety.get_controls_allowed())
          self.assertEqual(self.safety.aol_get_permission_mask(), 2 if self.scoped else 0)
          self.stream(cruise=1, request=False)
          self.assertEqual(self.safety.aol_get_permission_mask(), 3 if self.scoped else 0)
          self.stream(eps=7, cruise=1, request=False)
          self.assertEqual(self.safety.aol_get_permission_mask(), 0)

  def test_aol_only_temporary_eps_recovers_without_restart(self):
    for case in CASES:
      with self.subTest(case=case[0]):
        self.configure(case)
        self.arm()
        for eps in (2, 6, 2):
          self.stream(eps=eps)
          self.assertFalse(self.safety.get_controls_allowed())
          self.assertEqual(self.safety.aol_get_permission_mask(), 0)
          self.safety.aol_set_host_request(0)
          self.stream()
          self.assertFalse(self.safety.get_controls_allowed())
          self.assertEqual(self.safety.aol_get_permission_mask(), 1 if self.scoped else 0)

  def test_normal_grant_session_loss_before_first_permission_query(self):
    for case in CASES:
      for loss in ("heartbeat", "sources"):
        with self.subTest(case=case[0], loss=loss):
          self.configure(case)
          self.stream()
          self.normal_engage()  # No request/permission query has activated a session yet.
          if loss == "heartbeat":
            self.safety.set_aol_test_heartbeat(False)
          else:
            self.safety.set_timer(1_300_001)
            self.safety.aol_set_host_request(1)  # Isolate stale CAN sources from host expiry.
          self.assertEqual(self.safety.aol_get_permission_mask(), 0)
          self.safety.set_aol_test_heartbeat(True)
          self.stream(cruise=1)
          self.press_brake()
          self.safety.aol_set_host_request(3)
          self.assertFalse(self.safety.get_controls_allowed())
          self.assertEqual(self.safety.aol_get_permission_mask(), 0)

  def arm(self):
    self.stream(main=False)
    self.stream(main=True)
    self.stream(main=True, lkas=1)
    self.assertEqual(self.safety.aol_get_permission_mask(), 1 if self.scoped else 0)

  def test_validated_sources_and_fresh_gesture(self):
    for case in CASES:
      with self.subTest(case=case[0]):
        self.configure(case)
        self.stream()
        self.assertEqual(self.safety.aol_get_permission_mask(), 0)
        self.arm()

  def test_reverse_temporary_eps_and_heartbeat_loss(self):
    for case in CASES:
      with self.subTest(case=case[0]):
        self.configure(case)
        self.arm()
        self.stream(gear=2)
        self.assertEqual(self.safety.aol_get_permission_mask(), 0)
        self.safety.aol_set_host_request(0)
        self.stream(eps=6)
        self.assertEqual(self.safety.aol_get_permission_mask(), 0)
        self.stream()
        self.assertEqual(self.safety.aol_get_permission_mask(), 1 if self.scoped else 0)
        self.safety.set_aol_test_heartbeat(False)
        self.assertEqual(self.safety.aol_get_permission_mask(), 0)
        self.safety.set_aol_test_heartbeat(True)
        self.stream()
        self.assertEqual(self.safety.aol_get_permission_mask(), 0)
        self.stream(lkas=1)
        self.assertEqual(self.safety.aol_get_permission_mask(), 1 if self.scoped else 0)

  def test_permanent_eps_then_clean_same_batch_does_not_rearm(self):
    for case in CASES:
      with self.subTest(case=case[0]):
        self.configure(case)
        self.arm()
        self.assertTrue(self.safety.safety_rx_hook(self.packet("STEER_STATUS", {"STEER_STATUS": 7})))
        self.assertTrue(self.safety.safety_rx_hook(self.packet("STEER_STATUS", {"STEER_STATUS": 0})))
        self.stream()
        self.assertEqual(self.safety.aol_get_permission_mask(), 0)
        self.stream(lkas=1)
        self.assertEqual(self.safety.aol_get_permission_mask(), 1 if self.scoped else 0)

  def test_selected_eps_or_gear_loss_requires_new_gesture(self):
    for case in CASES:
      for source in ("STEER_STATUS", case[6]):
        with self.subTest(case=case[0], source=source):
          self.configure(case)
          self.arm()
          self.safety.set_timer(1_300_001)
          self.stream(omit=source)
          self.assertEqual(self.safety.aol_get_permission_mask(), 0)
          self.stream()
          self.assertEqual(self.safety.aol_get_permission_mask(), 0)
          self.stream(lkas=1)
          self.assertEqual(self.safety.aol_get_permission_mask(), 1 if self.scoped else 0)

  def test_ae0_remains_without_independent_permission(self):
    for case in CASES:
      if case[2] in (260, 804):
        continue
      with self.subTest(case=case[0]):
        self.configure(case, experience=0)
        self.stream(main=False)
        self.stream(main=True, lkas=1)
        self.assertEqual(self.safety.aol_get_permission_mask(), 0)

  def test_cold_valid_normal_grant_precedes_engaged_heartbeat(self):
    for case in CASES:
      for button in ((3, 4) if case[2] in (10, 11, 544, 548, 804, 1058, 1059, 1570, 1571, 2082, 2083, 2211) else (3,)):
        with self.subTest(case=case[0], button=button):
          self.configure(case)
          self.safety.set_aol_test_heartbeat(False)
          self.stream(request=False)
          self.normal_engage(button=button)
          self.assertEqual(self.safety.aol_get_request_mask(), 0)
          self.assertEqual(self.safety.aol_get_permission_mask(), 0)
          self.safety.set_timer(1_010_000)
          self.safety.aol_set_host_request(3)
          self.safety.set_aol_test_heartbeat(True)
          self.assertEqual(self.safety.aol_get_permission_mask(), 3 if self.scoped else 0)
          self.press_brake()
          self.assertFalse(self.safety.get_controls_allowed())
          self.assertEqual(self.safety.aol_get_permission_mask(), 1 if self.scoped else 0)

  def test_prior_session_new_lkas_survives_status_before_request_and_heartbeat(self):
    for case in CASES:
      with self.subTest(case=case[0]):
        self.configure(case)
        self.stream()
        self.normal_engage()
        self.safety.aol_set_host_request(3)
        self.assertEqual(self.safety.aol_get_permission_mask(), 3 if self.scoped else 0)
        self.press_brake()
        self.safety.set_aol_test_heartbeat(False)
        self.safety.aol_set_host_request(0)
        self.safety.set_timer(1_010_000)
        self.stream(cruise=1, request=False)  # New neutral after old session loss.
        self.stream(cruise=1, lkas=1, request=False)
        self.assertFalse(self.safety.get_controls_allowed())
        self.assertEqual(self.safety.aol_get_request_mask(), 0)  # Actual first status order.
        self.assertEqual(self.safety.aol_get_permission_mask(), 0)
        self.safety.set_timer(1_020_000)
        self.safety.aol_set_host_request(1)
        self.safety.set_aol_test_heartbeat(True)
        self.assertEqual(self.safety.aol_get_request_mask(), 1 if self.scoped else 0)
        self.assertEqual(self.safety.aol_get_permission_mask(), 1 if self.scoped else 0)
        self.safety.set_aol_test_heartbeat(False)
        self.assertEqual(self.safety.aol_get_permission_mask(), 0)  # Active grant still revoked.
        self.safety.set_aol_test_heartbeat(True)
        self.stream(cruise=1, lkas=1)
        self.assertEqual(self.safety.aol_get_permission_mask(), 0)  # Held button cannot replay.

  def test_pending_physical_grant_requires_post_gesture_request(self):
    for case in CASES:
      with self.subTest(case=case[0]):
        self.configure(case)
        self.safety.set_aol_test_heartbeat(False)
        self.stream()
        self.safety.set_timer(1_010_000)
        self.stream(lkas=1, request=False)
        self.safety.set_aol_test_heartbeat(True)
        self.assertEqual(self.safety.aol_get_permission_mask(), 0)  # Previous request is insufficient.
        self.safety.set_timer(1_020_000)
        self.safety.aol_set_host_request(1)
        self.assertEqual(self.safety.aol_get_permission_mask(), 1 if self.scoped else 0)

  def test_expired_pending_heartbeat_and_sources_require_new_gesture(self):
    for case in CASES:
      for loss in ("heartbeat_expired", "eps_source", "gear_source", "main_source", "host_expired", "relay", "eps_invalid", "rx_checksum"):
        with self.subTest(case=case[0], loss=loss):
          self.configure(case)
          self.safety.set_aol_test_heartbeat(False)
          self.stream(request=False)
          self.normal_engage()
          self.assertEqual(self.safety.aol_get_permission_mask(), 0)
          self.safety.aol_set_host_request(0)
          if loss in ("heartbeat_expired", "eps_source", "gear_source", "main_source", "host_expired"):
            self.safety.set_timer(1_300_001)
            source = {"eps_source": "STEER_STATUS", "gear_source": case[6],
                      "main_source": "SCM_BUTTONS" if self.alt else "SCM_FEEDBACK"}.get(loss)
            self.stream(cruise=1, omit=source, request=False)
            if loss != "host_expired":
              self.safety.aol_set_host_request(0)
          elif loss == "relay":
            self.safety.set_relay_malfunction(True)
          elif loss == "rx_checksum":
            invalid = self.packet("POWERTRAIN_DATA", {"ACC_STATUS": 1})
            invalid.data[7] ^= 1
            self.assertFalse(self.safety.safety_rx_hook(invalid))
          else:
            self.stream(cruise=1, eps=7, request=False)
          self.assertEqual(self.safety.aol_get_permission_mask(), 0)
          self.safety.set_relay_malfunction(False)
          self.safety.set_aol_test_heartbeat(True)
          self.stream(cruise=1)
          self.press_brake()
          self.safety.aol_set_host_request(3)
          self.assertFalse(self.safety.get_controls_allowed())
          self.assertEqual(self.safety.aol_get_permission_mask(), 0)
          self.stream(cruise=0)
          self.normal_engage()
          self.press_brake()
          self.safety.aol_set_host_request(1)
          self.assertEqual(self.safety.aol_get_permission_mask(), 1 if self.scoped else 0)

  def test_pending_grant_clock_wrap_and_deadline(self):
    for case in CASES:
      for delay in (100_000, 300_000, 300_001):
        with self.subTest(case=case[0], delay=delay):
          self.configure(case)
          self.safety.set_timer(0xffff_0000)
          self.safety.set_aol_test_heartbeat(False)
          self.stream(request=False)
          self.stream(lkas=1, request=False)
          self.safety.set_timer((0xffff_0000 + delay) & 0xffff_ffff)
          self.stream(lkas=1, request=False)  # Actual fresh CAN, held level, no new edge.
          self.safety.aol_set_host_request(0)
          self.assertEqual(self.safety.aol_get_permission_mask(), 0)
          self.safety.set_aol_test_heartbeat(True)
          self.safety.aol_set_host_request(1)
          self.assertEqual(self.safety.aol_get_permission_mask(),
                           int(self.scoped and delay <= 300_000))
