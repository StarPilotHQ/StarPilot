"""Copied to /run before activation; intentionally has no checkout imports.

An older checkout removes VERSION. Its existing phone_profile.release() restarts
bluealsa, so this retained launcher removes the v5 override and execs stock v4.
A reboot also removes /run overrides. Neither path modifies /usr or v4 state.
"""
from pathlib import Path
import os
import subprocess
import sys


def select(bundle: Path, legacy: Path, override: Path, reload):
  marker = bundle / 'VERSION'
  if marker.exists():
    if marker.read_text().strip() != '5.0.0':
      raise RuntimeError('Unsupported bundled BlueALSA version')
    binary = bundle / 'bin' / 'bluealsad'
    if not binary.is_file():
      raise RuntimeError('Incomplete BlueALSA 5 bundle')
    # Keep the existing scheduling policy; no new real-time priority over driving.
    # Keep-alive: an output that briefly closes and reopens (underrun recovery, sink reload) keeps the car's stream
    # instead of tearing it down and renegotiating, which the car hears as a disconnect.
    return [str(binary), '-p', 'a2dp-source', '-p', 'a2dp-sink', '-c', 'SBC', '-c', 'LDAC', '--keep-alive=5']
  override.unlink(missing_ok=True)
  (legacy.parent.parent / 'starpilot-bluealsa-launch.py').unlink(missing_ok=True)
  reload()
  argv = ['/usr/bin/bluealsa', '-p', 'a2dp-source']
  if legacy.exists():
    argv += ['-p', 'a2dp-sink']
  return argv


if __name__ == '__main__':
  argv = select(*(Path(p) for p in sys.argv[1:]),
                reload=lambda: subprocess.run(['/usr/bin/systemctl', 'daemon-reload'], check=True, timeout=15))
  if argv[0] != '/usr/bin/bluealsa':
    Path('/data/starpilot/bluealsa5/lib').mkdir(mode=0o700, parents=True, exist_ok=True)
  os.execv(argv[0], argv)
