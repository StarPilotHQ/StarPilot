import json
from pathlib import Path
import subprocess
from unittest.mock import Mock, patch

import pytest

from openpilot.common.hardware.comma import agnos

ROOT = Path(__file__).resolve().parents[5]


@pytest.fixture
def manifest(tmp_path):
  path = tmp_path / 'agnos.json'
  path.write_text(json.dumps([{'name': 'abl', 'hash_raw': agnos.INCOMPATIBLE_C3_ABL, 'full_check': True}]))
  return path


@pytest.mark.parametrize('entrypoint', ['flash_agnos_update', 'verify_agnos_update', 'swap'])
def test_c3_rejected_before_any_partition_or_slot_operation(tmp_path, manifest, entrypoint):
  model = tmp_path / 'model'
  model.write_bytes(b'comma tici\0')
  with patch.object(agnos, 'DEVICE_MODEL_PATH', model), \
       patch.object(agnos, 'verify_partition') as verify, \
       patch.object(agnos, 'flash_partition') as flash, \
       patch.object(agnos, 'clear_partition_hash') as clear, \
       patch.object(agnos.subprocess, 'run') as run, \
       patch.object(agnos.subprocess, 'check_output') as activate:
    args = (str(manifest), 1) if entrypoint == 'verify_agnos_update' else (str(manifest), 1, Mock())
    with pytest.raises(RuntimeError, match='incompatible with comma 3'):
      getattr(agnos, entrypoint)(*args)
    for operation in (verify, flash, clear, run, activate):
      operation.assert_not_called()


@pytest.mark.parametrize('model_name', ['comma tizi', 'comma mici'])
def test_other_devices_keep_manifest_verification(tmp_path, manifest, model_name):
  model = tmp_path / 'model'
  model.write_bytes(model_name.encode() + b'\0')
  with patch.object(agnos, 'DEVICE_MODEL_PATH', model), patch.object(agnos, 'verify_partition', return_value=True) as verify:
    assert agnos.verify_agnos_update(str(manifest), 1)
    verify.assert_called_once()


@pytest.mark.parametrize('model_name,policy', [('comma tici', 'retain'), ('comma tizi', 'auto'), ('comma mici', 'auto'), ('', 'retain'), ('unknown', 'retain')])
def test_new_policy_protects_older_updater(tmp_path, model_name, policy):
  model = tmp_path / 'model'
  model.write_bytes(model_name.encode() + b'\0')
  script = (ROOT / 'launch_env.sh').read_text().replace('/sys/firmware/devicetree/base/model', str(model))
  result = subprocess.check_output(['bash', '-c', script + '\nprintf "%s" "$AGNOS_UPDATE_POLICY"'], text=True)
  assert result == policy


def test_previous_compatible_manifest_not_rejected(tmp_path):
  model = tmp_path / 'model'
  model.write_bytes(b'comma tici\0')
  with patch.object(agnos, 'DEVICE_MODEL_PATH', model):
    agnos.check_manifest_compatibility([{'name': 'abl', 'hash_raw': '32a2174b5f764e95dfc54cf358ba01752943b1b3b90e626149c3da7d5f1830b6'}])


@pytest.mark.parametrize('model_name', ['', 'unknown', 'comma tici extra'])
def test_unknown_hardware_rejected(tmp_path, manifest, model_name):
  model = tmp_path / 'model'
  model.write_text(model_name)
  with patch.object(agnos, 'DEVICE_MODEL_PATH', model), patch.object(agnos, 'verify_partition') as verify:
    with pytest.raises(RuntimeError, match='Cannot identify supported hardware'):
      agnos.verify_agnos_update(str(manifest), 1)
    verify.assert_not_called()


def test_missing_model_on_agnos_rejected_before_verification(tmp_path, manifest):
  marker = tmp_path / 'AGNOS'
  marker.touch()
  with patch.object(agnos, 'DEVICE_MODEL_PATH', tmp_path / 'missing'), patch.object(agnos, 'AGNOS_MARKER', marker), \
       patch.object(agnos, 'verify_partition') as verify:
    with pytest.raises(RuntimeError, match='Cannot identify hardware'):
      agnos.verify_agnos_update(str(manifest), 1)
    verify.assert_not_called()


def test_missing_model_keeps_agnos_policy_retained(tmp_path):
  marker = tmp_path / 'AGNOS'
  marker.touch()
  script = (ROOT / 'launch_env.sh').read_text().replace('/sys/firmware/devicetree/base/model', str(tmp_path / 'missing'))
  script = script.replace('/AGNOS', str(marker))
  result = subprocess.check_output(['bash', '-c', script + '\nprintf "%s" "$AGNOS_UPDATE_POLICY"'], text=True)
  assert result == 'retain'


@pytest.mark.parametrize('model_name', ['comma tizi', 'comma mici'])
def test_other_devices_keep_flash_and_activation(tmp_path, manifest, model_name):
  model = tmp_path / 'model'
  model.write_bytes(model_name.encode() + b'\0')
  with patch.object(agnos, 'DEVICE_MODEL_PATH', model), patch.object(agnos, 'flash_partition') as flash, \
       patch.object(agnos.subprocess, 'run') as run, \
       patch.object(agnos.subprocess, 'check_output', return_value='lun as boot lun') as activate:
    agnos.flash_agnos_update(str(manifest), 1, Mock())
    run.assert_called_once()
    flash.assert_called_once()
    agnos.swap(str(manifest), 1, Mock())
    activate.assert_called_once()
