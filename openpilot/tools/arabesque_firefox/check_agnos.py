"""Check the shipped offline Firefox bundle against a pinned AGNOS system image."""

import argparse
import hashlib
import json
import lzma
import os
import platform
import shutil
import subprocess
import tempfile
from pathlib import Path

from openpilot.tools.arabesque_firefox import setup
from tools.laptop_device_build.extract_sysroot_from_agnos import download, ensure_debugfs_readable_image


def check_loader(root, runtime, chroot, emulator):
  multiarch = 'lib/aarch64-linux-gnu'
  common = runtime / 'usr' / multiarch
  display = runtime / 'display/usr'
  system = Path('/usr') / multiarch
  x_path = f'{display / multiarch}:{common}'
  checks = [
    (display / 'bin/Xvfb', x_path),
    (display / 'bin/xauth', x_path),
    (runtime / 'isolation/xkb/usr/bin/xkbcomp', x_path),
    (runtime / 'firefox/firefox', str(common)),
    # Gecko loads its sibling libraries before libxul. The standalone loader needs their directory.
    (runtime / 'firefox/libxul.so', f'{runtime / "firefox"}:{common}'),
    (common / 'libgtk-3.so.0', str(common)),
    (common / 'libxcb-damage.so.0', str(common)),
    (common / 'libxcb-xtest.so.0', str(common)),
  ]
  for name in ('libxcb.so.1', 'libxcb-shm.so.0'):
    library = common / name
    checks.append((library if os.path.lexists(root / library.relative_to('/')) else system / name, str(common)))

  if emulator:
    shutil.copyfile(emulator, root / 'arabesque-check-qemu')
    (root / 'arabesque-check-qemu').chmod(0o755)
  command = [chroot, f'--userspec=+{setup.BROWSER_UID}:+{setup.BROWSER_UID}', '--groups=', str(root)]
  if emulator:
    command.append('/arabesque-check-qemu')
  command += ['/lib/ld-linux-aarch64.so.1', '--inhibit-cache']
  for binary, libraries in checks:
    result = subprocess.run([*command, '--library-path', libraries, '--list', str(binary)],
                            env={'LC_ALL': 'C'}, capture_output=True, text=True, timeout=30)
    if result.returncode or '=> not found' in result.stdout:
      raise RuntimeError(f'AGNOS loader failed for {binary.name}:\n{result.stderr}{result.stdout}')
    print(f'AGNOS loader OK for {binary.name}', flush=True)


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--manifest', type=Path, default=Path('openpilot/common/hardware/comma/agnos.json'))
  parser.add_argument('--image', type=Path, help='Use a local copy of the compressed system image (.img.xz).')
  args = parser.parse_args()
  if os.geteuid() != 0:
    parser.error('Run with sudo on a Linux development machine.')
  for tool in ('chroot', 'debugfs', 'simg2img', 'dpkg-deb', 'glib-compile-schemas'):
    if shutil.which(tool) is None:
      parser.error(f'{tool} is required.')
  emulator = None
  if platform.machine() != 'aarch64':
    emulator = shutil.which('qemu-aarch64-static')
    if emulator is None:
      parser.error('qemu-aarch64-static is required on non-ARM64 hosts.')

  system, = (entry for entry in json.loads(args.manifest.read_text()) if entry['name'] == 'system')
  target = setup.approved()
  with tempfile.TemporaryDirectory(prefix='arabesque-agnos-') as directory:
    work = Path(directory)
    setup.ROOT = work / 'bundle'
    setup.ROOT.mkdir()
    staging = setup.ROOT / 'runtime'
    setup.build(target, staging, offline_only=True)

    compressed = args.image
    if compressed is None:
      compressed = work / 'system.img.xz'
      download(system['url'], compressed)
    image = work / 'system.img'
    with lzma.open(compressed, 'rb') as source, image.open('wb') as dest:
      shutil.copyfileobj(source, dest)
    with image.open('rb') as stream:
      if hashlib.file_digest(stream, 'sha256').hexdigest() != system['hash'].lower():
        raise ValueError(f'AGNOS system image checksum mismatch: {compressed}')

    filesystem = ensure_debugfs_readable_image(image, work)
    print('Extracting the verified AGNOS filesystem…', flush=True)
    root = work / 'root'
    root.mkdir()
    # A real chroot keeps even absolute AGNOS symlinks and loader fallback paths away from the host.
    result = subprocess.run(['debugfs', '-R', 'rdump / .', str(filesystem.resolve())],
                            cwd=root, capture_output=True, text=True, check=True)
    if result.stderr.splitlines()[1:]:
      raise RuntimeError(result.stderr.strip())
    runtime = Path('/arabesque-check-runtime')
    staging.rename(root / runtime.relative_to('/'))
    check_loader(root, runtime, shutil.which('chroot'), emulator)
    version = (root / 'VERSION').read_text().strip()
    print(f'Offline Firefox {target["version"]} revision {target["bundle_revision"]} passed on AGNOS {version}')


if __name__ == '__main__':
  main()
