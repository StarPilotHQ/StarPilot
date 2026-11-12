"""Pulling away from a stop: transport feedback must not disarm Mazda AOL either.

card.py disarms the AOL intent through two independent signals:
  - native_latch_rejected() -> native_rejection_ns (covered by the intent filter,
    test_standstill_release_grace.py), and
  - TransportPauseFeedback.observe() -> fault_active=True.
While stopped, the transport path retains intent through _angle_stop_intent_current()
(the Mazda temporary restriction). On the first moving frame that restriction ends,
but panda's latest sample (requestedLateral and not lateralAllowed) was taken while
still stopped. _configured_transport_native() treats that denial as unhealthy, so
observe() reported a hard failure and the intent disarmed even with the
native-rejection filter in place.

These tests run the real TransportPauseFeedback, native_latch_rejected and
MazdaStockCardIntent together, frame by frame, the way card.py chains them, with
panda modelled as re-granting lateral PANDA_CATCHUP_NS after the car moves.
"""
import importlib
import sys
import types

import pytest
from opendbc.car import structs
from opendbc.car.mazda import stock_aol
from opendbc.car.mazda.stock_aol import RESTRICTION_RELEASE_GRACE_NS
from openpilot.cereal import log
from openpilot.starpilot.aol.intent import AolSettings
from openpilot.starpilot.aol.runtime import SAFETY_MAX_AGE_NS
from openpilot.starpilot.aol.transport_pause import TransportPauseFeedback
from openpilot.starpilot.aol.vehicle import native_latch_rejected
from openpilot.starpilot.aol.wire import PandaSlot, SafetyState, encode_safety
from openpilot.starpilot.car.mazda import stock_intent
from openpilot.starpilot.car.mazda.stock_intent import MazdaStockCardIntent
from openpilot.starpilot.car.mazda.tests.test_standstill_release_grace import marked_gen2_cp

FRAME_NS = 10_000_000
WIRE_LAG_NS = 30_000_000
PANDA_CATCHUP_NS = 40_000_000
SESSION = 'mazda-axis'
PRODUCER = 'mazda-card'
SERIAL = 'mazda-panda'
EVENT = log.OnroadEvent.EventName


class _FakeParams:
  def __init__(self, *args, **kwargs):
    pass

  def get(self, *args, **kwargs):
    return None

  def get_bool(self, *args, **kwargs):
    return False


@pytest.fixture(scope='module', autouse=True)
def host_native_stubs():
  # transport_pause lazily imports selfdrived.events, which needs cereal.messaging
  # (annotations only) and, via calibrationd, Params. Host checkouts without the
  # compiled msgq IPC extension or params library cannot import them; stand in
  # for them here only (as the Mazda controller tests do for Params), and remove
  # the stubs and anything imported against them afterwards. On a device or CI
  # the real modules import and no stub is installed.
  before = set(sys.modules)
  stubs = {}
  try:
    importlib.import_module('openpilot.cereal.messaging')
  except ModuleNotFoundError:
    stubs['openpilot.cereal.messaging'] = types.ModuleType('openpilot.cereal.messaging')
    stubs['openpilot.cereal.messaging'].SubMaster = stubs['openpilot.cereal.messaging'].PubMaster = object
  try:
    importlib.import_module('openpilot.common.params')
  except OSError:
    stubs['openpilot.common.params'] = types.ModuleType('openpilot.common.params')
    stubs['openpilot.common.params'].Params = _FakeParams
  sys.modules.update(stubs)
  yield
  if stubs:
    for name in set(sys.modules) - before:
      del sys.modules[name]
    for name in stubs:
      sys.modules.pop(name, None)


def event(service, stamp, size=None):
  msg = log.Event.new_message(logMonoTime=stamp, valid=True)
  if size is not None:
    msg.init(service, size)
  elif service != 'aolSafetyWire':  # Data field: assigned, not initialised
    msg.init(service)
  return msg


class Peers:
  def __init__(self, messages):
    self.messages = {m.which(): m.as_reader() for m in messages}
    self.logMonoTime = {n: int(m.logMonoTime) for n, m in self.messages.items()}
    self.seen = dict.fromkeys(self.messages, True)
    self.valid = dict(self.seen)
    self.alive = dict(self.seen)
    self.updated = dict(self.seen)

  def __getitem__(self, name):
    return getattr(self.messages[name], name)


class Drive:
  def __init__(self, wire_lag_ns=WIRE_LAG_NS):
    self.wire_lag_ns = wire_lag_ns
    self.cp = marked_gen2_cp()
    self.feedback = TransportPauseFeedback(self.cp)
    self.intent = MazdaStockCardIntent(self.cp, AolSettings(True, 0., 0, 0, (0, 0, 0), (0, 0, 0)))
    self.now = 10_000_000_000
    self.sequence = 0
    self.moving_since = 0
    self.panda_revoked = False
    self.disarmed_by = ''

  def panda_allows(self, observed):
    return not self.panda_revoked and 0 < self.moving_since and observed >= self.moving_since + PANDA_CATCHUP_NS

  def sample(self, cs, *, desired, allowed, observed, pause_event=False, extra_event=None):
    cfg = self.cp.safetyConfigs[0]
    slot = PandaSlot(0, SERIAL, cfg.safetyModel.raw, cfg.safetyParam, 32, False)
    native = SafetyState(1, True, observed, observed + SAFETY_MAX_AGE_NS, cfg.safetyModel.raw, cfg.safetyParam,
                         allowed, False, desired, False, SERIAL, SESSION,
                         sourcePandaStatesMonoTime=observed, pandaInventory=(slot,))
    wire = event('aolSafetyWire', observed)
    wire.aolSafetyWire = encode_safety(native)
    self.sequence += 1
    axis = event('aolAxisState', self.now)
    a = axis.aolAxisState
    a.sessionId = a.faultSessionId = SESSION
    a.sequence, a.qualified, a.faultReason = self.sequence, True, 'none'
    a.observedMonoTime, a.sourceCarStateMonoTime, a.validUntilMonoTime = self.now, self.now, self.now + 30_000_000
    a.intentProducerSessionId, a.intentIntegrityEpoch = PRODUCER, 1
    a.faultEventMonoTime = self.now
    a.desiredLateral, a.lateralActive = desired, desired and allowed
    a.desiredLongitudinal = a.longitudinalActive = False
    a.nativeAcknowledged = True
    a.mode = 'lateralOnly' if desired else 'off'
    panda = event('pandaStates', observed, 1)
    panda.pandaStates[0].safetyModel, panda.pandaStates[0].safetyParam = cfg.safetyModel, cfg.safetyParam
    panda.pandaStates[0].alternativeExperience = 32
    names = [(EVENT.pcmDisable, False, False, True)]
    if pause_event:
      # selfdrived: audible-and-soft while moving, silent at standstill (as logged).
      names.append((EVENT.steerTempUnavailableSilent, False, False, False) if cs.standstill else
                   (EVENT.steerTempUnavailable, False, True, False))
    if extra_event is not None:
      names.append(extra_event)
    events = event('onroadEvents', self.now, len(names))
    for slot, (name, immediate, soft, user) in zip(events.onroadEvents, names, strict=True):
      slot.name, slot.immediateDisable, slot.softDisable, slot.userDisable = name, immediate, soft, user
    return Peers([wire, axis, panda, events]), native

  def tick(self, *, standstill=False, available=True, temp_fault=False, pause_event=False, extra_event=None):
    self.now += FRAME_NS
    if standstill:
      self.moving_since = 0
    elif not self.moving_since:
      self.moving_since = self.now
    cs = structs.CarState(canValid=True, gearShifter='drive', standstill=standstill, vEgo=0.0 if standstill else 0.05,
                          steerFaultTemporary=temp_fault)
    cs.cruiseState.available = available
    observed = self.now - self.wire_lag_ns
    latched = self.intent.allowed_latch
    # The runtime withdraws its lateral request during a temporary steering fault;
    # panda withholds lateral during a TI2 pause (and while stopped, as modelled above).
    sm, native = self.sample(cs, desired=latched and not temp_fault,
                             allowed=self.panda_allows(observed) and not temp_fault, observed=observed,
                             pause_event=pause_event, extra_event=extra_event)
    # As card.py: transport feedback raises fault_active; native denial stamps a rejection.
    transport_reset = self.feedback.observe(sm, self.cp, cs.as_reader(), self.now, latched=latched,
                                            intent_producer_session=PRODUCER)
    rejected = native_latch_rejected(self.cp, native, state=cs.as_reader())
    self.intent.update(cs.as_reader(), fault_active=True if transport_reset else None, now_ns=self.now,
                       native_rejection_ns=int(native.observedMonoTime) if rejected else 0)
    if latched and not self.intent.allowed_latch and not self.disarmed_by:
      self.disarmed_by = 'transport' if transport_reset else 'native_rejection' if rejected else 'other'
    return self.intent.allowed_latch

  def arm_and_cruise(self):
    for _ in range(20):
      self.tick(available=False)
    assert self.tick(), "MAIN-on did not arm"
    for frame in range(100):
      assert self.tick(), f"lost arming while cruising (frame {frame}, {self.disarmed_by})"
    assert self.feedback.lateral_only_baseline and self.feedback.lateral_inventory, "no transport baseline"


@pytest.mark.parametrize('wire_lag_ns', (WIRE_LAG_NS, SAFETY_MAX_AGE_NS - PANDA_CATCHUP_NS))
def test_stop_and_pull_away_keeps_aol_armed_through_transport_feedback(wire_lag_ns):
  drive = Drive(wire_lag_ns)
  drive.arm_and_cruise()
  for frame in range(1300):
    assert drive.tick(standstill=True), f"stopped frame {frame}: disarmed by {drive.disarmed_by}"
  for frame in range(300):
    assert drive.tick(), f"pull-away frame {frame}: disarmed by {drive.disarmed_by}"


def test_real_revocation_after_a_stop_still_disarms_through_transport_feedback():
  drive = Drive()
  drive.arm_and_cruise()
  for _ in range(200):
    drive.tick(standstill=True)
  drive.panda_revoked = True
  drive.tick(standstill=True)
  last_stopped = drive.now
  frames = 0
  while drive.tick():
    frames += 1
    assert frames < 100, "a persistent denial never disarmed"
  assert RESTRICTION_RELEASE_GRACE_NS < drive.now - last_stopped <= RESTRICTION_RELEASE_GRACE_NS + FRAME_NS
  assert drive.disarmed_by == 'transport'


def test_denial_without_a_preceding_restriction_disarms_at_once():
  drive = Drive()
  drive.arm_and_cruise()
  drive.panda_revoked = True
  assert not drive.tick()


@pytest.mark.parametrize('path', ('transport', 'intent'))
def test_each_retention_is_needed(monkeypatch, path):
  # Remove one of the two releases: the pull-away must disarm through the other path.
  if path == 'transport':
    # transport_pause imports the helper at call time; stock_intent bound its own.
    monkeypatch.setattr(stock_aol, 'within_restriction_grace', lambda restricted_ns, now_ns: False)
  else:
    monkeypatch.setattr(stock_intent, 'within_restriction_grace', lambda restricted_ns, now_ns: False)
  drive = Drive()
  drive.arm_and_cruise()
  for _ in range(200):
    drive.tick(standstill=True)
  assert not all(drive.tick() for _ in range(30))
  assert drive.disarmed_by == ('transport' if path == 'transport' else 'native_rejection')


def test_stale_inventory_is_not_retained():
  # The release keeps every other transport check: a different panda is a hard failure.
  drive = Drive()
  drive.arm_and_cruise()
  for _ in range(200):
    drive.tick(standstill=True)
  drive.feedback.mazda_inventory = ('other-panda', ('other-panda',))
  assert not drive.tick()
  assert drive.disarmed_by == 'transport'



# TI2 0x02 pauses that overlap a stop (route 00000036--7daad355da, 2026-11-03).
# A: the pause ends while still stopped. Its first post-pause frame is a healthy idle
#    frame (request withdrawn), which clears the active-lateral baseline; panda's normal
#    standstill denial on the next frame then had nothing to retain it (438-468 s).
# B: the pause starts on the frame the car stops. The standstill path excluded a temporary
#    steering fault and the pause event arrives a frame or two later (572, 2929, 3187 s).

def pause(drive, frames, *, standstill, event_delay=2):
  # A TI2 pause: the temporary fault at once, selfdrived's event event_delay frames later.
  for frame in range(frames):
    assert drive.tick(standstill=standstill, temp_fault=True, pause_event=frame >= event_delay), \
      f"pause frame {frame} (standstill={standstill}): disarmed by {drive.disarmed_by}"


def stopped(drive, frames, label):
  for frame in range(frames):
    assert drive.tick(standstill=True), f"{label} frame {frame}: disarmed by {drive.disarmed_by}"


def pull_away(drive, frames=300):
  for frame in range(frames):
    assert drive.tick(), f"pull-away frame {frame}: disarmed by {drive.disarmed_by}"


def test_pattern_a_pause_ends_while_still_stopped():
  drive = Drive()
  drive.arm_and_cruise()
  stopped(drive, 100, 'stopped before pause')
  pause(drive, 1000, standstill=True)
  stopped(drive, 2000, 'stopped after pause')
  pull_away(drive)


def test_pattern_b_pause_starts_as_the_car_stops():
  drive = Drive()
  drive.arm_and_cruise()
  pause(drive, 1000, standstill=True)
  stopped(drive, 500, 'stopped after pause')
  pull_away(drive)


def test_pause_that_continues_after_pulling_away():
  drive = Drive()
  drive.arm_and_cruise()
  stopped(drive, 100, 'stopped before pause')
  pause(drive, 200, standstill=True)
  pause(drive, 300, standstill=False, event_delay=0)
  pull_away(drive)


@pytest.mark.parametrize('pattern', ('a', 'b'))
def test_the_mazda_retention_is_what_keeps_a_and_b_armed(monkeypatch, pattern):
  monkeypatch.setattr(TransportPauseFeedback, '_mazda_restricted_lateral_current', lambda self, *a, **k: False)
  drive = Drive()
  drive.arm_and_cruise()
  ticks = [drive.tick(standstill=True) for _ in range(100 if pattern == 'a' else 0)]
  ticks += [drive.tick(standstill=True, temp_fault=True, pause_event=frame >= 2) for frame in range(1000)]
  ticks += [drive.tick(standstill=True) for _ in range(500)]
  assert not all(ticks) and drive.disarmed_by == 'transport'


@pytest.mark.parametrize('event_spec', [(EVENT.controlsMismatch, True, False, False),
                                        (EVENT.steerUnavailable, False, True, False)])
def test_disabling_event_during_a_stopped_pause_disarms(event_spec):
  drive = Drive()
  drive.arm_and_cruise()
  stopped(drive, 100, 'stopped before pause')
  pause(drive, 50, standstill=True)
  assert not drive.tick(standstill=True, temp_fault=True, pause_event=True, extra_event=event_spec)


@pytest.mark.parametrize('anchor', ('inventory', 'producer', 'epoch'))
def test_changed_session_anchor_after_a_stopped_pause_disarms(anchor):
  # After the pause ends while stopped, only the Mazda retention holds intent; it
  # must not survive a different panda, intent producer or integrity epoch.
  drive = Drive()
  drive.arm_and_cruise()
  stopped(drive, 100, 'stopped before pause')
  pause(drive, 300, standstill=True)
  stopped(drive, 50, 'stopped after pause')
  if anchor == 'inventory':
    drive.feedback.mazda_inventory = ('other-panda', ('other-panda',))
  elif anchor == 'producer':
    drive.feedback.mazda_producer = 'other-card'
  else:
    drive.feedback.mazda_epoch = 2
  assert not drive.tick(standstill=True)
  assert drive.disarmed_by == 'transport'


def test_persistent_denial_after_a_stopped_pause_still_disarms_after_pulling_away():
  drive = Drive()
  drive.arm_and_cruise()
  stopped(drive, 100, 'stopped before pause')
  pause(drive, 300, standstill=True)
  stopped(drive, 200, 'stopped after pause')
  drive.panda_revoked = True
  stopped(drive, 300, 'stopped, panda revoked')  # explained by the stop until the car moves
  last_stopped = drive.now
  frames = 0
  while drive.tick():
    frames += 1
    assert frames < 100, "a persistent denial never disarmed"
  assert RESTRICTION_RELEASE_GRACE_NS < drive.now - last_stopped <= RESTRICTION_RELEASE_GRACE_NS + FRAME_NS
