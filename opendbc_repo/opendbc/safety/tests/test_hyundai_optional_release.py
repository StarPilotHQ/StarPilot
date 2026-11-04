"""Optional torque-AOL release owns a separate, negotiated physical lease."""
import unittest

from opendbc.car import structs
from opendbc.safety.tests.libsafety import libsafety_py
import opendbc.safety.tests.test_hyundai_ioniq6_stock_aol as stock_helpers
import opendbc.safety.tests.test_hyundai_hda2_long_aol as long_helpers


class TestHyundaiOptionalRelease(unittest.TestCase):
  packet = stock_helpers.TestHyundaiIoniq6StockAol.packet
  rx = stock_helpers.TestHyundaiIoniq6StockAol.rx

  def assert_rx_profile(self):
    self.assertFalse(self.safety.get_relay_malfunction(), self.context)
    self.assertEqual(bool(self.safety.safety_config_valid()), not self.rejected, self.context)
    self.assertEqual(bool(self.safety.aol_rx_healthy()), not self.rejected, self.context)
    if self.rejected:
      self.assertFalse(self.safety.get_controls_allowed(), self.context)
      self.assertEqual(self.safety.aol_get_request_mask(), 0, self.context)
      self.assertEqual(self.safety.aol_get_permission_mask(), 0, self.context)
      self.assertFalse(self.tx(1, 1), self.context)
      self.assertFalse(self.tx(0, 0), self.context)

  def healthy(self, acc=False):
    if not self.release and self.word & 4:
      # The disabled ADAS SCC cannot supply RX authority in DEBUG LONG.
      for name, values in (("ACCELERATOR", {"GEAR": 5}), ("TCS", {}), ("WHEEL_SPEEDS", {}),
                           ("MDPS", {"MDPS_StrTqSnsrVal": 0}), ("CRUISE_BUTTONS", {})):
        self.rx(name, values)
    else:
      stock_helpers.TestHyundaiIoniq6StockAol.healthy(self, acc)
    self.assert_rx_profile()

  def tx(self, torque, request):
    if not self.release and self.word & 4:
      frame = long_helpers.TestHyundaiHda2LongAol.steering_frames(self, torque)[0]
      self.assertEqual((frame[0], frame[2]), (0x12A, 1), self.context)
      data = frame[1]
      self.assertEqual((((data[6] & 0xF) << 7) | (data[5] >> 1)) - 1024, torque, self.context)
      self.assertEqual((data[6] >> 4) & 1, request, self.context)
      return self.safety.safety_tx_hook(libsafety_py.make_CANPacket(frame[0], frame[2], data))
    return stock_helpers.TestHyundaiIoniq6StockAol.tx(self, torque, request)

  def mode(self, raw=0x815, experience=32):
    self.word = raw
    self.safety = libsafety_py.libsafety
    self.release = self.safety.set_safety_hooks(structs.CarParams.SafetyModel.allOutput, 0) != 0
    self.rejected = raw in (0x815, 0x895) and (self.release or experience != 32)
    self.context = f"raw=0x{raw:04x}, experience={experience}, release={self.release}, rejected={self.rejected}"
    self.safety.set_alternative_experience(experience)
    stock_helpers.TestHyundaiIoniq6StockAol.mode(self, raw)
    self.safety.set_alternative_experience(experience)
    self.now = 1_000_000
    self.healthy()
    self.safety.set_aol_test_heartbeat(True)

  def buttons(self, cruise=0, *, main=0, lkas=0, counter=None, advance=20_000):
    self.now += advance
    self.safety.set_timer(self.now)
    values = {"CRUISE_BUTTONS": cruise, "ADAPTIVE_CRUISE_MAIN_BTN": main, "LDA_BTN": lkas}
    if counter is not None:
      values["COUNTER"] = counter
    self.rx("CRUISE_BUTTONS", values)

  def release_gesture(self, button=2):
    self.buttons()
    self.buttons(button)
    self.buttons()

  def claimed(self, button=2):
    self.safety.aol_set_host_request(5)
    self.release_gesture(button)
    self.assert_rx_profile()
    self.safety.aol_set_host_request(7)
    self.buttons(4)
    self.assertFalse(self.safety.get_controls_allowed(), self.context)
    self.safety.aol_set_host_request(5)
    self.assertEqual(self.safety.aol_get_request_mask(), 0 if self.release else 1, self.context)
    self.assertEqual(self.safety.aol_get_permission_mask(), 0 if self.release else 1, self.context)

  def assert_denied(self, *, neutral=True):
    self.assertEqual(self.safety.aol_get_permission_mask() & 1, 0, self.context)
    self.assertFalse(self.tx(1, 1), self.context)
    self.assertEqual(bool(self.tx(0, 0)), neutral and not self.rejected, self.context)

  def test_optional_release_policy_never_grants_without_fresh_physical_release(self):
    for raw in (0x815, 0x895):
      for button in (1, 2):
        with self.subTest(raw=raw, button=button):
          self.mode(raw)
          self.safety.aol_set_host_request(5)
          self.buttons()
          self.assert_denied()
          self.buttons(button)
          self.assert_denied()
          self.buttons()
          self.assert_rx_profile()
          self.safety.aol_set_host_request(7)
          self.buttons(4)
          self.safety.aol_set_host_request(5)
          self.assertEqual(self.safety.aol_get_permission_mask(), 0 if self.release else 1, self.context)
          self.assertEqual(bool(self.tx(1, 1)), not self.release, self.context)
          self.mode(raw)
          self.safety.aol_set_host_request(1)
          self.release_gesture(button)
          self.buttons(4)
          self.safety.aol_set_host_request(1)
          self.assert_denied()

  def test_optional_release_counter_replay_gap_and_cold_held_require_neutral(self):
    for boundary in ("cold_set", "cold_resume", "replay", "counter_gap", "time_gap"):
      with self.subTest(boundary=boundary):
        self.mode()
        self.safety.aol_set_host_request(5)
        if boundary.startswith("cold"):
          self.buttons(2 if boundary == "cold_set" else 1)
          self.buttons()
        else:
          self.buttons()
          self.buttons(2)
          previous = self.counters["CRUISE_BUTTONS"] % 16
          counter = previous if boundary == "replay" else ((previous + 2) % 16 if boundary == "counter_gap" else None)
          self.buttons(counter=counter, advance=100_001 if boundary == "time_gap" else 20_000)
        self.safety.aol_set_host_request(5)
        self.buttons(4)
        self.assert_denied()
        self.safety.aol_set_host_request(0)
        self.healthy()
        self.safety.set_aol_test_heartbeat(True)
        self.safety.aol_set_host_request(5)
        self.release_gesture()
        self.assert_rx_profile()
        self.safety.aol_set_host_request(7)
        self.buttons(4)
        self.safety.aol_set_host_request(5)
        self.assertEqual(self.safety.aol_get_permission_mask(), 0 if self.release else 1, self.context)

  def test_optional_release_off_lease_fault_reset_and_cancel_axis_ownership(self):
    for boundary in ("policy_off", "axis_off", "expiry", "heartbeat", "relay", "invalid_rx", "reset"):
      with self.subTest(boundary=boundary):
        self.mode()
        self.claimed()
        if boundary == "policy_off":
          self.safety.aol_set_host_request(1)
        elif boundary == "axis_off":
          self.safety.aol_set_host_request(4)
          self.safety.aol_set_host_request(5)
        elif boundary == "expiry":
          self.safety.set_timer(self.now + 600_001)
        elif boundary == "heartbeat":
          self.safety.set_aol_test_heartbeat(False)
        elif boundary == "relay":
          self.safety.set_relay_malfunction(True)
        elif boundary == "invalid_rx":
          frame = self.packer.make_can_msg("TCS", 1, {"COUNTER": (self.counters["TCS"] + 1) % 256})
          data = bytearray(frame[1])
          self.assertEqual(len(data), 24, self.context)
          data[0] ^= 1
          # An empty rejected catalog does not interpret this RX as authority.
          accepted = self.safety.safety_rx_hook(libsafety_py.make_CANPacket(frame[0], frame[2], data))
          self.assertEqual(bool(accepted), self.rejected, self.context)
        else:
          self.mode()
          self.safety.aol_set_host_request(5)
        self.assert_denied(neutral=boundary != "relay")
    self.mode()
    self.safety.aol_set_host_request(5)
    self.buttons()
    self.buttons(lkas=1)
    self.buttons()
    self.safety.aol_set_host_request(1)
    self.assertEqual(self.safety.aol_get_permission_mask(), 0 if self.release else 1, self.context)

  def test_optional_release_capability_exact_words_debug_release_and_mode_reset(self):
    for raw, experience in ((0x815, 32), (0x895, 32), (0x815, 0), (0x15, 32), (0x811, 32), (0x891, 32)):
      for button in (1, 2):
        with self.subTest(raw=raw, experience=experience, button=button):
          self.mode(raw, experience)
          expected = not self.release and experience == 32 and raw in (0x815, 0x895)
          self.safety.aol_set_host_request(5)
          self.assertEqual(self.safety.aol_get_request_mask() & 4, 0, self.context)
          self.assertFalse(self.safety.get_controls_allowed(), self.context)
          self.assertEqual(self.safety.aol_get_permission_mask(), 0, self.context)
          self.assertFalse(self.tx(1, 1), self.context)
          # Claim a real SET/RES release, then withdraw ordinary cruise with CANCEL.
          self.release_gesture(button)
          self.assert_rx_profile()
          self.safety.aol_set_host_request(7)
          self.buttons(4)
          self.safety.aol_set_host_request(5)
          self.assertFalse(self.safety.get_controls_allowed(), self.context)
          self.assertEqual(self.safety.aol_get_request_mask() & 4, 0, self.context)
          self.assertEqual(self.safety.aol_get_permission_mask(), int(expected), self.context)
          self.assertEqual(bool(self.tx(1, 1)), expected, self.context)
    self.mode()
    self.claimed()
    self.assertEqual(self.safety.set_safety_hooks(structs.CarParams.SafetyModel.silent, 0), 0, self.context)
    self.safety.aol_set_host_request(7)
    self.assertEqual(self.safety.aol_get_request_mask(), 0, self.context)
    self.assertEqual(self.safety.aol_get_permission_mask(), 0, self.context)
    self.assertFalse(self.tx(1, 1), self.context)


  def test_claimed_lateral_survives_timely_button_counter_interruption(self):
    for raw in (0x815, 0x895):
      for button in (1, 2):
        for defect in ("replay", "gap"):
          with self.subTest(raw=raw, button=button, defect=defect):
            self.mode(raw)
            self.claimed(button)
            self.now += 20_000
            self.safety.set_timer(self.now)
            self.healthy()
            counter = self.counters["CRUISE_BUTTONS"] % 16
            self.buttons(counter=counter if defect == "replay" else (counter + 2) % 16)
            self.assert_rx_profile()
            self.assertEqual(self.safety.aol_get_permission_mask(), 0 if self.release else 1, self.context)
            self.assertEqual(bool(self.tx(1, 1)), not self.release, self.context)
            self.buttons(counter=(counter + 2) % 16)
            self.safety.aol_set_host_request(5)
            self.assertFalse(self.safety.get_controls_allowed(), self.context)
            self.assertEqual(self.safety.aol_get_permission_mask(), 0 if self.release else 1, self.context)
            self.safety.aol_set_host_request(0)
            self.safety.aol_set_host_request(5)
            self.assert_denied()

  def test_claimed_lateral_still_requires_bounded_button_source_time(self):
    for raw in (0x815, 0x895):
      for elapsed in (0, 100_001):
        with self.subTest(raw=raw, elapsed=elapsed):
          self.mode(raw)
          self.claimed()
          self.buttons(counter=self.counters["CRUISE_BUTTONS"] % 16, advance=elapsed)
          self.assert_denied()
