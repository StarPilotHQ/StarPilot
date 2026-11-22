import pytest
from opendbc.car.subaru.avh import AVH_REQUEST, AVH_STATUS, INPUTS, AvhStartup, avh_request, checksum

def sample(address, counter):
  data = bytearray(8)
  data[1] = counter & 15
  if address == AVH_REQUEST:
    data[3], data[5], data[6] = 1, 0x80, 0x0E  # captured Legacy payload, not Outback constants
  elif address == 0x40:
    data[2:4] = (800).to_bytes(2, 'little')
  elif address == 0x48:
    data[3] = 4
  elif address == 0x174:
    data[2] = 8
  data[0] = checksum(address, data)
  return bytes(data)

def prepare(fast_counter_step=1):
  policy = AvhStartup()
  frames = {}
  for tick in range(101):
    now = 100 + tick / 10
    for address in INPUTS:
      if address != AVH_REQUEST or tick % 10 == 0:
        counter = tick // 10 if address == AVH_REQUEST else tick * (1 if address == AVH_STATUS else fast_counter_step)
        frames[address] = (now, sample(address, counter))
    sent = policy.update(now, frames, True, True, False)
    if tick < 100:
      assert sent == []
  assert sent == [avh_request(frames[AVH_REQUEST][1], 1)]
  return policy, frames

def test_captured_legacy_press_bytes():
  template = bytes.fromhex('5b0b000100800e00')
  assert avh_request(template, 1) == (0x6BB, bytes.fromhex('5e0c020100800e00'), 1)
  assert avh_request(template, 2) == (0x6BB, bytes.fromhex('5f0d020100800e00'), 1)
  assert avh_request(template, 3) == (0x6BB, bytes.fromhex('600e020100800e00'), 1)
  assert avh_request(template, 4) == (0x6BB, bytes.fromhex('610f020100800e00'), 1)
  wrap = sample(AVH_REQUEST, 15)
  assert avh_request(wrap, 1)[1][1] == 0
  assert avh_request(wrap, 2)[1][1] == 1
  assert avh_request(wrap, 3)[1][1] == 2
  assert avh_request(wrap, 4)[1][1] == 3

def test_four_frames_only_and_no_retry():
  policy, frames = prepare()
  assert policy.update(110.04, frames, True, True, False) == []
  assert policy.update(110.06, frames, True, True, False) == [avh_request(frames[AVH_REQUEST][1], 2)]
  assert policy.update(110.10, frames, True, True, False) == []
  assert policy.update(110.12, frames, True, True, False) == [avh_request(frames[AVH_REQUEST][1], 3)]
  assert policy.update(110.16, frames, True, True, False) == []
  assert policy.update(110.18, frames, True, True, False) == [avh_request(frames[AVH_REQUEST][1], 4)]
  assert policy.update(110.20, frames, True, True, False) == []
  assert policy.update(111, frames, True, True, False) == []

def test_controller_snapshots_may_skip_fast_can_samples():
  policy, frames = prepare(fast_counter_step=2)
  assert policy.update(110.06, frames, True, True, False) == [avh_request(frames[AVH_REQUEST][1], 2)]
  assert policy.update(110.12, frames, True, True, False) == [avh_request(frames[AVH_REQUEST][1], 3)]
  assert policy.update(110.18, frames, True, True, False) == [avh_request(frames[AVH_REQUEST][1], 4)]

@pytest.mark.parametrize('reason', ['late','new_template','manual','ack','moving','gas','gear','invalid','disabled','engaged','stale'])
def test_followup_aborts_permanently(reason):
  policy, frames = prepare()
  address, offset, value = {
    'manual': (AVH_REQUEST, 2, 1), 'ack': (AVH_STATUS, 5, 32),
    'moving': (0x13A, 2, 1), 'gas': (0x40, 4, 1), 'gear': (0x48, 3, 3),
    'new_template': (AVH_REQUEST, 1, 11),
  }.get(reason, (None, None, None))
  if address is not None:
    data = bytearray(frames[address][1])
    data[1] = (data[1] + 1) & 15
    data[offset] = value
    data[0] = checksum(address, data)
    frames[address] = (110.05, bytes(data))
  if reason == 'stale':
    frames[0x40] = (109, frames[0x40][1])
  now = 110.08 if reason == 'late' else 110.06
  assert policy.update(now, frames, reason != 'disabled', reason != 'invalid', reason == 'engaged') == []
  assert policy.done
  assert policy.update(111, frames, True, True, False) == []


def test_skips_state_change_and_50ms_burst_waits_for_steady_heartbeat():
  policy = AvhStartup()
  frames = {}
  counter = 0
  fast_counter = 0
  for tick in range(100):
    now = 100.0 + tick / 10
    fast_counter = tick
    for address in INPUTS:
      if address != AVH_REQUEST or tick % 10 == 0:
        if address == AVH_REQUEST:
          counter += 1
        c = counter if address == AVH_REQUEST else fast_counter
        frames[address] = (now, sample(address, c))
    assert policy.update(now, frames, True, True, False) == []

  # Event transition (byte 6 changes) -> 1st burst frame at 109.95s, then 50ms burst at 110.00, 110.05, 110.10 (10s met mid-burst).
  for offset, changed in ((109.95, True), (110.00, False), (110.05, False), (110.10, False)):
    counter += 1
    fast_counter += 1
    data = bytearray(sample(AVH_REQUEST, counter))
    data[6] = 0x0F if changed else 0x0E
    data[0] = checksum(AVH_REQUEST, data)
    frames[AVH_REQUEST] = (offset, bytes(data))
    for address in INPUTS:
      if address != AVH_REQUEST:
        frames[address] = (offset, sample(address, fast_counter))
    assert policy.update(offset, frames, True, True, False) == []
    assert not policy.done

  # Wait past 1.20s at 100ms ticks so configured/stable continuity stays intact, then send steady heartbeat at 111.30s.
  for tick in range(1, 12):
    now = 110.10 + tick * 0.10
    fast_counter += 1
    for address in INPUTS:
      if address != AVH_REQUEST:
        frames[address] = (now, sample(address, fast_counter))
    assert policy.update(now, frames, True, True, False) == []

  counter += 1
  fast_counter += 1
  for address in INPUTS:
    frames[address] = (111.30, sample(address, counter if address == AVH_REQUEST else fast_counter))
  assert policy.update(111.30, frames, True, True, False) == [avh_request(frames[AVH_REQUEST][1], 1)]
  assert policy.update(111.36, frames, True, True, False) == [avh_request(frames[AVH_REQUEST][1], 2)]
  assert policy.update(111.42, frames, True, True, False) == [avh_request(frames[AVH_REQUEST][1], 3)]
  assert policy.update(111.48, frames, True, True, False) == [avh_request(frames[AVH_REQUEST][1], 4)]
  assert policy.done
