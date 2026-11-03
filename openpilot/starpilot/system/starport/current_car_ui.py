"""Headless producer for a separate large StarPilot projection view.

Frames are rendered only while requested. Touch input controls navigation favorites and drive bookmarks.
Onroad, each new camera frame is drawn exactly once, as soon as it lands (see
CameraPacer). When the encoder takes NV12, the frame is converted on the GPU,
and it can be read back without stalling the renderer.
"""

from __future__ import annotations

import argparse
from contextlib import ExitStack
import gc
import json
import math
import os
import signal
import time

from openpilot.starpilot.system.starport.frame_source import (FLAG_ASYNC_READBACK, FLAG_NV12, FORMAT_NV12, FORMAT_RGBA,
                                                                  FrameProducer, FrameRequest, frame_bytes)

from openpilot.starpilot.system.starport.projection_geometry import projection_geometry

STARTUP_WAIT_SECONDS = 15.0
CAMERA_WAIT_STEP = 0.01    # s; keeps demand/stop checks responsive while waiting for the camera
READBACK_POLL_STEP = 0.002  # s; while a frame is still on the GPU, how often waiting checks whether it is done
CAMERA_MAX_GAP = 0.1       # s; a shown camera silent this long stops pacing (the encoder rate applies)
ASYNC_READBACK_FAILURES = 3  # consecutive failed asynchronous readbacks before the session reads back synchronously


# ANDROID AUTO VIDEO PERFORMANCE GUARDRAIL: Camera-driven pacing avoids duplicate frames and uneven video cadence.
# Altering this can degrade video smoothness or introduce lag on Android Auto.
# Do not change it autonomously; require an explicit user request specifically for this video optimization.
class CameraPacer:
  """Onroad, draw each frame of the camera on screen exactly once, as soon as it lands.

  The road cameras and the driving model run at 20 Hz. A 30 fps timer redraws
  every third frame for nothing and shows camera frames in an uneven 2-1
  cadence; a 20 fps timer drifts against the camera clock and periodically
  repeats one frame and skips the next. camerad hands the frame to VisionIPC
  before it publishes the matching CameraState, so waking on that message means
  CameraView's non-blocking recv already has the new frame.

  Only the shown camera's CameraState is subscribed, conflated and never
  deserialized: the pacer only needs to know that one arrived. When that camera
  goes quiet, it stops pacing and the encoder's frame rate applies again.
  """

  def __init__(self, sock_factory=None, stream_types=None):
    if sock_factory is None:
      import openpilot.cereal.messaging as messaging

      def sock_factory(name):
        poller = messaging.Poller()
        return poller, messaging.sub_sock(name, poller=poller, conflate=True)
    if stream_types is None:
      from openpilot.cereal.visionipc import VisionStreamType as stream_types
    self.state_for_stream = {int(stream_types.VISION_STREAM_NARROW_ROAD): "narrowRoadCameraState",
                             int(stream_types.VISION_STREAM_WIDE_ROAD): "wideRoadCameraState",
                             int(stream_types.VISION_STREAM_CABIN): "cabinCameraState"}
    self._sock_factory = sock_factory
    self._socks: dict[str, tuple] = {}   # opened on first use, so an unshown camera costs nothing
    self._last_arrival: dict[str, float] = {}

  def wait(self, stream_type, now: float, step: float = CAMERA_WAIT_STEP) -> bool:
    """True when a new frame of ``stream_type`` is ready, or when that camera is quiet; waits at most ``step``."""
    state = self.state_for_stream.get(int(stream_type))
    if state is None:
      return True
    if state not in self._socks:
      self._socks[state] = self._sock_factory(state)
    poller, sock = self._socks[state]
    quiet = now - self._last_arrival.get(state, float("-inf")) >= CAMERA_MAX_GAP
    # A quiet camera must not hold rendering back: just check whether it has resumed.
    arrived = bool(poller.poll(0 if quiet else max(1, int(step * 1000)))) and sock.receive(non_blocking=True) is not None
    if arrived:
      self._last_arrival[state] = now
    return arrived or quiet


def create_readback(factory, size: int, asynchronous: bool):
  try:
    return factory(size, asynchronous=asynchronous)
  except (RuntimeError, OSError, AttributeError) as error:
    if not asynchronous:
      raise
    print(f"Async readback unavailable, using synchronous readback: {error}", flush=True)
    return factory(size, asynchronous=False)


def visible_geometry(request: FrameRequest) -> tuple[int, int, float, int, int]:
  geometry = projection_geometry(request.width, request.height, request.margin_w, request.margin_h)
  return (geometry.width, geometry.height, geometry.scale,
          round((geometry.width - geometry.logical_width * geometry.scale) / 2),
          round((geometry.height - geometry.logical_height * geometry.scale) / 2))


def projected_touches(events, geometry):
  """Undo the composition's fit/letterboxing inside the receiver's margins."""
  from openpilot.starpilot.system.starport.touch import TouchEvent
  width, height = geometry.logical_width * geometry.scale, geometry.logical_height * geometry.scale
  left, top = (geometry.width - width) / 2, (geometry.height - height) / 2
  mapped = []
  for event in events:
    x, y = (event.x * geometry.width - left) / width, (event.y * geometry.height - top) / height
    if event.kind == 'cancel' or not 0 <= x <= 1 or not 0 <= y <= 1:
      mapped.append(TouchEvent('cancel', 0, 0))
    else:
      mapped.append(TouchEvent(event.kind, x, y))
  return mapped


def scale_scissors(rl, scale: float, left: float = 0, top: float = 0) -> None:
  """Scale scissor rectangles like the matrix that draws the UI into the visible-size target.

  The UI clips in logical coordinates, but raylib applies scissors in target pixels, unaffected by
  rl_scalef. This is gui_app._patch_scissor_mode for a renderer that never opens a window; it runs
  in its own process, so it touches only the car view.
  """
  if scale == 1.0 and left == top == 0:
    return
  if not hasattr(rl, "_orig_begin_scissor_mode"):
    rl._orig_begin_scissor_mode = rl.begin_scissor_mode

  def begin_scissor_mode_scaled(x, y, width, height):
    return rl._orig_begin_scissor_mode(int(left + x * scale), int(top + y * scale),
                                       int(math.ceil(width * scale)), int(math.ceil(height * scale)))

  rl.begin_scissor_mode = begin_scissor_mode_scaled


def wait_for_request(producer: FrameProducer) -> FrameRequest:
  deadline = time.monotonic() + STARTUP_WAIT_SECONDS
  while time.monotonic() < deadline:
    producer._next_open_check = 0.0
    request = producer.pending_request(require_demand=False)
    if request is not None:
      return request
    time.sleep(0.1)
  raise TimeoutError("Android Auto did not request current UI frames")


def run(frames_path: str, touch_path: str | None = None, control_path: str | None = None) -> int:
  if os.geteuid() == 0:
    raise RuntimeError("Car display must run as the comma user")
  parent = os.getppid()
  # SCHED_IDLE placement, set before any thread starts so they all inherit it:
  # never compete with openpilot's own processes (see placement.py).
  from openpilot.starpilot.system.starport.placement import BACKGROUND_RENDER_CORES, RendererPlacement
  placement = RendererPlacement(core=BACKGROUND_RENDER_CORES, report=lambda event: print(json.dumps(event), flush=True))
  placement.start()

  os.environ['BIG'] = '1'
  os.environ['STARPILOT_PROJECTION_READ_ONLY'] = '1'
  stopped = False

  def stop(*_) -> None:
    nonlocal stopped
    stopped = True

  signal.signal(signal.SIGTERM, stop)
  signal.signal(signal.SIGINT, stop)
  from openpilot.starpilot.system.starport.headless_egl import FrameReadback, HeadlessContext
  from openpilot.starpilot.system.starport import gpu_nv12
  from openpilot.starpilot.system.starport.projection_control import DEFAULT_CONTROL_SOCKET, NATIVE_FOCUS, ProjectionControlSender
  from openpilot.starpilot.system.starport.projection_bookmark import BookmarkSender, request_bookmark
  from openpilot.starpilot.system.starport.projection_onroad import ProjectionOnroad
  from openpilot.starpilot.system.starport.touch import TouchReceiver, DEFAULT_TOUCH_SOCKET
  from openpilot.starpilot.ui.raylib import rl
  from openpilot.system.ui.lib.application import gui_app
  from openpilot.selfdrive.ui.ui_state import ui_state

  with ExitStack() as resources:
    producer = FrameProducer(frames_path)
    resources.callback(producer.close)
    request = wait_for_request(producer)
    geometry = projection_geometry(request.width, request.height, request.margin_w, request.margin_h)
    context = HeadlessContext(request.width, request.height)
    resources.callback(context.close)

    def close_gui_textures():
      for texture in gui_app._textures.values():
        rl.unload_texture(texture)
      gui_app._textures.clear()

    resources.callback(close_gui_textures)
    gui_app._width, gui_app._height = geometry.logical_width, geometry.logical_height
    gui_app._scale = 1.0
    gui_app._render_texture = None
    gui_app._load_fonts()
    from openpilot.starpilot.system.starport import reconnect_notice
    from openpilot.starpilot.system.starport.identity import certificate_days_left
    from openpilot.starpilot.system.starport.projection_layout_runtime import ProjectionLayoutWatcher, comma_display_seed
    viewport = (geometry.logical_width, geometry.logical_height)
    watcher = ProjectionLayoutWatcher(viewport, display_seed=comma_display_seed)
    layout = ProjectionOnroad(viewport=viewport, customization=watcher.load(),
                              certificate_days=certificate_days_left(), layout_watcher=watcher,
                              reconnect_notice=reconnect_notice.take())
    resources.callback(layout.close)
    watcher.start()
    resources.callback(watcher.close)
    touch = TouchReceiver(touch_path or DEFAULT_TOUCH_SOCKET)
    resources.callback(touch.close)
    control = ProjectionControlSender(control_path or DEFAULT_CONTROL_SOCKET)
    resources.callback(control.close)
    layout.native_focus = lambda: control.send(NATIVE_FOCUS)
    bookmark_sender = BookmarkSender()
    resources.callback(bookmark_sender.close)

    layout.bookmark = lambda: request_bookmark(bookmark_sender, ui_state, layout.native.display_message, time.monotonic_ns())
    # Confirm only what was really saved: the bookmark the native UI publishes, whichever button asked for it.
    from openpilot.cereal import messaging
    saved_bookmarks = messaging.sub_sock('userBookmark', conflate=True)
    from openpilot.starpilot.system.starport.projection_slc_action import SlcActionSender
    slc_sender = SlcActionSender()
    resources.callback(slc_sender.close)
    layout.slc_action = slc_sender.send
    # ANDROID AUTO VIDEO PERFORMANCE GUARDRAIL: Visible-size targets, GPU NV12 conversion, and async readback limit GPU work and copies.
    # Altering this can degrade video smoothness or introduce lag on Android Auto.
    # Do not change it autonomously; require an explicit user request specifically for this video optimization.
    # The UI draws in logical coordinates scaled by geometry.scale, so its target is the visible area:
    # the NV12 pass reads it pixel for pixel and requires exactly that size.
    content = rl.load_render_texture(geometry.width, geometry.height)
    if not content.id:
      raise RuntimeError('Projection content target unavailable')
    resources.callback(rl.unload_render_texture, content)
    left = (geometry.width - geometry.logical_width * geometry.scale) / 2
    top = (geometry.height - geometry.logical_height * geometry.scale) / 2
    scale_scissors(rl, geometry.scale, left, top)
    converter = None
    if request.flags & FLAG_NV12:
      try:
        converter = gpu_nv12.Nv12Converter(request.width, request.height,
                                           margin_w=request.margin_w, margin_h=request.margin_h, compose=True,
                                           content_left=request.content_left, content_top=request.content_top)
        resources.callback(converter.close)
      except Exception as error:
        print(f"NV12 conversion unavailable, publishing RGBA: {error}", flush=True)
        converter = None
    # NV12 composes directly from the content texture. Only the RGBA fallback
    # needs a second full-frame render target.
    output = rl.load_render_texture(request.width, request.height) if converter is None else None
    if output is not None:
      if not output.id:
        raise RuntimeError('Projection output target unavailable')
      resources.callback(rl.unload_render_texture, output)
    pixel_format = FORMAT_NV12 if converter is not None else FORMAT_RGBA
    readback = create_readback(FrameReadback, frame_bytes(request.width, request.height, pixel_format),
                             asynchronous=bool(request.flags & FLAG_ASYNC_READBACK))
    resources.callback(lambda: readback.close())
    rgba_regions = [(output.id, request.width, request.height, 0)] if output is not None else []
    pixel_format_name = 'nv12' if converter is not None else 'rgba'
    pipeline = f"{pixel_format_name}, {'async' if readback.asynchronous else 'sync'} readback"
    print(f"car view pipeline: {pipeline}", flush=True)
    from openpilot.starpilot.system.starport import identity as identity_store
    from openpilot.starpilot.system.starport.render_profile import RenderPipelineSummary, RenderSampler, RenderSummary
    config = identity_store.load_config()
    sampler = None
    if config["render_profile"]:
      sampler = RenderSampler(identity_store.LOG_DIR / "render_profile.txt", max_bytes=config["render_profile_kb"] * 1024)
      sampler.start()
      resources.callback(sampler.close)
    summary = RenderSummary(time.monotonic())
    pipeline_summary = RenderPipelineSummary(time.monotonic_ns()) if sampler is not None else None
    camera_pacer = CameraPacer()
    in_flight_ns = 0
    submitted_ns = 0
    in_flight_camera = None

    async_failures = 0

    def publish_readback() -> None:
      """Hand the frame read back last to starportd.

      A failed asynchronous wait drops only that frame; repeated failures switch
      the session to synchronous readback instead of crashing the renderer."""
      nonlocal async_failures
      try:
        try:
          pixels = readback.finish()
        except RuntimeError as error:
          if not readback.asynchronous:
            raise
          async_failures += 1
          print(f"car view dropped a frame: {error}", flush=True)
          if async_failures >= ASYNC_READBACK_FAILURES:
            readback.fall_back_to_sync()
            print(f"car view pipeline: {pixel_format_name}, sync readback (async failed {async_failures} times in a row)", flush=True)
          return
        async_failures = 0
        producer.publish(request, pixels, in_flight_ns, pixel_format, advance=False)
        if pipeline_summary is not None and sampler is not None:
          report = pipeline_summary.published(in_flight_ns, submitted_ns, time.monotonic_ns(), in_flight_camera)
          if report is not None:
            sampler.summary = {**(sampler.summary or {}), **report}
      finally:
        readback.release()

    # ANDROID AUTO VIDEO PERFORMANCE GUARDRAIL: GC freezing and the frame loop below avoid periodic stalls and unnecessary waits.
    # Altering this can degrade video smoothness or introduce lag on Android Auto.
    # Do not change it autonomously; require an explicit user request specifically for this video optimization.
    # Everything built so far lives for the whole session. Frozen, it is never rescanned
    # by the collector, whose full passes over the UI's objects stall a frame every few seconds.
    gc.collect()
    gc.freeze()
    while not stopped and os.getppid() == parent:
      now = time.monotonic()
      placement.maintain(now)  # once a second: pins graphics-driver threads, re-pins after power saving
      pending = producer.pending_request(now)
      if pending is None:
        touch.drain()
        layout.cancel_touch()
        if readback.pending:
          readback.release()
        if sampler is not None:
          sampler.rendering = False
        context.pause()
        time.sleep(0.05)
        summary.reset(time.monotonic())
        continue
      if pending != request:
        return 3  # supervisor starts a new renderer for the new geometry
      captured_ns = time.monotonic_ns()
      # The requested frame rate (the encoder's budget) always applies. Rendering
      # only when it is due also keeps the schedule from running ahead of real time.
      delay = producer.capture_delay(request, captured_ns)
      # ANDROID AUTO VIDEO PERFORMANCE GUARDRAIL: While waiting, publish a frame only once the GPU has finished it.
      # The LOW-priority context queues behind the driving model for ~30 ms; blocking on it here
      # stacked that wait on top of the next frame's CPU work and skipped camera frames (user-approved, 2026-10-08).
      # Altering this can degrade video smoothness or introduce lag on Android Auto.
      # Do not change it autonomously; require an explicit user request specifically for this video optimization.
      if delay > 0:
        if readback.pending and readback.ready():
          publish_readback()  # never hold a finished frame back just to pace the next one
        if sampler is not None:
          sampler.rendering = False
        time.sleep(min(delay, READBACK_POLL_STEP if readback.pending else 0.05))
        continue
      if layout.camera_stream is not None:
        # Within that budget, draw as soon as the camera on screen has a new frame.
        if readback.pending and readback.ready():
          publish_readback()
        step = READBACK_POLL_STEP if readback.pending else CAMERA_WAIT_STEP
        if not camera_pacer.wait(layout.camera_stream, now, step):
          if sampler is not None:
            sampler.rendering = False
          continue
        captured_ns = time.monotonic_ns()
        now = captured_ns / 1e9
      if sampler is not None:
        sampler.rendering = True
      frame_began = time.monotonic()
      context.begin_frame(now)
      ui_state.update()
      if messaging.recv_one_or_none(saved_bookmarks) is not None and ui_state.started:
        layout.bookmark_saved()
      if sampler is not None:
        sampler.onroad = ui_state.started
      layout.prepare()  # offscreen passes (the map) must not run inside the content target
      # ANDROID AUTO VIDEO PERFORMANCE GUARDRAIL: Publish the previous frame before drawing the next; moving this later adds video latency.
      # Altering this can degrade video smoothness or introduce lag on Android Auto.
      # Do not change it autonomously; require an explicit user request specifically for this video optimization.
      if readback.pending:
        # Match AAComma: publish after CPU preparation, before queuing another
        # frame's drawing. A busy renderer must not hold video until that draw ends.
        # A frame still on the GPU when the next camera frame arrived is waited for here.
        publish_readback()
      rl.begin_texture_mode(content)
      try:
        rl.clear_background(rl.BLACK)
        rl.rl_push_matrix()
        try:
          rl.rl_translatef(left, top, 0)
          rl.rl_scalef(geometry.scale, geometry.scale, 1.0)
          layout.render()
        finally:
          rl.rl_pop_matrix()
      finally:
        rl.end_texture_mode()
      layout.handle_touches(projected_touches(touch.drain(), geometry))
      if output is not None:
        gpu_nv12.compose_rgba(content.texture, output, request.margin_w, request.margin_h,
                              content_left=request.content_left, content_top=request.content_top)
      regions = converter.convert(content.texture) if converter is not None else rgba_regions
      producer.advance(request, captured_ns)
      readback.start(regions)
      in_flight_ns = captured_ns
      if pipeline_summary is not None:
        submitted_ns = time.monotonic_ns()
        camera = layout.camera
        in_flight_camera = ((int(layout.camera_stream), camera.client.frame_id, camera.client.timestamp_eof)
                            if layout.camera_stream is not None and camera.frame is not None else None)
      if not readback.asynchronous:
        publish_readback()
      report = summary.frame_done(frame_began, time.monotonic())
      if report is not None and sampler is not None:
        sampler.summary = {**(sampler.summary or {}), **report}
      gui_app._frame += 1
  return 0


def main() -> int:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--frames", required=True)
  parser.add_argument("--touch")
  parser.add_argument("--control")
  args = parser.parse_args()
  return run(args.frames, args.touch, args.control)


if __name__ == "__main__":
  raise SystemExit(main())
