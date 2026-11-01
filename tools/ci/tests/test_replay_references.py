from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from tools.ci.replay_references import REFERENCE_DIR, config_signature, reviewed_reference


@dataclass
class Config:
  proc_name: str = "card"
  pubs: list[str] = field(default_factory=lambda: ["can"])
  ignore: list[str] = field(default_factory=list)
  tolerance: float = 0.0


class Callback:
  def __init__(self, trigger):
    self.trigger = trigger
  def __call__(self):
    return self.trigger


class TestReviewedReferences(unittest.TestCase):
  def setUp(self):
    self.directory = tempfile.TemporaryDirectory()
    self.addCleanup(self.directory.cleanup)
    self.root = Path(self.directory.name)
    self.cfg = Config()
    self.input = b"independent input"
    expected = b"approved expected output"
    (self.root / "card.zst").write_bytes(expected)
    self.record = {"official_baseline_commit": "official", "official_artifact_commit": "artifact",
                   "segment": "known", "process": "card", "file": "card.zst", "bytes": len(expected),
                   "sha256": hashlib.sha256(expected).hexdigest(), "input_bytes": len(self.input),
                   "input_sha256": hashlib.sha256(self.input).hexdigest(), "config_sha256": config_signature(self.cfg)}
    self.write_manifest()

  def write_manifest(self):
    (self.root / "manifest.json").write_text(json.dumps({"version": 1, "official_baseline_commit": "official",
                                                         "official_artifact_commit": "artifact", "references": [self.record]}))

  def select(self, segment="known", raw=None):
    return reviewed_reference(segment, self.cfg, self.input if raw is None else raw,
                              official_commit="official", artifact_commit="artifact", reference_dir=self.root)

  def test_exact_pair_and_official_fallback(self):
    self.assertEqual(self.select(), str(self.root / "card.zst"))
    self.assertIsNone(self.select("unknown"))
    self.cfg.proc_name = "controlsd"
    self.assertIsNone(self.select())

  def test_modified_reference_is_rejected(self):
    (self.root / "card.zst").write_bytes(b"unreviewed")
    with self.assertRaisesRegex(ValueError, "reference changed"):
      self.select()

  def test_modified_input_is_rejected(self):
    with self.assertRaisesRegex(ValueError, "input changed"):
      self.select(raw=b"other input")

  def test_comparison_configuration_change_is_rejected(self):
    self.cfg.ignore.append("sendcan")
    with self.assertRaisesRegex(ValueError, "configuration changed"):
      self.select()

  def test_callback_signature_is_stable_and_includes_parameters(self):
    @dataclass
    class CallbackConfig:
      callback: object
    self.assertEqual(config_signature(CallbackConfig(Callback("can"))), config_signature(CallbackConfig(Callback("can"))))
    self.assertNotEqual(config_signature(CallbackConfig(Callback("can"))), config_signature(CallbackConfig(Callback("carState"))))

  def test_local_filename_and_duplicate_checks(self):
    self.record["file"] = "../card.zst"
    self.write_manifest()
    with self.assertRaisesRegex(ValueError, "local filename"):
      self.select()
    (self.root / "manifest.json").write_text(json.dumps({"version": 1, "official_baseline_commit": "official",
                                                         "official_artifact_commit": "artifact", "references": [self.record, self.record]}))
    with self.assertRaisesRegex(ValueError, "Duplicate"):
      self.select()


  def test_reviewed_ford_card_reference_matches_manifest(self):
    manifest = json.loads((REFERENCE_DIR / "manifest.json").read_text())
    filename = "regen755D8CB1E1F_2025-04-08--23-13-43--0_card.zst"
    records = [record for record in manifest["references"] if record["file"] == filename]
    self.assertEqual(len(records), 1)
    raw = (REFERENCE_DIR / filename).read_bytes()
    self.assertEqual(len(raw), records[0]["bytes"])
    self.assertEqual(hashlib.sha256(raw).hexdigest(), records[0]["sha256"])
