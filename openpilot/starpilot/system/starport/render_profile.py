"""Always-on sampling profile of the car renderer, in a size-capped file.

A daemon thread looks at the render thread's Python stack every INTERVAL
seconds (sys._current_frames: no tracing, so the renderer runs at full speed)
and counts, per function, how often it was running (self) or on the stack
(inclusive), plus the hottest source lines. Samples taken while the renderer
is idle (pacing, or the car showing its own screen) are only counted as idle.

Every WINDOW seconds the busiest entries of that window are appended to the
report file together with the renderer's latest ``render_stats`` (see RenderSummary). When the
file would grow past ``max_bytes`` it becomes ``<name>.1.txt`` (replacing the
previous one) and a new file starts, so at most twice ``max_bytes`` is kept.
The file outlives renderer restarts, unlike car_ui.log.

The sampler can only look while the render thread lets go of the GIL, which it
does in every raylib/GL call, so pure-Python work is credited to the next such
call in the innermost-frame sections. The "on the stack" section is accurate
per function and is the one to read for which part of the layout costs time.
Sampling runs at 25 samples a second.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from collections import Counter, deque
from pathlib import Path

INTERVAL = 0.04    # 25 samples a second
WINDOW = 60.0
TOP = 25
TOP_LINES = 15
SUMMARY_INTERVAL = 5.0


class RenderPipelineSummary:
  """Bounded measurements of published frames; no tracing or per-frame log I/O.

  Camera EOF is an exposure timestamp, not CameraState arrival. Keep it separate
  from renderer capture-to-publish latency so unlike clocks/stages aren't compared.
  ID discontinuities count observations, not proof of a renderer-caused drop.
  """

  def __init__(self, now_ns: int, interval_ns: int = 5_000_000_000):
    self.started_ns, self.interval_ns = now_ns, interval_ns
    self.samples = deque(maxlen=240)
    self.previous = None
    self.repeats = self.skipped = self.resets = self.frames = 0

  def published(self, captured_ns, submitted_ns, published_ns, camera=None):
    if not captured_ns <= submitted_ns <= published_ns:
      return None
    camera_age = None
    if camera is None:
      self.previous = None
    else:
      stream, frame_id, eof_ns = camera
      if 0 < eof_ns <= published_ns:
        camera_age = published_ns - eof_ns
      if self.previous is not None and self.previous[0] == stream:
        delta = frame_id - self.previous[1]
        self.repeats += delta == 0
        self.skipped += max(0, delta - 1)
        self.resets += delta < 0
      self.previous = (stream, frame_id)
    self.frames += 1
    self.samples.append((submitted_ns - captured_ns, published_ns - submitted_ns,
                         published_ns - captured_ns, camera_age))
    if published_ns - self.started_ns < self.interval_ns:
      return None
    report = {'published_frames': self.frames, 'camera_id_repeats': self.repeats,
              'camera_id_gaps': self.skipped, 'camera_id_resets': self.resets}
    for index, name in enumerate(('capture_to_submit', 'submit_to_publish', 'capture_to_publish', 'camera_eof_to_publish')):
      values = sorted(sample[index] for sample in self.samples if sample[index] is not None)
      report[name + '_p95_ms'] = round(values[max(0, (95 * len(values) + 99) // 100 - 1)] / 1e6, 2) if values else None
    self.started_ns = published_ns
    self.samples.clear()
    self.repeats = self.skipped = self.resets = self.frames = 0
    return report


class RenderSummary:
  """Frame rate and mean frame time of the renderer while it renders, for the profile header.

  Given a ``content`` mode, each window also reports where a frame's time went, as means per rendered frame in ms
  (update_ms: ui_state.update, prepare_ms: layout.prepare, draw_ms: the content target pass, submit_ms: NV12/RGBA
  conversion plus readback start, readback_wait_ms: time inside the readback's finish), ``dropped`` (frames whose
  readback failed), ``content`` and ``paced`` ("camera" when most frames waited on a camera, else "interval").
  The renderer only adds monotonic() pairs around each phase, so this costs nothing measurable.
  """

  PHASES = ("update", "prepare", "draw", "submit", "readback_wait")

  def __init__(self, now: float, interval: float = SUMMARY_INTERVAL, content: str | None = None):
    self.interval = interval
    self.content = content
    self.reset(now)

  def reset(self, now: float) -> None:
    self.started, self.frames, self.frame_seconds = now, 0, 0.0
    self.phase_seconds = dict.fromkeys(self.PHASES, 0.0)
    self.dropped = self.camera_paced = 0

  def add(self, phase: str, seconds: float) -> None:
    """Time spent in one phase of the current window (``phase`` is one of PHASES)."""
    self.phase_seconds[phase] += max(0.0, seconds)

  def drop(self) -> None:
    self.dropped += 1

  def frame_done(self, began: float, done: float, camera_paced: bool = False) -> dict | None:
    """Count one frame; every ``interval`` returns the window's render_stats and starts a new one."""
    self.frames += 1
    self.frame_seconds += done - began
    self.camera_paced += bool(camera_paced)
    if done - self.started < self.interval:
      return None
    report = {"event": "render_stats", "fps": round(self.frames / (done - self.started), 1),
              "frame_ms": round(1000 * self.frame_seconds / self.frames, 1)}
    if self.content is not None:
      for phase in self.PHASES:
        report[f"{phase}_ms"] = round(1000 * self.phase_seconds[phase] / self.frames, 2)
      report["dropped"] = self.dropped
      report["content"] = self.content
      report["paced"] = "camera" if 2 * self.camera_paced > self.frames else "interval"
    self.reset(done)
    return report


class RenderSampler:
  def __init__(self, path: Path, *, max_bytes: int = 256 * 1024, interval: float = INTERVAL, window: float = WINDOW,
               clock=time.monotonic):
    self.path, self.max_bytes = path, max_bytes
    self.interval, self.window, self.clock = interval, window, clock
    self.thread_id = threading.get_ident()  # created on the render thread, which it samples
    self.rendering = False                  # set by the render loop around each frame
    self.onroad = False
    self.summary: dict | None = None        # latest render_stats
    self._names: dict = {}
    self._lock = threading.Lock()
    self._stop = threading.Event()
    self._worker = threading.Thread(target=self._run, name="car_ui_sampler", daemon=True)
    self._reset()

  def _reset(self) -> None:
    self.started = self.clock()
    self.samples = self.idle = self.onroad_samples = 0
    self.self_counts: Counter = Counter()
    self.total_counts: Counter = Counter()
    self.line_counts: Counter = Counter()
    self.param_samples: Counter = Counter()

  def start(self) -> None:
    self._worker.start()

  def close(self) -> None:
    self._stop.set()
    if self._worker.is_alive():
      self._worker.join(timeout=1.0)
    self.flush()

  def _name(self, code) -> str:
    name = self._names.get(code)
    if name is None:
      name = self._names[code] = f"{Path(code.co_filename).name}:{code.co_firstlineno} {code.co_name}"
    return name

  def sample(self, frame) -> None:
    """Count one sample of the render thread's stack (``frame`` is its innermost frame)."""
    with self._lock:
      self.samples += 1
      if not self.rendering:
        self.idle += 1
        return
      self.onroad_samples += self.onroad
      code = frame.f_code
      self.line_counts[f"{Path(code.co_filename).name}:{frame.f_lineno} {code.co_name}"] += 1
      self.self_counts[self._name(code)] += 1
      seen = set()
      param_seen = False
      while frame is not None:
        code = frame.f_code
        # Attribute sampled waits to keys without wrapping every Params call.
        # Only key names are captured; values (including credentials) never are.
        if not param_seen and code.co_name in ("get", "get_bool") and code.co_filename.endswith("/common/params.py"):
          key = frame.f_locals.get("key")
          if isinstance(key, str):
            self.param_samples[f"{code.co_name}({key})"] += 1
            param_seen = True
        name = self._name(frame.f_code)
        if name not in seen:
          seen.add(name)
          self.total_counts[name] += 1
        frame = frame.f_back

  def _run(self) -> None:
    while not self._stop.wait(self.interval):
      frame = sys._current_frames().get(self.thread_id)
      if frame is not None:
        self.sample(frame)
      del frame
      if self.clock() - self.started >= self.window:
        self.flush()

  def report(self) -> str:
    with self._lock:
      busy = self.samples - self.idle
      if busy <= 0:
        return ""
      state = "onroad" if self.onroad_samples * 2 >= busy else "offroad"
      lines = [f"==== {time.strftime('%Y-%m-%d %H:%M:%S')}  {self.clock() - self.started:.0f} s, {state} " +
               f"({100 * self.onroad_samples // busy}% onroad), rendering {100 * busy // self.samples}% of {self.samples} samples ===="]
      if self.summary:
        lines.append("render_stats " + " ".join(f"{key}={value}" for key, value in self.summary.items() if key != "event"))
      for title, counts, top in (("on the stack (time inside each function)", self.total_counts, TOP),
                                 ("innermost function (pure Python shows up at the next raylib/GL call)", self.self_counts, TOP),
                                 ("innermost line", self.line_counts, TOP_LINES)):
        lines.append(f"-- {title}")
        # Frames on every sample (the render loop and interpreter startup) say nothing.
        shown = [(name, count) for name, count in counts.most_common() if count < busy or counts is not self.total_counts]
        lines += [f"{100 * count / busy:6.1f}%  {name}" for name, count in shown[:top]]
      if self.param_samples:
        lines.append("-- Params keys sampled on the render thread (% of busy samples, not read counts)")
        lines += [f"{100 * count / busy:6.1f}%  {name}" for name, count in self.param_samples.most_common(TOP)]
    return "\n".join(lines) + "\n\n"

  def flush(self) -> None:
    text = self.report()
    with self._lock:
      self._reset()
    if text:
      self._write(text)

  def _write(self, text: str) -> None:
    try:
      self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
      try:
        if self.path.stat().st_size + len(text) > self.max_bytes:
          os.replace(self.path, self.path.with_name(f"{self.path.stem}.1{self.path.suffix}"))
      except FileNotFoundError:
        pass
      with open(self.path, "a") as handle:
        handle.write(text)
    except OSError:
      pass
