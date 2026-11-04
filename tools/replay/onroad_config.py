#!/usr/bin/env python3
from __future__ import annotations

import os
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openpilot.common.params import Params, UnknownKeyName
from openpilot.system.version import terms_version, training_version
from openpilot.tools.lib.logreader import LogReader, ReadMode, parse_direct, parse_indirect
from openpilot.tools.lib.route import SegmentRange
from openpilot.starpilot.navigation.destination_store import FAVORITE_DESTINATIONS_KEY, load_favorite_destinations


DEMO_ROUTE = "a2a0ccea32023010|2023-07-27--13-01-19"
NAV_DEMO_MAPBOX_SECRET = "desktop-nav-demo-placeholder"
NAV_DEMO_FAVORITES = [{
  "name": "Demo Home",
  "latitude": 0.01,
  "longitude": 0.02,
  "is_home": True,
}]

_VALUE_OPTIONS = {
  "-a", "--allow",
  "-b", "--block",
  "-c", "--cache",
  "-s", "--start",
  "-x", "--playback",
  "-d", "--data_dir",
  "-p", "--prefix",
}
_NO_VALUE_OPTIONS = {
  "--dcam",
  "--ecam",
  "--no-loop",
  "--no-cache",
  "--qcam",
  "--no-hw-decoder",
  "--no-vipc",
  "--all",
  "--headless",
}
_SEGMENT_SLICE_RE = re.compile(r"(?P<start>-?[0-9]+)?:?(?P<end>-?[0-9]+)?:?(?P<step>-?[0-9]+)?$")


@dataclass(frozen=True)
class ReplayArgs:
  route: str | None
  data_dir: str | None
  auto_source: bool = False


@dataclass
class ReplayGpuState:
  """Reconstruct modeld's transient GPU Params from replayed messages."""
  present: bool = False
  compiled: bool = False
  loading: bool = False
  active: bool = False
  failed: bool = False

  def update(self, *, present: bool | None = None, event_names: Sequence[str] = (), model_updated: bool = False) -> tuple[bool, bool, bool, bool]:
    loading_event = "bigModelLoading" in event_names
    failed_event = "bigModelFailed" in event_names

    if present is not None:
      self.present = present
      if not present:
        self.compiled = False
        self.loading = False
        self.active = False
        self.failed = False

    if loading_event or failed_event:
      self.present = True
      self.compiled = True

    if loading_event:
      self.loading = True
      self.active = False
      self.failed = False
    elif failed_event:
      self.loading = False
      self.active = False
      self.failed = True
    elif model_updated and self.present and self.compiled and not self.failed:
      self.compiled = True
      self.loading = False
      self.active = True

    return self.present, self.compiled, self.loading, self.active


def _truthy_env(name: str) -> bool:
  return os.getenv(name, "").lower() in {"1", "true", "yes", "on"}


def parse_replay_args(args: Sequence[str]) -> ReplayArgs:
  route: str | None = None
  data_dir: str | None = None
  auto_source = False

  idx = 0
  while idx < len(args):
    arg = args[idx]
    if arg == "--":
      idx += 1
      continue
    if arg == "--demo":
      route = DEMO_ROUTE
      idx += 1
      continue
    if arg == "--auto":
      auto_source = True
      idx += 1
      continue
    if arg.startswith("--data_dir="):
      data_dir = arg.split("=", 1)[1]
      idx += 1
      continue
    if arg in ("-d", "--data_dir"):
      if idx + 1 < len(args):
        data_dir = args[idx + 1]
      idx += 2
      continue
    if any(arg.startswith(f"{option}=") for option in _VALUE_OPTIONS if option.startswith("--")):
      idx += 1
      continue
    if arg in _VALUE_OPTIONS:
      idx += 2
      continue
    if arg in _NO_VALUE_OPTIONS:
      idx += 1
      continue
    if arg.startswith("-"):
      # Unknown replay option. Treat it as a flag so we do not mistake its value
      # for a route; replay itself will validate it later.
      idx += 1
      continue
    if route is None:
      route = arg
    idx += 1

  return ReplayArgs(route=route, data_dir=data_dir, auto_source=auto_source)


def _first_selected_segment(sr: SegmentRange) -> int:
  if not sr.slice:
    return 0

  if ":" not in sr.slice:
    segment = int(sr.slice)
    return sr.seg_idxs[0] if segment < 0 else segment

  match = _SEGMENT_SLICE_RE.fullmatch(sr.slice)
  if match is None:
    return sr.seg_idxs[0]

  start_raw = match.group("start")
  start = int(start_raw) if start_raw else 0
  return sr.seg_idxs[0] if start < 0 else start


def first_segment_identifier(route: str) -> str:
  parsed = parse_indirect(route)
  direct = parse_direct(parsed)
  if direct is not None:
    return str(direct)

  sr = SegmentRange(parsed)
  selector = sr.selector or "a"
  segment = _first_selected_segment(sr)
  return f"{sr.dongle_id}/{sr.log_id}/{segment}/{selector}"


def _local_route_identifiers(route: str, data_dir: str) -> list[str]:
  direct = parse_direct(route)
  if direct is not None:
    return [direct]

  try:
    parsed = parse_indirect(route)
    sr = SegmentRange(parsed)
    segment = _first_selected_segment(sr)
    route_name = sr.route_name.replace("/", "|")
  except Exception:
    # Local comma route format, e.g. 00000040--d083928baf.
    # replay accepts this format directly, while SegmentRange does not.
    data_root = Path(data_dir)
    if (data_root / f"{route}--0").is_dir():
      route_name = route
      segment = 0
    else:
      return []

  data_root = Path(data_dir)
  route_name_slash = route_name.replace("|", "/")
  segment_names = (f"{route_name}--{segment}", f"{route_name_slash}/{segment}")
  filenames = ("rlog.zst", "rlog.bz2", "qlog.zst", "qlog.bz2")

  identifiers: list[str] = []
  for segment_name in segment_names:
    for filename in filenames:
      candidate = data_root / segment_name / filename
      if candidate.exists():
        identifiers.append(str(candidate))

  for filename in filenames:
    explorer_candidate = data_root / f"{route_name}--{segment}--{filename}"
    if explorer_candidate.exists():
      identifiers.append(str(explorer_candidate))

  return identifiers


def replay_log_identifiers(replay_args: ReplayArgs) -> list[str]:
  if replay_args.route is None or replay_args.auto_source:
    return []

  if replay_args.data_dir:
    identifiers = _local_route_identifiers(replay_args.route, replay_args.data_dir)
    if identifiers:
      return identifiers

  try:
    return [first_segment_identifier(replay_args.route)]
  except Exception as exc:
    print(f"Unable to resolve route metadata for onroad preview: {exc}", file=sys.stderr)
    return []


def load_init_data(replay_args: ReplayArgs) -> Any | None:
  for identifier in replay_log_identifiers(replay_args):
    try:
      init_data = LogReader(identifier, default_mode=ReadMode.AUTO).first("initData")
    except Exception as exc:
      print(f"Unable to read route initData from {identifier}: {exc}", file=sys.stderr)
      continue

    if init_data is not None:
      return init_data

  return None


def logged_params(init_data: Any | None) -> dict[str, bytes]:
  if init_data is None:
    return {}

  try:
    entries = init_data.params.entries
  except Exception:
    return {}

  params: dict[str, bytes] = {}
  for entry in entries:
    params[str(entry.key)] = bytes(entry.value)
  return params


def select_ui_target(init_data: Any | None) -> str:
  if init_data is None:
    return "c3"

  device_type = str(getattr(init_data, "deviceType", "")).lower()
  if device_type in {"mici", "c4"}:
    return "c4"

  return "c3"


def seed_logged_params(init_data: Any | None, params: Params) -> int:
  seeded = 0
  for key, raw_value in logged_params(init_data).items():
    if raw_value == b"":
      continue

    try:
      value = params.cpp2python(key, raw_value)
      if value is None:
        continue
      params.put(key, value)
      seeded += 1
    except (UnknownKeyName, TypeError, ValueError):
      continue

  return seeded


def _model_uses_external_gpu(model_key: str) -> bool:
  from openpilot.starpilot.assets.model_manager import model_uses_external_gpu

  return model_uses_external_gpu(model_key)


def seed_replay_gpu_model(init_data: Any | None, params: Params) -> None:
  raw_model = logged_params(init_data).get("Model", b"")
  model_key = raw_model.decode("utf-8", errors="replace").strip()
  if model_key and _model_uses_external_gpu(model_key):
    params.put_bool("UsbGpuCompiled", True)


def seed_nav_offroad_preview(params: Params) -> None:
  secret = params.get("MapboxSecretKey", encoding="utf-8") or ""
  if not str(secret).strip():
    params.put("MapboxSecretKey", NAV_DEMO_MAPBOX_SECRET)

  raw_favorites = params.get(FAVORITE_DESTINATIONS_KEY, encoding="utf-8")
  if not load_favorite_destinations(raw_favorites):
    params.put(FAVORITE_DESTINATIONS_KEY, NAV_DEMO_FAVORITES)


def seed_desktop_overrides(params: Params) -> None:
  params.put("HasAcceptedTerms", terms_version)
  params.put("CompletedTrainingVersion", training_version)
  params.put_bool("OpenpilotEnabledToggle", True)
  params.put_bool("IsDriverViewEnabled", False)
  params.put_bool("ForceOnroad", False)
  params.put_bool("ForceOffroad", False)
  if _truthy_env("SP_ONROAD_NAV_DEMO"):
    params.put_bool("NavigationUI", True)
    if _truthy_env("SP_ONROAD_OFFROAD_DEMO"):
      params.put_bool("ForceOffroad", True)
      seed_nav_offroad_preview(params)


def seed_onroad_params(init_data: Any | None, params: Params | None = None) -> int:
  params = params or Params()
  seeded = seed_logged_params(init_data, params)
  seed_replay_gpu_model(init_data, params)
  seed_desktop_overrides(params)
  return seeded


def _cmd_select_ui(args: Sequence[str]) -> int:
  init_data = load_init_data(parse_replay_args(args))
  print(select_ui_target(init_data))
  return 0


def _cmd_seed(args: Sequence[str]) -> int:
  init_data = load_init_data(parse_replay_args(args))
  seeded = seed_onroad_params(init_data)
  if init_data is not None:
    print(f"Seeded {seeded} logged route params for onroad preview.", file=sys.stderr)
  else:
    print("No route initData found; using desktop onroad defaults.", file=sys.stderr)
  return 0


def _cmd_sync_gpu() -> int:
  from cereal import messaging

  params = Params()
  sm = messaging.SubMaster(["deviceState", "onroadEvents", "modelV2"])
  state = ReplayGpuState(compiled=params.get_bool("UsbGpuCompiled"))
  last_values: tuple[bool, bool, bool, bool] | None = None

  while True:
    sm.update(1000)
    if not any(sm.updated.values()):
      continue

    present = bool(sm["deviceState"].chestnutPresent) if sm.updated["deviceState"] else None
    event_names = tuple(str(event.name) for event in sm["onroadEvents"]) if sm.updated["onroadEvents"] else ()
    values = state.update(present=present, event_names=event_names, model_updated=sm.updated["modelV2"])
    if values == last_values:
      continue

    for key, value in zip(("UsbGpuPresent", "UsbGpuCompiled", "UsbGpuLoading", "UsbGpuActive"), values, strict=True):
      params.put_bool(key, value)
    last_values = values


def main(argv: Sequence[str] | None = None) -> int:
  argv = list(sys.argv[1:] if argv is None else argv)
  if not argv or argv[0] in {"-h", "--help"}:
    print("Usage: onroad_config.py (select-ui|seed|sync-gpu) <replay-args...>", file=sys.stderr)
    return 2

  command = argv[0]
  args = argv[1:]
  if args[:1] == ["--"]:
    args = args[1:]

  if command == "select-ui":
    return _cmd_select_ui(args)
  if command == "seed":
    return _cmd_seed(args)
  if command == "sync-gpu":
    return _cmd_sync_gpu()

  print(f"Unknown command: {command}", file=sys.stderr)
  return 2


if __name__ == "__main__":
  raise SystemExit(main())
