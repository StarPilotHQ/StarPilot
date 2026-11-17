"""Camera ownership regression; native imports, synthetic resource handles.

No graphics context is opened. Release functions record ownership operations,
so this checks close/destructor behavior rather than native GPU rendering.
"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from openpilot.common.basedir import BASEDIR


class TestCameraResources(unittest.TestCase):
  def test_close_then_destructor_releases_each_resource_once(self):
    script = '''
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import patch

for path in ("openpilot.selfdrive.ui.onroad.cameraview", "openpilot.selfdrive.ui.mici.onroad.cameraview"):
  module = import_module(path)
  owner = import_module(module.CameraView._clear_textures.__module__)
  for hardware in (False, True):
    view = object.__new__(module.CameraView)
    view.texture_y, view.texture_uv = SimpleNamespace(id=11), SimpleNamespace(id=12)
    view.shader = SimpleNamespace(id=19)
    view.egl_texture = SimpleNamespace(id=13) if hardware else None
    view.egl_images = {1: "owned-image"} if hardware else {}
    view.frame, view.client, view.available_streams = None, None, set()
    view._clients = {}
    textures, shaders, images = [], [], []
    with patch.object(module, "COMMA_HARDWARE", hardware), \\
         patch.object(owner, "COMMA_HARDWARE", hardware), \\
         patch.object(module.rl, "unload_texture", side_effect=lambda texture: textures.append(texture.id)), \\
         patch.object(module.rl, "unload_shader", side_effect=lambda shader: shaders.append(shader.id)), \\
         patch.object(owner, "destroy_egl_image", side_effect=images.append):
      try:
        view.close()
        view.close()
        view.__del__()
        assert textures == ([11, 12, 13] if hardware else [11, 12]), (path, hardware, textures)
        assert shaders == [19], (path, hardware, shaders)
        assert images == (["owned-image"] if hardware else []), (path, hardware, images)
      finally:
        # Keep the synthetic handle out of real native cleanup even on failure.
        view.shader.id = 0
        view.texture_y = view.texture_uv = view.egl_texture = None
        view.egl_images.clear()
    del view
'''
    ipc_root = "/tmp" if sys.platform == "darwin" else "/dev/shm"
    with tempfile.TemporaryDirectory() as temporary, tempfile.TemporaryDirectory(prefix="msgq_camera-close-", dir=ipc_root) as ipc:
      environment = dict(os.environ, PARAMS_ROOT=str(Path(temporary) / "params"),
                         OPENPILOT_PREFIX=Path(ipc).name.removeprefix("msgq_"))
      for name in ("CEREAL_FAKE", "CEREAL_FAKE_PREFIX", "SIMULATION"):
        environment.pop(name, None)
      if sys.platform == "darwin":
        environment.pop("RAYLIB_BACKEND", None)
      result = subprocess.run([sys.executable, "-c", script], cwd=BASEDIR, env=environment, capture_output=True, text=True, timeout=30)
      self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
  unittest.main()
