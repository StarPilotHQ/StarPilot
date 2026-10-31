"""CAN FD display continuity never grants steering or survives loss of native authority."""
import pytest

from opendbc.car import gen_empty_fingerprint
from opendbc.car.ford import fordcan
from opendbc.car.ford.fordcan import CanBus
from opendbc.safety.tests.libsafety import libsafety_py
from opendbc.safety.tests import test_ford_aol


@pytest.fixture(params=(2, 3, 258, 259))
def owner(request):
  native = test_ford_aol.TestFordAolDriverIntent()
  native.setUp()
  native.reset(request.param)
  # Standard matured relay setup, with the same configured AE before and after.
  native.safety.init_tests()
  native.safety.set_alternative_experience(32)
  native.safety.set_aol_test_heartbeat(True)
  native.arm()
  yield native
  native.tearDown()  # AE 32 must not reach the next test's profile


def command(owner, *, active, curvature=0., offset_byte=False):
  frame = fordcan.create_lat_ctl2_msg(owner.packer, CanBus(fingerprint=gen_empty_fingerprint()),
                                     int(active), 0., 0., curvature, 0., 0)
  data = bytearray(frame[1])
  if offset_byte:
    data[5] ^= 1
  return libsafety_py.make_CANPacket(frame[0], frame[2], data)


def announce(owner):
  frame = fordcan.create_lka_msg(owner.packer, CanBus(fingerprint=gen_empty_fingerprint()))
  assert owner.safety.safety_tx_hook(libsafety_py.make_CANPacket(frame[0], frame[2], frame[1]))


def acquire(owner):
  announce(owner)
  assert owner.safety.safety_tx_hook(command(owner, active=True))
  assert owner.safety.safety_fwd_hook(2, 0x3D6) == -1
  assert owner.safety.safety_fwd_hook(2, 0x18A) == -1
  assert owner.safety.aol_get_permission_mask() == 1


def stock_display(owner, *, relay=False):
  for address in (0x18A, 0x3D8):
    assert owner.safety.safety_fwd_hook(2, address) == (-1 if relay else 0)
    active = bytearray(8)
    if address == 0x18A:
      active[5] = 7 << 2  # Hands-free/active status, never neutral Off.
    else:
      active[6] = 6 << 3  # Both active lane lines.
    assert not owner.safety.safety_tx_hook(libsafety_py.make_CANPacket(address, 0, active))


@pytest.mark.parametrize('first', ('none', 'inactive', 'rejected'))
def test_active_display_needs_first_accepted_active_command(owner, first):
  announce(owner)
  if first == 'inactive':
    assert owner.safety.safety_tx_hook(command(owner, active=False))
  elif first == 'rejected':
    assert not owner.safety.safety_tx_hook(command(owner, active=True, offset_byte=True))
  stock_display(owner)


def test_display_survives_fresh_inactive_packets_without_steering_ownership(owner):
  acquire(owner)
  for _ in range(30):
    owner.pump(5, mask=1)
    assert owner.safety.safety_tx_hook(command(owner, active=False))
    assert owner.safety.safety_fwd_hook(2, 0x3D6) == -1  # one 0x3D6 sender: the accepted inactive host frame
    for address in (0x18A, 0x3D8):
      assert owner.safety.safety_fwd_hook(2, address) == -1
      assert owner.safety.safety_tx_hook(libsafety_py.make_CANPacket(address, 0, bytes(8)))
    assert owner.safety.safety_fwd_hook(2, 0x321) == 0
    assert owner.safety.aol_get_permission_mask() == 1
    assert not owner.safety.get_controls_allowed()


@pytest.mark.parametrize('loss', ('rejected', 'expired', 'host_lat', 'heartbeat', 'eps', 'main', 'cancel', 'rx', 'relay', 'eps_temp', 'lka', 'gear'))
def test_display_drops_on_actual_authority_loss_and_does_not_reacquire_inactive(owner, loss):
  acquire(owner)
  assert owner.safety.safety_tx_hook(command(owner, active=False))
  if loss == 'rejected':
    assert not owner.safety.safety_tx_hook(command(owner, active=True, offset_byte=True))
  elif loss == 'expired':
    owner.pump(31, mask=1)
  elif loss == 'host_lat':
    owner.safety.aol_set_host_request(0)
  elif loss == 'heartbeat':
    owner.pump(2, mask=1, heartbeat=False)
  elif loss == 'eps':
    owner.rx('EPAS_INFO', {'SteeringColumnTorque': 0, 'EPAS_Failure': 2})
  elif loss == 'eps_temp':
    owner.rx('EPAS_INFO', {'SteeringColumnTorque': 0, 'EPAS_Failure': 1})
  elif loss == 'lka':
    owner.rx('Lane_Assist_Data3_FD1', {'LatCtlSte_D_Stat': 0, 'LaActAvail_D_Actl': 0, 'LaActDeny_B_Actl': 1})
  elif loss == 'gear':
    owner.pump(10, mask=1, gear=1)
  elif loss == 'main':
    owner.pump(10, main=0, mask=1)
  elif loss == 'cancel':
    owner.rx('Steering_Data_FD1', {'CcAslButtnCnclPress': 1})
  elif loss == 'rx':
    address, data, bus = owner.packer.make_can_msg('BrakeSysFeatures', 0, {'Veh_V_ActlBrk': 36., 'VehVActlBrk_D_Qf': 3})
    bad = bytearray(data)
    bad[0] ^= 1
    assert not owner.safety.safety_rx_hook(libsafety_py.make_CANPacket(address, bus, bad))
  else:
    assert owner.safety.safety_rx_hook(command(owner, active=False))
    assert owner.safety.get_relay_malfunction()
  stock_display(owner, relay=loss == 'relay')
  owner.pump(10, mask=1)
  if loss in ('eps_temp', 'lka', 'gear'):
    assert owner.safety.aol_get_permission_mask() == 1
  assert owner.safety.safety_tx_hook(command(owner, active=False)) == (loss != 'relay')
  stock_display(owner, relay=loss == 'relay')


@pytest.mark.parametrize('word', (8,))
def test_other_ford_owners_keep_original_inactive_forwarding(word):
  native = test_ford_aol.TestFordAolDriverIntent()
  native.setUp()
  native.reset(word)
  native.arm()
  bus = CanBus(fingerprint=gen_empty_fingerprint())
  announce(native)
  for active in (True, False):
    if word == 8:
      address, data, src = fordcan.create_lat_ctl_msg(native.packer, bus, active, 0., 0., 0., 0.)
      packet = libsafety_py.make_CANPacket(address, src, data)
    else:
      packet = command(native, active=active)
    assert native.safety.safety_tx_hook(packet)
    for address in (0x18A, 0x3D8):
      assert native.safety.safety_fwd_hook(2, address) == (-1 if active else 0)


@pytest.mark.parametrize('veto', ('eps', 'lka', 'gear'))
def test_transient_physical_veto_in_same_rx_batch_revokes_display_only(owner, veto):
  acquire(owner)
  assert owner.safety.safety_tx_hook(command(owner, active=False))
  if veto == 'eps':
    frames = [('EPAS_INFO', {'EPAS_Failure': value}) for value in (1, 0)]
  elif veto == 'lka':
    frames = [('Lane_Assist_Data3_FD1', {'LatCtlSte_D_Stat': value, 'LaActAvail_D_Actl': 3, 'LaActDeny_B_Actl': 0})
              for value in (0, 1)]
  elif owner.word & 256:
    frames = [('BCM_Lamp_Stat_FD1', {'RvrseLghtOn_B_Stat': value}) for value in (1, 0)]
  else:
    frames = [('PowertrainData_10', {'TrnRng_D_Rq': value}) for value in (1, 3)]
  for name, values in frames:
    owner.rx(name, values)
  assert owner.safety.aol_get_permission_mask() == 1
  assert owner.safety.safety_tx_hook(command(owner, active=False))
  stock_display(owner)
  # Current retained AOL permission can reacquire only through accepted action.
  assert owner.safety.safety_tx_hook(command(owner, active=True))
  assert owner.safety.safety_fwd_hook(2, 0x18A) == -1
