"""Current-schema peers for the retained actual SelfdriveD step; no Params shim."""

from unittest.mock import patch
from openpilot.cereal import messaging


def selfdrive_step(sd, published, native, previous_controls, cp, safety, now, *, calibration, driver_lockout=False,
                   driver_monitoring_valid=True, queued=(), panda_health=None):
  samples = (*queued, published)
  cars = iter(sample['carState'] for sample in samples[1:])
  intents = iter(sample['aolIntentWire'] for sample in samples)

  def receive(sock):
    if queued and sock is sd.car_state_sock:
      return next(cars, None)
    if queued and sock is sd.aol_intent_companion.sock:
      return next(intents, None)
    return None

  peers = []
  for service in sd.sm.data:
    if service in ('alertDebug', 'lateralManeuverPlan', 'laneChangeAssistWire', 'starpilotLongitudinalPlan'):
      continue
    if service == 'aolIntentWire':
      peers.append(samples[0][service])
      continue
    if service == 'aolSafetyWire':
      peers.append(native.as_reader())
      continue
    if service in ('carControl', 'controlsState'):
      if service in previous_controls:
        peers.append(previous_controls[service])
      continue
    if service == 'pandaStates' and panda_health is not None:
      peers.append(panda_health)
      continue
    size = 1 if service == 'pandaStates' else None
    event = messaging.new_message(service, size, valid=True, logMonoTime=now)
    value = getattr(event, service)
    if service == 'pandaStates':
      value[0].safetyModel = cp.safetyConfigs[0].safetyModel
      value[0].safetyParam = cp.safetyConfigs[0].safetyParam
      value[0].alternativeExperience = cp.alternativeExperience
      value[0].controlsAllowed = bool(safety.get_controls_allowed())
      value[0].safetyRxChecksInvalid = not bool(safety.safety_config_valid())
    elif service == 'driverMonitoringState':
      value.lockout = driver_lockout
      event.valid = driver_monitoring_valid
    elif service == 'extrinsicsCalibration':
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
    patch('openpilot.selfdrive.selfdrived.selfdrived.messaging.recv_one_or_none', side_effect=receive),
    patch('openpilot.selfdrive.selfdrived.selfdrived.messaging.recv_one', return_value=samples[0]['carState']),
    patch.object(sd.sm, 'update', side_effect=lambda _: sd.sm.update_msgs(now / 1e9, peers)),
    patch.object(sd.pm, 'send', side_effect=capture),
  ):
    sd.step()
  return output
