"""OEM receive semantics and driver pedal identity from the Malibu route."""

import pytest
from opendbc.car.gm.hybrid_cc import HybridButtons, physical_semantic
from opendbc.safety.tests import test_gm_hybrid_cc as native_fixture

WORDS = {
  1: (0x10FF, 0x15EE, 0x1ADD, 0x1FCC),
  2: (0x20EF, 0x25DE, 0x2ACD, 0x2FBC),
  3: (0x30DF, 0x35CE, 0x3ABD, 0x3FAC),
  5: (0x50BF, 0x55AE, 0x5A9D, 0x5F8C),
  6: (0x60AF, 0x659E, 0x6A8D, 0x6F7C),
}
NAMES = {1: 'neutral', 2: 'resume', 3: 'set', 5: 'main', 6: 'cancel'}


def raw(word, prefix=0x41):
  return bytes((0, 0, 0, prefix, 0, word >> 8, word & 255))


@pytest.mark.parametrize('button', WORDS)
@pytest.mark.parametrize('prefix', (1, 0x41))
def test_all_recorded_oem_phases(button, prefix):
  for word in WORDS[button]:
    assert physical_semantic(raw(word, prefix), bus=0) == NAMES[button]
    for bus in (2, 128, 130):
      assert physical_semantic(raw(word, prefix), bus=bus) is None


def test_held_resume_is_one_press_not_main():
  h = HybridButtons()
  edges = []
  for tick, word in enumerate((0x15EE,) + WORDS[2] * 20 + (0x1FCC,)):
    h.observe_packets([(1_000_000_000 + tick * 30_000_000, [(0x1E1, raw(word), 0)])])
    edges += h.update(True)[2]
  assert edges == [(1, 2), (2, 1)]


@pytest.mark.parametrize('profile', (0xE802, 0xE803))
@pytest.mark.parametrize('alternative', (0, 32))
def test_native_output_is_not_driver_gas(profile, alternative):
  f = native_fixture.TestGmHybridCc(methodName='runTest')
  f.setUp()
  try:
    f.reset(profile, alternative)
    f.warm()
    f.slot(0x30DF)
    f.slot(0x1FCC)
    assert f.safety.get_controls_allowed()
    f.advance(20_000)
    f.feed(gas=True, tracks=(604, 304))
    assert f.pedal(counter=0)
    f.advance(20_000)
    f.feed(gas=False, tracks=(1000, 500))
    assert not f.pedal(counter=1)
    f.advance(20_000)
    f.feed(gas=True, tracks=(604, 304))
    assert f.pedal(counter=2)
    f.advance(110_000)
    f.feed(gas=True, missing=0x201)
    assert not f.pedal(counter=3)
  finally:
    f.tearDown()


@pytest.mark.parametrize('profile', (0xE800, 0xE801, 0xE802, 0xE803))
def test_native_resume_hold_and_cancel_each_phase(profile):
  f = native_fixture.TestGmHybridCc(methodName='runTest')
  f.setUp()
  try:
    for cancel in WORDS[6]:
      f.reset(profile)
      f.warm()
      for word in WORDS[2] * 3:
        f.slot(word)
        assert f.safety.get_controls_allowed()
      f.slot(cancel)
      assert not f.safety.get_controls_allowed()
      f.slot(0x15EE)
      assert not f.safety.get_controls_allowed()
      f.slot(0x20EF)
      assert f.safety.get_controls_allowed()
      f.slot(0x55AE)
      assert not f.safety.get_controls_allowed()
  finally:
    f.tearDown()
