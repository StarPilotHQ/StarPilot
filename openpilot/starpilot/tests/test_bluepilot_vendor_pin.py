"""The BluePilot release is pristine; StarPilot integration stays outside it."""
import hashlib
import json
from pathlib import Path


def test_bluepilot_copy_matches_the_pinned_upstream_release():
  root = Path(__file__).resolve().parents[3]
  pin = json.loads((root / 'openpilot/starpilot/car/ford/bluepilot_pin.json').read_text())
  library = root / 'opendbc_repo/opendbc/bluepilot_lateral'
  assert library.joinpath('VERSION').read_text().split() == [pin['version'], pin['commit']]
  actual = {str(p.relative_to(library)) for p in library.rglob('*')
            if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc'}
  assert actual == set(pin['files']) | {'VERSION'}
  for name, entry in pin['files'].items():
    path = library / name
    data = path.read_bytes()
    assert hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest() == entry['blob'], name
    assert ('100755' if path.stat().st_mode & 0o111 else '100644') == entry['mode'], name
