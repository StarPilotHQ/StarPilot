"""Parked Jetlink selection and its read-only provisioning status."""
from __future__ import annotations

from openpilot.starpilot.models.jetlink_adapter import UNSUPPORTED

MODES = ("off", "usb", "ios")

# jetlink's Status.phase in Galaxy's words, where they differ
PHASES = {"waiting": "waiting for disengagement", "reconnecting": "fallback / reconnecting"}


class JetlinkSettings:
  def __init__(self, *, params=None, link=None, device_type=None, chestnut=None):
    self._params = params
    self._link = link
    self._device_type = device_type
    self._chestnut = chestnut

  def params(self):
    if self._params is None:
      from openpilot.common.params import Params
      self._params = Params()
    return self._params

  def snapshot(self):
    try:
      return self._snapshot()
    except (OSError, RuntimeError, ValueError, ImportError):
      return {"mode": "off", "supported": False, "chestnut": False, "enabled": False,
              "present": False, "prepared": False, "active": False, "runtimeState": "unavailable", "state": "unavailable",
              "reason": "Jetlink status is unavailable; the local model remains in use.",
              "progress": "", "model": None, "transport": "USB", "chargePhone": False}

  def _setting(self):
    """The Jetlink setting, whether this device runs Jetlink, and whether a Chestnut is fitted."""
    from openpilot.starpilot.models.jetlink_adapter import adapter, supported_device
    supported = supported_device(self._device_type() if self._device_type is not None else None)
    chestnut = self._chestnut() if self._chestnut is not None else adapter().chestnut_present()
    raw = self.params().get("JetlinkMode")
    return MODES[raw] if type(raw) is int and 0 <= raw < len(MODES) else "off", supported, chestnut

  def chooses_big(self, view=None):
    """Does Active Big choose Jetlink's model? With Jetlink on, on a device it runs on, and no Chestnut to run the
    big model itself. From `view`, a snapshot, when one is at hand."""
    try:
      mode, supported, chestnut = (view["mode"], view["supported"], view["chestnut"]) if view else self._setting()
    except (OSError, RuntimeError, ValueError, ImportError):
      return False
    return mode != "off" and supported and not chestnut

  def _snapshot(self):
    from openpilot.starpilot.models.jetlink_adapter import hooks
    mode, supported, chestnut = self._setting()
    params = self.params()
    status = (self._link() if self._link is not None else hooks()).status()
    reason = "" if supported else UNSUPPORTED + "."
    if mode != "off" and status is None:
      reason = reason or "Jetlink is unavailable; the local model remains in use."
    if status is not None and status.reason:
      reason = str(status.reason)
    usable = mode != "off" and supported and status is not None
    # the big model drives modeld's frames now, by modeld's runtime record
    active = usable and not chestnut and status.active
    state = "off" if mode == "off" else "unavailable" if reason else PHASES.get(status.phase, status.phase)
    progress = status.progress if status is not None and isinstance(status.progress, dict) else {}
    return {"mode": mode, "supported": supported, "chestnut": chestnut, "enabled": bool(usable and status.enabled),
            "present": bool(status and status.present), "prepared": bool(usable and status.ready),
            "active": active, "runtimeState": (status.running if status is not None else None) or "unavailable",
            "activeArtifactSha256": status.running_sha256 if active else None,
            "activeCheckpoint": status.running_checkpoint if active else None,
            "state": state, "reason": reason, "progress": str(progress.get("msg", "")),
            "model": status.model if status is not None else None,
            "transport": status.transport if status is not None else "USB",
            "chargePhone": params.get_bool("JetlinkChargePhone")}

  def pick(self):
    """Jetlink's big model as Active Big chose it, {sha256, displayName}, or None for the Jetlink app's choice.
    jetlink writes a newer choice made in its app here too, with the commit's ref where it has one."""
    from openpilot.starpilot.models.manager import SHA
    value = self.params().get("JetlinkModel")
    return value if isinstance(value, dict) and isinstance(value.get("sha256"), str) and SHA.fullmatch(value["sha256"]) else None

  def choose(self, sha256, name=""):
    """Choose Jetlink's big model by its ONNX's hash, or None to leave it to the Jetlink app. Only on a user's
    choice: jetlink dates the choice by this write, and the newer of it and the app's drives."""
    params = self.params()
    if sha256 is None:
      params.remove("JetlinkModel")
    else:
      params.put("JetlinkModel", {"sha256": sha256, "displayName": name}, block=True)

  @staticmethod
  def _wired_session_running():
    """Wired Android Auto holds the comma's USB-C port while a session runs."""
    try:
      from openpilot.starpilot.system.starport.protocol import StarPortClient
      status = StarPortClient().status()
      return status.get("connection") == "wired" and bool(status.get("running"))
    except Exception:
      return False

  def configure(self, payload):
    if not payload or set(payload) - {"mode", "chargePhone"}:
      raise ValueError("Invalid Jetlink settings")
    if "mode" in payload and payload["mode"] not in MODES:
      raise ValueError("Choose Off, USB, or iPhone")
    if "chargePhone" in payload and type(payload["chargePhone"]) is not bool:
      raise ValueError("Invalid phone charging setting")
    view = self.snapshot()
    if payload.get("mode", "off") != "off" and not view["supported"]:
      raise ValueError("Jetlink requires a comma 3X or comma 4")
    if payload.get("mode", "off") != "off" and view["chestnut"]:
      raise ValueError("Chestnut is connected; Jetlink cannot run alongside it")
    if payload.get("mode", "off") != "off" and self._wired_session_running():
      raise ValueError("Disconnect wired Android Auto before enabling Jetlink")
    params = self.params()
    if "chargePhone" in payload:
      params.put_bool("JetlinkChargePhone", payload["chargePhone"], block=True)
    if "mode" in payload:
      params.put("JetlinkMode", MODES.index(payload["mode"]), block=True)
    return {"message": "Jetlink settings saved. Check connection status before driving."}
