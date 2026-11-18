"""Declared selected-steering closure, not the platform discovery/fitter tree.

CP, the actual selected types, baseline and persistent settings remain separate
scope fields. Brand-local sources are deliberately conservative: an encoder or
vehicle-interface change requires review even if a particular branch was idle.
"""
from pathlib import Path
import hashlib

REVISION = 'selected-steering-compatibility-v1'
FLM = (
  'steering_compatibility', 'vehicle_identity', 'identity_transition', 'runtime_adapter_registry', 'canonical_surface_adapter', 'offroad_surface',
  'offroad_runtime', 'offroad_store', 'torque_stages', 'pid_stages', 'selected_effects', 'manual_coordinates',
  'live', 'torque_surface', 'global_runtime', 'adaptive_profile', 'torque_adapter', 'learning_wire', 'native_refinement_wire',
)
COMMON = ('constants', 'filter_simple', 'pid', 'realtime')


def source_paths(root, controller_file, brand_file):
  root = Path(root)
  brand = Path(brand_file).parent
  controller = Path(controller_file).parent
  paths = {Path(controller_file), Path(brand_file), root / 'openpilot/starpilot/flm/identity_transition.json'}
  paths.update(root / 'openpilot/starpilot/flm' / (name + '.py') for name in FLM)
  paths.update(root / 'openpilot/common' / (name + '.py') for name in COMMON)
  paths.update(p for p in controller.glob('*.py') if p.name.startswith(('latcontrol', 'vehicle_model', 'drive_helpers')))
  paths.update((root / 'openpilot/starpilot/lateral').glob('*.py'))
  paths.update(brand.glob('*.py'))
  paths.update((brand.parent / name) for name in ('__init__.py', 'interfaces.py', 'lateral.py', 'structs.py', 'vehicle_model.py'))
  paths.update((brand.parent / 'common').glob('*.py'))
  dbc = root / 'opendbc_repo/opendbc/dbc'
  paths.update((dbc / 'generator' / brand.name).rglob('*.dbc'))
  paths.update(p for p in dbc.glob(brand.name + '*.dbc') if not p.is_symlink())
  safety = root / 'opendbc_repo/opendbc/safety'
  paths.update(safety.glob('*.h'))
  paths.update((safety / 'modes').glob(brand.name + '*.h'))
  # Source data used by conversion/policy code, never other brands' discovery.
  paths.update(p for directory in (brand, root / 'openpilot/starpilot/lateral')
               for suffix in ('*.json', '*.toml') for p in directory.glob(suffix))
  return tuple(sorted(paths))


def revision(root, controller_file, brand_file, names, native):
  root = Path(root).resolve()
  paths = source_paths(root, controller_file, brand_file)
  if not 10 <= len(paths) <= 512:
    raise ValueError('Selected steering source closure bound')
  result = hashlib.sha256(repr((REVISION, names, native)).encode())
  for path in paths:
    if path.is_symlink() or not path.is_file() or not 0 <= path.stat().st_size <= 2 * 1024 * 1024:
      raise ValueError('Selected steering source unavailable: ' + str(path.relative_to(root)))
    result.update(str(path.relative_to(root)).encode())
    result.update(hashlib.sha256(path.read_bytes()).digest())
  return REVISION + ':' + result.hexdigest()
