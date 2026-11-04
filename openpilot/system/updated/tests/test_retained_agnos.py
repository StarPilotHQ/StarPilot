"""Retained-OS launcher/updater checks use only disposable files and fake hardware."""
import os
import re
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
from types import ModuleType
import unittest
from unittest.mock import Mock, patch

from openpilot.system.updated.tests.test_vendored_update import load_updater

ROOT = Path(__file__).resolve().parents[4]


def shell_function(path, name):
  source = path.read_text()
  match = re.search(r'function ' + re.escape(name) + r'(?:\(\))? \{', source)
  assert match is not None
  start = match.start()
  return source[start:source.index('\n}\n', start) + 3]


class TestRetainedAgnos(unittest.TestCase):
  def setUp(self):
    self.directory = tempfile.TemporaryDirectory()
    self.addCleanup(self.directory.cleanup)
    self.root = Path(self.directory.name)
    self.environment = self.root / 'launch_env.sh'
    self.environment.write_text('export AGNOS_VERSION="19.8.2"\nexport AGNOS_UPDATE_POLICY="retain"\n')
    self.updater = load_updater()
    self.updater.OVERLAY_MERGED = str(self.root)
    self.updater.set_consistent_flag = Mock()
    self.updater.HARDWARE.get_os_version.return_value = '19.8.2'
    self.flash = Mock()
    self.slot = Mock(return_value=1)
    agnos = ModuleType('openpilot.common.hardware.comma.agnos')
    agnos.flash_agnos_update = self.flash
    agnos.get_target_slot_number = self.slot
    patcher = patch.dict(sys.modules, {agnos.__name__: agnos})
    patcher.start()
    self.addCleanup(patcher.stop)

  def test_readiness_flag_never_follows_or_replaces_existing_marker(self):
    updated = load_updater()
    updated.FINALIZED = str(self.root)
    marker = self.root / '.overlay_consistent'
    target = self.root / 'untouched'
    target.write_text('keep')
    marker.symlink_to(target)
    with patch.object(updated.os, 'sync'):
      with self.assertRaises(FileExistsError):
        updated.set_consistent_flag(True)
      self.assertEqual(target.read_text(), 'keep')
      updated.set_consistent_flag(False)
      updated.set_consistent_flag(True)
      self.assertTrue(marker.is_file())
      self.assertFalse(marker.is_symlink())
      with self.assertRaises(FileExistsError):
        updated.set_consistent_flag(True)

  def test_launcher_readiness_rejects_symlink(self):
    function = shell_function(ROOT / 'launch_chffrplus.sh', 'update_is_ready')
    target = self.root / 'target'
    target.touch()
    marker = self.root / 'marker'
    marker.symlink_to(target)
    command = function + f'\nupdate_is_ready {shlex.quote(str(marker))}'
    self.assertNotEqual(subprocess.run(['bash', '-c', command]).returncode, 0)
    marker.unlink()
    marker.touch()
    self.assertEqual(subprocess.run(['bash', '-c', command]).returncode, 0)

  def test_launch_discards_inherited_os_version_overrides(self):
    function = shell_function(ROOT / 'launch_chffrplus.sh', 'reset_update_os_environment')
    env_file = self.root / 'target_launch_env.sh'
    env_file.write_text('if [ -z "$AGNOS_VERSION" ]; then export AGNOS_VERSION="19.8.1"; fi\n' +
                        'if [ -z "$AGNOS_ACCEPTED_VERSIONS" ]; then export AGNOS_ACCEPTED_VERSIONS="19.8.1 20.2.1"; fi\n')
    script = function + '\nexport AGNOS_VERSION=wrong AGNOS_ACCEPTED_VERSIONS=wrong\n' + \
      f'reset_update_os_environment\nsource {shlex.quote(str(env_file))}\nprintf "%s|%s" "$AGNOS_VERSION" "$AGNOS_ACCEPTED_VERSIONS"'
    result = subprocess.check_output(['bash', '-c', script], text=True)
    self.assertEqual(result, '19.8.1|19.8.1 20.2.1')

  def test_legacy_launch_checks_exact_commit_and_os_before_swap_or_slot_activation(self):
    repo = self.root / 'staged'
    repo.mkdir()
    subprocess.run(['git', 'init', '-q', str(repo)], check=True)
    subprocess.run(['git', '-C', str(repo), '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                    'commit', '--allow-empty', '-qm', 'fixture'], check=True)
    commit = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()
    version = self.root / 'VERSION'
    version.write_text('20.2.1\n')
    marker = repo / '.git/starpilot-retain-agnos'
    launcher = ROOT / 'launch_chffrplus.sh'
    text = launcher.read_text()
    audited = next(iter(load_updater().TRUSTED_LEGACY_RELEASES['StarPilot']))
    self.assertIn(f'TRUSTED_LEGACY_STARPILOT_COMMIT="{audited}"', text)
    check = shell_function(launcher, 'staged_os_is_compatible').replace('/VERSION', str(version))
    activate = shell_function(launcher, 'maybe_activate_legacy_agnos')
    verifier = self.root / 'verifier'
    log = self.root / 'calls'
    verifier.write_text(f'#!/bin/sh\necho verify >> {shlex.quote(str(log))}\n')
    verifier.chmod(0o755)
    manifest = self.root / 'agnos.json'
    manifest.write_text('[]')
    common = f'TRUSTED_LEGACY_STARPILOT_COMMIT={commit}\n{check}\n{activate}\n'
    def admission():
      return subprocess.run(['bash', '-c', common + f'staged_os_is_compatible {shlex.quote(str(repo))}'], check=False).returncode
    self.assertNotEqual(admission(), 0)  # Missing marker is not a modern update.
    marker.write_text('wrong\n')
    self.assertNotEqual(admission(), 0)
    marker.unlink()
    marker.symlink_to(version)
    self.assertNotEqual(admission(), 0)
    marker.unlink()
    marker.write_text('20.2.1\n')
    self.assertEqual(admission(), 0)
    version.write_text('changed\n')
    self.assertNotEqual(admission(), 0)  # Manual reboot must recheck the OS.
    shell = common + f'sudo() {{ echo reboot >> {shlex.quote(str(log))}; }}\n' + \
      f'maybe_activate_legacy_agnos {shlex.quote(str(repo))} {shlex.quote(str(verifier))} {shlex.quote(str(manifest))}\n'
    subprocess.run(['bash', '-c', shell], check=True)
    self.assertFalse(log.exists())  # A pinned legacy install never activates a slot.
    marker.unlink()
    modern = common.replace(f'TRUSTED_LEGACY_STARPILOT_COMMIT={commit}', 'TRUSTED_LEGACY_STARPILOT_COMMIT=other')
    self.assertEqual(subprocess.run(['bash', '-c', modern + f'staged_os_is_compatible {shlex.quote(str(repo))}']).returncode, 0)
    subprocess.run(['bash', '-c', shell.replace(f'TRUSTED_LEGACY_STARPILOT_COMMIT={commit}',
                                                  'TRUSTED_LEGACY_STARPILOT_COMMIT=other')], check=True)
    self.assertEqual(log.read_text().splitlines(), ['verify', 'reboot'])

  def test_retained_match_never_flashes_or_queries_slot(self):
    with patch.dict(os.environ, {'AGNOS_VERSION': 'unexpected', 'AGNOS_UPDATE_POLICY': 'auto'}):
      self.updater.handle_agnos_update()
    self.flash.assert_not_called()
    self.slot.assert_not_called()
    self.updater.set_consistent_flag.assert_not_called()

  def test_retained_mismatch_blocks_finalization_without_flash(self):
    for current in ('19.6.20', '19.8', '', '19.8.2 '):
      with self.subTest(current=current):
        self.updater.HARDWARE.get_os_version.return_value = current
        with self.assertRaisesRegex(RuntimeError, 'No OS update was attempted'):
          self.updater.handle_agnos_update()
        self.updater.set_consistent_flag.assert_called_with(False)
        self.flash.assert_not_called()
        self.slot.assert_not_called()

  def test_other_branch_auto_policy_uses_current_manifest_location(self):
    self.environment.write_text('export AGNOS_VERSION="19.8"\n')
    manifest = self.root / "openpilot/common/hardware/comma/agnos.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("[]")
    self.updater.handle_agnos_update()
    self.flash.assert_called_once_with(str(self.root / 'openpilot/common/hardware/comma/agnos.json'), 1, self.updater.cloudlog)
    self.assertTrue((ROOT / 'openpilot/common/hardware/comma/agnos.json').is_file())
    self.updater.set_consistent_flag.assert_called_once_with(False)

  def test_old_layout_auto_policy_uses_legacy_manifest_without_changing_retain_policy(self):
    self.environment.write_text('export AGNOS_VERSION="19.8"\n')
    manifest = self.root / "system/hardware/tici/agnos.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("[]")
    self.updater.handle_agnos_update()
    self.flash.assert_called_once_with(str(manifest), 1, self.updater.cloudlog)
    self.updater.set_consistent_flag.assert_called_once_with(False)

  def test_invalid_policy_cannot_finalize_or_flash(self):
    for document in ('export AGNOS_VERSION="19.8.2"\nexport AGNOS_UPDATE_POLICY="unknown"\n',
                     'export AGNOS_VERSION=""\n', 'echo unexpected-output\nexport AGNOS_VERSION="19.8.2"\n'):
      with self.subTest(document=document):
        self.environment.write_text(document)
        with self.assertRaises(ValueError):
          self.updater.handle_agnos_update()
        self.flash.assert_not_called()
        self.slot.assert_not_called()
        self.updater.set_consistent_flag.assert_called_with(False)

  def run_shell(self, function, tail, current, model='comma tizi', udev_failure=''):
    version = self.root / 'VERSION'
    version.write_text(current)
    marker = self.root / 'AGNOS'
    marker.touch()
    device_model = self.root / 'model'
    device_model.write_text(model + '\0')
    udevadm = self.root / 'udevadm'
    udevadm.write_text('''#!/bin/sh
echo "$*" >> "$UDEV_TEST_LOG"
case "$*" in "trigger --settle "*) exit 1;; esac
if [ "$UDEV_TEST_FAILURE" = hang ]; then sleep 60; fi
if [ "$UDEV_TEST_FAILURE" = "$1" ]; then exit 1; fi
''')
    udevadm.chmod(0o755)
    gpio_rules = self.root / 'stock-gpio.rules'
    if not gpio_rules.exists():
      gpio_rules.write_text('SUBSYSTEM=="gpio*", PROGRAM="/bin/sh -c \'find -L /sys/class/gpio/ -maxdepth 2 ' +
                            '-exec chown root:gpio {} \\; -exec chmod 770 {} \\; || true\'"\n' +
                            'SUBSYSTEM=="gpio", KERNEL=="gpiochip[0]", GROUP="gpio", MODE="660"\n')
    for name, body in {
      'mountpoint': '[ -f "$UDEV_TEST_MOUNTED" ]',
      'mount': '''[ "$UDEV_TEST_FAILURE" != mount ] || exit 1
echo "$*" >> "$UDEV_TEST_MOUNT_LOG"
cp "$2" "$3"
touch "$UDEV_TEST_MOUNTED"''',
    }.items():
      command = self.root / name
      command.write_text('#!/bin/sh\n' + body + '\n')
      command.chmod(0o755)
    script = self.root / 'test.sh'
    # Replace only device observations in a copy of the actual function. Every
    # effectful command is a test double; no launch manager or device is run.
    function = function.replace('/VERSION', str(version)).replace('/AGNOS', str(marker))
    function = function.replace('/sys/firmware/devicetree/base/model', str(device_model))
    function = function.replace('/run/udev/rules.d', str(self.root / 'udev-rules'))
    function = function.replace('/etc/udev/rules.d/99-gpio.rules', str(gpio_rules))
    if udev_failure == 'hang':
      function = function.replace('timeout -k 1s 5s', 'timeout -k 0.1s 0.2s')
    script.write_text('''source "$1/launch_env.sh"
DIR="$1"
OPENPILOT_ROOT="$1"
BOLD=''
log="$1/effects"
rm() { echo rm >> "$log"; }
sudo() { if [ "$1" = timeout ]; then "$@"; else echo sudo >> "$log"; fi; }
read() { echo prompt >> "$log"; return 1; }
op_run_command() { echo effect >> "$log"; }
''' + function + '\n' + tail + '\n')
    environment = dict(os.environ, PATH=f'{self.root}:{os.environ["PATH"]}',
                       UDEV_TEST_LOG=str(self.root / 'udev.log'), UDEV_TEST_FAILURE=udev_failure,
                       UDEV_TEST_MOUNTED=str(self.root / 'mounted'), UDEV_TEST_MOUNT_LOG=str(self.root / 'mount.log'))
    return subprocess.run(['bash', str(script), str(self.root)], env=environment,
                          capture_output=True, text=True, timeout=8)

  def test_launcher_refuses_mismatched_os_before_any_effect(self):
    result = self.run_shell(shell_function(ROOT / 'launch_chffrplus.sh', 'agnos_init'), 'agnos_init', '19.8')
    self.assertEqual(result.returncode, 1, result.stderr)
    self.assertIn('No OS update was attempted', result.stdout)
    self.assertFalse((self.root / 'effects').exists())

  def test_panda_usb_permissions_are_tici_only_and_repeatable(self):
    function = shell_function(ROOT / 'launch_chffrplus.sh', 'agnos_init')
    for model in ('comma tizi', 'comma mici', 'comma tici2', 'unknown', 'comma tici'):
      with self.subTest(model=model):
        result = self.run_shell(function, 'agnos_init', '19.8.2', model=model)
        self.assertEqual(result.returncode, 0, result.stderr)
        if model != 'comma tici':
          self.assertFalse((self.root / 'udev-rules').exists())
          self.assertFalse((self.root / 'udev.log').exists())
          self.assertFalse((self.root / 'mount.log').exists())
    rules = self.root / 'udev-rules/99-starpilot-panda.rules'
    expected = ('SUBSYSTEM=="usb", ATTRS{idVendor}=="3801", ATTRS{idProduct}=="ddcc", MODE="0666"\n' +
                'SUBSYSTEM=="usb", ATTRS{idVendor}=="3801", ATTRS{idProduct}=="ddee", MODE="0666"\n')
    self.assertEqual(rules.read_text(), expected)
    gpio_rules = self.root / 'stock-gpio.rules'
    self.assertEqual(gpio_rules.read_text().count('{} +'), 2)
    self.assertNotIn('\\;', gpio_rules.read_text())
    self.assertIn('KERNEL=="gpiochip[0]", GROUP="gpio", MODE="660"', gpio_rules.read_text())
    self.assertEqual(len((self.root / 'mount.log').read_text().splitlines()), 1)
    commands = ['control --reload-rules', *[
      f'trigger --subsystem-match=usb --attr-match=idVendor=3801 --attr-match=idProduct={product}'
      for product in ('ddcc', 'ddee')]]
    self.assertEqual((self.root / 'udev.log').read_text().splitlines(), commands)
    result = self.run_shell(function, 'agnos_init', '19.8.2', model='comma tici')
    self.assertEqual(result.returncode, 0, result.stderr)
    self.assertEqual(rules.read_text(), expected)
    self.assertEqual((self.root / 'udev.log').read_text().splitlines(), commands * 2)
    self.assertEqual(len((self.root / 'mount.log').read_text().splitlines()), 1)

  def test_panda_usb_permission_failure_never_blocks_manager_start(self):
    function = shell_function(ROOT / 'launch_chffrplus.sh', 'agnos_init')
    for failure in ('mount', 'control', 'trigger', 'hang'):
      with self.subTest(failure=failure):
        result = self.run_shell(function, 'agnos_init && echo manager-start', '19.8.2',
                                model='comma tici', udev_failure=failure)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Panda USB permission setup failed; continuing startup.', result.stdout)
        self.assertIn('manager-start', result.stdout)
    rules_directory = self.root / 'udev-rules'
    for child in rules_directory.iterdir():
      child.unlink()
    rules_directory.rmdir()
    rules_directory.write_text('not a directory')
    result = self.run_shell(function, 'agnos_init && echo manager-start', '19.8.2', model='comma tici')
    self.assertEqual(result.returncode, 0, result.stderr)
    self.assertIn('Panda USB permission setup failed; continuing startup.', result.stdout)
    self.assertIn('manager-start', result.stdout)

  def test_gpio_permissions_batch_commands_without_changing_targets(self):
    function = shell_function(ROOT / 'launch_chffrplus.sh', 'agnos_init')
    self.run_shell(function, 'agnos_init', '19.8.2', model='comma tici')
    rule = (self.root / 'stock-gpio.rules').read_text().splitlines()[0]
    program = re.search(r'PROGRAM="(.*)"', rule)
    assert program is not None
    command = shlex.split(program[1])[2]
    self.assertEqual(command.count('{} +'), 2)

    gpio = self.root / 'gpio'
    gpio.mkdir()
    pin = self.root / 'pin'
    pin.mkdir()
    for name in ('direction', 'value', 'edge', 'uevent'):
      (pin / name).touch()
    (pin / 'subsystem').symlink_to(gpio, target_is_directory=True)
    (gpio / 'gpio134').symlink_to(pin, target_is_directory=True)
    (gpio / 'export').touch()
    command = command.replace('/sys/class/gpio/', str(gpio) + '/').replace('root:gpio', f'{os.getuid()}:{os.getgid()}')
    commands = self.root / 'permission-commands'
    commands.mkdir()
    log = self.root / 'permission.log'
    for name in ('chown', 'chmod'):
      wrapper = commands / name
      executable = shutil.which(name)
      assert executable is not None, f"Required permission command missing: {name}"
      wrapper.write_text(f'#!/bin/sh\necho {name} >> "$GPIO_TEST_LOG"\nexec {shlex.quote(executable)} "$@"\n')
      wrapper.chmod(0o755)
    environment = dict(os.environ, PATH=f'{commands}:{os.environ["PATH"]}', GPIO_TEST_LOG=str(log))
    paths = [gpio, gpio / 'export', pin, *(pin / name for name in ('direction', 'value', 'edge', 'uevent'))]
    results = []
    for program in (command.replace('{} +', r'{} \;'), command):
      for path in paths:
        path.chmod(0o755 if path.is_dir() else 0o644)
      log.write_text('')
      result = subprocess.run(['sh', '-c', program], env=environment, capture_output=True, text=True, timeout=5)
      self.assertEqual(result.returncode, 0, result.stderr)
      results.append([(path.stat().st_mode, path.stat().st_uid, path.stat().st_gid) for path in paths])
      if program == command:
        self.assertCountEqual(log.read_text().splitlines(), ['chown', 'chmod'])
      else:
        self.assertGreater(len(log.read_text().splitlines()), 2)
    self.assertEqual(results[0], results[1])
    self.assertTrue(all(path.stat().st_mode & 0o777 == 0o770 for path in paths))

  def test_developer_helper_retained_match_or_mismatch_never_prompts(self):
    function = shell_function(ROOT / 'tools/op.sh', 'op_check_agnos_update')
    for current, code in (('19.8.2', 0), ('19.8', 1)):
      with self.subTest(current=current):
        result = self.run_shell(function, 'op_check_agnos_update', current)
        self.assertEqual(result.returncode, code, result.stderr)
        self.assertFalse((self.root / 'effects').exists())


if __name__ == '__main__':
  unittest.main()
