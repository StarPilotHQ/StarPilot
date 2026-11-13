"""Run the browser-native monitor contract through the repository unittest runner."""

import os
import json
from pathlib import Path
import shutil
import subprocess
import unittest


TESTS = tuple(path.name for path in sorted(Path(__file__).parent.glob('test_*.mjs')))


class SystemMonitorJavaScriptTest(unittest.TestCase):
  def test_projection_backend_snapshot_survives_browser_edit_and_save(self):
    from openpilot.starpilot.system.starport.projection_layout import default_layout, layout_metadata, validate_layout
    from openpilot.starpilot.ui.onroad_customization import default_document

    screen = {'version': 1, 'width': 1280, 'height': 720, 'margin_width': 0,
              'margin_height': 240, 'fps': 60, 'config_index': 0}
    document, colors = default_layout(screen), default_document()
    raw = {'version': 1, 'document': document, 'defaults': document, 'screen': screen,
           'metadata': layout_metadata(screen), 'revision': 'backend-layout', 'valid': True, 'editable': True,
           'colors': {'palette': colors['palette'], 'widgetColors': colors['widgetColors']['large'],
                      'roadColors': colors['roadColors']['large']}}
    node = shutil.which(os.environ.get('STARPILOT_NODE') or 'node')
    self.assertIsNotNone(node, 'Node.js is required for the projection editor contract')
    assert node is not None
    result = subprocess.run([node, '--input-type=module', '-e', """
import { readFileSync } from 'node:fs'
import assert from 'node:assert/strict'
import { editorSnapshot, projectionPayload } from './web/js/projection-layout.js'
import { validSnapshot } from './web/js/onroad-layout.js'
const raw = JSON.parse(readFileSync(0, 'utf8'))
const snapshot = editorSnapshot(raw)
assert.ok(validSnapshot(snapshot))
snapshot.document.layouts.large.current_speed.x += 20
console.log(JSON.stringify(projectionPayload({revision: raw.revision, document: snapshot.document}, snapshot.metadata)))
"""], cwd=Path(__file__).parents[1], input=json.dumps(raw), capture_output=True, text=True, timeout=30, check=False)
    self.assertEqual(result.returncode, 0, result.stderr)
    payload = json.loads(result.stdout.splitlines()[-1])
    document['widgets']['current_speed']['x'] += 20
    self.assertEqual(payload['revision'], raw['revision'])
    self.assertEqual(validate_layout(payload['document'], screen), document)

  def test_snapshot_and_process_contract(self):
    requested = os.environ.get("STARPILOT_NODE") or "node"
    node = shutil.which(requested)
    self.assertIsNotNone(node, f"Node.js runtime not found: {requested}. Set STARPILOT_NODE to a Node 24 executable.")
    assert node is not None

    for test in TESTS:
      with self.subTest(test=test):
        timeout = 55 if test == "test_flm_ux_browser.mjs" else 30
        try:
          result = subprocess.run([node, str(Path(__file__).with_name(test))], capture_output=True, text=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired as exc:
          self.fail(f"System Monitor JavaScript contract timed out after {timeout} seconds: {exc}")
        detail = "\n".join((f"System Monitor JavaScript contract failed ({result.returncode}).",
                            f"stdout:\n{result.stdout}", f"stderr:\n{result.stderr}"))
        self.assertEqual(result.returncode, 0, detail)
