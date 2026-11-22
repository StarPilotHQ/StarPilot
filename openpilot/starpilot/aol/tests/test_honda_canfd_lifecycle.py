"""Stock CAN-FD Honda caller, C++ negotiation, and native TX lifecycle."""
from opendbc.car.honda.values import CAR
from openpilot.starpilot.aol.tests.test_honda_recovery_lifecycle import TestHondaCurrentCaller as CurrentCaller

class TestHondaCanfdCaller(CurrentCaller):
  def test_canfd_0_eps_reverse_recovery(self):
    self.run_current_caller(candidate=CAR.HONDA_CRV_6G, word=16)

  def test_canfd_1_eps_reverse_recovery(self):
    self.run_current_caller(candidate=CAR.HONDA_PILOT_4G, word=16)

  def test_canfd_2_eps_reverse_recovery(self):
    self.run_current_caller(candidate=CAR.HONDA_PASSPORT_4G, word=16)

  def test_canfd_3_eps_reverse_recovery(self):
    self.run_current_caller(candidate=CAR.ACURA_MDX_4G_MMR, word=16)

  def test_canfd_4_eps_reverse_recovery(self):
    self.run_current_caller(candidate=CAR.ACURA_TLX_2G_MMR, word=16)


  def test_canfd_alternate_brake_eps_reverse_recovery(self):
    self.run_current_caller(candidate=CAR.HONDA_CRV_6G, word=17, alternate_brake=True)

  def test_accord_mvl_eps_reverse_recovery(self):
    self.run_current_caller(candidate=CAR.HONDA_ACCORD_11G, word=80)

  def test_canfd_crv_can2_diagnostic_from_start(self):
    self.run_current_caller(candidate=CAR.HONDA_CRV_6G, word=16, diagnostic_from_start=True, hybrid=True)

  def test_canfd_pilot_can2_diagnostic_from_start(self):
    self.run_current_caller(candidate=CAR.HONDA_PILOT_4G, word=16, diagnostic_from_start=True)

  def test_canfd_passport_can2_diagnostic_from_start(self):
    self.run_current_caller(candidate=CAR.HONDA_PASSPORT_4G, word=16, diagnostic_from_start=True)

  def test_canfd_mdx_can2_diagnostic_from_start(self):
    self.run_current_caller(candidate=CAR.ACURA_MDX_4G_MMR, word=16, diagnostic_from_start=True)

  def test_canfd_tlx_can2_diagnostic_from_start(self):
    self.run_current_caller(candidate=CAR.ACURA_TLX_2G_MMR, word=16, diagnostic_from_start=True)

  def test_canfd_alternate_brake_can2_diagnostic_from_start(self):
    self.run_current_caller(candidate=CAR.HONDA_CRV_6G, word=17, alternate_brake=True, diagnostic_from_start=True)
