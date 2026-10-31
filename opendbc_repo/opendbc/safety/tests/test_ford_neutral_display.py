"""Inactive cluster continuity requires fresh, accepted neutral replacements."""
import pytest

from opendbc.car import gen_empty_fingerprint
from opendbc.car.ford import fordcan
from opendbc.car.ford.fordcan import CanBus
from opendbc.safety.tests.libsafety import libsafety_py
from opendbc.safety.tests import test_ford_aol


@pytest.fixture(params=(3, 259))
def owner(request):
  native = test_ford_aol.TestFordAolDriverIntent()
  native.setUp()
  native.reset(request.param)
  native.safety.init_tests()
  native.safety.set_alternative_experience(32)
  native.pump(40, main=0, mask=0, heartbeat=False)
  bus = CanBus(fingerprint=gen_empty_fingerprint())
  address, data, src = fordcan.create_lka_msg(native.packer, bus)
  assert native.safety.safety_tx_hook(libsafety_py.make_CANPacket(address, src, data))
  yield native
  native.tearDown()  # AE 32 must not reach the next test's profile


def inactive(owner):
  bus = CanBus(fingerprint=gen_empty_fingerprint())
  address, data, src = fordcan.create_lat_ctl2_msg(owner.packer, bus, 0, 0., 0., 0., 0., 0)
  assert owner.safety.safety_tx_hook(libsafety_py.make_CANPacket(address, src, data))


def ui(owner, address, data=bytes(8)):
  return owner.safety.safety_tx_hook(libsafety_py.make_CANPacket(address, 0, data))


def acquire(owner):
  inactive(owner)
  assert ui(owner, 0x18A)
  assert ui(owner, 0x3D8)
  assert owner.safety.safety_fwd_hook(2, 0x18A) == -1
  assert owner.safety.safety_fwd_hook(2, 0x3D8) == -1
  assert owner.safety.safety_fwd_hook(2, 0x3D6) == -1  # the accepted inactive actuator replaces the camera's copy
  assert owner.safety.aol_get_permission_mask() == 0
  assert not owner.safety.get_controls_allowed()


def test_neutral_display_requires_accepted_inactive_and_each_actual_ui_receipt(owner):
  assert not ui(owner, 0x18A)
  assert not ui(owner, 0x3D8)
  inactive(owner)
  assert owner.safety.safety_fwd_hook(2, 0x18A) == 0
  assert owner.safety.safety_fwd_hook(2, 0x3D8) == 0
  assert ui(owner, 0x18A)
  assert owner.safety.safety_fwd_hook(2, 0x18A) == -1
  assert owner.safety.safety_fwd_hook(2, 0x3D8) == 0
  assert ui(owner, 0x3D8)
  assert owner.safety.safety_fwd_hook(2, 0x3D8) == -1
  assert owner.safety.safety_fwd_hook(2, 0x321) == 0
  assert owner.safety.aol_get_permission_mask() == 0


def test_main_off_continuity_has_no_steering_or_longitudinal_authority(owner):
  acquire(owner)
  for tick in range(40):
    owner.pump(5, main=0, mask=0, heartbeat=False)
    inactive(owner)
    if tick % 2 == 0:
      assert ui(owner, 0x18A)
    assert owner.safety.safety_fwd_hook(2, 0x18A) == -1
    assert owner.safety.safety_fwd_hook(2, 0x3D8) == -1
    assert owner.safety.safety_fwd_hook(2, 0x3D6) == -1
    assert owner.safety.aol_get_permission_mask() == 0
    assert not owner.safety.get_controls_allowed()
  bus = CanBus(fingerprint=gen_empty_fingerprint())
  address, data, src = fordcan.create_lat_ctl2_msg(owner.packer, bus, 1, 0., 0., 0., 0., 1)
  assert not owner.safety.safety_tx_hook(libsafety_py.make_CANPacket(address, src, data))
  assert owner.safety.safety_fwd_hook(2, 0x18A) == 0


@pytest.mark.parametrize('status', (2, 3, 4, 5, 6, 7))
def test_neutral_cannot_show_active_intervention_or_hands_free(owner, status):
  acquire(owner)
  data = bytearray(8)
  data[5] = status << 2
  assert not ui(owner, 0x18A, data)
  assert owner.safety.safety_fwd_hook(2, 0x18A) == 0
  assert owner.safety.aol_get_permission_mask() == 0


@pytest.mark.parametrize('data', (bytes([0] * 6 + [6 << 3, 0]), bytes([0] * 6 + [2, 0])))
def test_neutral_cannot_show_active_lane_lines_or_hands_off(owner, data):
  acquire(owner)
  assert not ui(owner, 0x3D8, data)
  assert owner.safety.safety_fwd_hook(2, 0x3D8) == 0


@pytest.mark.parametrize('loss', ('expired', 'display-expired', 'eps', 'gear', 'lka', 'acc-fault', 'rx'))
def test_neutral_returns_stock_on_real_faults_or_stale_inactive_source(owner, loss):
  acquire(owner)
  if loss == 'expired':
    owner.pump(31, main=0, mask=0, heartbeat=False)
  elif loss == 'display-expired':
    for _ in range(7):
      owner.pump(5, main=0, mask=0, heartbeat=False)
      inactive(owner)
  elif loss == 'eps':
    owner.rx('EPAS_INFO', {'EPAS_Failure': 1})
  elif loss == 'gear':
    owner.pump(10, main=0, mask=0, gear=1, heartbeat=False)
  elif loss == 'lka':
    owner.rx('Lane_Assist_Data3_FD1', {'LatCtlSte_D_Stat': 0})
  elif loss == 'acc-fault':
    owner.rx('EngBrakeData', {'CcStat_D_Actl': 1, 'BpedDrvAppl_D_Actl': 1})
  else:
    address, data, bus = owner.packer.make_can_msg('BrakeSysFeatures', 0, {'Veh_V_ActlBrk': 36., 'VehVActlBrk_D_Qf': 3})
    bad = bytearray(data)
    bad[0] ^= 1
    assert not owner.safety.safety_rx_hook(libsafety_py.make_CANPacket(address, bus, bad))
  assert owner.safety.safety_fwd_hook(2, 0x18A) == 0
  assert owner.safety.safety_fwd_hook(2, 0x3D8) == 0
  if loss != 'display-expired':
    assert not ui(owner, 0x18A)
    assert not ui(owner, 0x3D8)
  assert owner.safety.aol_get_permission_mask() == 0


def test_neutral_continuity_across_real_microsecond_timer_wrap(owner):
  actual = owner.safety

  class WrappedClock:
    def set_timer(self, value):
      actual.set_timer((value - 1_500_000) & 0xffffffff)

    def __getattr__(self, name):
      return getattr(actual, name)

  owner.safety = WrappedClock()
  owner.reset(owner.word)
  owner.safety.init_tests()
  owner.safety.set_alternative_experience(32)
  owner.pump(40, main=0, mask=0, heartbeat=False)
  bus = CanBus(fingerprint=gen_empty_fingerprint())
  address, data, src = fordcan.create_lka_msg(owner.packer, bus)
  assert owner.safety.safety_tx_hook(libsafety_py.make_CANPacket(address, src, data))
  acquire(owner)
  for tick in range(40):
    owner.pump(5, main=0, mask=0, heartbeat=False)
    inactive(owner)
    if tick % 2 == 1:
      assert ui(owner, 0x18A)
    assert owner.safety.safety_fwd_hook(2, 0x18A) == -1
    assert owner.safety.safety_fwd_hook(2, 0x3D8) == -1
    assert owner.safety.aol_get_permission_mask() == 0


def active_then_off(owner, full=False):
  owner.arm()
  bus = CanBus(fingerprint=gen_empty_fingerprint())
  address, data, src = fordcan.create_lka_msg(owner.packer, bus)
  assert owner.safety.safety_tx_hook(libsafety_py.make_CANPacket(address, src, data))
  address, data, src = fordcan.create_lat_ctl2_msg(owner.packer, bus, 1, 0., 0., 0., 0., 0)
  assert owner.safety.safety_tx_hook(libsafety_py.make_CANPacket(address, src, data))
  assert owner.safety.aol_get_permission_mask() == 1
  if full:
    owner.pump(10, main=5, mask=3, heartbeat=True)
    assert owner.safety.aol_get_permission_mask() == 3
  owner.safety.aol_set_host_request(0)
  owner.safety.set_aol_test_heartbeat(False)
  assert owner.safety.aol_get_permission_mask() == 0


@pytest.mark.parametrize('full', [False, True])
def test_off_display_precedes_next_inactive_actuator_without_stock_warning_gap(owner, full):
  active_then_off(owner, full=full)
  assert owner.safety.safety_fwd_hook(2, 0x18A) == 0
  assert ui(owner, 0x18A)
  assert owner.safety.safety_fwd_hook(2, 0x18A) == -1
  assert owner.safety.safety_fwd_hook(2, 0x3D8) == 0
  assert ui(owner, 0x3D8)
  assert owner.safety.safety_fwd_hook(2, 0x3D8) == -1
  assert owner.safety.safety_fwd_hook(2, 0x3D6) == -1  # the host is still sending the actuator
  assert owner.safety.aol_get_permission_mask() == 0
  owner.pump(4, main=3, mask=0, heartbeat=False)
  inactive(owner)
  assert owner.safety.safety_fwd_hook(2, 0x18A) == -1


def test_off_handover_ui_and_host_repeats_do_not_renew_the_original_command_lease(owner):
  active_then_off(owner)
  assert ui(owner, 0x18A)
  for _ in range(10):
    owner.pump(3, main=3, mask=0, heartbeat=False)
    assert ui(owner, 0x18A)
  owner.pump(1, main=3, mask=0, heartbeat=False)
  assert not ui(owner, 0x18A)
  assert owner.safety.safety_fwd_hook(2, 0x18A) == 0
  assert owner.safety.aol_get_permission_mask() == 0


@pytest.mark.parametrize('loss', ['eps', 'gear', 'lka', 'acc-fault', 'rx'])
def test_off_handover_returns_stock_on_real_faults(owner, loss):
  active_then_off(owner)
  assert ui(owner, 0x18A)
  if loss == 'eps':
    owner.rx('EPAS_INFO', {'EPAS_Failure': 1})
  elif loss == 'gear':
    owner.pump(10, main=3, mask=0, gear=1, heartbeat=False)
  elif loss == 'lka':
    owner.rx('Lane_Assist_Data3_FD1', {'LatCtlSte_D_Stat': 0})
  elif loss == 'acc-fault':
    owner.rx('EngBrakeData', {'CcStat_D_Actl': 1, 'BpedDrvAppl_D_Actl': 1})
  else:
    address, data, bus = owner.packer.make_can_msg('BrakeSysFeatures', 0, {'Veh_V_ActlBrk': 36., 'VehVActlBrk_D_Qf': 3})
    bad = bytearray(data)
    bad[0] ^= 1
    assert not owner.safety.safety_rx_hook(libsafety_py.make_CANPacket(address, bus, bad))
  assert not ui(owner, 0x18A)
  assert owner.safety.safety_fwd_hook(2, 0x18A) == 0
  assert owner.safety.aol_get_permission_mask() == 0
