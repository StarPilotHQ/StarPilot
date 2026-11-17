from types import SimpleNamespace
import pytest
from opendbc.car import structs
from opendbc.car.gm.interface import CarInterface
from opendbc.car.gm.tests.test_bolt_pedal import params
from opendbc.car.gm.values import CAR
from openpilot.starpilot.aol.tests.test_gm_held_native import parse


def setup():
  cp = params(CAR.CHEVROLET_BOLT_CC_2018_2021, pedal=True, camera=True)
  cp.alternativeExperience = 32
  ci = CarInterface(cp)
  shell = SimpleNamespace(CP=cp, last_apply=1_360_000_000)
  for tick in range(37):
    now = 1_000_000_000 + tick * 10_000_000
    ci.update([(now, parse(shell, now, counter=tick % 4, packets_only=True))])
  control = structs.CarControl(latActive=True)
  control.actuators.torque = .1
  return ci, shell, control.as_reader()


def step(ci, shell, control, now, receipt=None):
  messages = []
  for tick in range(shell.last_apply + 10_000_000, now + 1, 10_000_000):
    packets = parse(shell, tick, counter=(tick // 10_000_000) % 4, packets_only=True)
    batches = [receipt] if receipt is not None and tick == now else []
    batches.append((tick, packets))
    cs = ci.update(batches)
    _, emitted = ci.apply(control, tick)
    messages.extend(msg for msg in emitted if msg[0] == 0x180 and msg[2] == 0)
  shell.last_apply = now
  return cs, messages


def first():
  ci, shell, control = setup()
  cs, messages = step(ci, shell, control, 1_400_000_000)
  assert cs.canValid and len(messages) == 1 and messages[0][1][0] & 8
  return ci, shell, control, messages[0]


def test_delayed_receipt_never_retransmits_an_unconfirmed_counter():
  ci, shell, control, sent = first()
  counter = (sent[1][0] >> 4) & 3
  for now in [1_420_000_000, 1_430_000_000, 1_440_000_000]:
    _, messages = step(ci, shell, control, now)
    assert not messages
  receipt = (1_441_000_000, [(sent[0], sent[1], 128)])
  _, messages = step(ci, shell, control, 1_460_000_000, receipt)
  assert len(messages) == 1 and (messages[0][1][0] >> 4) & 3 == (counter + 1) % 4


@pytest.mark.parametrize('kind', ['wrong_counter', 'wrong_bus', 'old', 'short'])
def test_unrelated_receipt_does_not_release_pending_command(kind):
  ci, shell, control, sent = first()
  raw = bytearray(sent[1])
  if kind == 'wrong_counter':
    raw[0] ^= 0x10
  receipt = (1_399_000_000 if kind == 'old' else 1_421_000_000,
             [(sent[0], bytes(raw[:3] if kind == 'short' else raw), 130 if kind == 'wrong_bus' else 128)])
  _, messages = step(ci, shell, control, 1_440_000_000, receipt)
  assert not messages and ci.CC.bolt_steering_pending is not None


def test_explicit_native_rejection_retries_the_same_counter():
  ci, shell, control, sent = first()
  receipt = (1_421_000_000, [(sent[0], sent[1], 192)])
  _, messages = step(ci, shell, control, 1_440_000_000, receipt)
  assert len(messages) == 1 and messages[0][1][0] & 0x30 == sent[1][0] & 0x30


def test_missing_receipt_is_reported_and_late_confirmation_recovers():
  ci, shell, control, sent = first()
  cs, messages = step(ci, shell, control, 1_510_000_000)
  assert cs.steerFaultTemporary and not messages
  receipt = (1_521_000_000, [(sent[0], sent[1], 128)])
  cs, messages = step(ci, shell, control, 1_540_000_000, receipt)
  assert not cs.steerFaultTemporary and len(messages) == 1
