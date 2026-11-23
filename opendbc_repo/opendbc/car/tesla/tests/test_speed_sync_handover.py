"""Speed sync requests around the accelerator pedal, lost feedback and unanswered requests. Pure protocol: no device."""
import pytest
from opendbc.car.tesla.speed_sync import (ENGAGE_DELAY_NS, FAST_TICKS, FRESH_NS, HANDOVER_NS, MANUAL_COMPLETION_NS, MAX_RETRIES,
                                          TEMPLATE_SEND_NS, SpeedSynchronizer, neutral_wheel, plan_step, scroll_request)

NEUTRAL = bytes.fromhex('2955000000000040')


def clicks(msg):
  """The signed scroll count of a request."""
  ticks = msg[1][3] & 63
  return ticks - 64 if ticks >= 32 else ticks


class Rig:
  def __init__(self, *, engaged=True):
    self.sync = SpeedSynchronizer()
    self.now = 1_000_000_000
    self.counter = 0
    self.speed, self.target, self.units = 45, 50, 1
    self.sent = []
    self.echo = True   # the panda transmits each request and echoes it on bus 129
    self._echo = None
    if engaged:
      # Engaged without longitudinal authority for longer than the panda needs to hear about engagement.
      for _ in range(ENGAGE_DELAY_NS // 10_000_000 + 1):
        assert self.tick(long=False) is None

  def tick(self, dt=10_000_000, *, long=True, gas=False, safe=True, enabled=True,
           feedback=True, wheel=NEUTRAL, wheel_bus=1):
    self.now += dt
    frames = []
    if self._echo is not None:
      frames.append((0x3c2, self._echo, 129))
      self._echo = None
    if feedback:
      self.counter = (self.counter + 1) % 16
      bits = (self.counter << 8) | (2 << 12) | (round(self.speed * 2) << 15) | (self.units << 24)
      di = bytearray(bits.to_bytes(8, 'little'))
      di[0] = (0x88 + sum(di[1:])) & 255
      frames.append((0x286, bytes(di), 0))
    if wheel is not None:
      frames.append((0x3c2, wheel, wheel_bus))
    self.sync.observe([(self.now, frames)])
    msg = self.sync.update(self.now, target_kph=self.target, enabled=enabled,
                           safe=safe, longitudinal_active=long, gas_pressed=gas)
    if msg:
      self.sent.append((self.now, msg))
      if self.echo:
        self._echo = msg[1]
    return msg


@pytest.mark.parametrize('before', [False, True])
def test_pending_response_on_either_side_of_release(before):
  # A request sent under the pedal is answered before or after the release; nothing new is sent in the hand-over
  # window, and requests carry on once longitudinal control is back.
  r = Rig()
  assert r.tick(long=False, gas=True)
  if before:
    r.speed += 1
  assert r.tick(long=False) is None
  assert r.sync.blocked is None
  r.speed = 46
  for _ in range(15):
    msg = r.tick()
    if msg:
      break
  assert r.sync.blocked is None
  assert msg is not None and len(r.sent) == 2


def test_no_initial_authority_and_held_pedal_engage():
  r = Rig()
  for _ in range(20):
    assert r.tick(long=False) is None
  assert not r.sent and r.sync.handover_until == 0
  assert r.tick(long=False, gas=True) is not None


def test_no_new_command_during_handover_even_after_response():
  r = Rig()
  assert r.tick(long=False, gas=True)
  r.speed += 1
  for _ in range(8):
    assert r.tick(long=False) is None
  assert r.sync.pending is None and len(r.sent) == 1
  assert r.sync.status == 'waiting_for_handover'


def test_grace_expires_once_not_renewed_by_false_samples():
  r = Rig()
  r.target = r.speed
  r.tick(long=False, gas=True)
  r.target = 50
  r.tick(long=False)
  deadline = r.sync.handover_until
  assert deadline == r.now + HANDOVER_NS
  for _ in range(9):
    assert r.tick(long=False) is None
    assert r.sync.handover_until == deadline and r.sync.status == 'waiting_for_handover'
  assert r.tick(long=False) is None
  assert r.now == deadline and r.sync.handover_until == 0 and r.sync.status == 'inhibited'
  for _ in range(20):
    assert r.tick(long=False) is None, 'no authority, no request'
  assert r.sync.blocked is None and not r.sent


def test_release_with_a_request_open_sends_nothing_until_authority_returns():
  r = Rig()
  assert r.tick(long=False, gas=True)
  for _ in range(20):
    assert r.tick(long=False) is None
  r.speed += 1   # the Tesla answers after the release
  for _ in range(20):
    assert r.tick(long=False) is None
  assert r.sync.status == 'inhibited' and len(r.sent) == 1
  assert any(r.tick() for _ in range(5)), 'carries on with longitudinal control'


def test_hard_failure_and_disengagement_clear_edge_history():
  r = Rig()
  r.target = r.speed
  r.tick(gas=True)
  r.tick(safe=False)
  r.target = 50
  assert r.tick(long=False) is None and r.sync.handover_until == 0
  r.tick(gas=True, enabled=False)
  assert r.tick(long=False) is None and r.sync.handover_until == 0


def test_feedback_loss_pauses_until_fresh_feedback():
  r = Rig()
  r.target = r.speed
  for _ in range(30):
    assert r.tick(feedback=False) is None
  r.target = 50
  assert r.tick(feedback=False) is None
  assert r.sync.status == 'waiting_for_native_speed' and r.sync.blocked is None
  assert r.tick() is not None, 'carries on once the Tesla speed is fresh again'


def test_units_change_inflight_is_not_handover():
  r = Rig()
  r.tick(gas=True)
  r.units = 0
  assert r.tick(long=False) is None
  assert r.sync.blocked == 'units_changed_during_request'


def test_manual_input_during_a_request_wins():
  # The driver's own scroll is recorded and given time to land; the open request is not repeated.
  r = Rig()
  assert r.tick(gas=True)
  assert r.tick(long=False, wheel=scroll_request(NEUTRAL, -1)[1]) is None
  assert r.sync.blocked is None
  assert any(h['event'] == 'input_during_request' for h in r.sync.history)
  r.speed -= 1
  for _ in range(10):
    assert r.tick() is None
  assert r.sync.status == 'waiting_for_manual_processing', "waits for the comma to act on the driver's scroll"
  assert len(r.sent) == 1
  for _ in range(MANUAL_COMPLETION_NS // 10_000_000):
    r.tick()
  assert 'manual_action_unconfirmed' in r.sync.noticed and r.sync.blocked is None
  assert len(r.sent) > 1, "after the wait the Tesla is scrolled back to the comma's speed"


def test_no_response_is_retried_a_few_times_then_waits_for_the_tesla():
  r = Rig()
  r.tick()
  for _ in range(100):
    r.tick(dt=100_000_000)
  assert r.sync.blocked is None and 'no_native_response' in r.sync.noticed
  assert r.sync.status == 'waiting_for_native_change'
  sent = len(r.sent)
  assert sent == MAX_RETRIES + 1, 'the first request and its retries'
  for _ in range(20):
    assert r.tick() is None
  r.speed += 1   # the Tesla moves: requests carry on
  assert any(r.tick() for _ in range(20))


def test_request_needs_a_new_physical_neutral_frame():
  r = Rig()
  r.tick()
  r.speed += 1
  assert r.tick(wheel=None) is None, 'no neutral frame newer than the one the last request copied'
  assert r.tick(wheel=NEUTRAL, wheel_bus=129) is None, "a bus-129 echo is not the driver's wheel"
  assert r.sync.status == 'waiting_for_neutral_wheel'
  assert r.tick() is not None


def test_request_not_echoed_by_the_panda_is_not_transmitted():
  r = Rig()
  r.echo = False
  assert r.tick()
  for _ in range(9):
    assert r.tick() is None
  r.tick()
  assert any(h['event'] == 'not_transmitted' and h['reason'] == 'no_bus_return' for h in r.sync.history)
  assert r.sync.pending is None and r.sync.blocked is None


def test_template_age_is_tighter_than_receive_freshness():
  r = Rig()
  r.target = r.speed
  r.tick()
  r.target = 50
  assert r.tick(dt=TEMPLATE_SEND_NS + 1, wheel=None) is None
  assert r.sync.status == 'waiting_for_neutral_wheel'
  assert r.tick() is not None


@pytest.mark.parametrize('direction', [-1, 1])
def test_convergence_one_request_per_tesla_change(direction):
  r = Rig()
  r.target = r.speed + 10 * direction
  waiting = None
  for _ in range(400):
    msg = r.tick()
    if msg:
      assert waiting is None, 'never two requests open at once'
      waiting = r.speed
    elif waiting is not None and r.sync.pending is not None and r.now - r.sync.pending.sent_ns >= 30_000_000:
      r.speed += direction   # the Tesla answers about 30 ms later
      waiting = None
    if r.sync.status == 'matched':
      break
  assert r.speed == r.target and len(r.sent) == 10
  assert len(r.sync.history) <= 32


def test_same_rounded_target_never_sends():
  r = Rig()
  r.speed = 45.5
  r.target = 46
  for _ in range(10):
    assert r.tick() is None
  assert r.sync.status == 'matched'


def test_controller_clock_gap_pauses_one_frame():
  r = Rig()
  r.tick(gas=True)
  r.speed += 1
  assert r.tick(dt=FRESH_NS + 1, long=False) is None
  assert r.sync.status == 'controller_clock_gap' and r.sync.blocked is None and r.sync.handover_until == 0


def test_disengaging_clears_the_engagement():
  r = Rig()
  assert r.tick()
  assert r.tick(enabled=False) is None
  assert r.sync.status == 'inactive' and r.sync.pending is None and r.sync.target is None and not r.sync.active
  assert not r.sync.history and not r.sync.noticed


@pytest.mark.parametrize('native,target,fast,expected', [
  (50, 51, True, (1, 51)), (50, 49, True, (-1, 49)),
  (50, 55, True, (FAST_TICKS, 55)), (50, 45, True, (-FAST_TICKS, 45)),
  (52, 60, True, (FAST_TICKS, 55)), (52, 44, True, (-FAST_TICKS, 50)),
  (52, 54, True, (1, 53)), (53, 51, True, (-1, 52)),      # the next multiple of five would pass the target
  (54, 60, True, (1, 55)), (56, 50, True, (-1, 55)),      # one away from the multiple: a click is as quick
  (50, 60, False, (1, 51)), (50, 40, False, (-1, 49)),    # fast steps paused, or km/h
])
def test_next_step_toward_the_target(native, target, fast, expected):
  assert plan_step(native, target, fast) == expected


@pytest.mark.parametrize('ticks', [-FAST_TICKS, -1, 1, FAST_TICKS])
def test_a_request_is_the_neutral_frame_with_only_the_scroll_count_set(ticks):
  address, data, bus = scroll_request(NEUTRAL, ticks)
  assert (address, bus) == (0x3C2, 1) and len(data) == 8
  assert data[3] == (NEUTRAL[3] & 0xC0) | (ticks & 0x3F)
  assert data[:3] == NEUTRAL[:3] and data[4:] == NEUTRAL[4:]
  assert not neutral_wheel(data), 'a scroll is not a template for the next request'


@pytest.mark.parametrize('ticks', [0, 2, 3, 4, 6, -2, -6, 31, -32])
def test_only_one_click_or_one_fast_flick_is_ever_requested(ticks):
  assert scroll_request(NEUTRAL, ticks) is None


def test_no_request_is_built_from_a_frame_that_is_not_neutral():
  assert neutral_wheel(NEUTRAL)
  pressed = bytearray(NEUTRAL)
  pressed[1] = (pressed[1] & ~0x03) | 0x02  # right wheel tilted left
  for frame in (bytes(pressed), scroll_request(NEUTRAL, 1)[1], NEUTRAL[:7], bytes(8)):
    assert not neutral_wheel(frame)
    assert scroll_request(frame, 1) is None


@pytest.mark.parametrize('direction', [-1, 1])
def test_mph_convergence_uses_fast_steps_and_lands_on_the_target(direction):
  r = Rig(engaged=False)
  r.units, r.speed = 0, 52
  r.target = (52 + 14 * direction) * 1.609344
  for _ in range(ENGAGE_DELAY_NS // 10_000_000 + 1):
    assert r.tick(long=False) is None
  for _ in range(2000):
    r.tick()
    p = r.sync.pending
    if p is not None and p.returned and r.now - p.sent_ns >= 30_000_000:
      r.speed = p.expected   # the Tesla answers about 30 ms later
    if r.sync.status == 'matched':
      break
  assert r.speed == 52 + 14 * direction and r.sync.blocked is None
  sizes = [abs(clicks(msg)) for _, msg in r.sent]
  assert FAST_TICKS in sizes and 1 in sizes
  assert len(r.sent) <= 5, 'far fewer requests than fourteen single clicks'
