import pytest

from opendbc.safety.tests import test_hyundai_gv60_hda1_long_aol as fixtures

WORDS = (0x0C0C, 0x0C0E, 0x0C2C, 0x0C2E)


@pytest.fixture
def case():
  value = fixtures.TestGv60Hda1LongAol(methodName='runTest')
  value.setUp()
  try:
    yield value
  finally:
    value.tearDown()


def rx(case, word, counter, **values):
  name = 'CRUISE_BUTTONS_ALT' if word & 32 else 'CRUISE_BUTTONS'
  frame = case.packer.make_can_msg(name, 0, {'COUNTER': counter, **values})
  assert case.safety.safety_rx_hook(case.packet(frame))


def release_denied(case, word):
  if not case.release:
    return False
  case.healthy(word & ~4)
  case.gesture(word)
  assert not case.safety.safety_config_valid()
  assert case.safety.aol_get_permission_mask() == 0
  assert not case.safety.safety_tx_hook(case.packet(case.packer.make_can_msg('LFA', 0, {})))
  return True


@pytest.mark.parametrize('word', WORDS)
@pytest.mark.parametrize('button', ('LDA_BTN', 'ADAPTIVE_CRUISE_MAIN_BTN'))
def test_fresh_physical_gesture_and_cancel_preserve_only_lateral(case, word, button):
  case.mode(word)
  if release_denied(case, word):
    return
  case.healthy(word)
  assert case.safety.safety_config_valid()
  case.safety.set_aol_test_heartbeat(True)
  case.safety.aol_set_host_request(3)
  assert case.safety.aol_get_permission_mask() == 0
  for counter, pressed in ((7, 1), (8, 0)):
    case.safety.set_timer(1_000_000 + counter * 1000)
    rx(case, word, counter, **{button: pressed})
  assert case.safety.aol_get_permission_mask() == 1
  rx(case, word, 9, CRUISE_BUTTONS=4)
  assert not case.safety.get_controls_allowed()
  assert case.safety.aol_get_permission_mask() == 1
  for counter, braking in ((7, 1), (8, 0)):
    message = case.packer.make_can_msg('TCS', 0, {'COUNTER': counter, 'DriverBraking': braking})
    assert case.safety.safety_rx_hook(case.packet(message))
    assert case.safety.aol_get_permission_mask() == 1
  throttle = case.packer.make_can_msg('SCC_CONTROL', 0, {'ACCMode': 1, 'aReqRaw': .1, 'aReqValue': .1})
  assert not case.safety.safety_tx_hook(case.packet(throttle))
  case.safety.set_timer(1_500_001)
  assert case.safety.aol_get_permission_mask() == 0


@pytest.mark.parametrize('word', WORDS)
@pytest.mark.parametrize('withdrawal', ('cancel', 'brake', 'gas'))
def test_full_set_release_and_physical_withdrawal(case, word, withdrawal):
  case.mode(word)
  if release_denied(case, word):
    return
  case.healthy(word)
  assert case.safety.safety_config_valid()
  case.safety.set_aol_test_heartbeat(True)
  case.safety.aol_set_host_request(3)
  for tick, counter, button in ((0, 7, 0), (1, 8, 2), (2, 9, 0)):
    case.safety.set_timer(1_000_000 + tick * 20_000)
    rx(case, word, counter, CRUISE_BUTTONS=button)
  case.safety.aol_set_host_request(3)
  assert case.safety.get_controls_allowed()
  assert case.safety.aol_get_permission_mask() == 3
  case.safety.set_timer(1_060_000)
  if withdrawal == 'cancel':
    rx(case, word, 10, CRUISE_BUTTONS=4)
  elif withdrawal == 'brake':
    message = case.packer.make_can_msg('TCS', 0, {'COUNTER': 7, 'DriverBraking': 1})
    assert case.safety.safety_rx_hook(case.packet(message))
  else:
    name = 'ACCELERATOR_ALT' if word & 2 else 'ACCELERATOR_BRAKE_ALT'
    message = case.packer.make_can_msg(name, 0, {'COUNTER': 7, 'ACCELERATOR_PEDAL': 20} if word & 2 else
                                       {'COUNTER': 7, 'ACCELERATOR_PEDAL_PRESSED': 1})
    assert case.safety.safety_rx_hook(case.packet(message))
  assert case.safety.aol_get_permission_mask() & 1
  if withdrawal != 'gas':
    assert not case.safety.get_controls_allowed()
    assert case.safety.aol_get_permission_mask() == 1
    throttle = case.packer.make_can_msg('SCC_CONTROL', 0, {'ACCMode': 1, 'aReqRaw': .1, 'aReqValue': .1})
    assert not case.safety.safety_tx_hook(case.packet(throttle))


@pytest.mark.parametrize('word', WORDS)
@pytest.mark.parametrize('experience', (0, 1, 33))
def test_wrong_experience_and_release_cannot_acquire_profile(case, word, experience):
  case.mode(word, experience)
  case.healthy(word & ~4)
  case.gesture(word)
  assert not case.safety.safety_config_valid()
  case.safety.set_aol_test_heartbeat(True)
  case.safety.aol_set_host_request(3)
  assert case.safety.aol_get_permission_mask() == 0
  assert not case.safety.safety_tx_hook(case.packet(case.packer.make_can_msg('LFA', 0, {})))


@pytest.mark.parametrize('word', WORDS)
@pytest.mark.parametrize('source', ('fuel', 'TCS', 'WHEEL_SPEEDS', 'MDPS', 'buttons'))
@pytest.mark.parametrize('fault', ('omitted', 'wrong_bus'))
def test_every_physical_source_is_required(case, word, source, fault):
  case.mode(word)
  if release_denied(case, word):
    return
  fuel = 'ACCELERATOR_ALT' if word & 2 else 'ACCELERATOR_BRAKE_ALT'
  button = 'CRUISE_BUTTONS_ALT' if word & 32 else 'CRUISE_BUTTONS'
  source = {'fuel': fuel, 'buttons': button}.get(source, source)
  case.healthy(word, **{'omit' if fault == 'omitted' else 'wrong_bus': source})
  if source != button:
    case.gesture(word)
  case.safety.set_aol_test_heartbeat(True)
  case.safety.aol_set_host_request(3)
  assert case.safety.aol_get_permission_mask() == 0


@pytest.mark.parametrize('word', WORDS)
@pytest.mark.parametrize('fault', ('relay', 'heartbeat', 'expiry'))
def test_real_faults_still_withdraw_native_permission(case, word, fault):
  case.mode(word)
  if release_denied(case, word):
    return
  case.healthy(word)
  case.gesture(word)
  assert case.safety.aol_get_permission_mask() == 1
  if fault == 'relay':
    case.safety.set_relay_malfunction(True)
  elif fault == 'heartbeat':
    case.safety.set_aol_test_heartbeat(False)
  else:
    case.safety.set_timer(1_500_001)
  assert case.safety.aol_get_permission_mask() == 0


@pytest.mark.parametrize('word', WORDS)
def test_granted_steering_still_enforces_torque_limits(case, word):
  case.mode(word)
  if release_denied(case, word):
    return
  case.healthy(word)
  case.gesture(word)
  assert case.safety.aol_get_permission_mask() == 1
  frame = case.packer.make_can_msg('LFA', 0, {'StrTqReqVal': 1, 'ActToiSta': 1})
  assert case.safety.safety_tx_hook(case.packet(frame))
  excessive = case.packer.make_can_msg('LFA', 0, {'StrTqReqVal': 410, 'ActToiSta': 1})
  assert not case.safety.safety_tx_hook(case.packet(excessive))
