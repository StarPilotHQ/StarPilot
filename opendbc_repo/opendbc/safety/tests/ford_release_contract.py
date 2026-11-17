"""Read-only RELEASE configuration observations; selection status is not admission."""

from opendbc.car import gen_empty_fingerprint
from opendbc.car.ford.fordcan import CanBus, create_acc_msg, create_lat_ctl2_msg
from opendbc.safety.tests.libsafety import libsafety_py

# Existing global/type in declarations.h, exposed read-only; no native code edits.
libsafety_py.ffi.cdef('extern safety_config current_safety_config;')

STOCK_CANFD_TX = frozenset(((0x83, 0, 8), (0x83, 2, 8), (0x18A, 0, 8), (0x3CA, 0, 8), (0x3D8, 0, 8), (0x3D6, 0, 8)))
# the retired Mach-E (19) and generic CAN FD (67) extended long words: stock CAN FD TX, no AOL or LONG
RELEASE_LEDGER = {19: (6, STOCK_CANFD_TX), 67: (6, STOCK_CANFD_TX)}


def assert_release_denial(native, word):
  """Prove absent AOL/LONG, preserving the existing stock fallback of a retired parameter.

  A physical PCM edge can still set the ordinary boolean even with an empty TX
  table. No assertion equates that boolean or hook return code with authority.
  """
  safety = native.safety
  native.word, native.tick = word & ~1, 0
  safety.set_aol_test_heartbeat(True)
  config = safety.current_safety_config
  expected_rx_count, expected_tx = RELEASE_LEDGER[word]
  assert config.rx_checks_len == expected_rx_count
  actual_tx = frozenset((config.tx_msgs[i].addr, config.tx_msgs[i].bus, config.tx_msgs[i].len) for i in range(config.tx_msgs_len))
  assert actual_tx == expected_tx
  assert all(address != 0x186 for address, _bus, _length in actual_tx)
  assert all(config.rx_checks[i].msg[0].addr not in (0x82, 0x83, 0x5A, 0x176, 0x230, 0x3CC) for i in range(config.rx_checks_len))
  native.pump(40, main=3)
  native.pump(10, main=4, mask=3)
  assert safety.safety_config_valid()  # Common6 RX remain; not a qualified AOL inventory.
  assert safety.get_controls_allowed()  # Observed PCM boolean is not native AOL admission.
  bus = CanBus(fingerprint=gen_empty_fingerprint())
  for mask in (0, 1, 2, 3):
    safety.aol_set_host_request(mask)
    assert safety.aol_get_request_mask() == safety.aol_get_permission_mask() == 0
    for active in (False, True):
      address, data, _ = create_acc_msg(native.packer, bus, active, 0.5 if active else -5.0, 0.5 if active else 0.0, False, False, 60.0)
      for target_bus in range(3):
        assert not safety.safety_tx_hook(libsafety_py.make_CANPacket(address, target_bus, data))
  if not expected_tx:
    for address in (0x83, 0x186, 0x18A, 0x3CA, 0x3D3, 0x3D6, 0x3D8):
      for target_bus in range(3):
        assert not safety.safety_tx_hook(libsafety_py.make_CANPacket(address, target_bus, bytes(8)))
  native.rx('EngBrakeData', {'BpedDrvAppl_D_Actl': 2, 'CcStat_D_Actl': 3})
  assert not safety.get_controls_allowed()
  safety.aol_set_host_request(3)
  assert safety.aol_get_permission_mask() == 0
  address, data, target_bus = create_lat_ctl2_msg(native.packer, bus, 1, 0.0, 0.0, 0.0, 0.0, 0)
  assert not safety.safety_tx_hook(libsafety_py.make_CANPacket(address, target_bus, data))
  native.pump(20, main=3, mask=3)
  assert safety.aol_get_request_mask() == safety.aol_get_permission_mask() == 0
  assert not safety.get_controls_allowed()
