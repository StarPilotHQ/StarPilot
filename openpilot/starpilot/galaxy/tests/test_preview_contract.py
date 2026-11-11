"""Offline Galaxy preview contract; no application or device modules are imported."""

import json
import re
import unittest
from pathlib import Path


WEB = Path(__file__).resolve().parents[1] / "web"


class PreviewContractTest(unittest.TestCase):
  def test_catalog_has_only_explicit_preview_capabilities(self):
    catalog = json.loads((WEB / "data/catalog.json").read_text())
    self.assertEqual(catalog["mode"], "offline-preview")
    paths = [tool["path"] for tool in catalog["tools"]]
    self.assertEqual(len(paths), len(set(paths)))
    self.assertTrue(all(path.startswith("/") and path != "/tools" for path in paths))
    self.assertEqual([tool["path"] for tool in catalog["tools"] if tool["availability"] == "partial-preview"], ["/logs"])
    self.assertEqual([tool["path"] for tool in catalog["tools"] if tool["availability"] == "local-only"],
                     ["/bluetooth", "/cameras", "/galaxy", "/manage_models", "/model_laboratory", "/navigation", "/system",
                      "/theme_maker", "/tuning", "/starport"])
    self.assertEqual([tool["path"] for tool in catalog["tools"] if tool["availability"] == "always"], [])
    self.assertTrue(all(tool["availability"] in {"always", "unavailable", "partial-preview", "local-only"} for tool in catalog["tools"]))
    self.assertTrue(all(tool["name"] and tool["description"] and tool["icon"] for tool in catalog["tools"]))

  def test_static_preview_and_read_only_runtime_are_explicit(self):
    scripts = "\n".join(path.read_text() for path in (WEB / "js").glob("*.js"))
    self.assertEqual(sorted(re.findall(r"\bfetch\s*\(\s*[\"']([^\"']+)", scripts)),
                     ['./api/auth/session', './api/drive-state/action', './api/drive-state/status',
                      '/_gateway/devices'])
    config = json.loads((WEB / 'data/runtime.json').read_text())
    self.assertEqual(config, {'schemaVersion': 1, 'monitor': 'sample'})
    self.assertIn('./api/ui/layout', scripts)
    self.assertIn('./api/ui/layout/preview', scripts)
    self.assertIn('this.fetcher', (WEB / 'js/onroad-layout.js').read_text())
    self.assertIn('./api/system/monitor', scripts)
    self.assertIn('./data/system-monitor.sample.json', scripts)
    self.assertIn('./api/software/status', scripts)
    self.assertIn('./api/maps/setup', scripts)
    self.assertIn('./api/models/status', scripts)
    self.assertIn('./api/plots/live', scripts)
    self.assertFalse(re.search(r"\bWebSocket\b", scripts))
    xhr_sources = [path.name for path in (WEB / "js").glob("*.js") if re.search(r"\bXMLHttpRequest\b", path.read_text())]
    self.assertEqual(xhr_sources, ["starport.js"])
    self.assertFalse(re.search(r"[\"']/embed", scripts))
    self.assertFalse(re.search(r"<iframe|createElement\s*\(\s*[\"']iframe", scripts))
    self.assertIn("No operation was attempted.", scripts)
    self.assertIn("Illustrative values only. No device was read.", scripts)

  def test_system_monitor_fixture_is_explicitly_synthetic(self):
    sample = json.loads((WEB / "data/system-monitor.sample.json").read_text())
    self.assertEqual(sample["mode"], "synthetic-preview")
    self.assertEqual(sample["schemaVersion"], 1)
    self.assertNotIn("deviceId", sample)


if __name__ == "__main__":
  unittest.main()
