"""Jetlink connection choices do not change the local model catalog."""
from types import SimpleNamespace
import json
import tempfile
from pathlib import Path
import unittest

from jetlink.openpilot import Status
from openpilot.starpilot.models.catalog import CATALOG_PATH
from openpilot.starpilot.models.jetlink_settings import JetlinkSettings
from openpilot.starpilot.models.manager import ModelError, ModelManager, preferences

ONNX = "d" * 64


class MemoryParams:
  def __init__(self):
    self.values: dict[str, int | dict[str, str]] = {"JetlinkMode": 0, "JetlinkChargePhone": False}

  def get(self, key):
    return self.values.get(key)

  def get_bool(self, key):
    return self.values.get(key) is True

  def put(self, key, value, *, block):
    assert block
    self.values[key] = value

  put_bool = put

  def remove(self, key):
    self.values.pop(key, None)


class TestJetlinkSettings(unittest.TestCase):
  def setUp(self):
    self.params = MemoryParams()
    self.chestnut = False
    self.device = "tizi"
    self.status = Status(enabled=True, mode="usb", transport="USB", present=True, port=None, ready=True, reason=None,
                         progress={"stage": "ready"}, model="remote", default_model="remote")
    self.owner = JetlinkSettings(params=self.params, device_type=lambda: self.device,
                                chestnut=lambda: self.chestnut, link=lambda: SimpleNamespace(status=lambda: self.status))

  def test_prepared_is_not_execution(self):
    self.owner.configure({"mode": "usb"})
    view = self.owner.snapshot()
    self.assertEqual(view["state"], "prepared")
    self.assertTrue(view["prepared"])
    self.assertFalse(view["active"])
    self.assertIsNone(view["activeArtifactSha256"])
    prepared = self.status
    self.status = prepared._replace(running="running", active=True, running_sha256="b" * 64, running_checkpoint="ckpt")
    view = self.owner.snapshot()
    self.assertEqual((view["state"], view["runtimeState"], view["active"]), ("active", "running", True))
    self.assertEqual((view["activeArtifactSha256"], view["activeCheckpoint"]), ("b" * 64, "ckpt"))
    # jetlink's phases, in Galaxy's words where they differ
    for running, state in (("ready", "waiting for disengagement"), ("retrying", "fallback / reconnecting")):
      self.status = prepared._replace(running=running)
      self.assertEqual(self.owner.snapshot()["state"], state)
    self.status = prepared._replace(ready=False, progress={"stage": "download", "msg": "Downloading"})
    self.assertEqual((self.owner.snapshot()["state"], self.owner.snapshot()["progress"]), ("preparing", "Downloading"))
    self.status = prepared._replace(reason="no gadget")
    self.assertEqual((self.owner.snapshot()["state"], self.owner.snapshot()["reason"]), ("unavailable", "no gadget"))

  def test_exact_choices_and_conflicts(self):
    for payload in ({"mode": 1}, {"mode": "other"}, {"chargePhone": 1}, {"unknown": True}):
      with self.assertRaises(ValueError):
        self.owner.configure(payload)
    self.chestnut = True
    with self.assertRaisesRegex(ValueError, "Chestnut"):
      self.owner.configure({"mode": "usb"})
    self.owner.configure({"mode": "off"})
    self.chestnut = False
    self.device = "tici"
    with self.assertRaisesRegex(ValueError, "3X"):
      self.owner.configure({"mode": "ios"})
    self.owner.configure({"mode": "off"})
    self.device = "mici"
    self.owner.configure({"mode": "ios", "chargePhone": True})
    self.assertEqual(self.params.values, {"JetlinkMode": 2, "JetlinkChargePhone": True})

  def test_manager_parked_guard_and_catalog_preserved(self):
    with tempfile.TemporaryDirectory() as tmp:
      parked = [True]
      manager = ModelManager(root=Path(tmp), parked=lambda: parked[0], gpu_present=lambda: False, jetlink=self.owner)
      before = preferences(Path(tmp))
      manager.action("jetlink", {"mode": "usb"})
      self.assertEqual(preferences(Path(tmp)), before)
      parked[0] = False
      with self.assertRaisesRegex(ValueError, "vehicle off"):
        manager.action("jetlink", {"mode": "off"})
      self.assertEqual(self.params.values["JetlinkMode"], 1)

  def manager_with_onnx(self, tmp):
    """A catalog whose Lebowski names the ONNX it was built from and whose TT Driving does not, as the downloaded
    manifest does for the newer models."""
    catalog = json.loads(CATALOG_PATH.read_text())
    catalog["models"] = [{**m, "source_sha256": {"lebowski3_driving_supercombo.onnx": ONNX}} if m["id"] == "lebowski3" else m
                         for m in catalog["models"] if m["id"] in ("lebowski3", "tt-driving")]
    (Path(tmp) / "catalog.json").write_text(json.dumps(catalog))
    return ModelManager(root=Path(tmp), parked=lambda: True, gpu_present=lambda: False, jetlink=self.owner)

  def test_active_big_is_jetlinks_choice_by_its_onnx(self):
    with tempfile.TemporaryDirectory() as tmp:
      manager = self.manager_with_onnx(tmp)
      self.owner.configure({"mode": "usb"})
      view = manager.snapshot()
      self.assertTrue(view["jetlinkBig"])
      # neither is downloaded: the one whose ONNX the catalog names is selectable
      ready = {m["value"]: m["selectable"] for m in view["models"] if m["requiresGpu"]}
      self.assertEqual((ready["lebowski3"], ready["tt-driving"]), (True, False))
      self.assertEqual((view["activeBigModel"], view["bigNoneLabel"]), ("", "Jetlink app's choice"))
      # no download: Jetlink's host fetches the ONNX itself
      manager.action("active", {"profile": "big", "model": "lebowski3"})
      label = next(m["label"] for m in view["models"] if m["value"] == "lebowski3")
      self.assertEqual(self.params.values["JetlinkModel"], {"sha256": ONNX, "displayName": label})
      self.assertEqual(manager.snapshot()["activeBigModel"], "lebowski3")
      # Chestnut's own choice is not Jetlink's
      self.assertEqual(preferences(Path(tmp))["big"], "")
      with self.assertRaisesRegex(ModelError, "names no ONNX"):
        manager.action("active", {"profile": "big", "model": "tt-driving"})
      # a newer choice made in the Jetlink app, which jetlink writes back: shown as the app's when not listed
      self.params.values["JetlinkModel"] = {"sha256": "c" * 64, "ref": "b" * 40, "displayName": "Cinque Terre V3 Model"}
      view = manager.snapshot()
      self.assertEqual((view["activeBigModel"], view["bigNoneLabel"]), ("", "Cinque Terre V3 Model · from the Jetlink app"))
      # none leaves it to the Jetlink app
      manager.action("active", {"profile": "big", "model": ""})
      self.assertNotIn("JetlinkModel", self.params.values)

  def test_without_jetlink_active_big_is_chestnuts(self):
    with tempfile.TemporaryDirectory() as tmp:
      manager = self.manager_with_onnx(tmp)
      view = manager.snapshot()
      self.assertFalse(view["jetlinkBig"])
      self.assertFalse(next(m for m in view["models"] if m["value"] == "lebowski3")["selectable"])
      with self.assertRaisesRegex(ModelError, "Download and verify"):
        manager.action("active", {"profile": "big", "model": "lebowski3"})
      self.owner.configure({"mode": "usb"})
      self.chestnut = True
      self.assertFalse(manager.snapshot()["jetlinkBig"])
      with self.assertRaisesRegex(ModelError, "Download and verify"):
        manager.action("active", {"profile": "big", "model": "lebowski3"})
      self.assertNotIn("JetlinkModel", self.params.values)
