import hashlib
import re
import secrets
from dataclasses import dataclass
from functools import lru_cache

from opendbc.car import structs
from openpilot.starpilot.conditional_mode.manual import Button, ButtonTracker, Press, ioniq6_media_eligible
from openpilot.starpilot.saved_source import read_saved
from openpilot.starpilot.conditional_mode.ui_action import fresh_service


ACTIONS = {
  "Off": 0, "Cycle personality": 1, "Force coast": 2, "Pause steering": 3, "Pause longitudinal": 4,
  "Experimental override": 5, "Traffic mode": 6, "Switchback Mode": 7, "Bookmark": 8,
  "Toggle AOL": 9, "Adopt speed limit": 10, "Quick Select 1": 11, "Quick Select 2": 12,
  "Quick Select 3": 13, "Pulse and glide": 14,
}
KEYS = ("LKASButtonControl", "MainCruiseButtonControl", "DistanceButtonControl", "LongDistanceButtonControl",
        "VeryLongDistanceButtonControl", "CancelButtonControl", "LongCancelButtonControl", "VeryLongCancelButtonControl",
        "ModeButtonControl", "LongModeButtonControl", "VeryLongModeButtonControl", "StarButtonControl",
        "LongStarButtonControl", "VeryLongStarButtonControl", "LeftPaddleButtonControl")
PADDLE_KEY = "LeftPaddleButtonControl"
LIFETIME_NS = 250_000_000
SESSION = re.compile(r"[0-9a-f]{32}\Z")
DELIVERED_ACTIONS = frozenset((1, 2, 5, 6, 7, 8, 10, 11, 12, 13, 14))
DEFAULTS = {"DistanceButtonControl": 1, "LongDistanceButtonControl": 5, "VeryLongDistanceButtonControl": 6}
BUTTON_KEYS = {
  Button.LKAS: (KEYS[0],), Button.DISTANCE: KEYS[2:5], Button.MODE: KEYS[8:11], Button.CUSTOM: KEYS[11:14],
}


def eligible(cp):
  try:
    return bool(cp is not None and not cp.notCar and not cp.passive and not cp.dashcamOnly and cp.carFingerprint)
  except (AttributeError, TypeError, ValueError):
    return False


def button_reported(cp, key):
  """Expose only button events the current vehicle parser actually emits."""
  if key == PADDLE_KEY:
    from opendbc.car.hyundai.left_paddle import has_left_paddle
    return has_left_paddle(cp)
  brand = cp.brand
  if key not in KEYS[:8]:
    return True  # Physical media rows have their separate receiver contract.
  if brand in ('body', 'byd', 'mg', 'mock', 'psa', 'rivian', 'subaru'):
    return False
  if brand == 'toyota':
    from opendbc.car.toyota import legacy_advert_filter, camera_2aa_filter
    from opendbc.car.toyota.values import CAR, ToyotaFlags
    legacy_advert = bool(cp.flags & ToyotaFlags.RADAR_CAN_FILTER) and legacy_advert_filter.configured(cp)
    if key == 'MainCruiseButtonControl' or key in KEYS[5:8]:
      return False
    if key in KEYS[2:5] and bool(cp.flags & ToyotaFlags.RADAR_CAN_FILTER) and camera_2aa_filter.configured(cp):
      return False  # No verified FD_BUTTON receiver is implied by a 2AA discriminator.
    if key == 'LKASButtonControl':
      return bool(cp.carFingerprint != CAR.TOYOTA_CAMRY and
                  (cp.flags & (ToyotaFlags.TSS2 | ToyotaFlags.LONG_FILTER | ToyotaFlags.FILTER_BUTTONS) or
                   cp.carFingerprint == CAR.TOYOTA_PRIUS_RETROFIT or legacy_advert))
    return bool(cp.flags & ToyotaFlags.TSS2 and not cp.flags & (ToyotaFlags.RADAR_ACC | ToyotaFlags.SECOC) or
                cp.flags & (ToyotaFlags.LONG_FILTER | ToyotaFlags.FILTER_BUTTONS) or legacy_advert or cp.carFingerprint in
                (CAR.TOYOTA_PRIUS, CAR.TOYOTA_PRIUS_RETROFIT, CAR.TOYOTA_SIENNA_4TH_GEN))
  if brand == 'tesla':
    from opendbc.car.tesla.values import CAR, TeslaFlags
    if key == 'LKASButtonControl':
      return bool(cp.carFingerprint not in (CAR.TESLA_MODEL_S_PREAP, CAR.TESLA_MODEL_S_HW1) and
                  cp.flags & TeslaFlags.AOL_SCREEN_BUTTON)
    return bool(key in KEYS[5:8] and cp.carFingerprint == CAR.TESLA_MODEL_S_PREAP)
  if key == 'LKASButtonControl':
    if brand == 'gm':
      from opendbc.car.gm.values import is_volt_flashed_profile
      return is_volt_flashed_profile(cp)
    if brand in ('chrysler', 'mazda', 'nissan', 'volkswagen', 'volvo'):
      return False
    if brand == 'hyundai':
      from opendbc.car.hyundai.values import CAR, HyundaiFlags, is_blended
      return bool(cp.flags & (HyundaiFlags.CANFD | HyundaiFlags.HAS_LDA_BUTTON) or
                  cp.carFingerprint == CAR.HYUNDAI_ELANTRA_HEV_2024 or is_blended(cp))
  if key == 'MainCruiseButtonControl' and brand in ('chrysler', 'ford', 'mazda', 'nissan', 'volkswagen'):
    return False
  if key in KEYS[5:8]:
    if brand in ('chrysler', 'nissan'):
      return False
    if brand == 'ford':
      return bool(cp.openpilotLongitudinalControl)
    if brand == 'mazda':
      from opendbc.car.mazda.stock_aol import qualified
      return qualified(cp, marked_only=True)
  return True


def paddle_bookmark_available(params, cp):
  # An invalid pause setting never authorizes a second paddle action.
  if not button_reported(cp, PADDLE_KEY):
    return False
  raw, readable = read_saved(params, "NostalgiaMode", 8)
  return bool(readable and raw in (None, b"0"))


@lru_cache(maxsize=64)
def _cp_fingerprint(serialized):
  # Default substructures can materialize without changing any CarParams values.
  # Rebuild every field so allocation layout cannot invalidate a wheel command.
  with structs.CarParams.from_bytes(serialized) as reader:
    canonical = structs.CarParams.new_message(**reader.to_dict())
    return hashlib.sha256(canonical.to_bytes()).hexdigest()


def cp_fingerprint(cp):
  reader = cp.as_reader() if hasattr(cp, "as_reader") else cp
  return _cp_fingerprint(reader.as_builder().to_bytes())


def capture(params):
  result = {}
  for key in KEYS:
    raw, readable = read_saved(params, key, 8)
    if not readable or raw is not None and raw not in tuple(str(value).encode() for value in range(15)):
      return None
    result[key] = int(raw) if raw is not None else DEFAULTS.get(key, 0)
  return result


def key_for(gesture):
  keys = BUTTON_KEYS[gesture.button]
  index = (Press.SHORT, Press.LONG, Press.VERY_LONG).index(gesture.press)
  return keys[index] if index < len(keys) else None


@dataclass(frozen=True)
class WheelCommand:
  session: str
  sequence: int
  observed_ns: int
  drive_id: int
  source_car_ns: int
  key: str
  action: int


class WheelPublisher:
  def __init__(self, params=None):
    self.session = secrets.token_hex(16)
    self.sequence = 0
    self.drive = 0
    self.tracker = ButtonTracker()
    self.cancel_tracker = ButtonTracker()
    self.main_tracker = ButtonTracker()
    self.mapping = None
    self.paddle_held = False
    self.paddle_armed = False
    self.last_heartbeat = 0
    self.checked_ns = 0
    self.media_sample_state = None
    self.suppress_distance_release = False
    initial = capture(params) if params is not None else None
    self.axis_keys = {key for key, action in (initial or {}).items() if action in (3, 4, 9)}
    self.initial_axis_read = initial is not None

  def media_audit_needed(self, media):
    if media is None:
      return False
    if not media.valid:
      self.media_sample_state = None
      return bool(media.samples)
    if not media.samples:
      return False
    previous = self.media_sample_state
    changed = previous is None or previous[0] != media.source_epoch
    for sample in media.samples:
      current = (media.source_epoch, sample.mode_held, sample.custom_held)
      # Keep auditing a held gesture: an assignment can change and return to
      # its original value before release, which must still cancel that press.
      changed |= current != previous or sample.mode_held or sample.custom_held
      previous = current
    self.media_sample_state = previous
    # Neutral media packets also used to audit assignments during conventional
    # button holds. Preserve those revocations until every gesture is idle.
    held = any(tracker.distance_held or tracker.media_held is not None
               for tracker in (self.tracker, self.cancel_tracker, self.main_tracker))
    return changed or bool(media.samples and held)

  def observe(self, params, cp, state, *, now_ns, drive_id, media=None, blocked_keys=frozenset()):
    self.suppress_distance_release = any(event.type == structs.CarState.ButtonEvent.Type.gapAdjustCruise and not event.pressed
                                         for event in state.buttonEvents)
    # Repeated neutral packets are not button edges. Keep held-input audits,
    # periodic held-input audits, and the final read before emitted commands below.
    media_edge = self.media_audit_needed(media)
    edge = bool(state.buttonEvents) or media_edge
    held = self.paddle_held or any(tracker.distance_held or tracker.media_held is not None
                                    for tracker in (self.tracker, self.cancel_tracker, self.main_tracker))
    audit = self.mapping is None or drive_id != self.drive or edge or held and now_ns - self.checked_ns >= 1_000_000_000
    source_eligible = bool(eligible(cp) and state.canValid and not state.canTimeout and type(now_ns) is int and
                           type(drive_id) is int and 0 < drive_id < now_ns)
    mapping = capture(params) if audit and source_eligible else self.mapping
    if audit:
      self.checked_ns = now_ns
    if not source_eligible or mapping is None:
      self.tracker = ButtonTracker()
      self.cancel_tracker = ButtonTracker()
      self.main_tracker = ButtonTracker()
      self.mapping = None
      self.paddle_held = self.paddle_armed = False
      return ()
    if not self.initial_axis_read:
      self.axis_keys = {key for key, action in mapping.items() if action in (3, 4, 9)}
      self.initial_axis_read = True
    if drive_id != self.drive or mapping != self.mapping:
      self.tracker = ButtonTracker()
      self.cancel_tracker = ButtonTracker()
      self.main_tracker = ButtonTracker()
      self.paddle_held = self.paddle_armed = False
      self.drive, self.mapping = drive_id, mapping
    gestures = [(key_for(gesture), gesture) for gesture in self.tracker.observe(
      state, media if ioniq6_media_eligible(cp) else None)]
    for tracker, source, keys in ((self.cancel_tracker, structs.CarState.ButtonEvent.Type.cancel, KEYS[5:8]),
                                   (self.main_tracker, structs.CarState.ButtonEvent.Type.mainCruise, KEYS[1:2])):
      if source == structs.CarState.ButtonEvent.Type.cancel and cp.brand == 'gm':
        from opendbc.car.gm.cancel_remap import enabled as gm_cancel_remap_enabled, supported as gm_cancel_remap_supported
        if gm_cancel_remap_supported(cp) and not gm_cancel_remap_enabled(cp):
          self.cancel_tracker = ButtonTracker()
          continue
      if not any(mapping[key] in DELIVERED_ACTIONS for key in keys):
        continue
      copy = structs.CarState(canValid=state.canValid, canTimeout=state.canTimeout)
      copy.buttonEvents = [structs.CarState.ButtonEvent(type=structs.CarState.ButtonEvent.Type.gapAdjustCruise,
                                                      pressed=event.pressed)
                           for event in state.buttonEvents if event.type == source]
      for gesture in tracker.observe(copy.as_reader()):
        index = (Press.SHORT, Press.LONG, Press.VERY_LONG).index(gesture.press)
        if index < len(keys):
          gestures.append((keys[index], gesture))
    paddle_commands = []
    paddle_edge = any(event.type == structs.CarState.ButtonEvent.Type.altButton2 for event in state.buttonEvents)
    paddle_allowed = ((self.paddle_held or paddle_edge) and mapping[PADDLE_KEY] == 8 and PADDLE_KEY not in blocked_keys and
                      paddle_bookmark_available(params, cp) and read_saved(params, PADDLE_KEY, 8) == (b"8", True))
    # Audit every held sample, so enabling pause even briefly cancels this press.
    self.paddle_armed &= paddle_allowed
    for event in state.buttonEvents:
      if event.type != structs.CarState.ButtonEvent.Type.altButton2:
        continue
      if event.pressed:
        if not self.paddle_held:
          self.paddle_armed = paddle_allowed
        self.paddle_held = True
      else:
        if self.paddle_held and self.paddle_armed:
          paddle_commands.append((PADDLE_KEY, 8))
        self.paddle_held = self.paddle_armed = False
    commands = [(key, mapping[key]) for key, _ in gestures if key is not None and key not in blocked_keys and
                key not in self.axis_keys and button_reported(cp, key) and mapping[key] in DELIVERED_ACTIONS and
                (key != "MainCruiseButtonControl" or mapping[key] == 10)]
    commands.extend(paddle_commands)
    if commands and not audit:
      current = capture(params)
      self.checked_ns = now_ns
      if current != mapping:
        self.tracker = ButtonTracker()
        self.cancel_tracker = ButtonTracker()
        self.main_tracker = ButtonTracker()
        self.mapping = current
        return ()
    if len(commands) > 1:
      return ()
    if not commands and now_ns - self.last_heartbeat >= 100_000_000:
      commands.append(("", 0))
      self.last_heartbeat = now_ns
    return tuple(commands)

  def publish(self, commands, cp, publisher, *, now_ns, drive_id, source_car_ns, source_control_ns):
    from openpilot.cereal import messaging

    for key, action in commands:
      self.sequence += 1
      event = messaging.new_message("slcCruiseEvent", valid=True, logMonoTime=now_ns)
      event.slcCruiseEvent.kind = "wheelAction"
      event.slcCruiseEvent.eventId = self.sequence
      event.slcCruiseEvent.producerSessionId = self.session
      event.slcCruiseEvent.observedMonoTime = now_ns
      event.slcCruiseEvent.wheelAction = {
        "version": 1, "sessionId": self.session, "sequence": self.sequence, "observedMonoTime": now_ns,
        "validUntilMonoTime": now_ns + LIFETIME_NS, "driveStartMonoTime": drive_id,
        "carFingerprint": cp.carFingerprint, "sourceCarControlMonoTime": source_control_ns,
        "carParamsFingerprint": cp_fingerprint(cp),
        "sourceCarStateMonoTime": source_car_ns, "buttonKey": key, "actionCode": action,
      }
      publisher.send("slcCruiseEvent", event)


class WheelConsumer:
  def __init__(self):
    self.drive = 0
    self.session = None
    self.retired = set()
    self.sequence = 0
    self.last_heartbeat = 0

  def accept(self, event, params, cp, sm, *, now_ns):
    try:
      if not event.valid or str(event.slcCruiseEvent.kind) != "wheelAction" or not eligible(cp):
        return None
      wire = event.slcCruiseEvent.wheelAction
      drive = int(sm["deviceState"].startedMonoTime)
      if drive != self.drive:
        self.__init__()
        self.drive = drive
      observed = int(wire.observedMonoTime)
      source = int(wire.sourceCarStateMonoTime)
      session = str(wire.sessionId)
      key, action = str(wire.buttonKey), int(wire.actionCode)
      if (wire.version != 1 or int(event.slcCruiseEvent.eventId) != int(wire.sequence) or
          str(event.slcCruiseEvent.producerSessionId) != session or
          int(event.slcCruiseEvent.observedMonoTime) != observed or not SESSION.fullmatch(session) or session in self.retired or
          int(wire.driveStartMonoTime) != drive or str(wire.carFingerprint) != cp.carFingerprint or
          str(wire.carParamsFingerprint) != cp_fingerprint(cp) or
          not sm["deviceState"].started or not sm["carState"].canValid or sm["carState"].canTimeout or
          not drive < source <= observed <= now_ns <= int(wire.validUntilMonoTime) or
          int(wire.validUntilMonoTime) - observed != LIFETIME_NS or int(event.logMonoTime) != observed or
          source > int(sm.logMonoTime["carState"]) or now_ns - source > LIFETIME_NS or
          not all(fresh_service(sm, name, drive, now_ns, LIFETIME_NS) for name in ("carState", "carControl")) or
          not fresh_service(sm, "deviceState", drive, now_ns, 1_000_000_000) or
          not drive < int(wire.sourceCarControlMonoTime) <= observed or
          int(wire.sourceCarControlMonoTime) > int(sm.logMonoTime["carControl"]) or
          now_ns - int(wire.sourceCarControlMonoTime) > LIFETIME_NS):
        return None
      if action == 0:
        if key:
          return None
      elif key not in KEYS or action not in DELIVERED_ACTIONS or not button_reported(cp, key):
        return None
      elif key == PADDLE_KEY and (action != 8 or not paddle_bookmark_available(params, cp)):
        return None
      else:
        mapping = capture(params)
        if mapping is None or mapping[key] != action:
          return None
      if session != self.session:
        if self.session is not None:
          if len(self.retired) >= 8:
            return None
          self.retired.add(self.session)
        self.session, self.sequence = session, 0
      if wire.sequence <= self.sequence:
        return None
      self.sequence = int(wire.sequence)
      self.last_heartbeat = observed
      return WheelCommand(session, self.sequence, observed, drive, source, key, action)
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
      return None
