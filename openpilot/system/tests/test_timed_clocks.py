import datetime

from openpilot.common.timezone_lookup import load_grid, pick_timezone
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


def test_timezone_lookup_regions():
  grid = load_grid()
  for lat, lon, zone in [(41.88, -87.63, "America/Chicago"), (52.13, -106.67, "America/Regina"),
                         (49.1, -116.51, "America/Creston"), (47.56, -52.71, "America/St_Johns"),
                         (53.28, -110.0, "America/Edmonton"), (31.76, -106.49, "America/Denver"),
                         (48.85, 2.35, "Europe/Paris"), (49.61, 6.13, "Europe/Luxembourg"), (60.17, 24.94, "Europe/Helsinki")]:
    assert grid.candidates(lat, lon)[0] == zone
  assert grid.candidates(0., -140.) == (None, set())


def test_pick_timezone_keeps_current_near_border():
  grid = load_grid()
  # Monaco cell borders France: an existing Paris zone is kept, anything else takes the cell's zone
  assert pick_timezone(grid, 43.73, 7.42, "Europe/Paris") == "Europe/Paris"
  assert pick_timezone(grid, 43.73, 7.42, "America/Los_Angeles") == "Europe/Monaco"
  assert pick_timezone(grid, 0., -140., "America/Chicago") is None


def test_timezone_tracker_confirms_before_switching():
  tracker = timed.TimezoneTracker(load_grid(), "America/Los_Angeles")
  assert tracker.update(41.88, -87.63) is None
  assert tracker.update(41.88, -87.63) == "America/Chicago"
  # back in the current zone resets the pending switch
  tracker = timed.TimezoneTracker(load_grid(), "America/Los_Angeles")
  assert tracker.update(41.88, -87.63) is None
  assert tracker.update(34.05, -118.24) is None
  assert tracker.update(41.88, -87.63) is None
  # no zone at sea keeps everything as is
  assert tracker.update(0., -140.) is None


def test_current_timezone(tmp_path):
  link = tmp_path / "localtime"
  assert timed.current_timezone(str(link)) is None
  link.symlink_to("/usr/share/zoneinfo/America/Chicago")
  assert timed.current_timezone(str(link)) == "America/Chicago"


def test_set_timezone_swaps_link_and_name(monkeypatch):
  calls = []
  monkeypatch.setattr(timed.subprocess, 'run', lambda cmd, **kw: calls.append((cmd, kw.get('input'))))
  monkeypatch.setattr(timed.os.path, 'isfile', lambda path: True)
  assert timed.set_timezone("America/Chicago")
  assert [c for c, _ in calls] == [
    ["sudo", "-n", "ln", "-sfn", "/usr/share/zoneinfo/America/Chicago", timed.LOCALTIME_PATH + ".tmp"],
    ["sudo", "-n", "mv", "-T", timed.LOCALTIME_PATH + ".tmp", timed.LOCALTIME_PATH],
    ["sudo", "-n", "tee", timed.TIMEZONE_NAME_PATH],
  ]
  assert calls[-1][1] == "America/Chicago\n"


def test_set_timezone_rejects_bad_names(monkeypatch):
  calls = []
  monkeypatch.setattr(timed.subprocess, 'run', lambda cmd, **kw: calls.append(cmd))
  monkeypatch.setattr(timed.os.path, 'isfile', lambda path: path.endswith("America/Chicago"))
  assert not timed.set_timezone("../../etc/passwd")
  assert not timed.set_timezone("Mars/Olympus")
  assert calls == []


def test_set_timezone_reports_sudo_failure(monkeypatch):
  def fail(cmd, **kw):
    raise timed.subprocess.CalledProcessError(1, cmd)
  monkeypatch.setattr(timed.subprocess, 'run', fail)
  monkeypatch.setattr(timed.os.path, 'isfile', lambda path: True)
  assert not timed.set_timezone("America/Chicago")
