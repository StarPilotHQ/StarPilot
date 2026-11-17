import pytest

from opendbc.car.hyundai.hyundaicanfd import hkg_can_fd_checksum
from opendbc.safety.tests import common
from opendbc.safety.tests import test_hyundai_blended_alpha as blended


@pytest.fixture(params=[0x2010, 0x2014])
def camera_owner(request):
  helper = blended.TestHyundaiBlendedAlphaAol()
  helper.setUp()
  try:
    helper.reset(request.param)
    helper.bus = 1
    if request.param == 0x2014 and helper.release:
      assert helper.safety.aol_get_permission_mask() == 0
      pytest.skip('Alpha mixed longitudinal is DEBUG-only')

    def physical(lda=False):
      helper.safety.set_timer(helper.now)
      if request.param == 0x2010:
        for message, values in (('SCC11', {}), ('SCC12', {'COUNTER': helper.counter % 16, 'MainMode_ACC': 1})):
          assert helper.safety.safety_rx_hook(helper.packer.make_can_msg_safety(message, 1, values))
      helper.feed(lda=lda)

    for _ in range(8):
      physical()
    physical(lda=True)
    assert helper.request(1) == 1
    assert not helper.safety.get_controls_allowed()
    yield helper, physical
  finally:
    helper.tearDown()


def test_previous_camera_echo_is_rejected_without_losing_lateral_or_renewing_ownership(camera_owner):
  helper, physical = camera_owner
  safety = helper.safety
  initial_time = helper.now - 10_000
  copy = helper.packer.make_can_msg_safety('CAM_0x2a4', 0, {'COUNTER': (helper.counter - 1) % 256})

  def primary(counter):
    return helper.packer.make_can_msg_safety('LKAS', 0, {
      'COUNTER': counter, 'TORQUE_REQUEST': 0, 'STEER_REQ': 1,
    })

  assert safety.safety_tx_hook(primary(0))
  assert safety.safety_tx_hook(copy)
  assert safety.safety_fwd_hook(2, 0x2A4) == -1
  helper.now = initial_time + 250_000
  physical(lda=True)
  assert helper.request(1) == 1
  assert safety.safety_tx_hook(primary(1))
  assert not safety.safety_tx_hook(copy)
  assert safety.aol_get_permission_mask() == 1
  assert not safety.get_controls_allowed()
  assert safety.safety_fwd_hook(2, 0x2A4) == -1
  safety.set_timer(initial_time + 300_001)
  assert safety.aol_get_permission_mask() == 1
  assert safety.safety_fwd_hook(2, 0x2A4) == 0


@pytest.mark.parametrize('fault', ['crc', 'payload', 'counter', 'unsuppressed', 'older_snapshot'])
def test_unproven_camera_copy_still_clears_authority(camera_owner, fault):
  helper, physical = camera_owner
  safety = helper.safety
  copy = helper.packer.make_can_msg_safety('CAM_0x2a4', 0, {'COUNTER': (helper.counter - 1) % 256})
  physical(lda=True)
  assert helper.request(1) == 1
  data = bytearray(copy[0].data[0:24])
  if fault == 'crc':
    data[0] ^= 1
  elif fault == 'older_snapshot':
    physical(lda=True)
  else:
    offset = {'payload': 3, 'counter': 2, 'unsuppressed': 7}[fault]
    data[offset] ^= 1 if fault != 'counter' else 0x40
    data[:2] = hkg_can_fd_checksum(0x2A4, None, data).to_bytes(2, 'little')
  assert not safety.safety_tx_hook(common.make_msg(0, 0x2A4, dat=bytes(data)))
  assert safety.aol_get_permission_mask() == 0
  assert not safety.get_controls_allowed()
