import uuid

import pytest

from unittest.mock import Mock

from openpilot.starpilot.system.starport.frame_source import FrameProducer, FrameRequest
from openpilot.starpilot.system.starport.view import ViewSource


def request():
  return FrameRequest(1280, 720, 0, 0, 33_333)


def test_uninstalled_mirror_source_never_consumes_a_stale_frame():
  source = ViewSource('mirror', request(), lambda *_a, **_k: None)
  assert source.view == 'unavailable'
  assert 'mirror source is not installed' in source.label
  assert source.source.latest() is None
  source.demand()
  source.release_demand()
  source.close()


def test_failed_car_renderer_withdraws_frames_and_reports_unavailable():
  class OldSource:
    def __init__(self): self.closed = False
    def close(self): self.closed = True

  old = OldSource()
  source = ViewSource.__new__(ViewSource)
  source.view = 'car'
  source.fallback_reason = ''
  source.frames = 3
  source.source = Mock(wraps=old)
  source.process = None
  source.touch = None
  source.log = lambda *_a, **_k: None
  source.fallback('renderer exited')
  assert old.closed
  assert source.view == 'unavailable' and source.source.latest() is None
  assert 'renderer exited' in source.label
  source.close()


def crashing_view(tmp_path, events, script="import sys; print('Traceback: boom', flush=True); sys.exit(1)"):
  import sys
  return ViewSource('car', request(), lambda event, **fields: events.append((event, fields)),
                    car_path=str(tmp_path / 'frame'), touch_path=str(tmp_path / 'touch'),
                    control_path=f"/tmp/aa-control-{uuid.uuid4().hex}.sock",
                    renderer_command=[sys.executable, '-c', script], renderer_log=tmp_path / 'car_ui.log')


def test_crashed_renderer_is_restarted_and_its_output_is_logged(tmp_path):
  events = []
  source = crashing_view(tmp_path, events)
  try:
    source.process.wait(timeout=10)
    source.check(100.0)
    assert source.view == 'car' and not source.fallback_reason
    assert source.waiting_for_first_frame
    output = [fields for event, fields in events if event == 'car_view_output']
    assert output == [{'reason': 'renderer exited with 1', 'lines': ['Traceback: boom']}]
    assert ('car_view_restart', {'reason': 'renderer exited with 1', 'attempt': 1}) in events
    assert sum(event == 'car_view_started' for event, _ in events) == 2
  finally:
    source.close()


def test_renderer_restarts_are_bounded_then_fall_back(tmp_path):
  from openpilot.starpilot.system.starport import view
  events = []
  source = crashing_view(tmp_path, events)
  try:
    for attempt in range(view.MAX_RESTARTS + 1):
      source.process.wait(timeout=10)
      source.check(100.0 + attempt)
    assert source.view == 'unavailable' and 'renderer exited with 1' in source.label
    assert [event for event, _ in events].count('car_view_restart') == view.MAX_RESTARTS
    assert [event for event, _ in events].count('car_view_output') == view.MAX_RESTARTS + 1
  finally:
    source.close()


def test_restart_budget_recovers_after_the_window(tmp_path):
  from openpilot.starpilot.system.starport import view
  events = []
  source = crashing_view(tmp_path, events)
  try:
    for attempt in range(view.MAX_RESTARTS):
      source.process.wait(timeout=10)
      source.check(100.0 + attempt)
    source.process.wait(timeout=10)
    source.check(100.0 + view.RESTART_WINDOW + 1)
    assert source.view == 'car'
  finally:
    source.close()


def test_stalled_renderer_output_is_captured_before_restart(tmp_path):
  events = []
  source = crashing_view(tmp_path, events, "import time; print('stuck', flush=True); time.sleep(60)")
  try:
    import time
    deadline = time.monotonic() + 10
    while not (tmp_path / 'car_ui.log').read_text() and time.monotonic() < deadline:
      time.sleep(0.05)
    from openpilot.starpilot.system.starport import view
    source.check(source.started_at + view.CAR_STARTUP_TIMEOUT + 1.0)
    assert ('car_view_output', {'reason': f'no frames after {view.CAR_STARTUP_TIMEOUT:.0f} s', 'lines': ['stuck']}) in events
    assert source.view == 'car'
  finally:
    source.close()


def test_unavailable_frame_draws_the_logo():
  from openpilot.starpilot.system.starport.supervisor import Supervisor
  req = request()
  frame = Supervisor._unavailable_frame(req, 'Starting StarPilot')
  assert len(frame) == req.width * req.height * 4
  # The logo is purple; without it the frame is only the dark background and white text.
  pixels = memoryview(frame).cast('B')
  purple = sum(1 for i in range(0, len(frame), 4 * 97) if pixels[i + 2] > 120 and pixels[i + 2] > pixels[i + 1] + 60)
  assert purple > 100


@pytest.mark.parametrize('cold_start_seconds', [26.0, 40.0, 59.0])
def test_cold_renderer_can_publish_first_frame_before_restart(tmp_path, cold_start_seconds):
  import time
  from openpilot.starpilot.system.starport import view

  events = []
  source = crashing_view(tmp_path, events, 'import time; time.sleep(120)')
  producer = FrameProducer(str(tmp_path / 'frame'))
  try:
    process = source.process
    # Advance only the watchdog clock, without spending a minute in the test.
    source.check(source.started_at + cold_start_seconds)
    assert source.process is process and process.poll() is None
    assert source.waiting_for_first_frame
    assert not any(event == 'car_view_restart' for event, _ in events)
    source.demand()
    req = producer.pending_request()
    assert req is not None and req == request()
    pixels = bytes([17, 29, 43, 255]) * (req.width * req.height)
    captured_ns = time.monotonic_ns()
    producer.publish(req, pixels, captured_ns)
    frame = source.latest()
    assert frame is not None and frame.data == pixels and frame.captured_ns == captured_ns
    assert source.frames == 1 and not source.waiting_for_first_frame
    # A real first frame switches immediately to the original active watchdog.
    source.check(source.last_frame_at + view.CAR_STALL_TIMEOUT - 0.1)
    assert source.process is process
    source.check(source.last_frame_at + view.CAR_STALL_TIMEOUT + 0.1)
    assert source.process is not process
    assert sum(event == 'car_view_restart' for event, _ in events) == 1
  finally:
    producer.close()
    source.close()


def test_cold_start_timeout_still_exhausts_restart_budget(tmp_path):
  from openpilot.starpilot.system.starport import view

  events = []
  source = crashing_view(tmp_path, events, 'import time; time.sleep(120)')
  try:
    for _ in range(view.MAX_RESTARTS + 1):
      source.check(source.started_at + view.CAR_STARTUP_TIMEOUT + 1.0)
    assert source.view == 'unavailable' and not source.waiting_for_first_frame
    assert sum(event == 'car_view_restart' for event, _ in events) == view.MAX_RESTARTS
    assert source.latest() is None
  finally:
    source.close()


def drawn_rows(frame, width):
  """Rows that differ from the card's dark background."""
  rows = set()
  for row in range(len(frame) // (width * 4)):
    line = frame[row * width * 4:(row + 1) * width * 4]
    if any(line[i] > 40 for i in range(0, len(line), 4)):
      rows.add(row)
  return rows


def test_unavailable_card_stays_inside_a_top_aligned_visible_area():
  from openpilot.starpilot.system.starport.supervisor import Supervisor
  # The 2023 Polestar 2: 720x1280 with all 541 margin rows at the bottom, so it shows only the top 739 rows.
  req = FrameRequest(720, 1280, 0, 541, 33333, content_left=0, content_top=0)
  rows = drawn_rows(Supervisor._unavailable_frame(req, 'Starting StarPilot'), req.width)
  assert rows and max(rows) < 1280 - 541
  assert abs((min(rows) + max(rows)) / 2 - (1280 - 541) / 2) < 30  # centered in what the car shows


@pytest.mark.parametrize('content_top', [None, 270, 271])
def test_unavailable_card_is_unchanged_for_centered_margins(content_top):
  from openpilot.starpilot.system.starport.supervisor import Supervisor
  base = FrameRequest(720, 1280, 0, 541, 33333)
  req = FrameRequest(720, 1280, 0, 541, 33333, content_left=0 if content_top is not None else None, content_top=content_top)
  assert Supervisor._unavailable_frame(req, 'Starting StarPilot') == Supervisor._unavailable_frame(base, 'Starting StarPilot')
