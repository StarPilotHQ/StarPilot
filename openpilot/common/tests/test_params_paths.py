"""Runtime device/PC path selection and inherited native-process environment."""

import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from openpilot.common.params_paths import params_path

ROOT = Path(__file__).resolve().parents[3]


class TestParamsPaths(unittest.TestCase):
  def test_device_default_is_persistent_without_native_hardware_detection(self):
    with patch.dict(os.environ, {}, clear=True), patch("os.path.isfile", return_value=True):
      self.assertEqual(params_path(), "/data/params")

  def test_actual_python_caller_and_native_reader_share_device_startup_params(self):
    import tempfile
    from openpilot.common import params as native_params

    with tempfile.TemporaryDirectory() as directory:
      device = Path(directory) / "device-params"
      create = native_params.params_create
      requested = []

      def isolated_create(path, size):
        # Observe the real caller's device path; keep all native I/O private.
        self.assertEqual(path, b"/data/params")
        self.assertEqual(size, len(path))
        requested.append(path)
        target = os.fsencode(device)
        return create(target, len(target))

      reader = native_params.Params(str(device))
      reader.put_bool("ControlsReady", True, block=True)
      reader.put_bool("FirmwareQueryDone", True, block=True)
      reader.put("CarParams", b"previous-route", block=True)
      reader.put_bool("AlwaysOnLateral", True, block=True)
      with patch.dict(os.environ, {k: v for k, v in os.environ.items() if k != "PARAMS_ROOT"}, clear=True), patch("os.path.isfile", return_value=True), \
           patch.object(native_params, "params_create", side_effect=isolated_create):
        writer = native_params.Params()
      writer.clear_all(native_params.ParamKeyFlag.CLEAR_ON_ONROAD_TRANSITION)
      self.assertEqual(requested, [b"/data/params"])
      self.assertEqual(writer.get_param_path(), reader.get_param_path())
      self.assertIsNone(reader.get("CarParams"))
      self.assertFalse(reader.get_bool("ControlsReady"))
      self.assertFalse(reader.get_bool("FirmwareQueryDone"))
      self.assertTrue(reader.get_bool("AlwaysOnLateral"))
      writer.put("GitCommit", "current-runtime", block=True)
      self.assertEqual(reader.get("GitCommit"), "current-runtime")

  def test_pc_default_remains_native_default(self):
    with patch.dict(os.environ, {}, clear=True), patch("os.path.isfile", return_value=False):
      self.assertEqual(params_path(), "")

  def test_explicit_path_wins_over_environment_on_device(self):
    with patch.dict(os.environ, {"PARAMS_ROOT": "/isolated/params"}), patch("os.path.isfile", return_value=True):
      self.assertEqual(params_path("/test/params"), "/test/params")
      self.assertEqual(params_path(b"/bytes/params"), b"/bytes/params")

  def test_explicit_environment_is_preserved_for_device_and_pc(self):
    for device in (False, True):
      with self.subTest(device=device), patch.dict(os.environ, {"PARAMS_ROOT": "/isolated/params"}), \
           patch("os.path.isfile", return_value=device):
        self.assertEqual(params_path(), "/isolated/params")

  def launch(self, device, configured, home):
    # Substitute hardware presence only; execute the actual launch_env.sh body.
    script = r"""
function [ {
  if [[ "$1" == "-f" && "$2" == "/AGNOS" ]]; then return "$DEVICE_STATUS"; fi
  builtin [ "$@"
}
source "$1"
bash -c 'printf "%s" "${PARAMS_ROOT-unset}"'
"""
    env = dict(os.environ, DEVICE_STATUS="0" if device else "1", HOME=home)
    env.pop("PARAMS_ROOT", None)
    if configured is not None:
      env["PARAMS_ROOT"] = configured
    return subprocess.check_output(["bash", "-c", script, "test", str(ROOT / "launch_env.sh")], env=env, text=True)

  def test_device_reboots_with_new_home_keep_same_inherited_root(self):
    for home in ("/tmp/first-home", "/tmp/rebooted-home"):
      for configured in (None, ""):
        with self.subTest(home=home, configured=configured):
          self.assertEqual(self.launch(True, configured, home), "/data/params")

  def test_launcher_preserves_explicit_root_and_pc_default(self):
    for device in (False, True):
      with self.subTest(device=device):
        self.assertEqual(self.launch(device, "/isolated/params", "/tmp/home"), "/isolated/params")
    self.assertEqual(self.launch(False, None, "/tmp/home"), "unset")


if __name__ == "__main__":
  unittest.main()
