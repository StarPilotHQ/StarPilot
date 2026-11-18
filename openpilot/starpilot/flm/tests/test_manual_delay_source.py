import json

from openpilot.starpilot.flm.tests.test_fixed_manual_baseline import fixed_controls, BASE
from openpilot.starpilot.flm.runtime_adapter_registry import read_source
from openpilot.starpilot.lateral.delay_preferences import KEY, replace_delay


def test_manual_delay_changes_source_epoch_without_disabling_torque_profile_learning():
  with fixed_controls(factor=False, friction=False) as (controls, _):
    def source():
      return read_source(controls.CP, controls.params, now_ns=BASE, calibration=(0., 0., 0.), vehicle_session='delay-test')
    initial = source()
    assert initial.enabled and not initial.manual and initial.source_epoch
    encoded = replace_delay(None, controls.CP, .6)
    controls.params.put(KEY, json.loads(encoded), block=True)
    manual = source()
    assert manual.enabled and not manual.manual and manual.source_epoch != initial.source_epoch
    controls.params.put(KEY, json.loads(replace_delay(encoded, controls.CP, None)), block=True)
    reset = source()
    assert reset.enabled and not reset.manual and reset.source_epoch != manual.source_epoch
