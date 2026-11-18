"""Parked, exact-source editor for the optional onroad side-camera preview."""

from collections.abc import Callable
from dataclasses import dataclass, replace
import fcntl
import json
import math
import os
from pathlib import Path
import tempfile

from openpilot.starpilot.ui.feature_settings_state import FeatureRow, FeatureSettingsRequest, FeatureSettingsState
from openpilot.starpilot.ui.pip_preferences import (
  BLINKER, BOOL_KEYS, BSM, CAMERA_FORMATS, ENABLED, INVERT, KEYS, MASK, MAX_MASK_BYTES,
  encode_mask, read_pip, starting_mask,
)
from openpilot.starpilot.ui.pip_sidecam import Mask


RESET = "pip:reset"
EDITOR = "pip:mask:editor"
FORMAT_PREFIX = "pip:format:"
MASK_PREFIX = "pip:mask:"
LABELS = {ENABLED: "Use blind spot camera", BLINKER: "Show on turn signal",
          BSM: "Show on blind spot warning", INVERT: "Mirror camera crop"}


@dataclass(frozen=True)
class WriteResult:
  committed: bool
  verified: bool


def _source_tuple(saved) -> tuple[tuple[str, bytes | None], ...]:
  return tuple((key, saved.source(key)[0]) for key in KEYS)


def _integer_mask(mask: Mask) -> bool:
  return (mask.crop_size.is_integer() and
          all(center is None or all(axis.is_integer() for axis in center)
              for center in (mask.center_left, mask.center_right)))


def _visual_editor_supported(mask: Mask) -> bool:
  return (mask.width, mask.height) in CAMERA_FORMATS and mask.crop_size >= 20 and _integer_mask(mask)


def validated_editor_draft(raw: str, current: Mask | None) -> Mask | None:
  """Accept one native-pixel crop edit; format replacement has its own confirmation."""
  if type(raw) is not str or current is None or not _visual_editor_supported(current):
    return None
  try:
    if len(raw.encode("utf-8")) > MAX_MASK_BYTES:
      return None
    def unique_pairs(pairs):
      result = {}
      for key, value in pairs:
        if key in result:
          raise ValueError("duplicate crop field")
        result[key] = value
      return result
    document = json.loads(raw, object_pairs_hook=unique_pairs)
    if not isinstance(document, dict) or set(document) != {"width", "height", "crop_size", "center_left", "center_right"}:
      return None
    if (type(document["width"]) is not int or type(document["height"]) is not int or
        (document["width"], document["height"]) != (current.width, current.height) or
        type(document["crop_size"]) is not int or document["crop_size"] < 20):
      return None
    centers = (document["center_left"], document["center_right"])
    if all(center is None for center in centers):
      return None
    if any(center is not None and (not isinstance(center, list) or len(center) != 2 or
                                   any(type(axis) is not int for axis in center)) for center in centers):
      return None
    return Mask.parse(document)
  except (UnicodeError, ValueError, TypeError, OverflowError, RecursionError):
    return None


def _commit(params, key: str, raw: bytes, expected: tuple[tuple[str, bytes | None], ...],
            parked: Callable[[], bool]) -> WriteResult:
  if key not in KEYS or len(raw) > (MAX_MASK_BYTES if key == MASK else 1) or not parked():
    return WriteResult(False, False)
  first = read_pip(params)
  if _source_tuple(first) != expected or not all(first.source(name)[1] for name in KEYS):
    return WriteResult(False, False)
  temporary: str | None = None
  lock_fd: int | None = None
  committed = False
  try:
    destination = Path(params.get_param_path(key))
    root = destination.parent.parent
    with tempfile.NamedTemporaryFile(prefix=".tmp_pip_", dir=root, delete=False) as stage:
      temporary = stage.name
      stage.write(raw)
      stage.flush()
      os.fsync(stage.fileno())
    lock_fd = os.open(root / ".lock", os.O_CREAT | os.O_RDONLY, 0o775)
    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    if not parked():
      return WriteResult(False, False)
    final = read_pip(params)
    if _source_tuple(final) != expected or not all(final.source(name)[1] for name in KEYS) or not parked():
      return WriteResult(False, False)
    os.replace(temporary, destination)
    temporary = None
    committed = True
    directory_fd = os.open(destination.parent, os.O_RDONLY)
    try:
      os.fsync(directory_fd)
    finally:
      os.close(directory_fd)
    observed = read_pip(params)
    return WriteResult(True, observed.source(key) == (raw, True))
  except (OSError, ValueError):
    return WriteResult(committed, False)
  finally:
    if lock_fd is not None:
      os.close(lock_fd)
    if temporary is not None:
      try:
        os.unlink(temporary)
      except OSError:
        pass


class PiPOwner:
  def __init__(self, params, parked: Callable[[], bool], camera_available: Callable[[], bool]):
    self.params = params
    self.parked = parked
    self.camera_available = camera_available
    self.last_write = WriteResult(False, False)

  def snapshot(self, *, include_editor: bool = False) -> FeatureSettingsState:
    saved = read_pip(self.params)
    parked = self.parked()
    dependencies = _source_tuple(saved)
    rows: list[FeatureRow] = []
    for key, value in ((ENABLED, saved.enabled), (BLINKER, saved.on_blinker),
                       (BSM, saved.on_bsm), (INVERT, saved.invert)):
      raw, readable = saved.source(key)
      available = parked and readable and (key != ENABLED or saved.mask is not None or value is not False)
      rows.append(FeatureRow(key, LABELS[key], "Invalid saved choice" if value is None else "On" if value else "Off",
                             raw, ("Off", "On") if value is not None else (), available=available,
                             reason="Saved source unreadable" if not readable else
                                    "Reset the invalid crop below before enabling" if key == ENABLED and saved.mask is None else
                                    "Explicitly set Off to repair" if value is None else
                                    "",
                             repair_value="Off" if readable and value is None else "", dependencies=dependencies))
    mask_raw, readable = saved.source(MASK)
    if saved.mask is None:
      rows.append(FeatureRow(RESET, "Restore default camera crop", "Invalid saved crop", mask_raw,
                             available=parked and readable, reason="Requires confirmation; old bytes stay until you reset",
                             dependencies=dependencies))
    else:
      mask = saved.mask
      centers = (("right", mask.center_left), ("left", mask.center_right))
      for side, center in centers:
        if center is None:
          rows.append(FeatureRow("", f"Vehicle {side} crop", "Not configured",
                                 reason="Restore default crop to add this view"))
          continue
        for axis, index, bound in (("x", 0, mask.width), ("y", 1, mask.height)):
          rows.append(FeatureRow(f"{MASK_PREFIX}{side}:{axis}", f"Vehicle {side} crop {axis.upper()}", str(center[index]),
                                 mask_raw, step=10, minimum=math.ceil(mask.crop_size / 2), maximum=math.floor(bound - mask.crop_size / 2),
                                 unit="px", available=parked and readable,
                                 reason="Image crop; view remains off without a live camera", dependencies=dependencies))
      max_size = min(mask.width, mask.height, *(2 * min(x, mask.width - x, y, mask.height - y)
                                                for _, center in centers if center is not None for x, y in (center,)))
      rows.append(FeatureRow(f"{MASK_PREFIX}size", "Camera crop size", str(mask.crop_size), mask_raw,
                             step=20, minimum=20, maximum=max_size, unit="px", available=parked and readable,
                             dependencies=dependencies))
      if include_editor:
        editor_ready = saved.invert is not None and _visual_editor_supported(mask)
        rows.append(FeatureRow(EDITOR, "Visual camera crop editor", "Edit in Galaxy", mask_raw,
                               available=parked and readable and editor_ready,
                               reason="" if editor_ready else
                                      "Repair mirror choice, use numeric controls for fractional/small crops, or reset the crop",
                               dependencies=dependencies))
      rows.append(FeatureRow(RESET, "Restore default camera crop", "Reset to Default", mask_raw,
                             available=parked and readable, reason="Replaces only the saved crop",
                             dependencies=dependencies))
    from openpilot.starpilot.galaxy.camera_request import frame_available
    live = bool(self.camera_available()) or frame_available()
    rows.append(FeatureRow("", "Camera availability", "Live cabin frames" if live else "No fresh cabin frame",
                           reason="" if live else "Open the live crop preview while parked to check the camera."))
    defaults = dict.fromkeys(BOOL_KEYS, 'Off')
    rows = [replace(row, default_value=defaults[row.key]) if row.key in defaults else row for row in rows]
    return FeatureSettingsState(page="pip", title="Blind Spot Camera & Preview",
                                subtitle="Choose when to show the camera and adjust its crop from the live preview. " +
                                         "Resolution follows the camera automatically.",
                                rows=tuple(rows), parked=parked)

  def editor_with_source(self) -> tuple[dict | None, tuple[tuple[str, bytes | None], ...]]:
    saved = read_pip(self.params)
    mask = saved.mask
    _, readable = saved.source(MASK)
    dependencies = _source_tuple(saved)
    if not readable or mask is None or saved.invert is None or not _visual_editor_supported(mask):
      return None, dependencies
    return ({"width": mask.width, "height": mask.height,
             "centerLeft": [int(axis) for axis in mask.center_left] if mask.center_left is not None else None,
             "centerRight": [int(axis) for axis in mask.center_right] if mask.center_right is not None else None,
             "cropSize": int(mask.crop_size), "invert": saved.invert}, dependencies)

  def apply(self, request: FeatureSettingsRequest) -> bool:
    self.last_write = WriteResult(False, False)
    key = request.key
    if key not in (*BOOL_KEYS, RESET, EDITOR) and not key.startswith((MASK_PREFIX, FORMAT_PREFIX)):
      return False
    saved = read_pip(self.params)
    expected = _source_tuple(saved)
    if (request.dependencies != expected or not all(saved.source(name)[1] for name in KEYS) or
        not self.parked() or (request.expected != saved.source(MASK if key.startswith((MASK_PREFIX, FORMAT_PREFIX)) or key in (RESET, EDITOR) else key)[0])):
      return False
    if key in BOOL_KEYS:
      if request.value not in ("Off", "On") or (key == ENABLED and request.value == "On" and saved.mask is None):
        return False
      target, raw = key, b"1" if request.value == "On" else b"0"
    elif key == RESET:
      if not request.confirmation or request.value != "confirm":
        return False
      target, raw = MASK, encode_mask(starting_mask(1928, 1208))
    elif key.startswith(FORMAT_PREFIX):
      if not request.confirmation or request.value != "confirm":
        return False
      suffix = key.removeprefix(FORMAT_PREFIX)
      if suffix not in {f"{width}x{height}" for width, height in CAMERA_FORMATS}:
        return False
      width, height = (int(part) for part in suffix.split("x"))
      target, raw = MASK, encode_mask(starting_mask(width, height))
    elif key == EDITOR:
      if not request.confirmation or saved.invert is None or saved.mask is None or \
         not _visual_editor_supported(saved.mask):
        return False
      changed = validated_editor_draft(request.value, saved.mask)
      if changed is None:
        return False
      target, raw = MASK, encode_mask(changed)
    else:
      mask = saved.mask
      if mask is None:
        return False
      try:
        value = float(request.value)
      except ValueError:
        return False
      if not math.isfinite(value) or not 0 <= value <= 8192 or value != round(value):
        return False
      document = {"width": mask.width, "height": mask.height, "crop_size": mask.crop_size,
                  "center_left": list(mask.center_left) if mask.center_left is not None else None,
                  "center_right": list(mask.center_right) if mask.center_right is not None else None}
      tail = key.removeprefix(MASK_PREFIX)
      if tail == "size":
        document["crop_size"] = value
      elif tail in ("left:x", "left:y", "right:x", "right:y"):
        side, axis = tail.split(":")
        image_side = "right" if side == "left" else "left"
        center = document[f"center_{image_side}"]
        if not isinstance(center, list):
          return False
        center[0 if axis == "x" else 1] = value
      else:
        return False
      changed = Mask.parse(document)
      if changed is None:
        return False
      target, raw = MASK, encode_mask(changed)
    self.last_write = _commit(self.params, target, raw, expected, self.parked)
    return self.last_write.verified
