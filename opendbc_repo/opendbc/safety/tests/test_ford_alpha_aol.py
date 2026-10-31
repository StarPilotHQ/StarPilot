"""Ordinary Ford Alpha Long needs a truthful independent native AOL axis."""

import pytest

from opendbc.car import gen_empty_fingerprint
from opendbc.car.ford.fordcan import CanBus, create_acc_msg
from opendbc.safety.tests.libsafety import libsafety_py
from opendbc.safety.tests.test_ford_full_engagement import ford_native as ford_native, select

LONG_WORDS = (1, 3, 9, 11, 13)
STOCK_WORDS = (0, 2, 8, 10, 12)


def tx(native, *, active):
  frame = create_acc_msg(
    native.packer, CanBus(fingerprint=gen_empty_fingerprint()), active, 0.5 if active else -5.0, 0.5 if active else 0.0, False, False, v_ego_kph=60.0
  )
  return bool(native.safety.safety_tx_hook(libsafety_py.make_CANPacket(frame[0], frame[2], frame[1])))


@pytest.mark.parametrize('word', LONG_WORDS)
def test_real_full_engagement_admits_long_and_brake_retains_only_lateral(ford_native, word):
  n = ford_native
  select(n, word)
  n.pump(40, main=3)
  n.pump(10, main=4, mask=3)
  assert n.safety.get_controls_allowed()
  assert n.safety.aol_get_permission_mask() == 3  # Original Ford masks bit2 away.
  assert tx(n, active=True)
  n.rx('EngBrakeData', {'BpedDrvAppl_D_Actl': 2, 'CcStat_D_Actl': 3})
  assert not n.safety.get_controls_allowed()
  assert n.safety.aol_get_permission_mask() == 1
  assert not tx(n, active=True)
  assert tx(n, active=False)
  n.pump(30, main=3, mask=3)  # Held/fresh requests cannot restore disabled LONG.
  assert n.safety.aol_get_permission_mask() == 1
  assert not tx(n, active=True)
  n.pump(10, main=4, mask=3)  # A new real ordinary engagement can restore LONG.
  assert n.safety.aol_get_permission_mask() == 3
  assert tx(n, active=True)


@pytest.mark.parametrize('word', STOCK_WORDS)
def test_stock_long_off_never_gains_a_long_axis_from_any_request(ford_native, word):
  n = ford_native
  select(n, word)
  n.pump(40, main=3)
  n.pump(10, main=4, mask=3)
  assert n.safety.aol_get_permission_mask() == 1
  assert not tx(n, active=True)


@pytest.mark.parametrize('fault', ('source', 'heartbeat', 'request-expired', 'eps-permanent'))
def test_native_long_recovery_requires_new_activation_after_real_revocation(ford_native, fault):
  n = ford_native
  select(n, 1)
  n.pump(40, main=3)
  n.pump(10, main=4, mask=3)
  assert n.safety.aol_get_permission_mask() == 3
  if fault == 'eps-permanent':
    n.rx('EPAS_INFO', {'EPAS_Failure': 2})
    n.rx('EPAS_INFO', {'EPAS_Failure': 0})  # Same-batch clean sample cannot erase the fault.
  elif fault == 'heartbeat':
    n.safety.set_aol_test_heartbeat(False)
  else:
    if fault == 'request-expired':
      # Refresh real physical RX without renewing the original host request.
      real = n.safety

      class WithoutHostRenewal:
        def __getattr__(self, name):
          return (lambda _mask: None) if name == 'aol_set_host_request' else getattr(real, name)

      n.safety = WithoutHostRenewal()
      try:
        n.pump(40, main=4, mask=0)
      finally:
        n.safety = real
    else:
      n.safety.set_timer(3_000_000)
      n.tick = 200  # Resume the fixture on the advanced physical clock, never backwards.
  assert n.safety.aol_get_permission_mask() & 2 == 0
  assert not n.safety.get_controls_allowed()
  assert not tx(n, active=True)
  n.pump(30, main=4, mask=3)  # Recovered health alone cannot revive ordinary LONG.
  assert n.safety.aol_get_permission_mask() & 2 == 0
  assert not tx(n, active=True)
  n.pump(10, main=3, mask=3)
  n.pump(10, main=4, mask=3)
  assert n.safety.aol_get_permission_mask() == 3
  assert tx(n, active=True)


@pytest.mark.parametrize('word', LONG_WORDS)
def test_long_only_full_activation_does_not_depend_on_a_lateral_command(ford_native, word):
  n = ford_native
  select(n, word)
  n.pump(40, main=3)
  n.pump(60, main=4, mask=2)
  assert n.safety.aol_get_permission_mask() == 2
  assert tx(n, active=True)
  n.rx('EngBrakeData', {'BpedDrvAppl_D_Actl': 2, 'CcStat_D_Actl': 3})
  n.safety.aol_set_host_request(3)
  assert n.safety.aol_get_permission_mask() == 1  # Full engagement armed lateral intent, not retained LONG.
  assert not tx(n, active=True)


@pytest.mark.parametrize('word', LONG_WORDS)
@pytest.mark.parametrize('gas,accel', ((2.1, 0.5), (-0.6, 0.5), (0.5, 2.1), (0.5, -3.6)))
def test_new_long_permission_preserves_existing_accel_gas_and_bus_limits(ford_native, word, gas, accel):
  n = ford_native
  select(n, word)
  n.pump(40, main=3)
  n.pump(10, main=4, mask=3)
  assert n.safety.aol_get_permission_mask() == 3 and tx(n, active=True)
  frame = create_acc_msg(n.packer, CanBus(fingerprint=gen_empty_fingerprint()), True, gas, accel, False, False, v_ego_kph=60.0)
  assert not n.safety.safety_tx_hook(libsafety_py.make_CANPacket(frame[0], frame[2], frame[1]))
  frame = create_acc_msg(n.packer, CanBus(fingerprint=gen_empty_fingerprint()), True, 0.5, 0.5, False, False, v_ego_kph=60.0)
  assert not n.safety.safety_tx_hook(libsafety_py.make_CANPacket(frame[0], 2, frame[1]))
