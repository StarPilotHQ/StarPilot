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


@pytest.mark.parametrize('model_name,policy', [('comma tici', 'auto'), ('comma tizi', 'auto'), ('comma mici', 'auto'), ('', 'retain'), ('unknown', 'retain')])
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



@pytest.fixture
def release_bundle(tmp_path):
  source = ROOT / 'openpilot/common/hardware/comma'
  for name in ('agnos.json', 'agnos-c3.json'):
    (tmp_path / name).write_bytes((source / name).read_bytes())
  return tmp_path / 'agnos.json'


@pytest.mark.parametrize('model_name,manifest_name', [
  ('comma tici', 'agnos-c3.json'), ('comma tizi', 'agnos.json'), ('comma mici', 'agnos.json'),
])
@pytest.mark.parametrize('entrypoint', ['flash_agnos_update', 'verify_agnos_update', 'swap'])
def test_actual_update_entrypoints_select_complete_device_bundle(tmp_path, release_bundle, model_name, manifest_name, entrypoint):
  model = tmp_path / 'model'
  model.write_bytes(model_name.encode() + b'\0')
  expected = json.loads((tmp_path / manifest_name).read_text())
  with patch.object(agnos, 'DEVICE_MODEL_PATH', model), \
       patch.object(agnos, 'verify_partition', return_value=True) as verify, \
       patch.object(agnos, 'flash_partition') as flash, \
       patch.object(agnos, 'clear_partition_hash') as clear, \
       patch.object(agnos.subprocess, 'run') as run, \
       patch.object(agnos.subprocess, 'check_output', return_value='lun as boot lun') as activate:
    if entrypoint == 'verify_agnos_update':
      assert agnos.verify_agnos_update(str(release_bundle), 1)
      assert [c.args[1] for c in verify.call_args_list] == expected
      run.assert_not_called()
      activate.assert_not_called()
    elif entrypoint == 'flash_agnos_update':
      agnos.flash_agnos_update(str(release_bundle), 1, Mock())
      assert [c.args[1] for c in flash.call_args_list] == expected
      run.assert_called_once_with('abctl --set_unbootable 1', shell=True)
      activate.assert_not_called()
    else:
      agnos.swap(str(release_bundle), 1, Mock())
      assert [c.args[1] for c in clear.call_args_list] == [p for p in expected if not p.get('full_check', False)]
      activate.assert_called_once()
      run.assert_not_called()


def test_c3_release_contains_complete_matching_chain_and_same_os():
  folder = ROOT / 'openpilot/common/hardware/comma'
  common = {p['name']: p for p in json.loads((folder / 'agnos.json').read_text())}
  c3 = {p['name']: p for p in json.loads((folder / 'agnos-c3.json').read_text())}
  expected = {
    'xbl': ('effa23294138e2297b85a5b482a885184c437b5ab25d74f2a62d4fce4e68f63b', 3282256),
    'xbl_config': ('63d019efed684601f145ef37628e62c8da73f5053a8e51d7de09e72b8b11f97c', 98124),
    'abl': ('32a2174b5f764e95dfc54cf358ba01752943b1b3b90e626149c3da7d5f1830b6', 274432),
    'aop': ('21370172e590bd4ea907a558bcd6df20dc7a6c7d38b8e62fdde18f4a512ba9e9', 184364),
    'devcfg': ('d7d7e52963bbedbbf8a7e66847579ca106a0a729ce2cf60f4b8d8ea4b535d620', 40336),
  }
  assert set(c3) == set(common) == {*expected, 'boot', 'system'}
  for name, (digest, size) in expected.items():
    entry = c3[name]
    assert entry['hash'] == entry['hash_raw'] == digest
    assert entry['size'] == size
    assert entry['full_check'] is True and entry['has_ab'] is True and entry['sparse'] is False
  assert c3['boot'] == common['boot']
  assert c3['system'] == common['system']


def test_missing_c3_bundle_still_rejects_incompatible_common_firmware(tmp_path, release_bundle):
  (tmp_path / 'agnos-c3.json').unlink()
  model = tmp_path / 'model'
  model.write_bytes(b'comma tici\0')
  with patch.object(agnos, 'DEVICE_MODEL_PATH', model), patch.object(agnos.subprocess, 'run') as run:
    with pytest.raises(RuntimeError, match='incompatible with comma 3'):
      agnos.flash_agnos_update(str(release_bundle), 1, Mock())
    run.assert_not_called()


def test_explicit_incompatible_manual_manifest_is_not_replaced(tmp_path, release_bundle):
  manual = tmp_path / 'manual.json'
  manual.write_bytes(release_bundle.read_bytes())
  model = tmp_path / 'model'
  model.write_bytes(b'comma tici\0')
  with patch.object(agnos, 'DEVICE_MODEL_PATH', model):
    with pytest.raises(RuntimeError, match='incompatible with comma 3'):
      agnos.load_manifest(str(manual))
