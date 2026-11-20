"""Resident Small-model switching and a fail-closed engagement handshake.

All GPU allocation and artifact loading happen at startup on the model thread. The engagement owner acknowledges a hold
before modeld may change runners, and releases it only after fresh output.
"""
import time
import uuid
import math

from openpilot.starpilot.models.conditional_policy import SpeedPolicy
from openpilot.starpilot.models.conditional_protocol import (
  SWITCH_TIMEOUT_NS, State, Ack, EngagementHold, ResidentSwitch, fresh,
)


def modeld_idle(sm, now: int) -> bool:
  """Unknown, stale, paused/armed, stock cruise and either active axis all deny."""
  try:
    for name in ('selfdriveState', 'carControl', 'carState'):
      if not (sm.seen[name] and sm.valid[name] and sm.alive[name] and fresh(int(sm.logMonoTime[name]), now)):
        return False
    state, command, car = sm['selfdriveState'], sm['carControl'], sm['carState']
    if (state.enabled or state.active or str(state.state) != 'disabled' or
        command.enabled or command.latActive or command.longActive or car.cruiseState.enabled or
        not car.canValid or car.canTimeout or not math.isfinite(car.vEgo) or not 0 <= car.vEgo <= 100):
      return False
    # Selfdrived's ack additionally covers armed independent control. This
    # cheap frame-side check covers active axes before/after that handshake.
    return True
  except (AttributeError, KeyError, TypeError, ValueError):
    return False


class ConditionalModelGate:
  def __init__(self):
    from openpilot.cereal import messaging
    self.sm = messaging.SubMaster(['conditionalModelState'])
    self.pm = messaging.PubMaster(['conditionalModelAck'])
    self.hold = EngagementHold()

  def alert(self):
    from openpilot.starpilot.models.conditional_alerts import conditional_model_alert
    state = self.sm['conditionalModelState']
    stamp = int(self.sm.logMonoTime['conditionalModelState'])
    now = time.monotonic_ns()
    valid = bool(self.sm.seen['conditionalModelState'] and self.sm.valid['conditionalModelState'] and fresh(stamp, now))
    return conditional_model_alert(state if valid else None, holding=self.hold.owner is not None,
                           stalled=bool(self.hold.owner and now - self.hold.held_since >= SWITCH_TIMEOUT_NS))

  def update_alerts(self, alerts, manager, frame=0):
    from openpilot.starpilot.models.conditional_alerts import ALERT_TYPES
    for name in ALERT_TYPES:
      manager.alerts.pop(name, None)
    notice = self.alert()
    retained = (entry.alert for entry in manager.alerts.values() if entry.alert is not None and entry.active(frame))
    if notice is not None and not any(alert.priority >= notice.priority for alert in (*alerts, *retained)):
      alerts.append(notice)

  def step(self, drive, car) -> bool:
    from openpilot.cereal import log, messaging
    from openpilot.starpilot.aol.runtime import current_intent
    from openpilot.selfdrive.selfdrived.events import ET
    self.sm.update(0)
    now = time.monotonic_ns()
    state = self.sm['conditionalModelState']
    stamp = int(self.sm.logMonoTime['conditionalModelState'])
    valid = self.sm.seen['conditionalModelState'] and self.sm.valid['conditionalModelState'] and self.sm.alive['conditionalModelState']
    wire = State(str(state.session), str(state.requestId), str(state.phase), int(state.frameMonoTime), int(state.sessionStartMonoTime)) if valid else None
    axes = getattr(drive, 'aol_axis_decision', None)
    idle = (drive.initialized and getattr(drive, 'conditional_car_state_valid', False) and
            not drive.enabled and not drive.active and drive.state_machine.state == log.SelfdriveState.OpenpilotState.disabled and
            car.canValid and not car.canTimeout and not car.cruiseState.enabled and
            not any(getattr(axes, field, False) for field in
                    ('desired_lateral', 'desired_longitudinal', 'lateral_active', 'longitudinal_active')))
    if drive.aol_replay:
      intent = current_intent(drive.sm, car_state_ns=drive.aol_car_state_log_ns, now_ns=now,
                              previous=getattr(drive, 'aol_last_intent', None), companion=getattr(drive, 'aol_intent_companion', None), drain=False)
      idle = bool(idle and intent is not None and not intent.allowedLatch and not intent.lateralArmed)
    model_valid = bool(drive.sm.valid['modelV2'] and drive.sm.alive['modelV2'])
    events = getattr(drive, 'events', None)
    holding = self.hold.step(wire, stamp, now, idle=idle, model_stamp=int(drive.sm.logMonoTime['modelV2']), model_valid=model_valid,
                             release_allowed=events is None or not events.contains(ET.ENABLE))
    message = messaging.new_message('conditionalModelAck', valid=True)
    message.conditionalModelAck = {'session': str(state.session), 'requestId': str(state.requestId),
                           'holding': holding and self.hold.owner == (str(state.session), str(state.requestId)),
                           'stateMonoTime': stamp, 'idle': idle}
    self.pm.send('conditionalModelAck', message)
    return holding


class ConditionalModelRuntime:
  """Startup-only allocation, frame-boundary selection, one existing publisher."""
  def __init__(self, model, prepared, config, *, root, cam_w, cam_h, enabled):
    import os
    from openpilot.common.swaglog import cloudlog
    from openpilot.cereal import messaging
    from openpilot.starpilot.models.manager import catalog, verified_artifact, shipped_default
    from openpilot.starpilot.models.catalog import DEFAULT_SMALL, DEFAULT_SMALL_SHA256
    from openpilot.starpilot.models.receipt import process_start_ticks
    from openpilot.starpilot.models.runner import load_verified_model
    self.config, self.pid = config, os.getpid()
    self.ticks = process_start_ticks(self.pid)
    self.ack = messaging.SubMaster(['conditionalModelAck'])
    self.policy = SpeedPolicy(config['thresholdMph'])
    self.paused = False
    self.last_status = None
    self.unavailable_reason = ''
    models, error = {}, ''
    if enabled and config['enabled']:
      try:
        if model.model_id != config['city']:
          raise ValueError('Selected model fell back; resident switching unavailable for this drive')
        models[model.model_id] = model, prepared, model.handoff_reset()
        entries = catalog(root)
        for mid in {config['highway'], config['city']} - {model.model_id}:
          path = verified_artifact(mid, entries, root)
          shipped = mid == DEFAULT_SMALL and path is None
          if shipped:
            path = shipped_default()
          if path is None:
            raise ValueError('Alternate Small artifact is missing or corrupt')
          # Disk size does not qualify GPU residency. Preserve substantial host
          # headroom; target-device pressure/timing still needs bench evidence.
          from pathlib import Path
          memory = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
          available = int(memory['MemAvailable'].split()[0]) * 1024
          if available < 512 * 1024**2 + 4 * path.stat().st_size:
            raise MemoryError('Insufficient memory headroom to preload the alternate model')
          other, artifact = load_verified_model(cam_w, cam_h, path, entries[mid]['version'], False, mid,
                                               DEFAULT_SMALL_SHA256 if shipped else entries[mid]['artifact_sha256'], root=root)
          models[mid] = other, artifact, other.handoff_reset()
      except Exception as exc:
        cloudlog.exception('Conditional Model preload failed; keeping the current Small model')
        models, error = {}, str(exc)[:300]
    elif config['enabled']:
      self.unavailable_reason = 'Conditional Model is inactive while an external or Split Model is in use'
    self.owner = ResidentSwitch(models, model.model_id, now=time.monotonic_ns(), error=error)

  def before_frame(self, sm):
    self.ack.update(0)
    now = time.monotonic_ns()
    a = self.ack['conditionalModelAck']
    ack = (Ack(str(a.session), str(a.requestId), bool(a.holding), int(a.stateMonoTime), int(self.ack.logMonoTime['conditionalModelAck']), bool(a.idle))
           if self.ack.seen['conditionalModelAck'] and self.ack.valid['conditionalModelAck'] and self.ack.alive['conditionalModelAck'] else None)
    idle = bool(modeld_idle(sm, now) and ack and ack.idle and fresh(ack.stamp, now))
    desired = None
    if self.owner.models:
      # A failure pauses automation for the drive; no repeated load/reset loop.
      self.paused |= bool(self.owner.error)
      current = 'highway' if self.owner.active == self.config['highway'] else 'city'
      car = sm['carState']
      role = self.policy.sample(float(car.vEgo), int(sm.logMonoTime['carState']), now,
                                valid=bool(sm.seen['carState'] and sm.valid['carState'] and sm.alive['carState'] and
                                           car.canValid and not car.canTimeout), current=current)
      if (role and not self.paused and self.owner.phase == 'ready' and idle and not ack.holding and
          fresh(self.owner.frame, now) and self.config[role] != self.owner.active):
        desired = {'session': self.owner.session, 'id': uuid.uuid4().hex, 'model': self.config[role]}
    changed = self.owner.before_frame(desired, now=now, idle=idle, ack=ack)
    if changed:
      self.policy.switched(now)
    status = (self.owner.request, self.owner.phase, self.owner.error)
    if status != self.last_status:
      from openpilot.common.swaglog import cloudlog
      cloudlog.event('conditionalModelState', request=status[0], phase=status[1], error=status[2], model_id=self.owner.active)
      self.last_status = status
    return self.owner.models[self.owner.active] if changed else None

  def publish(self, pm, sm, frame):
    from openpilot.cereal import messaging
    now = time.monotonic_ns()
    if frame:
      self.owner.published(frame, now)
    message = messaging.new_message('conditionalModelState', valid=True)
    owner = self.owner
    ack_idle = bool(self.ack.seen['conditionalModelAck'] and self.ack.valid['conditionalModelAck'] and self.ack.alive['conditionalModelAck'] and
                    fresh(int(self.ack.logMonoTime['conditionalModelAck']), now) and
                    self.ack['conditionalModelAck'].idle and not self.ack['conditionalModelAck'].holding)
    message.conditionalModelState = {'session': owner.session, 'requestId': owner.request, 'phase': owner.phase,
                            'activeModel': owner.active, 'highwayModel': self.config['highway'], 'cityModel': self.config['city'],
                            'error': owner.error, 'frameMonoTime': owner.frame,
                            'canSwitch': bool(owner.models and owner.phase == 'ready' and modeld_idle(sm, now) and ack_idle),
                            'processPid': self.pid, 'processStartTicks': self.ticks, 'sessionStartMonoTime': owner.born,
                            'transitionSeconds': owner.elapsed, 'fallbackModel': owner.previous,
                            'paused': self.paused or bool(owner.error),
                            'targetMode': self.policy.hint, 'thresholdMph': self.config['thresholdMph'],
                            'unavailableReason': self.unavailable_reason}
    if owner.phase == 'waiting' and not owner.announced:
      owner.announced = int(message.logMonoTime)
    pm.send('conditionalModelState', message)
