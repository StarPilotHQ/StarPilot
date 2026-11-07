"""Outback state-zero startup requests retain the existing bounded safety contract."""

from types import SimpleNamespace

import pytest

from opendbc.can import CANPacker
from opendbc.car import gen_empty_fingerprint, structs
from opendbc.car.subaru.interface import CarInterface
from opendbc.car.subaru.startup import StartupSources, StopStartRequest, checksum, prepare
from opendbc.car.subaru.values import CAR
from openpilot.starpilot.car.subaru import stop_start_caller


class StartupHarness:
  def __init__(self, candidate=CAR.SUBARU_OUTBACK_2023, enabled=True):
    self.cp = prepare(CarInterface.get_params(candidate, gen_empty_fingerprint(), [], False, False, False), enabled)
    self.source = StartupSources(self.cp)
    self.owner = StopStartRequest()
    self.command = structs.CarControl()
    self.state = SimpleNamespace(startup_sources=self.source,
                                 out=structs.CarState(canValid=True, standstill=True, gearShifter=structs.CarState.GearShifter.park))
    self.packer = CANPacker('subaru_global_2017_generated')
    self.tick = 0
    self.base_ns = 200_000_000_000_000
    self.emissions = []

  def step(self, engine_state=0, *, manual=False, fault=False, configured=True, omit=(), corrupt=False):
    now = self.base_ns + self.tick * 50_000_000
    frames = []
    for name, address, fields in (
      ('Dashlights', 0x390, {'COUNTER': self.tick % 16, 'STOP_START': int(manual)}),
      ('Transmission', 0x48, {'COUNTER': self.tick % 16, 'Gear': 4}),
      ('Engine_Stop_Start', 0x174, {'Counter': self.tick % 16, 'STOP_START_STATE': engine_state}),
      ('ES_Distance', 0x221, {'COUNTER': self.tick % 16}),
    ):
      if address not in omit:
        address, data, bus = self.packer.make_can_msg(name, self.source.buses[address], fields)
        if corrupt and address == 0x390:
          data = bytes([data[0] ^ 1]) + data[1:]
        frames.append((address, data, bus))
    self.source.observe([(now, frames)], boot_ns=now, mono_ns=now)

    class Messages(dict):
      pass

    sm = Messages(pandaStates=[SimpleNamespace(faults=['interruptRateCan2'] if fault else [])])
    sm.seen = sm.valid = sm.alive = {'carControl': True}
    sm.logMonoTime = {'carControl': now}
    sm.recv_time = {'carControl': now / 1e9}
    card = SimpleNamespace(CI=SimpleNamespace(CC=SimpleNamespace(stop_start=self.owner, avh_startup=None)),
                           sm=sm, startup_panda_configured=lambda: configured)
    stop_start_caller.update(card, now)
    request = self.owner.update(self.command, self.state, now / 1e9, now_nanos=now)
    if request is not None:
      self.emissions.append((now, request))
    self.tick += 1
    return request


@pytest.mark.parametrize('candidate', [CAR.SUBARU_OUTBACK_2023, CAR.SUBARU_LEGACY_2025])
@pytest.mark.parametrize('engine_state', [0, 1, 2, 3])
def test_state_zero_only_on_selected_outback(candidate, engine_state):
  harness = StartupHarness(candidate)
  for _ in range(50):
    harness.step(engine_state)
  expected = engine_state in (1, 2) or (candidate == CAR.SUBARU_OUTBACK_2023 and engine_state == 0)
  assert bool(harness.emissions) == expected
  assert len(harness.emissions) <= 6
  if expected:
    stamps = [stamp for stamp, _ in harness.emissions]
    assert stamps[0] - harness.base_ns >= 1_000_000_000
    assert stamps[-1] - stamps[0] < 300_000_000
    assert all(b - a >= 50_000_000 for a, b in zip(stamps, stamps[1:], strict=False))
    for _, (address, data, bus) in harness.emissions:
      assert address == 0x390 and bus == 1 and data[6] & 0x40
      assert checksum(address, data) == data[0]
      assert data[2:6] == bytes(4) and data[7] == 0


def test_state_zero_requires_selected_profile():
  for candidate in (CAR.SUBARU_OUTBACK_2023, CAR.SUBARU_LEGACY_2025):
    harness = StartupHarness(candidate, enabled=False)
    for _ in range(50):
      harness.step()
    assert not harness.emissions


@pytest.mark.parametrize('reason', ['manual', 'off', 'panda_fault', 'configuration', 'corrupt', 'stale', 'gear', 'gas', 'moving',
                                    'enabled', 'latActive', 'longActive', 'steerFaultPermanent', 'steerFaultTemporary', 'accFaulted',
                                    'canTimeout', 'canInvalid'])
def test_state_zero_abort_during_pulse_never_retries(reason):
  harness = StartupHarness()
  for _ in range(21):
    harness.step()
  assert harness.emissions
  before = len(harness.emissions)
  options = {'manual': reason == 'manual', 'fault': reason == 'panda_fault', 'configured': reason != 'configuration',
             'corrupt': reason == 'corrupt', 'engine_state': 3 if reason == 'off' else 0}
  if reason == 'gear':
    harness.state.out.gearShifter = structs.CarState.GearShifter.reverse
  elif reason == 'gas':
    harness.state.out.gasPressed = True
  elif reason == 'moving':
    harness.state.out.standstill = False
  elif reason in ('steerFaultPermanent', 'steerFaultTemporary', 'accFaulted', 'canTimeout'):
    setattr(harness.state.out, reason, True)
  elif reason == 'canInvalid':
    harness.state.out.canValid = False
  elif reason in ('enabled', 'latActive', 'longActive'):
    setattr(harness.command, reason, True)
  elif reason == 'stale':
    harness.tick += 7
    options['omit'] = (0x174,)
  assert harness.step(**options) is None
  harness.command = structs.CarControl()
  harness.state.out = structs.CarState(canValid=True, standstill=True, gearShifter=structs.CarState.GearShifter.park)
  for _ in range(50):
    assert harness.step() is None
  assert harness.owner.done and len(harness.emissions) == before


@pytest.mark.parametrize('options', [{'fault': True}, {'configured': False}, {'manual': True}, {'engine_state': 3}])
def test_state_zero_cannot_start_with_fault_or_ineligible_sources(options):
  harness = StartupHarness()
  for _ in range(50):
    assert harness.step(**options) is None
  assert not harness.emissions
