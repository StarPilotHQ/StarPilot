"""Driving-model receipt and shared native catalog action rules."""

import unicodedata

from openpilot.starpilot.models.catalog import BUNDLED_CURRENT, model_entries
from openpilot.starpilot.models.status import ModelHealth, ModelStatus, ModelVariant
from openpilot.starpilot.ui.feature_settings_state import FeatureRow, FeatureSettingsState


def model_page(status: ModelStatus, manager: dict | None = None) -> FeatureSettingsState:
  entries = model_entries()
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
    FeatureRow("", "Requested model", entries[status.requested_id].name if status.requested_id in entries else "Unavailable",
               reason="Saved selection for the next start" if status.pending_next_start else "Current request"),
    FeatureRow("", "Runtime", health, reason="Updates while driving"),
    FeatureRow("", "Loaded variant", variant),
    FeatureRow("", "Compiled artifact", status.artifact_sha256[:16] if status.artifact_sha256 else "Unavailable",
               reason="SHA-256 prefix" if status.artifact_sha256 else "No verified load receipt"),
  )
  if status.loaded_id:
    rows += (FeatureRow("", "Loaded model", entries[status.loaded_id].name if status.loaded_id in entries else status.loaded_id),)
  if status.fallback_reason == "chestnut-run-stalled":
    rows += (FeatureRow("", "Fallback", entries[status.loaded_id].name if status.loaded_id in entries else "Active Small",
                         reason="Chestnut output stopped; Small restarted for this drive"),)
  elif status.fallback_reason == "chestnut-load-failed":
    rows += (FeatureRow("", "Fallback", entries[status.loaded_id].name if status.loaded_id in entries else "Active Small",
                         reason="Chestnut load or run failed"),)
  elif status.fallback_reason == "selected-load-failed":
    rows += (FeatureRow("", "Fallback", "Bundled driving model", reason="Selected model load or run failed"),)
  if manager is not None:
    rows = model_manager_rows(manager) + rows
  return FeatureSettingsState(title="Driving Model", subtitle="Selections apply at next start; runtime shows the loaded model.",
                              rows=rows)


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



def model_profile_choices(data: dict, profile: str) -> tuple[tuple[str, str], ...]:
  rows = model_catalog_rows(data, "name", profile, installed_only=True)
  choices = tuple((m.get("label", m["value"]), m["value"]) for m in rows)
  return ((big_none_label(data), ""),) + choices if profile == "big" else choices


def model_profile_request(data: dict, profile: str, model_id: str) -> dict | None:
  model = next((m for m in data.get("models", ()) if m.get("value") == model_id), None)
  if model_id and model is None or not model_action_allowed(data, "active", model, profile):
    return None
  return {"profile": profile, "model": model_id}


def model_manager_rows(data: dict) -> tuple[FeatureRow, ...]:
  summary = data.get("summary", {})
  rows = [FeatureRow("", "Model catalog", f"{summary.get('installed', 0)} installed / {summary.get('total', 0)} total")]
  for profile, field in (("small", "activeSmallModel"), ("big", "activeBigModel")):
    mid = data.get(field, "")
    label = next((m.get("label", mid) for m in data.get("models", ()) if m.get("value") == mid), mid or big_none_label(data))
    available = (data.get("capabilities", {}).get("select") is True and
                 not (profile == "big" and data.get("jetlinkBig") and data.get("isOnroad") is not False) and
                 (not data.get("downloading") or data.get("refreshing") is True) and not data.get("randomizer"))
    rows.append(FeatureRow("", f"Active {profile.title()}", label, source=mid.encode(), available=available,
                           reason="Randomizer selects at the next start" if data.get("randomizer") else
                           "Applies next onroad cycle" if available else "Model selection unavailable", page=f"models:{profile}"))
  return tuple(rows)


def model_display_text(value: str) -> str:
  # The native Inter atlas has bullets and en dashes, but no emoji glyphs.
  text = value.replace("·", "•").replace("—", "–")
  return " ".join("".join(char for char in text if ord(char) <= 0xffff and
                          unicodedata.category(char) != "So" and char not in "\ufe0e\ufe0f\u200d").split())


def home_model_label(status: ModelStatus, commit: str = "") -> str:
  entry = model_entries().get(status.loaded_id or status.requested_id)
  if entry is None:
    return "Driving model unavailable"
  commit = commit.strip().lower()
  revision = commit[:7] if 7 <= len(commit) <= 64 and all(char in "0123456789abcdef" for char in commit) else ""
  name = entry.name
  if entry.model_id == BUNDLED_CURRENT:
    name = "Cinque v2" if status.variant is ModelVariant.CHESTNUT else "CD210"
  return model_display_text(name + (f" · {revision}" if revision else ""))
