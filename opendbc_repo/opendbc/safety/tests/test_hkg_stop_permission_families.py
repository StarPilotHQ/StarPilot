"""Native stop permission boundaries across HKG policy families."""
import pytest

from opendbc.car.hyundai.canfd_angle_aol import ANGLE_AOL_WORDS
from opendbc.car.hyundai.canfd_stock_aol import STOCK_AOL_WORDS
from opendbc.safety.tests import test_hyundai_classic_long_aol as classiclong
from opendbc.safety.tests import test_hyundai_hda2_long_aol as canfdlong
from opendbc.safety.tests import test_hyundai_shared_canfd_stock_aol as canfdstock
from opendbc.safety.tests import test_hyundai_blended_alpha as blendedlong
from opendbc.safety.tests import test_hyundai_stock_angle_aol as stockangle


@pytest.mark.parametrize('word', [0x404, 0xC04])
def test_classic_long_keeps_native_lateral_through_a_stopped_long_withdrawal(word):
  case = classiclong.TestHyundaiClassicLongAol(methodName='runTest')
  case.setUp()
  try:
    case.reset(word)
    if case.release:
      case.safety.set_controls_allowed(True)
      assert case.request(3) == 0
      return
    for _ in range(6):
      case.feed(word)
    case.feed(word, button=2)
    case.feed(word)
    assert not case.safety.get_vehicle_moving()
    assert case.request(3) == 3
    case.safety.set_controls_allowed(False)
    assert case.request(1) == 1
    case.safety.set_aol_test_heartbeat(False)
    assert case.request(1) == 0
  finally:
    case.tearDown()


@pytest.mark.parametrize('word', [0x815, 0x895])
def test_canfd_torque_long_keeps_native_lateral_at_a_stop(word):
  case = canfdlong.TestHyundaiHda2LongAol(methodName='runTest')
  case.setUp()
  try:
    case.mode(word)
    if case.release:
      case.safety.set_aol_test_heartbeat(True)
      case.safety.set_controls_allowed(True)
      case.safety.aol_set_host_request(3)
      assert case.safety.aol_get_permission_mask() == 0
      return
    case.healthy()
    case.arm()
    assert not case.safety.get_vehicle_moving()
    case.safety.set_controls_allowed(True)
    case.safety.aol_set_host_request(3)
    assert case.safety.aol_get_permission_mask() == 3
    case.safety.set_controls_allowed(False)
    case.safety.aol_set_host_request(1)
    assert case.safety.aol_get_permission_mask() == 1
    case.safety.set_aol_test_heartbeat(False)
    assert case.safety.aol_get_permission_mask() == 0
  finally:
    case.tearDown()


def test_every_shared_canfd_stock_word_keeps_stopped_lateral_independent():
  case = canfdstock.TestHyundaiSharedCanfdStockAol(methodName='runTest')
  case.setUp()
  for word in sorted(STOCK_AOL_WORDS):
    case.mode(word)
    case.healthy(word)
    case.gesture(word)
    assert not case.safety.get_vehicle_moving()
    case.safety.set_controls_allowed(True)
    assert case.safety.aol_get_permission_mask() == 1
    case.safety.set_controls_allowed(False)
    assert case.safety.aol_get_permission_mask() == 1
    case.safety.set_aol_test_heartbeat(False)
    assert case.safety.aol_get_permission_mask() == 0


@pytest.mark.parametrize('word', [0x2004, 0x2014])
def test_blended_long_keeps_stopped_lateral_without_long_authority(word):
  case = blendedlong.TestHyundaiBlendedAlphaAol(methodName='runTest')
  case.setUp()
  try:
    case.reset(word)
    if case.release:
      case.safety.set_controls_allowed(True)
      assert case.request(3) == 0
      return
    for _ in range(8):
      case.feed()
    case.feed(lda=True)
    case.feed()
    assert not case.safety.get_vehicle_moving()
    assert case.request(1) == 1
    case.safety.set_controls_allowed(True)
    assert case.request(3) == 3
    case.safety.set_controls_allowed(False)
    assert case.request(1) == 1
    case.safety.set_aol_test_heartbeat(False)
    assert case.request(1) == 0
  finally:
    case.tearDown()


@pytest.mark.parametrize('word', sorted(ANGLE_AOL_WORDS))
def test_each_stock_angle_word_pauses_native_lateral_at_stop_and_recovers(word):
  case = stockangle.TestHyundaiStockAngleAol(methodName='runTest')
  case.setUp()
  case.mode(word)
  case.sources(word)
  case.gesture(word)
  assert case.safety.get_vehicle_moving()
  assert case.safety.aol_get_permission_mask() == 1
  case.safety.set_controls_allowed(True)
  pt = 1 if word & 16 else 0
  speeds = ('WHL_SpdFLVal', 'WHL_SpdFRVal', 'WHL_SpdRLVal', 'WHL_SpdRRVal')
  case.rx('WHEEL_SPEEDS', pt, dict(COUNTER=7, **dict.fromkeys(speeds, 0)))
  assert not case.safety.get_vehicle_moving()
  assert case.safety.aol_get_permission_mask() == 0
  case.rx('WHEEL_SPEEDS', pt, dict(COUNTER=8, **dict.fromkeys(speeds, 36)))
  assert case.safety.get_vehicle_moving()
  assert case.safety.aol_get_permission_mask() == 1
  case.safety.set_aol_test_heartbeat(False)
  assert case.safety.aol_get_permission_mask() == 0
