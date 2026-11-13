"""Physical Hybrid SET uses a checksum phase independent of the standard counter."""
import pytest

from opendbc.car.gm.hybrid_cc import HybridButtons, physical_semantic, standard_set_bytes
from opendbc.safety.tests import test_gm_hybrid_cc as native_fixture

SET_WORDS = (0x30DF, 0x35CE, 0x3ABD, 0x3FAC)


def physical_set(word, prefix=0x41):
  return bytes((0, 0, 0, prefix, 0, word >> 8, word & 255))


@pytest.mark.parametrize('prefix', [1, 0x41])
@pytest.mark.parametrize('word', SET_WORDS)
def test_physical_set_checksum_phases_decode_as_one_press(word, prefix):
  assert physical_semantic(physical_set(word, prefix), bus=0) == 'set'
  for bus in (2, 128, 130):
    assert physical_semantic(physical_set(word, prefix), bus=bus) is None


@pytest.mark.parametrize('echo_first', [False, True])
def test_forwarded_echoes_cannot_consume_physical_button_observation(echo_first):
  buttons = HybridButtons()
  edges = []
  words = (0x15EE, *SET_WORDS, 0x1FCC)
  for tick, word in enumerate(words):
    packet = physical_set(word)
    frames = [(0x1E1, packet, bus) for bus in ((130, 0) if echo_first else (0, 130))]
    buttons.observe_packets([(1_000_000_000 + tick * 30_000_000, frames)])
    edges.extend(buttons.update(True)[2])
  assert edges == [(1, 3), (3, 1)]


@pytest.mark.parametrize('counter', range(4))
def test_existing_standard_set_and_unknown_packet_guards(counter):
  raw = standard_set_bytes(counter)
  assert physical_semantic(raw, bus=0) == 'set'
  for i in range(7):
    damaged = bytearray(raw)
    damaged[i] ^= 0x80
    assert physical_semantic(bytes(damaged), bus=0) is None
  # Custom checksum phase is valid only with its observed zero counter.
  assert physical_semantic(bytes.fromhex('000000410130df'), bus=0) is None


@pytest.mark.parametrize('word', [0xE800, 0xE801, 0xE802, 0xE803])
@pytest.mark.parametrize('prefix', [1, 0x41])
def test_native_physical_set_arms_only_on_release(word, prefix):
  case = native_fixture.TestGmHybridCc(methodName='runTest')
  case.setUp()
  try:
    for set_word in SET_WORDS:
      case.reset(word)
      case.warm()
      case.slot(0x15EE)
      case.rx(0x1E1, physical_set(set_word, prefix))
      assert not case.safety.get_controls_allowed()
      case.slot(0x1FCC)
      assert case.safety.get_controls_allowed()
  finally:
    case.tearDown()


@pytest.mark.parametrize('guard', ['brake', 'regen', 'main', 'gear', 'sensor'])
def test_native_physical_set_cannot_bypass_physical_guards(guard):
  case = native_fixture.TestGmHybridCc(methodName='runTest')
  case.setUp()
  try:
    case.reset(0xE802)
    kwargs = {'brake': {'brake': True}, 'gas': {'gas': True}, 'regen': {'regen': True},
              'main': {'main': False}, 'gear': {'gear': 2}, 'sensor': {'missing': 0x201}}[guard]
    case.warm(**kwargs)
    case.rx(0x1E1, physical_set(0x35CE))
    case.slot(0x1FCC)
    assert not case.safety.get_controls_allowed()
  finally:
    case.tearDown()


@pytest.mark.parametrize('alternative', [0, 32])
@pytest.mark.parametrize('sensor_override', [False, True])
def test_pedal_set_during_accelerator_override_retains_intent_without_actuation(alternative, sensor_override):
  case = native_fixture.TestGmHybridCc(methodName='runTest')
  case.setUp()
  try:
    case.reset(0xE802, alternative)
    case.warm()
    kwargs = {'tracks': (1000, 500)} if sensor_override else {'gas': True}
    case.feed(**kwargs)
    case.rx(0x1E1, standard_set_bytes(0))
    assert not case.safety.get_controls_allowed()
    case.slot(0x1FCC)
    assert case.safety.get_controls_allowed()
    assert not case.pedal()
    if alternative:
      assert case.safety.aol_get_permission_mask() == 1
    case.advance(20_000)
    case.feed(**kwargs)
    assert case.safety.get_controls_allowed()
    assert not case.pedal()
    case.advance(20_000)
    case.feed()
    assert case.pedal()
    case.slot(0x60AF)
    case.advance(20_000)
    case.feed()
    assert not case.safety.get_controls_allowed()
    assert not case.pedal(counter=1)
  finally:
    case.tearDown()


def test_actual_pedal_carstate_emits_set_release_during_override():
  from opendbc.can import CANPacker
  from opendbc.car import Bus
  from opendbc.car.gm.gmcan import pedal_crc
  from opendbc.car.gm.interface import CarInterface
  from opendbc.car.gm.tests.test_cc_gateway_stock import pt_frames
  from opendbc.car.gm.tests.test_ordinary_cc import malibu_hybrid_params
  from opendbc.car.gm.values import DBC
  cp = malibu_hybrid_params(pedal=True, removed=True, alternate=True)
  ci = CarInterface(cp)
  packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
  for tick in range(45):
    now = 1_000_000_000 + tick * 10_000_000
    gas = 30 <= tick < 42
    frames = [f for f in pt_frames(packer, gas=gas) if f[0] != 0x1E1]
    button = physical_set(SET_WORDS[tick % 4]) if 34 <= tick < 38 else physical_set(0x15EE if tick % 2 else 0x1FCC)
    sensor = bytearray((1000 if gas else 604).to_bytes(2, 'big') +
                       (500 if gas else 304).to_bytes(2, 'big') + bytes((tick % 16, 0)))
    sensor[-1] = pedal_crc(sensor)
    frames += [packer.make_can_msg('EBCMRegenPaddle', 0, {}),
               packer.make_can_msg('EBCMBrakePedalPosition', 0, {}), (0x201, bytes(sensor), 0),
               (0x1E1, button, 130), (0x1E1, button, 0)]
    state = ci.update([(now, frames)])
    if tick == 38:
      assert state.gasPressed
      assert state.buttonEnable
      assert any(str(b.type) == 'decelCruise' and not b.pressed for b in state.buttonEvents)
    elif tick > 38:
      assert not state.buttonEnable
