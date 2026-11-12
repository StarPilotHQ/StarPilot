"""Normal-runtime classic Bosch alpha owner, real FW/parser/native TX lifecycle."""
from opendbc.car.honda.values import CAR
from openpilot.starpilot.aol.tests.test_honda_recovery_lifecycle import TestHondaCurrentCaller as CurrentCaller

class TestHondaClassicAlpha(CurrentCaller):
  def test_classic_0_eps_reverse_recovery(self):
    self.run_current_caller(candidate=CAR.HONDA_CIVIC_BOSCH, word=2082, alpha=True)

  def test_classic_1_eps_reverse_recovery(self):
    self.run_current_caller(candidate=CAR.ACURA_RDX_3G, word=2211, alpha=True)

  def test_classic_2_eps_reverse_recovery(self):
    for scenario in (None, 'cancel'):
      with self.subTest(scenario=scenario):
        self.run_current_caller(candidate=CAR.HONDA_CRV_5G, word=2083, alpha=True, alternate_brake=True, host_scenario=scenario)
