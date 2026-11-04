"""Current-schema peers for the retained actual SelfdriveD step; no Params shim."""

from unittest.mock import patch
from openpilot.cereal import messaging


def selfdrive_step(sd, published, native, previous_controls, cp, safety, now, *, calibration, source_timeout=False):
  peers = []
  for service in sd.sm.data:
    if source_timeout and service in ('carState', 'carOutput', 'aolIntentWire'):
      continue
    if service in ('alertDebug', 'lateralManeuverPlan', 'laneChangeAssistWire', 'starpilotLongitudinalPlan'):
      continue
    if service == 'aolIntentWire':
      peers.append(published[service])
      continue
    if service == 'aolSafetyWire':
      if native is not None:
        peers.append(native.as_reader())
      continue
    if service == 'pandaStates':
      if service in published:
        peers.append(published[service])
      continue
    if service in published:
      peers.append(published[service])
      continue
    if service in ('carControl', 'controlsState'):
      if service in previous_controls:
        peers.append(previous_controls[service])
      continue
    size = 1 if service == 'pandaStates' else None
    event = messaging.new_message(service, size, valid=True, logMonoTime=now)
    value = getattr(event, service)
    if service == 'extrinsicsCalibration':
      value.calStatus, value.rpyCalib = calibration, [0.0, 0.0, 0.0]
    elif service == 'vehicleParameters':
      value.stiffnessFactor, value.steerRatio, value.valid = 1.0, cp.steerRatio, True
    elif service == 'deviceMotion':
      value.inputsOK = value.posenetOK = True
      value.velocityDevice.x = published['carState'].carState.vEgo
    elif service == 'deviceState':
      value.freeSpacePercent = 50.0
      value.started, value.startedMonoTime = True, 500_000_000
    peers.append(event.as_reader())
  output = {}

  def capture(service, message):
    # Snapshot only a real producer send, once; retain its source timestamp.
    wire = message if isinstance(message, bytes) else message.to_bytes()
    output[service] = messaging.log_from_bytes(wire)

  real_new_message = messaging.new_message

  def stamped_message(service=None, size=None, **kwargs):
    return real_new_message(service, size, **({'logMonoTime': now} | kwargs))

  with (
    patch('openpilot.selfdrive.selfdrived.selfdrived.messaging.new_message', side_effect=stamped_message),
    patch('openpilot.selfdrive.selfdrived.selfdrived.REPLAY', False),
    patch('openpilot.selfdrive.selfdrived.selfdrived.time.monotonic_ns', return_value=now),
    patch('openpilot.selfdrive.selfdrived.selfdrived.time.monotonic', return_value=now / 1e9),
    patch('openpilot.selfdrive.selfdrived.selfdrived.time.clock_gettime_ns', return_value=now),
    patch('openpilot.selfdrive.selfdrived.selfdrived.messaging.recv_one_or_none', return_value=None),
    patch('openpilot.selfdrive.selfdrived.selfdrived.messaging.recv_one', return_value=None if source_timeout else published['carState']),
    patch.object(sd.sm, 'update', side_effect=lambda _: sd.sm.update_msgs(now / 1e9, peers)),
    patch.object(sd.pm, 'send', side_effect=capture),
  ):
    sd.step()
  return output
