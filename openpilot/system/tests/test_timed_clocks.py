import datetime

from openpilot.system import timed

# drive 000000a8, 2026-11-09 04:15:03 CST
A8_FRAME = bytes.fromhex("4a040f036c1a0901")
A8_UTC = datetime.datetime(2026, 11, 9, 10, 15, 3)


def test_parse_car_clock():
  assert timed.parse_car_clock(A8_FRAME) == datetime.datetime(2026, 11, 9, 4, 15, 3)
  assert timed.parse_car_clock(b"\xff" * 8) is None
  assert timed.parse_car_clock(b"\x00" * 8) is None
  assert timed.parse_car_clock(bytes.fromhex("4a040f036c1a00")) is None


def test_learn_utc_offset():
  local = timed.parse_car_clock(A8_FRAME)
  assert timed.learn_utc_offset(local, A8_UTC + datetime.timedelta(seconds=40)) == datetime.timedelta(hours=-6)
  assert timed.learn_utc_offset(local, A8_UTC + datetime.timedelta(minutes=5)) is None
  assert timed.learn_utc_offset(local, A8_UTC - datetime.timedelta(days=100)) is None


def test_car_clock_to_utc():
  local = timed.parse_car_clock(A8_FRAME)
  assert timed.car_clock_to_utc(local, datetime.timedelta(hours=-6)) == A8_UTC
  assert timed.car_clock_to_utc(local, None) == A8_UTC
  assert timed.car_clock_to_utc(datetime.datetime(2026, 7, 4, 12, 0), None) == datetime.datetime(2026, 7, 4, 17, 0)


def test_utc_offset_roundtrip(monkeypatch, tmp_path):
  monkeypatch.setattr(timed, 'CAR_CLOCK_OFFSET_PATH', str(tmp_path / "offset"))
  assert timed.load_utc_offset() is None
  timed.save_utc_offset(datetime.timedelta(hours=-5, minutes=-30))
  assert timed.load_utc_offset() == datetime.timedelta(hours=-5, minutes=-30)


def test_recorded_clock_pair_is_bound_to_event_timestamp(monkeypatch):
  mono = iter((10_000_000, 10_100_000))
  monkeypatch.setattr(timed.time, 'monotonic_ns', lambda: next(mono))
  monkeypatch.setattr(timed.time, 'clock_gettime_ns', lambda clock: 20_000_000)
  monkeypatch.setattr(timed.time, 'time_ns', lambda: 1_800_000_000_000_000_000)
  monkeypatch.setattr(timed, 'system_time_valid', lambda: True)
  event = timed.clock_message()
  assert event.logMonoTime == event.clocks.deprecated.monotonicNanos == 10_000_000
  assert event.clocks.deprecated.bootTimeNanos == 20_000_000
  assert event.clocks.wallTimeNanos == 1_800_000_000_000_000_000


def test_delayed_clock_sample_does_not_claim_a_precise_pair(monkeypatch):
  mono = iter((10_000_000, 12_000_000))
  monkeypatch.setattr(timed.time, 'monotonic_ns', lambda: next(mono))
  monkeypatch.setattr(timed.time, 'clock_gettime_ns', lambda clock: 20_000_000)
  monkeypatch.setattr(timed, 'system_time_valid', lambda: True)
  event = timed.clock_message()
  assert event.clocks.deprecated.monotonicNanos == event.clocks.deprecated.bootTimeNanos == 0
