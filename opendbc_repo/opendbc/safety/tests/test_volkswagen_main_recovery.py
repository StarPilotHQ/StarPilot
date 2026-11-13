"""A physical MAIN cycle can await heartbeat, but cannot survive an EPS fault."""
import unittest

from opendbc.car import structs
from opendbc.safety.tests.libsafety import libsafety_py
from opendbc.safety.tests import test_volkswagen_first_press as physical


class TestVolkswagenMainRecovery(unittest.TestCase):
  def test_main_on_waits_for_heartbeat_and_grants_only_lateral(self):
    debug = libsafety_py.libsafety.set_safety_hooks(structs.CarParams.SafetyModel.allOutput, 0) == 0
    for word in (8, 24, 40, 9, 25, 41):
      with self.subTest(word=word):
        owner = physical.Owner()
        owner.direct = word in (40, 41)
        owner.configure(word)
        owner.manual = word in (24, 25)
        try:
          owner.safety.set_aol_test_heartbeat(False)
          for _ in range(12):
            owner.feed(main=False)
            owner.safety.aol_set_host_request(0)
            assert owner.safety.aol_get_permission_mask() == 0
          for _ in range(12):
            owner.feed()
            owner.safety.aol_set_host_request(0)
            assert owner.safety.aol_get_permission_mask() == 0
          owner.safety.set_aol_test_heartbeat(True)
          for _ in range(12):
            owner.feed()
          assert not owner.safety.get_controls_allowed()
          assert owner.safety.aol_get_permission_mask() == (1 if word in (8, 24, 40) or debug else 0)
        finally:
          owner.tearDown()

  def test_pending_main_cannot_rearm_after_permanent_eps_fault(self):
    for word in (8, 24, 40, 9, 25, 41):
      with self.subTest(word=word):
        owner = physical.Owner()
        owner.direct = word in (40, 41)
        owner.configure(word)
        owner.manual = word in (24, 25)
        try:
          owner.safety.set_aol_test_heartbeat(False)
          for _ in range(12):
            owner.feed(main=False)
          owner.feed(permanent=True)
          assert owner.safety.aol_get_permission_mask() == 0
          owner.safety.set_aol_test_heartbeat(True)
          for _ in range(12):
            owner.feed()
          assert not owner.safety.get_controls_allowed()
          assert owner.safety.aol_get_permission_mask() == 0
        finally:
          owner.tearDown()
