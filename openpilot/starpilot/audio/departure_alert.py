"""Optional stopped-traffic notices from the existing qualified scene projector."""

import math
import time

from openpilot.starpilot.saved_source import read_saved

KEYS = ('GreenLightAlert', 'LeadDepartingAlert')
TOGGLES = (
  (KEYS[0], 'Green Light Alert', 'Chimes when the model’s traffic-light stop prediction clears while you remain stopped.'),
  (KEYS[1], 'Lead Departing Alert', 'Chimes when the tracked vehicle ahead starts moving while you remain stopped.'),
)
DURATION_NS = 3_000_000_000
EVENT_TYPE = 'departureNotice'
ALERT_TYPES = ('greenLight/warning', 'leadDeparting/warning')


class DepartureEpisode:
  def __init__(self):
    self.reset()

  def reset(self):
    self.drive = 0
    self.last_model = 0
    self.light_armed = False
    self.sign_seen = False
    self.lead_distance = None
    self.lead_id = None
    self.fired = False
    self.notice = (0, 0)

  def step(self, *, drive, model_ns, now_ns, stationary, valid, light, model_stopped, tracked,
           distance, lead_speed, lead_id, enabled, sign=False):
    if drive != self.drive:
      self.reset()
      self.drive = drive
    elif model_ns < self.last_model:
      self.light_armed = False
      self.lead_distance = self.lead_id = None
      return (0, 0)
    if not stationary:
      self.sign_seen = False
      self.light_armed = False
      self.lead_distance = self.lead_id = None
      self.fired = False
      self.notice = (0, 0)
    if not valid:
      self.light_armed = False
      self.lead_distance = self.lead_id = None
      return (0, 0)
    if model_ns <= self.last_model:
      return self.notice if now_ns - self.notice[1] < DURATION_NS else (0, 0)
    self.last_model = model_ns
    if stationary and not self.fired:
      self.sign_seen |= sign
      if tracked:
        self.light_armed = False
        if self.lead_id != lead_id:
          self.lead_distance = None
        self.lead_id = lead_id
        if self.lead_distance is None and lead_speed < 1.:
          self.lead_distance = distance
        if (enabled[1] and self.lead_distance is not None and distance - self.lead_distance >= 1. and lead_speed >= 1.):
          self.notice, self.fired = (2, model_ns), True
      else:
        self.lead_distance = self.lead_id = None
        if enabled[0] and self.light_armed and not model_stopped and not self.sign_seen:
          self.notice, self.fired = (1, model_ns), True
        self.light_armed = light and model_stopped and not self.sign_seen
    if (not stationary or not self.notice[0] or not enabled[self.notice[0] - 1] or
        not 0 <= now_ns - self.notice[1] < DURATION_NS):
      return (0, 0)
    return self.notice


class DepartureNotifications:
  def __init__(self, params=None):
    from openpilot.common.params import Params
    self.params = params if params is not None else Params()
    self.enabled = (False, False)
    self.next_refresh = 0
    self.projector = None
    self.episode = DepartureEpisode()
    self.drive = 0

  def sample(self, sm, cp, planner, *, clocks=None):
    now = clocks[0] if clocks is not None else time.monotonic_ns()
    if now >= self.next_refresh:
      self.next_refresh = now + 1_000_000_000
      values = [read_saved(self.params, k, 8) for k in (*KEYS, 'SafeMode')]
      enabled = tuple(raw == b'1' and readable for raw, readable in values[:2])
      safe, readable = values[2]
      if not readable or safe not in (None, b'0'):
        enabled = (False, False)
      if enabled != self.enabled:
        self.projector = None
        self.episode.reset()
      self.enabled = enabled
    if not any(self.enabled):
      return (0, 0)
    from openpilot.starpilot.conditional_mode.projection import (
      ConditionalOwnerContext, ObservedBool, ObservedFloat, SceneProjector, paired_clocks_ns,
    )
    from openpilot.starpilot.conditional_mode.preferences import CEMOptions
    from openpilot.starpilot.conditional_mode.planner_host import selected_follow_time
    from openpilot.starpilot.longitudinal.force_stop_runtime import dashboard_stop_sign
    pair = clocks if clocks is not None else paired_clocks_ns()
    if pair is None:
      self.projector = None
      self.episode.reset()
      return (0, 0)
    now, boot, skew = pair
    try:
      device, car = sm['deviceState'], sm['carState']
      drive, model_ns, car_ns = int(device.startedMonoTime), int(sm.logMonoTime['modelV2']), int(sm.logMonoTime['carState'])
      if (not device.started or not sm.all_checks(['deviceState']) or not drive < sm.logMonoTime['deviceState'] <= now or
          now - sm.logMonoTime['deviceState'] > 2_000_000_000 or cp.passive or cp.dashcamOnly or cp.notCar):
        self.projector = None
        self.episode.reset()
        return (0, 0)
      if drive != self.drive:
        self.drive, self.projector = drive, None
        self.episode.reset()
      if self.projector is None:
        self.projector = SceneProjector()
      # These scene owners are disabled for advisory perception; they never
      # acquire or release longitudinal authority or alter a control-mode owner.
      off = ObservedBool(False, car_ns)
      context = ConditionalOwnerContext(traffic_mode=off, stop_sign_confirmed=off, forcing_stop=off,
        dashboard_stop_sign=off, pedal_override=ObservedBool(bool(car.gasPressed), car_ns),
        model_stop_time_s=ObservedFloat(CEMOptions().model_stop_s, car_ns))
      projected = self.projector.project(sm, cp, now_mono_ns=now, now_boot_ns=boot, sample_skew_ns=skew,
        safe_mode=False, selected_t_follow_s=selected_follow_time(planner), selected_t_follow_observed_mono_ns=now,
        owner_context=context, signal_options=CEMOptions())
      lead, tracked = projected.raw_lead, projected.lead_observation.tracked
      light, stopped, action_stop = projected.scene.stop_light_detected, projected.raw_model_stopped, projected.current_model_should_stop
      stationary = bool(car.standstill and not car.gasPressed and str(car.gearShifter) == 'drive')
      valid = (all(k in projected.fresh_services for k in ('carState', 'modelV2', 'radarState')) and
               type(tracked) is bool and type(light) is bool and type(stopped) is bool and type(action_stop) is bool and
               car.canValid and not car.canTimeout and drive < model_ns <= now and now - model_ns <= 150_000_000)
      lead_speed = lead.speed_mps if lead is not None else None
      distance = lead.distance_m if lead is not None else None
      valid &= (not tracked or lead is not None and
                all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in (distance, lead_speed)))
      radar_id = int(sm['radarState'].leadOne.radarTrackId)
      dashboard_sign = dashboard_stop_sign(sm, now_ns=now, drive_id=drive)
      return self.episode.step(drive=drive, model_ns=model_ns, now_ns=now, stationary=stationary, valid=bool(valid),
        light=light, model_stopped=bool(stopped or action_stop), tracked=tracked, distance=distance, lead_speed=lead_speed,
        lead_id=(bool(sm['radarState'].leadOne.radar), radar_id), enabled=self.enabled, sign=dashboard_sign)
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError, RuntimeError):
      self.projector = None
      self.episode.reset()
      return (0, 0)


def departure_alerts(sm, cs, *, enabled, now_ns, car_ns, car_valid):
  try:
    name = 'starpilotLongitudinalPlan'
    if not car_valid or not cs.canValid or cs.canTimeout or not cs.standstill or cs.gasPressed or str(cs.gearShifter) != 'drive':
      return []
    if not 0 < car_ns <= now_ns or now_ns - car_ns > 150_000_000 or not sm.all_checks([name, 'deviceState']):
      return []
    value, device = sm[name], sm['deviceState']
    stamp, device_ns = int(sm.logMonoTime[name]), int(sm.logMonoTime['deviceState'])
    kind, event_ns = int(value.departureAlertKind), int(value.departureAlertMonoTime)
    if (kind not in (1, 2) or not enabled[kind - 1] or value.version != 1 or not device.started or
        value.driveStartMonoTime != device.startedMonoTime or
        not 0 < device.startedMonoTime < value.modelMonoTime <= value.sourcePlanMonoTime == stamp <= now_ns or
        now_ns - stamp > 150_000_000 or now_ns - value.modelMonoTime > 250_000_000 or
        not 0 < device_ns <= now_ns or now_ns - device_ns > 2_000_000_000 or
        not device.startedMonoTime < event_ns <= value.modelMonoTime or not 0 <= now_ns - event_ns < DURATION_NS):
      return []
    for service, limit in ((name, 150_000_000), ('deviceState', 2_000_000_000)):
      receipt = int(sm.recv_time[service] * 1e9)
      if not device.startedMonoTime < receipt <= now_ns or now_ns - receipt > limit:
        return []
  except (AttributeError, KeyError, TypeError, ValueError, OverflowError, RuntimeError):
    return []
  from openpilot.selfdrive.selfdrived.events import Alert, AlertSize, AlertStatus, AudibleAlert, Priority, VisualAlert
  alert = Alert('Green Light' if kind == 1 else 'Lead Vehicle Departing', '', AlertStatus.userPrompt,
                AlertSize.small, Priority.LOW, VisualAlert.none, AudibleAlert.prompt, (DURATION_NS - (now_ns - event_ns)) / 1e9)
  alert.alert_type, alert.event_type = ALERT_TYPES[kind - 1], EVENT_TYPE
  return [alert]


class DepartureAlertDelivery:
  def __init__(self):
    self.last = None
    self.active = False

  def sample(self, sm, cs, **kwargs):
    alerts = departure_alerts(sm, cs, **kwargs)
    if not alerts:
      self.active = False
      return []
    value = sm['starpilotLongitudinalPlan']
    key = (int(value.driveStartMonoTime), int(value.departureAlertMonoTime), int(value.departureAlertKind))
    if key == self.last:
      return alerts if self.active else []
    if self.last is not None and key[0] == self.last[0] and key[1] <= self.last[1]:
      return []
    self.last, self.active = key, True
    return alerts


def protect_safety_alerts(alerts, retained=()):
  from openpilot.selfdrive.selfdrived.events import AlertStatus, AudibleAlert, Priority, VisualAlert
  if any(a.alert_type not in ALERT_TYPES and
         (a.alert_status != AlertStatus.normal or a.audible_alert != AudibleAlert.none or a.visual_alert != VisualAlert.none or
          a.priority >= Priority.LOW and a.alert_type != 'forceStopHold/warning')
         for a in (*alerts, *retained)):
    return [a for a in alerts if a.alert_type not in ALERT_TYPES]
  return alerts
