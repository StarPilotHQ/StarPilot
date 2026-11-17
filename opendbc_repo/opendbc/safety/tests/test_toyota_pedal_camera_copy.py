"""A camera pedal copy cannot renew RX or replace physical driver override."""
import pytest
from opendbc.can import CANPacker
from opendbc.car import structs
from opendbc.car.toyota import pedal
from opendbc.car.toyota.pedal_composite import PARENT_WORDS
from opendbc.safety.tests.libsafety import libsafety_py
from openpilot.starpilot.car.toyota.tests import test_rav4h_caller_native as helpers

source_matched_native = helpers.source_matched_native
libsafety_py.ffi.cdef('extern safety_config current_safety_config;')


@pytest.mark.parametrize('word', sorted(set(PARENT_WORDS) | {0x2049, 0x2249, 0x2449, 0x2649}))
def test_copy_cannot_supply_primary_lease_or_clear_driver_gas(word):
  safety = libsafety_py.libsafety
  safety.init_tests()
  safety.set_alternative_experience(32)
  assert safety.set_safety_hooks(structs.CarParams.SafetyModel.toyota, word) == 0
  packer = CANPacker('toyota_nodsu_pt_generated')

  def frame(bus, gas, counter):
    address, data, _ = packer.make_can_msg('GAS_SENSOR', 0, {
      'INTERCEPTOR_GAS': gas, 'INTERCEPTOR_GAS2': gas, 'COUNTER_PEDAL': counter, 'STATE': 0})
    data = bytearray(data)
    data[-1] = pedal.checksum(data)
    return libsafety_py.make_CANPacket(address, bus, bytes(data))

  try:
    cfg = safety.current_safety_config
    statuses = [safety.rx_status[i] for i in range(cfg.rx_checks_len)
                if cfg.rx_checks[i].msg[0].addr == 0x201 and cfg.rx_checks[i].msg[0].bus == 0]
    assert len(statuses) == 1
    status = statuses[0]
    safety.set_timer(1_000_000)
    assert safety.safety_rx_hook(frame(2, 0, 0))
    assert not status.msg_seen and not safety.safety_config_valid()
    assert safety.aol_get_permission_mask() == 0
    safety.set_timer(1_010_000)
    assert safety.safety_rx_hook(frame(0, 806, 0))
    assert status.msg_seen and safety.get_gas_pressed_prev()
    timestamp = status.last_timestamp
    for counter in range(1, 16):
      safety.set_timer(1_010_000 + counter * 10_000)
      assert safety.safety_rx_hook(frame(2, 0, counter))
      assert status.last_timestamp == timestamp
      assert safety.get_gas_pressed_prev()
    assert safety.aol_get_permission_mask() == 0
  finally:
    safety.set_alternative_experience(0)
    safety.set_safety_hooks(structs.CarParams.SafetyModel.noOutput, 0)
