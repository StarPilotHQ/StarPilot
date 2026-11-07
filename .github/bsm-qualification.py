from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET

BASE = '1d30dc343469e93967d2de3afdc966901009ed5d'
OUT = Path('bsm-results')
OUT.mkdir(exist_ok=True)
HOST = 'opendbc_repo/opendbc/car/hyundai/tests/test_ioniq6_bsm.py'
NATIVE = 'opendbc_repo/opendbc/safety/tests/test_hyundai_ioniq6_long.py'

def run(*args):
  subprocess.run(args, check=True)

def qualify():
  controller = Path('opendbc_repo/opendbc/car/hyundai/carcontroller.py')
  shipping = controller.read_bytes()
  try:
    controller.write_bytes(subprocess.check_output(['git', 'show', BASE + ':' + str(controller)]))
    result = subprocess.run([sys.executable, '-m', 'pytest', '-q', HOST, '-k',
                             'requires_a_matching_queued', '--junitxml=bsm-results/baseline.xml'])
    cases = list(ET.parse(OUT / 'baseline.xml').getroot().iter('testcase'))
    failures = [case for case in cases if case.find('failure') is not None]
    assert result.returncode == 1 and failures and not list(ET.parse(OUT / 'baseline.xml').iter('error'))
    assert all('UNQUEUED_BLINDSPOT_WARNING' in ''.join(case.find('failure').itertext()) for case in failures)
    (OUT / 'baseline.json').write_text(json.dumps({'expectedFailingCases': len(failures), 'confirmed': True}))
  finally:
    controller.write_bytes(shipping)
  assert controller.read_bytes() == shipping
  suites = [HOST, *map(str, sorted(Path('opendbc_repo/opendbc/car/hyundai/tests').glob('test_ioniq6_*.py'))),
            'openpilot/starpilot/lateral/tests/test_auto_lane_change.py',
            'openpilot/starpilot/lateral/tests/test_lane_change_defaults.py',
            'openpilot/starpilot/lateral/tests/test_lane_change_floor.py']
  run(sys.executable, '-m', 'pytest', '-q', '--junitxml=bsm-results/host.xml', *dict.fromkeys(suites))
  from tools.ci.run_vehicle_tests import build_safety_library
  native = [NATIVE,
            'opendbc_repo/opendbc/safety/tests/test_hyundai_canfd.py',
            'opendbc_repo/opendbc/safety/tests/test_hyundai_canfd_carnival.py',
            'opendbc_repo/opendbc/safety/tests/test_hyundai_canfd_torque_envelope.py',
            'opendbc_repo/opendbc/safety/tests/test_hyundai_ioniq6_stock_aol.py']
  for release in (False, True):
    variant = 'release' if release else 'debug'
    provenance = build_safety_library(release, OUT)
    (OUT / f'native-{variant}.json').write_text(json.dumps(provenance, indent=2))
    # A fresh interpreter avoids mixing the two C global-state libraries.
    loader = ('from opendbc.safety.tests.libsafety import libsafety_py; '
              f'libsafety_py.load({str((OUT / ("libsafety-" + variant + ".so")).resolve())!r}); '
              'import pytest,sys; sys.exit(pytest.main(sys.argv[1:]))')
    run(sys.executable, '-c', loader, '-q', f'--junitxml=bsm-results/native-{variant}.xml', *native)
  paths = [str(controller), HOST, NATIVE]
  run('.venv/bin/ruff', 'check', *paths)
  import re
  from collections import Counter
  def typing_result():
    result = subprocess.run(['.venv/bin/ty', 'check', '--output-format', 'concise', *paths], capture_output=True, text=True)
    output = result.stdout + result.stderr
    assert result.returncode in (0, 1) and ('error[' in output or result.returncode == 0), output
    return result.returncode, output, Counter(re.sub(r':\d+:\d+:', ':', line) for line in output.splitlines() if 'error[' in line)
  shipping_typing = typing_result()
  saved = {Path(name): Path(name).read_bytes() for name in paths}
  try:
    for path in saved:
      path.write_bytes(subprocess.check_output(['git', 'show', BASE + ':' + str(path)]))
    baseline_typing = typing_result()
  finally:
    for path, data in saved.items():
      path.write_bytes(data)
  (OUT / 'typing-shipping.txt').write_text(shipping_typing[1])
  (OUT / 'typing-baseline.txt').write_text(baseline_typing[1])
  assert not (shipping_typing[2] - baseline_typing[2]), shipping_typing[1]
  (OUT / 'typing.json').write_text(json.dumps({'introducedErrors': 0,
    'baselineErrors': sum(baseline_typing[2].values()), 'shippingErrors': sum(shipping_typing[2].values()),
    'baselineExit': baseline_typing[0], 'shippingExit': shipping_typing[0]}))
  (OUT / 'source.json').write_text(json.dumps({str(p): hashlib.sha256(p.read_bytes()).hexdigest()
    for p in [controller, Path(HOST), Path(NATIVE), Path('opendbc_repo/opendbc/safety/modes/hyundai_canfd.h')]}, indent=2))

def firmware():
  variant = os.environ['BSM_VARIANT']
  names = subprocess.check_output(['git', 'ls-tree', '-r', '--name-only', 'HEAD', 'panda/board/obj'], text=True).splitlines()
  targets = [name for name in names if name.endswith(('.bin.signed', '.bin'))]
  targets += ['panda/board/obj/version', 'panda/board/obj/gitversion.h', 'panda/board/obj/cert.h']
  assert sum(name.endswith('.bin.signed') for name in targets) == 17
  with (OUT / 'firmware-build.log').open('w') as log:
    result = subprocess.run(['.venv/bin/scons', '-j2', '--verbose', *targets], stdout=log, stderr=subprocess.STDOUT)
  print((OUT / 'firmware-build.log').read_text()[-18000:])
  assert result.returncode == 0
  run(sys.executable, 'tools/ci/record_firmware.py', '--variant', variant, '--build-log',
      str(OUT / 'firmware-build.log'), '--output', str(OUT / 'firmware-provenance.json'))
  import base64
  import zlib
  key = base64.b64decode(Path('panda/board/crypto/certs/debug.pub').read_bytes().split()[1])
  fields = []
  for _ in range(3):
    size = int.from_bytes(key[:4], 'big')
    fields.append(key[4:4 + size])
    key = key[4 + size:]
  exponent, modulus = map(lambda value: int.from_bytes(value, 'big'), fields[1:])
  checks = {}
  for name in targets:
    data = Path(name).read_bytes()
    if name.endswith('.bin.signed'):
      payload, signature = data[:-128], data[-128:]
      expected = b'\x00\x01' + b'\xff' * 0x69 + b'\x00' + hashlib.sha1(payload[4:]).digest()
      assert pow(int.from_bytes(signature, 'big'), exponent, modulus).to_bytes(128, 'big') == expected
      assert int.from_bytes(payload[:4], 'little') == len(payload)
      assert int.from_bytes(payload[-12:-8], 'little') == zlib.crc32(payload[:-12])
      assert payload[-8:-4] == b'VERS'
      elf = name.removesuffix('.bin.signed') + '/main.elf'
      symbols = subprocess.check_output(['readelf', '-sW', elf], text=True)
      if variant == 'debug' and not name.endswith('/body_h7.bin.signed'):
        assert 'hyundai_canfd_ioniq6_bsm_wheel_valid' in symbols
    if '/bootstub.' in name:
      project = Path(name).name.removeprefix('bootstub.').removesuffix('.bin')
      symbols = subprocess.check_output(['readelf', '-sW', f'panda/board/obj/{project}/bootstub.elf'], text=True)
      assert ' RSA_verify' in symbols
    checks[name] = {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data), 'mode': Path(name).stat().st_mode & 511}
  (OUT / 'all-firmware.json').write_text(json.dumps({'variant': variant, 'artifacts': checks}, indent=2))
  if variant == 'debug':
    import tarfile
    with tarfile.open(OUT / 'shipping-firmware.tar', 'w') as archive:
      for name in targets:
        archive.add(name, arcname=name)

def helper():
  import ctypes
  import struct
  target = Path('panda/tests/libpanda/libpanda.so')
  original = OUT / 'parent-libpanda.so'
  original.write_bytes(target.read_bytes())
  run('cc', '-fPIC', '-nostdlib', '-fno-builtin', '-std=gnu11', '-Wfatal-errors', '-Wno-pointer-to-int-cast',
      '-Ipanda/tests/libpanda', '-Ipanda', '-Ipanda/board', '-Iopendbc_repo', '-c',
      'panda/tests/libpanda/panda.c', '-o', str(OUT / 'panda.os'))
  run('cc', '-shared', str(OUT / 'panda.os'), '-o', str(target))
  for path in (original, target):
    assert struct.unpack_from('<H', path.read_bytes(), 18)[0] == 183
    ctypes.CDLL(str(path.resolve()))
  def exports(path):
    return {line.split()[-1] for line in subprocess.check_output(['nm', '-D', '--defined-only', str(path)], text=True).splitlines()}
  assert exports(original) == exports(target)
  deps = subprocess.check_output(['cc', '-MM', '-Ipanda/tests/libpanda', '-Ipanda', '-Ipanda/board', '-Iopendbc_repo',
                                  'panda/tests/libpanda/panda.c'], text=True)
  assert 'opendbc/safety/modes/hyundai_canfd.h' in deps
  (OUT / 'helper.json').write_text(json.dumps({'sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
       'arch': 'AArch64', 'exportsEqual': True, 'dlopen': True, 'includesChangedHeader': True,
       'compiler': subprocess.check_output(['cc', '--version'], text=True), 'dependencies': deps}, indent=2))
  import tarfile
  with tarfile.open(OUT / 'shipping-helper.tar', 'w') as archive:
    archive.add(target, arcname=str(target))

if __name__ == '__main__':
  {'qualify': qualify, 'firmware': firmware, 'helper': helper}[sys.argv[1]]()
