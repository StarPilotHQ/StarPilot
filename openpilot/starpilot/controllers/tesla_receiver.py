"""Tesla input worker: bounded UI dispatch; raw CAN decoding lives in a separate process."""
from openpilot.common.swaglog import cloudlog as LOGGER
import os
import socket
import subprocess
import sys
import time

from openpilot.starpilot.common.car_control_config import (
  effective_bindings, NO_LIMIT_REASON,
  PARAM, MAX_ASSIGNMENT_BYTES, parse_assignments, vehicle_supported, screen_reserved, SPEED_UP, SPEED_DOWN, SPEED_RESUME,
)
from openpilot.starpilot.saved_source import read_saved
from openpilot.starpilot.controllers.wheel_actions import cp_fingerprint
from openpilot.starpilot.controllers.tesla_input_protocol import (
  MAX_PACKET, MAX_EVENTS, READY, decode_event, document_hash, drive_id,
)

# A Resume press during a short speed-limit dropout waits this long for the limit to come back, then runs once.
RESUME_WAIT_SECONDS = 2.



class WorkerChannel:
  """An inherited private socketpair: no listening port, file socket or network."""
  def __init__(self):
    self.ready = False
    self.socket, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    self.socket.setblocking(False)
    try:
      self.process = subprocess.Popen(
        [sys.executable, '-B', '-m', 'openpilot.starpilot.controllers.tesla_input_worker',
         str(child.fileno()), str(os.getpid())],
        pass_fds=(child.fileno(),), stdin=subprocess.DEVNULL,
        # The ordinary process logger retains failures; never pipe unconsumed output.
        close_fds=True,
      )
    except BaseException:
      self.socket.close()
      raise
    finally:
      child.close()

  def receive(self):
    if self.process.poll() is not None:
      raise EOFError('Tesla input worker exited')
    try:
      value = self.socket.recv(MAX_PACKET + 1)
    except BlockingIOError:
      return None
    if not value:
      raise EOFError('Tesla input worker disconnected')
    if value == READY:
      self.ready = True
      return None
    return value

  def close(self):
    self.socket.close()
    if self.process.poll() is None:
      self.process.terminate()
    # close is shutdown/failure only, never normal frame work.
    try:
      self.process.wait(timeout=.1)
    except subprocess.TimeoutExpired:
      self.process.kill()
      self.process.wait(timeout=.1)


class TeslaReceiver:
  def __init__(self, params, actions, vehicle, *, clock=time.monotonic,
               messages=lambda: None, channel_factory=WorkerChannel, speed_actions=None, keyed_actions=None):
    self.params, self.actions, self.vehicle = params, actions, vehicle
    self.speed_actions = speed_actions
    # Looks up only the requested keys. The full catalog checks every settings
    # page (including the maps package), which took 2.3 s on a cold first press
    # and outlived MAX_AGE, so the press was dropped.
    self.keyed_actions = keyed_actions
    self._restart_drive = None
    self._restarts = 0
    self._diagnostic_at = 0.
    self.clock, self.messages, self.channel_factory = clock, messages, channel_factory
    self.channel = None
    self.sequence = 0
    self.next_start = 0.
    self.failed = False
    # (event, assignment document, deadline) of a Resume press waiting for the speed limit to come back.
    self._waiting_resume = None
    # A scroll step the UI refused to send (nothing was published), tried once more on the next poll.
    self._retry_step = None
    self._start()

  def _start(self):
    try:
      self.channel = self.channel_factory()
      self.sequence = 0
      self.failed = False
    except (OSError, RuntimeError):
      LOGGER.exception('Tesla input worker could not start')
      self.failed = True
    self.next_start = self.clock() + 2.

  def close(self):
    if self.channel is not None:
      channel, self.channel = self.channel, None
      channel.close()

  def drive(self):
    return drive_id(self.messages(), self.clock())

  def _offroad(self):
    sm = self.messages()
    now = self.clock()
    try:
      return bool(sm.seen['deviceState'] and sm.alive['deviceState'] and sm.valid['deviceState']
                  and not sm['deviceState'].started
                  and 0 <= now - sm.logMonoTime['deviceState'] / 1e9 <= 1.
                  and 0 <= now - sm.recv_time['deviceState'] <= 1.)
    except (AttributeError, KeyError, TypeError, ValueError):
      return False

  def poll(self):
    if self.channel is None:
      # Receive-only recovery is bounded per drive; a new worker must observe
      # a fresh neutral gesture and exact current authority before dispatch.
      drive = self.drive()
      if drive != self._restart_drive and drive is not None:
        self._restart_drive, self._restarts = drive, 0
      if self.clock() >= self.next_start and (self._offroad() or drive is not None and self._restarts < 3):
        self._restarts += 1
        self._start()
      return
    try:
      retry, self._retry_step = self._retry_step, None
      if retry is not None:
        self._dispatch(retry, retry=True)
      packets = []
      for _ in range(MAX_EVENTS + 1):
        raw = self.channel.receive()
        if raw is None:
          break
        packets.append(raw)
      if len(packets) > MAX_EVENTS:
        # Never replay a backlog across frames. Discard and reset the worker.
        raise RuntimeError('Tesla input event backlog')
      for raw in packets:
        self._dispatch(raw)
      self._retry_resume()
    except (OSError, EOFError, RuntimeError):
      LOGGER.exception('Tesla input worker stopped; bounded fresh-context recovery scheduled')
      self.failed = True
      self.close()
      self.next_start = self.clock() + 2.

  def _dispatch(self, raw, *, retry=False):
    event = decode_event(raw, self.clock())
    if event is None or (event['sequence'] <= self.sequence and not retry):
      self._diagnostic('invalid_expired_or_duplicate', event or {})
      return
    self.sequence = max(self.sequence, event['sequence'])
    cp = self.vehicle()
    if not vehicle_supported(cp) or self.drive() != event['drive']:
      self._diagnostic('vehicle_or_drive_unavailable', event)
      return
    if read_saved(self.params, 'SafeMode', 8) not in ((None, True), (b'0', True)):
      return
    current, readable = read_saved(self.params, PARAM, MAX_ASSIGNMENT_BYTES)
    if not readable or document_hash(current or b'') != event['document']:
      self._diagnostic('assignment_changed_or_unreadable', event)
      return
    doc = parse_assignments(current)
    if not doc or doc['vehicle'] not in ('', cp.carFingerprint) or cp_fingerprint(cp) != event['vehicle']:
      self._diagnostic('assignment_vehicle_identity_mismatch', event)
      return
    name = event['name']
    if name == 'screen_three_finger_tap' and screen_reserved(cp):
      return
    binding = effective_bindings(doc).get(name)
    if binding is None:
      return
    # The comma-speed actions (scroll steps and Resume Speed Limit + Offset) come from their own quick lookup, which
    # reads the car's messages with a button's age limit; the favorites catalog is for everything else.
    if binding['key'] in (SPEED_UP, SPEED_DOWN, SPEED_RESUME) and self.speed_actions is not None:
      action = self.speed_actions().get(binding['key'])
    elif self.keyed_actions is not None:
      action = self.keyed_actions({binding['key']}).get(binding['key'])
    else:
      action = self.actions().get(binding['key'])
    if action is None or not action.available:
      if (binding['key'] == SPEED_RESUME and binding['value'] is None and self.speed_actions is not None and
          getattr(action, 'reason', '') == NO_LIMIT_REASON):
        # The limit drops out for a second or two at a time: wait for it rather than refuse the press.
        self._waiting_resume = (event, current, self.clock() + RESUME_WAIT_SECONDS)
        self._diagnostic('resume_waiting_for_limit', event)
        return
      self._diagnostic('action_unavailable', event, getattr(action, 'reason', 'not found'))
      return
    # Action discovery may perform work. Recheck exact context at dispatch.
    if (decode_event(raw, self.clock()) is None or self.drive() != event['drive']
        or read_saved(self.params, PARAM, MAX_ASSIGNMENT_BYTES) != (current, True)
        or read_saved(self.params, 'SafeMode', 8) not in ((None, True), (b'0', True))
        or not vehicle_supported(self.vehicle()) or cp_fingerprint(self.vehicle()) != event['vehicle']):
      self._diagnostic('context_changed_or_expired', event)
      return
    value = binding['value']
    if binding['key'] in (SPEED_UP, SPEED_DOWN):
      if value is None and action.invoke_value is not None:
        from openpilot.starpilot.controllers.tesla_gesture_context import source_scope
        with source_scope(event):
          applied = action.invoke_value(1 if event['fast'] else 0)
        if applied is False and not retry:
          # Refused before anything was published (a live check failed this frame), so a retry cannot repeat the
          # step. Once only, and only while the event is still fresh.
          self._retry_step = raw
          self._diagnostic('retry_next_frame', event)
        else:
          self._diagnostic('dispatched' if applied is not False else 'publisher_rejected', event)
    elif value is None:
      if action.invoke is not None:
        if binding['key'] == SPEED_RESUME:
          self._waiting_resume = None
        applied = action.invoke()
        if binding['key'] == SPEED_RESUME:
          self._diagnostic('dispatched' if applied is not False else 'publisher_rejected', event)
    elif action.invoke_value is not None:
      action.invoke_value(value)

  def _retry_resume(self):
    """Run a Resume press that arrived during a speed-limit dropout once the limit is back, at most once and only
    within RESUME_WAIT_SECONDS of the press, in the same drive with the same assignments and vehicle."""
    if self._waiting_resume is None:
      return
    event, document, deadline = self._waiting_resume
    if self.clock() > deadline:
      self._waiting_resume = None
      self._diagnostic('action_unavailable', event, NO_LIMIT_REASON)
      return
    cp = self.vehicle()
    if (self.drive() != event['drive'] or read_saved(self.params, PARAM, MAX_ASSIGNMENT_BYTES) != (document, True)
        or read_saved(self.params, 'SafeMode', 8) not in ((None, True), (b'0', True))
        or not vehicle_supported(cp) or cp_fingerprint(cp) != event['vehicle'] or self.speed_actions is None):
      self._waiting_resume = None
      self._diagnostic('context_changed_or_expired', event)
      return
    action = self.speed_actions().get(SPEED_RESUME)
    if action is not None and not action.available and getattr(action, 'reason', '') == NO_LIMIT_REASON:
      return
    self._waiting_resume = None
    if action is None or not action.available or action.invoke is None:
      self._diagnostic('action_unavailable', event, getattr(action, 'reason', 'not found'))
      return
    applied = action.invoke()
    self._diagnostic('dispatched' if applied is not False else 'publisher_rejected', event)


  def _diagnostic(self, reason, event, detail=''):
    now = self.clock()
    if now < self._diagnostic_at:
      return
    self._diagnostic_at = now + .1
    LOGGER.info({'event':'tesla_speed_input', 'reason':reason, 'input':event.get('name',''), 'detail':detail,
                 'sequence':event.get('sequence',0), 'age_ms':round((now-event.get('stamp',now))*1000, 1)})
