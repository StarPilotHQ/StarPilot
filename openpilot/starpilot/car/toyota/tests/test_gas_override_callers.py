"""Gas override retains ordinary engagement through real host and native callers."""

import pytest
from opendbc.car.toyota import prius_advert_filter, prius_sdsu, toyotacan
from opendbc.car.toyota.values import CAR
from openpilot.starpilot.car.toyota.tests import test_rav4h_caller_native as helpers
from openpilot.starpilot.car.toyota.tests.test_prius_sdsu_filter import original_firmware
from openpilot.starpilot.car.toyota.tests.test_toyota_cpp_transport import host_heartbeat_bridge as host_heartbeat_bridge

source_matched_native = helpers.source_matched_native
daemon_gc_scope = helpers.daemon_gc_scope


@pytest.mark.parametrize('advert', (False, True))
def test_gas_override_recovers_without_reengagement(monkeypatch, host_heartbeat_bridge, advert):
  emitted = []
  original = toyotacan.create_accel_command

  def observe(packer, accel, cancel, *args, **kwargs):
    emitted.append((accel, cancel))
    return original(packer, accel, cancel, *args, **kwargs)

  monkeypatch.setattr(toyotacan, 'create_accel_command', observe)
  samples = set()

  def inspect(tick, host, cs, cc, safety, native):
    if 260 <= tick < 266:
      assert cs.gasPressed and cc.enabled and cc.latActive and not cc.longActive
      assert not cc.cruiseControl.cancel
      assert emitted and emitted[-1] == (0., False)
      samples.add('neutral_override')
    if 320 <= tick < 330:
      assert not cs.gasPressed and cc.enabled and cc.longActive
      samples.add('recovered')

  owner = prius_advert_filter if advert else prius_sdsu
  helpers.run_case(False, 'physical', candidate=CAR.TOYOTA_PRIUS, bridge=host_heartbeat_bridge,
                   input_clock=True, firmware_override=original_firmware(), filter_advert=advert,
                   sdsu_words=(owner.LONG_WORD, owner.STOCK_WORD), on_sample=inspect)
  assert samples == {'neutral_override', 'recovered'}
