"""Driving-model receipt and shared native catalog action rules."""

import unicodedata
from dataclasses import replace

from openpilot.starpilot.models.catalog import BUNDLED_CURRENT, model_entries
from openpilot.starpilot.models.status import ModelHealth, ModelStatus, ModelVariant
from openpilot.starpilot.ui.feature_settings_state import FeatureRow, FeatureSettingsState


OFFROAD = "Available with the vehicle off"
FILTERS = (("installed", "Installed"), ("favorites", "Favorites"), ("all", "All"))
SORTS = (("date", "Newest"), ("date_oldest", "Oldest"), ("name", "A-Z"))
SIZES = (("all", "All"), ("big", "Big"), ("small", "Small"))


def model_toolbar(page: str) -> tuple[tuple[str, str, tuple[tuple[str, str], ...]], ...]:
  # Active Small/Big lists are already one size.
  size = (("size", "Size", SIZES),) if page == "models:catalog" else ()
  return (("filter", "Show", FILTERS),) + size + (("sort", "Sort", SORTS),)


def model_page(status: ModelStatus, manager: dict | None = None, page: str = "models", filter_: str = "installed",
               sort: str = "date", size: str = "all") -> FeatureSettingsState:
  """Pages: models (overview), models:catalog and models:pick:<small|big>."""
  entries = model_entries()

  def name(mid):
    return model_display_text(entries[mid].name) if mid in entries else mid

  if status.health is ModelHealth.ACTIVE:
    health = "Active"
  elif status.health is ModelHealth.LOADING:
    health = "Starting"
  elif status.health is ModelHealth.STALE:
    health = "Model output stale"
  elif status.health is ModelHealth.FAILED:
    health = "Model status mismatch"
  elif status.health is ModelHealth.IDENTITY_UNAVAILABLE:
    health = "Identity unavailable"
  else:
    health = "Model not running"
  variant = ("Chestnut big" if status.variant is ModelVariant.CHESTNUT else "Small") if status.loaded_id else "Unavailable"
  rows = (
    FeatureRow("", "Requested model", name(status.requested_id) if status.requested_id in entries else "Unavailable",
               reason="Saved selection for the next start" if status.pending_next_start else "Current request"),
    FeatureRow("", "Runtime", health, reason="Updates while driving"),
    FeatureRow("", "Loaded variant", variant),
    FeatureRow("", "Compiled artifact", status.artifact_sha256[:16] if status.artifact_sha256 else "Unavailable",
               reason="SHA-256 prefix" if status.artifact_sha256 else "No verified load receipt"),
  )
  if status.loaded_id:
    rows += (FeatureRow("", "Loaded model", name(status.loaded_id)),)
  if status.fallback_reason == "chestnut-run-stalled":
    rows += (FeatureRow("", "Fallback", name(status.loaded_id) if status.loaded_id in entries else "Active Small",
                         reason="Chestnut output stopped; Small restarted for this drive"),)
  elif status.fallback_reason == "chestnut-load-failed":
    rows += (FeatureRow("", "Fallback", name(status.loaded_id) if status.loaded_id in entries else "Active Small",
                         reason="Chestnut load or run failed"),)
  elif status.fallback_reason == "selected-load-failed":
    rows += (FeatureRow("", "Fallback", "Bundled driving model", reason="Selected model load or run failed"),)
  progress = _plain(manager.get("progress", "")) if manager and manager.get("downloading") else ""
  if manager is None or page == "models":
    return FeatureSettingsState(page="models", title="Driving Model", subtitle=progress or f"{health} - Selections apply at next start",
                                subtitle_accent="" if progress else health,
                                subtitle_tone="ok" if status.health is ModelHealth.ACTIVE else "warn",
                                rows=(model_manager_rows(manager) if manager is not None else ()) + rows)
  profile = page.split(":", 2)[2] if page.startswith("models:pick:") else ""
  return FeatureSettingsState(page=page, title=f"Active {profile.title()}" if profile else "Model Catalog", parent_title="Driving Model",
                              subtitle=progress or "Selections apply at next start; tap a name for details",
                              rows=model_list_rows(manager, profile, filter_, sort, size),
                              toolbar=tuple((title, dict(options)[chosen]) for (_, title, options), chosen in
                                            zip(model_toolbar(page), (filter_, size, sort) if not profile else (filter_, sort), strict=True)))


def model_fits_profile(model: dict, profile: str) -> bool:
  profiles = model.get("profiles")
  return profile in profiles if isinstance(profiles, list) else model.get("requiresGpu") is (profile == "big")


def big_none_label(data: dict) -> str:
  """Active Big with no model of its own, as the manager words it: under Jetlink, the Jetlink app's choice."""
  return str(data.get("bigNoneLabel") or "None — always use Active Small")


def model_action_allowed(data: dict, action: str, model: dict | None = None, profile: str = "") -> bool:
  capability = {"active": "select", "preferences": "favorites", "download_all": "downloadAll", "refresh_manifest": "refresh",
                "exclusion": "exclusions"}.get(action, action)
  if data.get("capabilities", {}).get(capability) is not True:
    return False
  if data.get("isOnroad") is not False and action not in ("preferences", "cancel", "active"):
    return False
  if action == "active":
    if profile == "big" and data.get("jetlinkBig") and data.get("isOnroad") is not False:
      return False
    return (not data.get("randomizer") and (not data.get("downloading") or data.get("refreshing") is True) and profile in ("small", "big") and
            ((model is None and profile == "big") or
             bool(model and model.get("selectable") is True and model_fits_profile(model, profile))))
  if action == "download":
    return not data.get("downloading") and bool(model and not model.get("installed") and model.get("downloadAvailable") is True)
  if action == "download_all":
    return not data.get("downloading") and any(not m.get("installed") and m.get("downloadAvailable") is True for m in data.get("models", ()))
  if action == "cancel":
    return data.get("downloading") is True
  if action == "delete":
    return bool(not data.get("downloading") and model and not model.get("builtin") and model.get("deletable") is not False and
                (model.get("installed") or model.get("partial")) and
                model.get("value") not in (data.get("currentModel"), data.get("activeSmallModel"), data.get("activeBigModel")))
  if action == "randomizer":
    return True
  if action == "exclusion":
    return model is not None
  return action == "preferences" or action == "refresh_manifest" and not data.get("downloading")


def model_catalog_rows(data: dict, sort_mode: str = "date", profile: str = "", installed_only: bool = False) -> list[dict]:
  rows = [m for m in data.get("models", ()) if isinstance(m, dict) and isinstance(m.get("value"), str) and
          (not profile or model_fits_profile(m, profile)) and
          (not installed_only or m.get("selectable") is True)]
  if sort_mode == "favorites":
    rows = [m for m in rows if m.get("userFavorite")]
  elif sort_mode == "community":
    rows = [m for m in rows if m.get("communityFavorite")]
  rows.sort(key=lambda m: str(m.get("label", m["value"])).casefold())
  if sort_mode in ("date", "date_oldest"):
    rows.sort(key=lambda m: str(m.get("released", "")), reverse=sort_mode == "date")
  return rows



def _plain(text: str) -> str:
  # The large Inter atlas lacks bullets, dashes and ellipses; keep ASCII punctuation.
  return model_display_text(text).replace(" • ", ", ").replace("•", ",").replace("–", "-")


def _name(model: dict) -> str:
  return _plain(str(model.get("label", model["value"])))


def _main_button(data: dict, model: dict, profile: str) -> tuple[str, str, str] | None:
  mid = model["value"]
  if mid and data.get("downloading") and data.get("modelToDownload") == mid:
    return "Cancel", "cancel", ""
  if model.get("selectable"):
    active = data.get("activeSmallModel" if profile == "small" else "activeBigModel") == mid
    return ("Active", "", profile) if active else ("Use", "active", profile)
  if model.get("downloadAvailable"):
    size = model.get("artifactSize", 0)
    return (f"Get {size / 1e6:.0f} MB" if size else "Get", "download", "")
  return None


def model_buttons(data: dict, model: dict, profile: str = "") -> tuple[tuple[str, str, str], ...]:
  profile = profile or ("big" if model.get("requiresGpu") else "small")
  buttons = []
  if model["value"]:
    if not model.get("builtin") and (model.get("installed") or model.get("partial")):
      buttons.append(("Delete", "delete", ""))
    if data.get("randomizer"):  # exclusions only matter while the randomizer picks
      buttons.append(("Skipped" if model.get("blacklisted") else "Skip", "exclusion", ""))
    buttons.append(("Favorited" if model.get("userFavorite") else "Favorite", "preferences", ""))
  main = _main_button(data, model, profile)
  return tuple(buttons + ([main] if main else []))


def model_download_buttons(data: dict) -> tuple[tuple[str, str], ...]:
  return (("Cancel", "cancel"),) if data.get("downloading") else (("Refresh", "refresh_manifest"), ("Get All", "download_all"))


def model_row_intent(data: dict, page: str, key: str, index: int) -> tuple[str, str, str, str] | None:
  if key == "models:downloads":
    buttons = tuple((label, action, "") for label, action in model_download_buttons(data))
    mid = ""
  elif key.startswith("model:"):
    mid = key.split(":", 1)[1]
    profile = page.split(":", 2)[2] if page.startswith("models:pick:") else ""
    model = {"value": "", "selectable": True} if not mid else next((m for m in data.get("models", ()) if m.get("value") == mid), None)
    if model is None or not mid and profile != "big":
      return None
    buttons = model_buttons(data, model, profile)
  else:
    return None
  if not 0 <= index < len(buttons) or not buttons[index][1]:
    return None
  label, action, profile = buttons[index]
  return label, action, mid, profile


def _model_row(data: dict, model: dict, profile: str = "") -> FeatureRow:
  buttons = model_buttons(data, model, profile)
  mid = model["value"]
  main = buttons[-1][0] if buttons else ""
  status = ("Active" if main == "Active" else "Downloading" if main == "Cancel" else
            "" if not mid else "Installed" if model.get("installed") else model.get("unavailableReason") or "Download required")
  size = model.get("artifactSize", 0)
  tags = ("Chestnut" if model.get("requiresGpu") else "Small", model.get("version", ""), model.get("released", ""),
          f"{size / 1e6:.0f} MB" if size and not model.get("installed") else "", "Community pick" if model.get("communityFavorite") else "")
  return FeatureRow(f"model:{mid}", _name(model), status, reason=", ".join(_plain(t) for t in tags if t),
                    available=bool(buttons), actions=tuple(
                      (label, bool(action) and model_action_allowed(data, action, model if mid else None, p)) for label, action, p in buttons))


def model_list_rows(data: dict, profile: str, filter_: str, sort: str, size: str = "all") -> tuple[FeatureRow, ...]:
  rows = [] if profile else [_catalog_row(data)]
  if profile == "big":
    none = {"value": "", "label": big_none_label(data), "selectable": True}
    rows.append(replace(_model_row(data, none, "big"), reason="Run only Active Small"))
  models = model_catalog_rows(data, sort, profile or ("" if size == "all" else size), installed_only=filter_ == "installed")
  if filter_ == "favorites":
    models = [m for m in models if m.get("userFavorite")]
  rows.extend(_model_row(data, m, profile) for m in models)
  if not models:
    rows.append(FeatureRow("", "No models", "", reason="Try another filter" if filter_ != "all" else "Refresh the catalog"))
  return tuple(rows)


def model_needs_gpu(data: dict, action: str, model: dict | None) -> bool:
  return bool(action == "download" and model and model.get("requiresGpu") and not model.get("gpuAvailable") or
              action == "download_all" and any(not m.get("installed") and m.get("downloadAvailable") and m.get("requiresGpu") and
                                                not m.get("gpuAvailable") for m in data.get("models", ())))


def model_request(data: dict, action: str, model_id: str = "", profile: str = "", allow_gpu: bool = False) -> tuple[str, dict] | None:
  model = next((m for m in data.get("models", ()) if m.get("value") == model_id), None) if model_id else None
  if model_id and model is None or not model_action_allowed(data, action, model, profile):
    return None
  if action == "active":
    return action, {"profile": profile, "model": model_id}
  if action == "preferences":
    favorites = [m["value"] for m in data["models"] if m.get("userFavorite") and m["value"] != model_id]
    if model and not model.get("userFavorite"):
      favorites.append(model_id)
    return action, {"userFavorites": favorites}
  if action == "randomizer":
    return "preferences", {"randomizer": not data.get("randomizer", False)}
  if action == "exclusion":
    excluded = [m["value"] for m in data["models"] if m.get("blacklisted") and m["value"] != model_id]
    if model and not model.get("blacklisted"):
      excluded.append(model_id)
    return "preferences", {"blacklistedModels": excluded}
  if action == "download":
    return action, {"model": model_id, "allowGpuWithoutGpu": allow_gpu}
  if action == "download_all":
    return action, {"allowGpuWithoutGpu": allow_gpu}
  if action == "delete":
    return action, {"model": model_id}
  return action, {}


def model_manager_rows(data: dict) -> tuple[FeatureRow, ...]:
  names, buttons = [], []
  for profile, field in (("small", "activeSmallModel"), ("big", "activeBigModel")):
    mid = data.get(field, "")
    name = next((_name(m) for m in data.get("models", ()) if m.get("value") == mid),
                mid or (_plain(big_none_label(data)) if data.get("jetlinkBig") else "None"))
    names.append(f"{profile.title()}: {name}")
    buttons.append((profile.title(), data.get("capabilities", {}).get("select") is True and
                    not (profile == "big" and data.get("jetlinkBig") and data.get("isOnroad") is not False) and
                    (not data.get("downloading") or data.get("refreshing") is True) and not data.get("randomizer")))
  rows = [FeatureRow("models:active", "Active Models", "\n".join(names), available=True, actions=tuple(buttons),
                     reason="Randomizer picks at each start" if data.get("randomizer") else "")]
  randomizer = model_action_allowed(data, "randomizer")
  rows.append(FeatureRow("models:randomizer", "Model Randomizer", "On" if data.get("randomizer") else "Off", choices=("Off", "On"),
                         available=randomizer, reason="Picks an installed model at each start" if randomizer else OFFROAD))
  rows.append(FeatureRow("", "Model Catalog", _catalog_summary(data), reason="Download, delete and favorite", available=True,
                         page="models:catalog"))
  return tuple(rows)


def _catalog_summary(data: dict) -> str:
  summary = data.get("summary", {})
  return f"{summary.get('installed', 0)} installed, {summary.get('total', 0)} available"


def _catalog_row(data: dict) -> FeatureRow:
  return FeatureRow("models:downloads", "Catalog", _catalog_summary(data), available=True,
                    actions=tuple((label, model_action_allowed(data, action)) for label, action in model_download_buttons(data)),
                    reason="Downloading" if data.get("downloading") else _plain(data.get("progress", "")) or
                    ("Check for new models or get every missing one" if data.get("isOnroad") is False else OFFROAD))


def model_display_text(value: str) -> str:
  # The native Inter atlas has bullets and en dashes, but no emoji glyphs.
  text = value.replace("·", "•").replace("—", "–")
  return " ".join("".join(char for char in text if ord(char) <= 0xffff and
                          unicodedata.category(char) != "So" and char not in "\ufe0e\ufe0f\u200d").split())


def home_model_label(status: ModelStatus, commit: str = "", laboratory: dict | None = None) -> str:
  if laboratory and laboratory.get("lateralModel") and laboratory.get("longitudinalModel"):
    entries = model_entries()
    names = [model_display_text(entries[mid].name if mid in entries else mid)
             for mid in (laboratory["lateralModel"], laboratory["longitudinalModel"])]
    return "/".join(names)
  entry = model_entries().get(status.loaded_id or status.requested_id)
  if entry is None:
    return "Driving model unavailable"
  commit = commit.strip().lower()
  revision = commit[:7] if 7 <= len(commit) <= 64 and all(char in "0123456789abcdef" for char in commit) else ""
  name = entry.name
  if entry.model_id == BUNDLED_CURRENT:
    name = "Cinque v2" if status.variant is ModelVariant.CHESTNUT else "CD210"
  return model_display_text(name + (f" · {revision}" if revision else ""))
