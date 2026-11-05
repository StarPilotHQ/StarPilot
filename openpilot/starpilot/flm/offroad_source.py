"""Cheap onroad settings/calibration receipts; all fitting runs parked.

No fitting or tune writes run here; one durable provisional-use claim precedes publication.
The original Controls source/native/pose/clock gates stay authoritative.
"""

import time
import uuid

from openpilot.cereal import messaging


def record_drive(params, *, stopped, on_status=None):
  from opendbc.car import structs as car
  from openpilot.starpilot.flm.calibration_lifecycle import ProducerCalibrationLifecycle
  from openpilot.starpilot.flm.runtime_adapter_registry import read_source, car_params_digest
  from openpilot.starpilot.flm.learning_wire import result_message, write_source_receipt
  from openpilot.starpilot.flm.steerlearnerd import _configuration_calibration

  cp_raw = params.get('CarParams')
  if not cp_raw or stopped() or params.get_bool('IsOffroad'):
    return
  CP = messaging.log_from_bytes(cp_raw, car.CarParams)
  cp_sha = car_params_digest(CP)
  session = uuid.uuid4().hex
  from openpilot.starpilot.flm.offroad_cache import directory_for_params
  from openpilot.starpilot.flm.offroad_quality import from_configuration
  from openpilot.starpilot.flm.learning_wire import read_control

  guard = from_configuration(CP, params, directory_for_params(params), vehicle_session=session)

  class OriginalReceiver(messaging.SubMaster):
    def __init__(self):
      self.original = {}
      super().__init__(['extrinsicsCalibration', 'controlsState', 'carState', 'carControl', 'starpilotLateralState'])

    def update_msgs(self, cur_time, msgs):
      packets = tuple(msgs)
      for packet in packets:
        if packet is not None:
          self.original[packet.which()] = packet
      super().update_msgs(cur_time, packets)

  sm = OriginalReceiver()
  pm = messaging.PubMaster(['autoSteeringLearning'])
  calibration = ProducerCalibrationLifecycle()
  sequence = 0
  next_publish = 0
  last_quality_header = 0
  try:
    while not stopped() and not params.get_bool('IsOffroad'):
      sm.update(50)
      now = time.monotonic_ns()
      quality_header = int(sm.logMonoTime['controlsState'])
      if guard is not None and guard.stopped is None and sm.updated['controlsState'] and quality_header - last_quality_header >= 25_000_000:
        last_quality_header = quality_header
        packet = sm.original.get('starpilotLateralState')
        try:
          control = None if packet is None else read_control(packet)
        except (ValueError, KeyError, TypeError, AttributeError, OverflowError):
          control = None
        guard.observe(sm.original['controlsState'], sm.original, control, now_ns=now)
      if now < next_publish:
        continue
      frame = _configuration_calibration(sm, calibration, now)
      current = read_source(
        CP,
        params,
        now_ns=now,
        cp_sha256=cp_sha,
        calibration=() if frame is None else frame[1],
        vehicle_session=session,
        calibration_generation='' if frame is None else frame[0],
      )
      if stopped() or params.get_bool('IsOffroad'):
        break
      publication_now = time.monotonic_ns()
      # Failed/stale settings work never becomes a new successful receipt.
      if not current.observed_ns <= publication_now <= current.expires_ns:
        next_publish += ((publication_now - next_publish) // 250_000_000 + 1) * 250_000_000
        continue
      sequence += 1
      message = result_message(None, session, sequence, publication_now)
      message.autoSteeringLearning.learning.reason = guard.marker if guard is not None and guard.marker is not None else 'recording_for_offroad_analysis'
      write_source_receipt(message, current)
      pm.send('autoSteeringLearning', message)
      if on_status is not None:
        packet = sm.original.get('starpilotLateralState')
        try:
          actual = None if packet is None else read_control(packet)
        except (ValueError, KeyError, TypeError, AttributeError, OverflowError):
          actual = None
        if (
          actual is None
          or not actual.valid
          or not actual.active
          or actual.manual
          or actual.override
          or actual.fault
          or actual.limited
          or not 0 < actual.control_ns <= publication_now <= actual.control_ns + 100_000_000
          or not actual.route_applied_coordinates
        ):
          actual = None
        on_status(bool(current.enabled), actual, guard is not None and guard.marker is not None)
      next_publish = publication_now + 250_000_000
  finally:
    for sock in getattr(sm, 'sock', {}).values():
      close = getattr(sock, 'close', None)
      if close is not None:
        close()
    for sock in getattr(pm, 'sock', {}).values():
      close = getattr(sock, 'close', None)
      if close is not None:
        close()
