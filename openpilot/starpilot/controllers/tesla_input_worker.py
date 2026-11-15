"""Tesla input worker. Receive-only CAN; never invokes vehicle/UI actions."""
import os
import select
import secrets
import socket
import sys
import time

def configure_worker():
  # A subprocess can inherit UI FIFO policy/affinity at startup. This is solely
  # the new custom worker's policy, not a change to any stock process.
  os.sched_setscheduler(0, os.SCHED_OTHER, os.sched_param(0))
  os.setpriority(os.PRIO_PROCESS, 0, 10)
  os.sched_setaffinity(0, {0, 1, 2, 3})


if __name__ == '__main__':
  configure_worker()

from openpilot.starpilot.common.car_control_config import (PARAM, MAX_ASSIGNMENT_BYTES, STALK_HOLD, STALK_TAP, STALK_TAP_FULL,
                                                           effective_bindings, parse_assignments, vehicle_supported,
                                                           screen_reserved)
from openpilot.starpilot.saved_source import read_saved
from openpilot.starpilot.controllers.tesla_controls import TeslaInputDecoder
from openpilot.starpilot.controllers.tesla_input_protocol import MAX_AGE, READY, document_hash, drive_id, encode_event
from openpilot.starpilot.controllers.wheel_actions import cp_fingerprint


class InputPump:
  def __init__(self, params, send, *, clock=time.monotonic, socket_factory=None, receive=None, boot_clock_ns=None):
    self.params, self.send, self.clock = params, send, clock
    self.boot_clock_ns = boot_clock_ns or (lambda: time.clock_gettime_ns(time.CLOCK_BOOTTIME))
    self.worker_session = secrets.token_hex(16)
    self.sync_provenance = False
    self.socket_factory, self.receive = socket_factory, receive
    self.socket = None
    self.decoder = None
    self.identity = None
    self.sequence = 0
    self.raw = None
    self.document = None
    self.cp = None
    self.cp_hash = None

  def reset(self):
    self.socket = None
    self.decoder = None
    self.identity = None

  def set_vehicle(self, cp):
    fingerprint = cp_fingerprint(cp) if vehicle_supported(cp) else None
    provenance = bool(cp is not None and any(int(c.safetyParam) & 2048 for c in getattr(cp, 'safetyConfigs', ())))
    if fingerprint != self.cp_hash or provenance != self.sync_provenance:
      self.reset()
    self.cp = cp
    self.sync_provenance = provenance
    self.cp_hash = fingerprint

  def poll(self, sm):
    cp = self.cp
    now = self.clock()
    drive = drive_id(sm, now)
    if (not self.cp_hash or drive is None or
        read_saved(self.params, 'SafeMode', 8) not in ((None, True), (b'0', True))):
      self.reset()
      return
    raw, readable = read_saved(self.params, PARAM, MAX_ASSIGNMENT_BYTES)
    if raw != self.raw:
      self.raw = raw
      self.document = parse_assignments(raw) if readable else None
    doc = self.document if readable else None
    # Nothing saved yet still has the fixed and default bindings (the right stalk hold and half press).
    bindings = effective_bindings(doc) if doc and doc['vehicle'] in ('', cp.carFingerprint) else {}
    if not bindings:
      self.reset()
      return
    identity = (raw, self.cp_hash, drive)
    if identity != self.identity:
      self.reset()
      self.identity = identity
      from opendbc.car.tesla.values import CANBUS
      self.decoder = TeslaInputDecoder(CANBUS.vehicle, stalk_hold=STALK_HOLD in bindings,
                                       stalk_tap=STALK_TAP in bindings, stalk_tap_full=STALK_TAP_FULL in bindings)
      self.socket = self.socket_factory()
    try:
      self.decoder.cruise_engaged = bool(sm['carState'].cruiseState.enabled)
    except (AttributeError, KeyError, TypeError):
      self.decoder.cruise_engaged = False
    count = 0
    # A fixed per-tick bound prevents an unending drain. If still overloaded on
    # following ticks, original producer timestamps expire and reset the decoder.
    for _ in range(256):
      event = self.receive(self.socket)
      if event is None:
        break
      source_boot_ns = int(event.logMonoTime)
      stamp = source_boot_ns / 1e9
      if self.sync_provenance:
        # Keep the raw integer source stamp; only the private UI freshness stamp
        # is translated to MONOTONIC. Never compare BOOTTIME against MONOTONIC.
        before = self.clock()
        boot = self.boot_clock_ns()
        after = self.clock()
        if not (0 <= after - before <= .005 and 0 < source_boot_ns <= boot and
                boot - source_boot_ns <= int(MAX_AGE * 1e9)):
          self.decoder.reset()
          continue
        stamp = after - (boot - source_boot_ns) / 1e9
      if not event.valid or not 0 <= self.clock() - stamp <= MAX_AGE:
        self.decoder.reset()
        continue
      for frame_index, frame in enumerate(event.can):
        # These checks precede payload conversion and decoder access.
        if frame.src != self.decoder.bus or frame.address not in self.decoder.groups:
          continue
        for name, fast in self.decoder.feed(frame.address, bytes(frame.dat), frame.src, stamp, details=True):
          if name == 'screen_three_finger_tap' and screen_reserved(cp):
            continue
          if name not in bindings or count >= 4:
            continue
          if (read_saved(self.params, PARAM, MAX_ASSIGNMENT_BYTES) != (raw, True)
              or read_saved(self.params, 'SafeMode', 8) not in ((None, True), (b'0', True))
              or drive_id(sm, self.clock()) != drive or self.clock() - stamp > MAX_AGE):
            self.reset()
            return
          self.sequence += 1
          provenance = None
          if self.sync_provenance and name in ("right_wheel_up", "right_wheel_down"):
            from openpilot.starpilot.controllers.tesla_gesture_context import GestureSource
            provenance = GestureSource(self.worker_session, self.sequence, source_boot_ns, frame_index,
                                       name, fast, document_hash(raw or b''), self.cp_hash, drive).to_wire()
          packet = encode_event(self.sequence, name, fast, stamp, drive, document_hash(raw or b''), self.cp_hash,
                                provenance=provenance)
          try:
            self.send(packet)
          except (BlockingIOError, OSError):
            # No retry of an input action; fail the worker on a full/disconnected IPC channel.
            self.reset()
            raise
          count += 1


def main(fd, parent):
  from openpilot.cereal import messaging
  from openpilot.common.params import Params

  channel = socket.socket(fileno=fd)
  channel.setblocking(False)
  sm = messaging.SubMaster(['deviceState', 'carState', 'carParams'])
  pump = InputPump(Params(), channel.send,
                   socket_factory=lambda: messaging.sub_sock('can'),
                   receive=lambda sock: messaging.recv_one_or_none(sock))
  channel.send(READY)
  try:
    while os.getppid() == parent:
      # Closure is observed even when no gestures occur. No UI heartbeat writes.
      if select.select([channel], [], [], 0)[0]:
        break
      sm.update(0)
      if sm.updated['carParams']:
        pump.set_vehicle(sm['carParams'].as_builder().as_reader())
      pump.poll(sm)
      time.sleep(.01)
  finally:
    pump.reset()
    channel.close()


if __name__ == '__main__':
  from openpilot.common.swaglog import cloudlog
  try:
    main(int(sys.argv[1]), int(sys.argv[2]))
  except Exception:
    cloudlog.exception('Tesla input worker failed')
    raise

