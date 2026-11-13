"""Explicit, source-backed manual CEM/CCM wheel gestures.

No default button is assigned. This owner emits only typed proposals; it does
not write legacy CEStatus/CCStatus or change the effective driving mode.
"""

from dataclasses import dataclass
from enum import StrEnum
import hashlib
from typing import cast

from opendbc.car.structs import car
from opendbc.car.gm.distance_button import DistanceObservation, DistanceSample
from opendbc.car.hyundai.ioniq6_media import MediaObservation
from opendbc.car.hyundai.hyundaicanfd import CanBus
from opendbc.car.hyundai.canfd_owner import config_index
from opendbc.car.hyundai.values import CAR as HYUNDAI_CAR, HyundaiFlags
from openpilot.selfdrive.car.cruise import CRUISE_LONG_PRESS
from openpilot.starpilot.conditional_mode.policy import ModeChoice, next_manual_status, restore_manual_status
from openpilot.starpilot.conditional_mode.preferences import ManualState, manual_for_drive
from openpilot.starpilot.saved_source import read_saved


EXPERIMENTAL_MODE_ACTION = 5
TRAFFIC_MODE_ACTION = 6
VERY_LONG_PRESS = 5 * CRUISE_LONG_PRESS
MEDIA_LONG_NS = CRUISE_LONG_PRESS * 10_000_000  # Convert 100 Hz ticks to source-packet nanoseconds.
MEDIA_VERY_LONG_NS = VERY_LONG_PRESS * 10_000_000
ButtonType = car.CarState.ButtonEvent.Type


def ioniq6_media_eligible(CP: car.CarParams) -> bool:
  """Legacy name shared by the Ioniq 6 and restored first-generation EV6 path.

  Pre-rebase StarPilot decoded both models' 0x448 MODE/CUSTOM signals on
  selected E-CAN. Keep the expansion model-specific, not all Hyundai CAN-FD.
  """
  if CP.carFingerprint == HYUNDAI_CAR.HYUNDAI_IONIQ_6:
    return config_index(CP) is not None and CanBus(CP).ECAN in (1, 5)
  required = HyundaiFlags.CANFD | HyundaiFlags.EV
  return (CP.brand == 'hyundai' and CP.carFingerprint == HYUNDAI_CAR.KIA_EV6 and
          CP.flags & required == required and not CP.flags & (HyundaiFlags.CCNC | HyundaiFlags.CANFD_ANGLE_STEERING) and
          config_index(CP) is not None and CanBus(CP).ECAN in (0, 1, 4, 5))


class Button(StrEnum):
  LKAS = 'lkas'
  DISTANCE = 'distance'
  MODE = 'mode'
  CUSTOM = 'custom'


class Press(StrEnum):
  SHORT = 'short'
  LONG = 'long'
  VERY_LONG = 'veryLong'


@dataclass(frozen=True)
class Gesture:
  button: Button
  press: Press


@dataclass(frozen=True)
class ButtonMap:
  lkas: int = 0
  distance: int = 0
  distance_long: int = 0
  distance_very_long: int = 0
  mode: int = 0
  mode_long: int = 0
  mode_very_long: int = 0
  custom: int = 0
  custom_long: int = 0
  custom_very_long: int = 0

  def action(self, gesture: Gesture) -> int:
    if gesture.button is Button.LKAS:
      return self.lkas if gesture.press is Press.SHORT else 0
    if gesture.button is Button.DISTANCE:
      actions = {Press.SHORT: self.distance, Press.LONG: self.distance_long, Press.VERY_LONG: self.distance_very_long}
    elif gesture.button is Button.MODE:
      actions = {Press.SHORT: self.mode, Press.LONG: self.mode_long, Press.VERY_LONG: self.mode_very_long}
    else:
      actions = {Press.SHORT: self.custom, Press.LONG: self.custom_long, Press.VERY_LONG: self.custom_very_long}
    return actions.get(gesture.press, 0)

  def assigned(self, gesture: Gesture) -> bool:
    return self.action(gesture) == EXPERIMENTAL_MODE_ACTION

  def fingerprint(self) -> str:
    fields = (self.lkas, self.distance, self.distance_long, self.distance_very_long,
              self.mode, self.mode_long, self.mode_very_long, self.custom,
              self.custom_long, self.custom_very_long)
    return hashlib.sha256(b'Ioniq media map v1\0' + bytes(fields)).hexdigest()


def _action(params, key: str) -> int | None:
  try:
    raw, readable = read_saved(params, key, 8)
  except (OSError, TypeError, ValueError):
    return None
  if not readable:
    return None
  if raw is None:
    return 0
  # Registered INT defaults and Params::put values are canonical decimal.
  if raw not in tuple(str(value).encode() for value in range(15)):
    return None
  return int(raw)


def read_button_map(params, *, include_ioniq_media: bool = False, cp=None) -> ButtonMap | None:
  """Read non-Cancel slots; the exact GM owner dispatches remapped Cancel separately."""
  keys = ('LKASButtonControl', 'DistanceButtonControl', 'LongDistanceButtonControl',
          'VeryLongDistanceButtonControl', 'CancelButtonControl', 'LongCancelButtonControl',
          'VeryLongCancelButtonControl')
  if include_ioniq_media:
    keys += ('ModeButtonControl', 'LongModeButtonControl', 'VeryLongModeButtonControl',
             'StarButtonControl', 'LongStarButtonControl', 'VeryLongStarButtonControl')
  values = [_action(params, key) for key in keys]
  if any(value is None for value in values):
    return None
  assert all(value is not None for value in values)
  # Cancel-to-distance is GM Pedal-only; this gesture source must not reinterpret cancel.
  from opendbc.car.gm.cancel_remap import enabled as gm_cancel_remap_enabled
  if EXPERIMENTAL_MODE_ACTION in values[4:7] and not gm_cancel_remap_enabled(cp):
    return None
  return ButtonMap(*(cast(int, value) for value in (*values[:4], *values[7:])))


class WheelMapCache:
  """One Card-owned saved map for both consumers of the same physical source.

  Neutral packets never cause a Params scan. An edge, a held-press duration
  boundary, or the one-second audit rechecks the complete map before either
  consumer can interpret that source packet.
  """

  def __init__(self, *, include_ioniq_media=True, cp=None):
    self.include_ioniq_media = include_ioniq_media
    self.cp = cp
    self.buttons: ButtonMap | None = None
    self.checked_ns = 0
    self.held: tuple[bool, ...] | None = None
    self.epoch: int | None = None
    self.press_ns = 0
    self.last_sample_ns = 0

  def sample(self, params, media: MediaObservation | DistanceObservation | None, now_ns: int) -> ButtonMap | None:
    if media is None or not media.valid:
      self.buttons = None
      self.checked_ns = 0
      self.held = None
      self.epoch = None
      self.press_ns = 0
      self.last_sample_ns = 0
      return None
    changed_epoch = media.source_epoch != self.epoch
    if changed_epoch:
      self.held = None
      self.press_ns = 0
      self.last_sample_ns = 0
      self.epoch = media.source_epoch
    edge = False
    boundary = False
    for sample in media.samples:
      held = (sample.held,) if isinstance(sample, DistanceSample) else (sample.mode_held, sample.custom_held)
      if held != self.held:
        edge = True
        self.press_ns = sample.source_boot_ns if any(held) else 0
      elif any(held) and self.press_ns:
        elapsed = sample.source_boot_ns - self.press_ns
        prior = self.last_sample_ns - self.press_ns
        offset = 10_000_000 if isinstance(sample, DistanceSample) else 0
        boundary = boundary or (prior < MEDIA_LONG_NS - offset <= elapsed or
                                prior < MEDIA_VERY_LONG_NS - offset <= elapsed)
      self.held = held
      self.last_sample_ns = sample.source_boot_ns
    if (changed_epoch or self.checked_ns == 0 or edge or boundary or
        now_ns < self.checked_ns or now_ns - self.checked_ns >= 1_000_000_000):
      self.buttons = read_button_map(params, include_ioniq_media=self.include_ioniq_media, cp=self.cp)
      self.checked_ns = now_ns
    return self.buttons


IoniqMediaMapCache = WheelMapCache


class ButtonTracker:
  """One 100 Hz Card edge/duration tracker; held-at-start buttons are ignored."""

  def __init__(self):
    self.neutral_seen = False
    self.distance_held = False
    self.distance_frames = 0
    self.distance_claim: tuple[int, str] | None = None
    self.suppress_distance_release = False
    self.media_epoch: int | None = None
    self.media_neutral_seen = False
    self.media_held: Button | None = None
    self.media_press_ns = 0
    self.media_long_emitted = False
    self.media_very_long_emitted = False
    self.media_context: tuple[int, str, ButtonMap] | None = None

  def _reset_media(self) -> None:
    self.media_neutral_seen = False
    self.media_held = None
    self.media_press_ns = 0
    self.media_long_emitted = False
    self.media_very_long_emitted = False

  def invalidate_media(self) -> None:
    self.media_context = None
    self._reset_media()

  def bind_media(self, context: tuple[int, str, ButtonMap]) -> None:
    if self.media_context != context:
      self._reset_media()
      self.media_context = context

  def observe_media(self, observation: MediaObservation) -> tuple[Gesture, ...]:
    return self._observe_media(observation)

  def observe_distance_source(self, observation: DistanceObservation) -> tuple[Gesture, ...]:
    return self._observe_media(observation)

  def _observe_media(self, observation: MediaObservation | DistanceObservation | None) -> tuple[Gesture, ...]:
    if observation is None:
      return ()
    if self.media_epoch != observation.source_epoch or not observation.valid:
      self.media_epoch = observation.source_epoch
      self._reset_media()
    if not observation.valid:
      return ()
    # The distance counter includes its first held 100 Hz frame.
    offset = 10_000_000 if isinstance(observation, DistanceObservation) else 0
    long_ns, very_long_ns = MEDIA_LONG_NS - offset, MEDIA_VERY_LONG_NS - offset
    gestures = []
    for sample in observation.samples:
      if isinstance(sample, DistanceSample):
        button = Button.DISTANCE if sample.held else None
      else:
        if sample.mode_held and sample.custom_held:
          self._reset_media()  # One simultaneous sample has no unambiguous owner.
          return ()
        button = Button.MODE if sample.mode_held else (Button.CUSTOM if sample.custom_held else None)
      if not self.media_neutral_seen:
        if button is None:
          self.media_neutral_seen = True
        continue
      if button is not None and self.media_held is not None and button is not self.media_held:
        self._reset_media()  # A direct handoff without neutral is ambiguous.
        return ()
      if button is not self.media_held:
        if self.media_held is not None:
          elapsed = sample.source_boot_ns - self.media_press_ns
          if elapsed >= very_long_ns + offset and not self.media_very_long_emitted:
            gestures.append(Gesture(self.media_held, Press.VERY_LONG))
          elif elapsed >= long_ns + offset and not self.media_long_emitted:
            gestures.append(Gesture(self.media_held, Press.LONG))
          elif 0 < elapsed < long_ns + offset:
            gestures.append(Gesture(self.media_held, Press.SHORT))
        self.media_held = button
        self.media_press_ns = sample.source_boot_ns if button is not None else 0
        self.media_long_emitted = False
        self.media_very_long_emitted = False
      elif button is not None:
        elapsed = sample.source_boot_ns - self.media_press_ns
        if elapsed >= very_long_ns and not self.media_very_long_emitted:
          gestures.append(Gesture(button, Press.VERY_LONG))
          self.media_very_long_emitted = True
        elif elapsed >= long_ns and not self.media_long_emitted:
          gestures.append(Gesture(button, Press.LONG))
          self.media_long_emitted = True
    return tuple(gestures)

  def clear_claim(self) -> None:
    self.distance_claim = None

  def keep_claim(self, drive_id: int, settings_fingerprint: str | None) -> None:
    if self.distance_claim is not None and self.distance_claim != (drive_id, settings_fingerprint):
      self.clear_claim()

  def claim(self, gesture: Gesture, drive_id: int, settings_fingerprint: str) -> None:
    if gesture.button is Button.DISTANCE:
      self.distance_claim = (drive_id, settings_fingerprint)
      if not self.distance_held:
        self.suppress_distance_release = True

  def observe(self, state: car.CarState, media: MediaObservation | DistanceObservation | None = None) -> tuple[Gesture, ...]:
    self.suppress_distance_release = False
    if not state.canValid or state.canTimeout:
      self.neutral_seen = False
      self.distance_held = False
      self.distance_frames = 0
      self.clear_claim()
      self.invalidate_media()
      return ()
    gap_events = [event for event in state.buttonEvents if event.type == ButtonType.gapAdjustCruise]
    lkas_events = [event for event in state.buttonEvents if event.type == ButtonType.lkas]
    if not self.neutral_seen:
      if not any(event.pressed for event in (*gap_events, *lkas_events)):
        self.neutral_seen = True
      return self._observe_media(media)

    gestures = []
    if any(event.pressed for event in lkas_events):
      gestures.append(Gesture(Button.LKAS, Press.SHORT))
    gap_pressed = any(event.pressed for event in gap_events)
    gap_released = any(not event.pressed for event in gap_events)
    if gap_pressed:
      self.clear_claim()  # A new physical press cannot inherit an older claim.
      self.distance_held = True
      self.distance_frames = 0
    if self.distance_held and not gap_released:
      self.distance_frames += 1
      if self.distance_frames == CRUISE_LONG_PRESS:
        gestures.append(Gesture(Button.DISTANCE, Press.LONG))
      elif self.distance_frames == VERY_LONG_PRESS:
        gestures.append(Gesture(Button.DISTANCE, Press.VERY_LONG))
    if gap_released and self.distance_held:
      self.suppress_distance_release = self.distance_claim is not None
      if 1 <= self.distance_frames < CRUISE_LONG_PRESS:
        gestures.append(Gesture(Button.DISTANCE, Press.SHORT))
      self.distance_held = False
      self.distance_frames = 0
      self.clear_claim()
    return (*gestures, *self._observe_media(media))


class ManualSession:
  """Pure per-drive manual latch for the future single effective-mode owner."""

  def __init__(self):
    self.drive_id = 0
    self.choice = ModeChoice.STOCK
    self.code = 0
    self.last_sequence = 0
    self.last_observed_ns = 0
    self.ready = False

  def start(self, choice: ModeChoice, drive_id: int, observed_ns: int, *, persist_manual: bool,
            persisted_code: int | None = None) -> ManualState | None:
    self.__init__()
    if (choice not in (ModeChoice.CEM, ModeChoice.CCM) or type(drive_id) is not int or drive_id <= 0 or
        type(observed_ns) is not int or observed_ns < drive_id or type(persist_manual) is not bool or
        (persist_manual and (type(persisted_code) is not int or persisted_code not in (0, 1, 2)))):
      return None
    self.drive_id = drive_id
    self.choice = choice
    self.code = persisted_code if persist_manual and persisted_code is not None else 0
    self.last_observed_ns = observed_ns
    self.ready = True
    intent = restore_manual_status(choice, 0, self.code, persist_manual)
    return manual_for_drive(intent, drive_id, observed_ns)

  def apply(self, *, sequence: int, drive_id: int, observed_ns: int, effective_experimental: bool,
            assigned: bool) -> ManualState | None:
    if (not self.ready or type(sequence) is not int or sequence <= self.last_sequence or
        type(drive_id) is not int or drive_id != self.drive_id or type(observed_ns) is not int or
        observed_ns <= self.last_observed_ns or type(effective_experimental) is not bool or assigned is not True):
      return None
    self.code = next_manual_status(self.choice, self.code, effective_experimental)
    self.last_sequence = sequence
    self.last_observed_ns = observed_ns
    intent = restore_manual_status(self.choice, self.code, 0, False)
    return manual_for_drive(intent, drive_id, observed_ns)
