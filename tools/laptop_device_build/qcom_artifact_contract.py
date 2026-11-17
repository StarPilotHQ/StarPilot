"""Exact source and artifact contract for laptop-only DM and warp QCOM imports."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import sys
import tempfile

VERSION = 1
FLAGS = "DEV=QCOM IMAGE=1 FLOAT16=1 NOLOCALS=1 JIT_BATCH_SIZE=0 OPENPILOT_HACKS=1"
MODEL_DIR = Path("openpilot/selfdrive/modeld/models")
COMPATIBILITY_SOURCE = "tinygrad_repo/tinygrad/runtime/ops_qcom.py"
MAX_COMPATIBILITY_FILES = 8
MAX_COMPATIBILITY_FILE_BYTES = 131072
RETIRED_TARGET = "driving_tinygrad.pkl"
RETIRED_SOURCE = "openpilot/selfdrive/modeld/models/driving_supercombo.onnx"
RETIRED_SOURCE_SHA256 = "65a08adc31d5c456219687d99b7bf5e44d61dae2d49ea67850e76105c7248cce"
RETIRED_ARTIFACT_SHA256 = "a1a0e77fae061c6ac62dc3527c6d567c8456b6c314691a7622bf4c46cb625601"
COMPATIBILITY_OUTPUTS = {
  "dm_warp_1344x760_tinygrad.pkl": ([1, 1382400], "uint8"),
  "dm_warp_1928x1208_tinygrad.pkl": ([1, 1382400], "uint8"),
  "driving_warp_1344x760_tinygrad.pkl": ([2, 6, 128, 256], "uint8"),
  "driving_warp_1928x1208_tinygrad.pkl": ([2, 6, 128, 256], "uint8"),
  "dmonitoring_model_tinygrad.pkl": ([1, 553], "float32"),
  "driving_tinygrad.pkl": ([1, 2576], "float32"),
}
STATIC_SOURCES = (
  "openpilot/common/transformations/camera.py",
  "openpilot/common/transformations/model.py",
  "openpilot/system/camerad/cameras/nv12_info.py",
  "openpilot/selfdrive/modeld/models/dmonitoring_model.onnx",
)
TINYGRAD_SOURCE_ROOTS = ("tinygrad", "extra", "examples/openpilot")
SOURCE_SUFFIXES = frozenset((".py", ".c", ".cc", ".cpp", ".h", ".hpp", ".cl", ".metal", ".json"))


def _sha(path: Path) -> str:
  digest = hashlib.sha256()
  with path.open("rb") as source:
    while data := source.read(1024 * 1024):
      digest.update(data)
  return digest.hexdigest()


def compiler_tree(root: Path) -> Path:
  """The build provenance describes the compiler, independently of the global runtime."""
  legacy = root / 'tinygrad_model_runtime/legacy9d'
  if not legacy.exists():
    return root / 'tinygrad_repo'
  from openpilot.starpilot.models.runtime_bridge.bootstrap import _inventory, _digest_file
  manifest = legacy / 'runtime-manifest.json'
  expected_manifest = '20ca9e77010c1ab788f2adb824103db3d595b0da8c4069d6e32b1266f7977b1f'
  if manifest.is_symlink() or _sha(manifest) != expected_manifest:
    raise ValueError('Unqualified model compiler library')
  data = json.loads(manifest.read_bytes())
  if data.get('compiler_revision') != '9d0446a4ba8a532c8b674fb6ad795af015cd9dcf':
    raise ValueError('Unexpected legacy compiler revision')
  if _inventory(legacy) != set(data['files']) | {'runtime-manifest.json'}:
    raise ValueError('Legacy compiler source inventory changed')
  for path, digest in data['files'].items():
    if _digest_file(legacy / path) != digest:
      raise ValueError('Legacy compiler source changed: ' + path)
  return legacy / 'tinygrad_repo'


def commands(root: Path) -> dict[str, str]:
  sys.path.insert(0, str(root))
  from openpilot.common.transformations.camera import _ar_ox_fisheye, _os_fisheye
  from openpilot.common.transformations.model import MEDMODEL_INPUT_SIZE, DM_INPUT_SIZE
  from openpilot.system.camerad.cameras.nv12_info import get_nv12_info

  compiler = compiler_tree(root) / "examples/openpilot"
  commands = {}
  for source, target in (("dmonitoring_model.onnx", "dmonitoring_model_tinygrad.pkl"),):
    commands[target] = " ".join((f'{FLAGS} taskset -c 0-7 python3 "{compiler}/compile_onnx.py"',
                                 f'"{root / MODEL_DIR / source}" "{root / MODEL_DIR / target}"',
                                 "--out-of-band --benchmark-runs 1"))
  for camera in (_ar_ox_fisheye, _os_fisheye):
    width, height = camera.width, camera.height
    stride, y_height, uv_height, frame_size = get_nv12_info(width, height)
    target = f"driving_warp_{width}x{height}_tinygrad.pkl"
    commands[target] = " ".join((f'{FLAGS} taskset -c 0-7 python3 "{compiler}/compile_warp.py"',
                                 f'--frame {width},{height},{stride},{y_height},{uv_height},{stride * (y_height + uv_height)}',
                                 f'--warp-to {MEDMODEL_INPUT_SIZE[0]}x{MEDMODEL_INPUT_SIZE[1]} --layout yuv420 --frames 2',
                                 f'--output {root / MODEL_DIR / target}'))
    target = f"dm_warp_{width}x{height}_tinygrad.pkl"
    commands[target] = " ".join((f'{FLAGS} python3 "{compiler}/compile_warp.py"',
                                 f'--frame {width},{height},{stride},{y_height},{uv_height},{frame_size}',
                                 f'--warp-to {DM_INPUT_SIZE[0]}x{DM_INPUT_SIZE[1]}',
                                 f'--layout luma --border-fill 16 --transform-device NPY --output {root / MODEL_DIR / target}'))
  return commands


def _sources(root: Path) -> dict[str, str]:
  paths = [root / source for source in STATIC_SOURCES]
  for directory in TINYGRAD_SOURCE_ROOTS:
    paths.extend(path for path in (compiler_tree(root) / directory).rglob("*")
                 if path.is_file() and path.suffix in SOURCE_SUFFIXES and "__pycache__" not in path.parts)
  compiler = compiler_tree(root)
  def source_name(path):
    # Logical compiler paths retain the original package provenance after relocation.
    return ('tinygrad_repo/' + path.relative_to(compiler).as_posix() if path.is_relative_to(compiler)
            else path.relative_to(root).as_posix())
  return {source_name(path): _sha(path) for path in sorted(paths)}


def _tokens(command: str, root: Path) -> list[str]:
  compiler = compiler_tree(root)
  return [token.replace(str(compiler), '<ROOT>/tinygrad_repo').replace(str(root), '<ROOT>')
          for token in shlex.split(command)]


def source_inventory(root: Path) -> dict:
  root = root.resolve()
  return {"sources": _sources(root), "commands": {name: _tokens(command, root) for name, command in commands(root).items()}}


def source_signature(root: Path) -> str:
  return _inventory_signature(source_inventory(root))


def _inventory_signature(inventory: dict) -> str:
  return hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _historical_inventory(manifest: dict, current: dict, expected: dict, root: Path) -> tuple[dict, dict]:
  if set(manifest.get("artifacts", {})) != set(expected) | {RETIRED_TARGET}:
    return current, expected
  saved = manifest.get("source")
  command = " ".join((f'{FLAGS} taskset -c 0-7 python3 "{root}/tinygrad_repo/examples/openpilot/compile_onnx.py"',
                      f'"{root / RETIRED_SOURCE}" "{root / MODEL_DIR / RETIRED_TARGET}" --out-of-band --benchmark-runs 1'))
  retired = manifest["artifacts"][RETIRED_TARGET]
  if (type(saved) is not dict or type(saved.get("sources")) is not dict or type(saved.get("commands")) is not dict or
      saved["sources"].get(RETIRED_SOURCE) != RETIRED_SOURCE_SHA256 or
      saved["commands"].get(RETIRED_TARGET) != _tokens(command, root) or
      type(retired) is not dict or retired.get("sha256") != RETIRED_ARTIFACT_SHA256 or
      type(retired.get("size")) is not int or retired["size"] != 70473368):
    raise ValueError("Unreviewed retired QCOM driving model provenance")
  # Retain the complete historical signatures and evidence; only live imports shrink.
  return {"sources": {**current["sources"], RETIRED_SOURCE: RETIRED_SOURCE_SHA256},
          "commands": {**current["commands"], RETIRED_TARGET: _tokens(command, root)}}, {**expected, RETIRED_TARGET: command}


def record(root: Path, artifacts: Path) -> dict:
  expected = commands(root.resolve())
  from tools.laptop_device_build.validate_artifacts import require_qcom_pickle
  entries = {}
  for name in expected:
    path = artifacts / name
    if not path.is_file() or path.is_symlink():
      raise ValueError(f"Missing regular QCOM artifact: {name}")
    require_qcom_pickle(path)
    entries[name] = {"sha256": _sha(path), "size": path.stat().st_size}
  return {"version": VERSION, "source_sha256": source_signature(root),
          "source": source_inventory(root), "artifacts": entries}


def _unique_pairs(pairs):
  value = {}
  for key, item in pairs:
    if key in value:
      raise ValueError(f"Duplicate QCOM manifest field: {key}")
    value[key] = item
  return value


def _valid_sha(value) -> bool:
  return type(value) is str and len(value) == 64 and all(char in "0123456789abcdef" for char in value)


# Exact reviewed import/diagnostic changes; compiled provenance stays in manifest.json.
RUNTIME_EXTENSION_PROFILE = "deferred-benchmark-and-startup-trace-v1"
RUNTIME_EXTENSION_CHANGES = {
  'tinygrad_repo/examples/openpilot/helpers.py': (
    'f4bae8ab81931781dc7134d6450c9fa2f24410b81cbc6ecfcebd6c708eb6d912',
    '6494f23757a1058d8c90db8434004e8f703bb61d55903ac4500c04a7d511c8a3',
  ),
  'tinygrad_repo/tinygrad/runtime/support/am/amdev.py': (
    '3ceddc6b7aabe0f03019c6a6d60b99da11d18ea29065f804dbe15a6ba8d1dd98',
    '90222e5c3817b7358f2155971f2e1c7c83480ace83832c4e35a27cef320e82e7',
  ),
  'tinygrad_repo/tinygrad/runtime/support/am/ip.py': (
    '92e13084d4603c908721584411f2587f73792ca318b0978640caefbbb54366c4',
    '7739c7284002379addcc192e870ef1d7ea7e24a5faec7cab154428833001f923',
  ),
  'tinygrad_repo/tinygrad/runtime/support/usb.py': (
    '310ff4f4743a17c80de975196fd4dc21835cf6f672d0a82485d6edd635ad9275',
    '546b2b8b2ea773be48e3ba16904b53aae8c2774af21aa519d64ec113b58397c4',
  ),
}
RUNTIME_EXTENSION_ADDITIONS = {
  'tinygrad_repo/tinygrad/runtime/support/am/startup_trace.py':
    '321e1fd0513f8f8ee98cb7c1dc637b609350eeb77ca4d84c3821e88e1f2da8ad',
}

RUNTIME_EXTENSION_TYPED_ADDITIONS = {
  'tinygrad_repo/tinygrad/runtime/support/am/startup_trace.py':
    '93eb3813e692f9ca84e7ef51eed7dedc95a7beafe746dbff73e2acb7600f9a42',
}

def _runtime_parent_source(package: Path, current_source: dict) -> dict:
  """Bind supplemental runtime changes to the existing paired-output attestation."""
  path = package / "runtime-compatibility.json"
  if not path.exists():
    return current_source
  if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= 4096:
    raise ValueError("Invalid QCOM runtime extension")
  extension = json.loads(path.read_text(), object_pairs_hook=_unique_pairs)
  parent = package / "compatibility.json"
  if (type(extension) is not dict or set(extension) != {
      "version", "profile", "parent_evidence_sha256", "compatible_source_sha256"} or
      type(extension["version"]) is not int or extension["version"] != 1 or
      extension["profile"] != RUNTIME_EXTENSION_PROFILE or
      not parent.is_file() or parent.is_symlink() or
      extension["parent_evidence_sha256"] != _sha(parent) or
      extension["compatible_source_sha256"] != _inventory_signature(current_source)):
    raise ValueError("QCOM runtime extension is not bound to source and parent evidence")
  sources = dict(current_source["sources"])
  for name, (before, after) in RUNTIME_EXTENSION_CHANGES.items():
    if sources.get(name) != after:
      raise ValueError(f"Unreviewed QCOM runtime source: {name}")
    sources[name] = before
  for name, expected_sha in RUNTIME_EXTENSION_ADDITIONS.items():
    if sources.pop(name, None) not in (expected_sha, RUNTIME_EXTENSION_TYPED_ADDITIONS.get(name, expected_sha)):
      raise ValueError(f"Unreviewed QCOM runtime addition: {name}")
  return {"sources": sources, "commands": current_source["commands"]}


def _verify_runtime_compatibility(package: Path, manifest_path: Path, manifest: dict,
                                  current_source: dict, expected: dict[str, str]) -> None:
  """Accept one reviewed runtime-only source delta, never a new build provenance."""
  saved = manifest.get("source")
  if type(saved) is not dict or type(saved.get("sources")) is not dict or type(saved.get("commands")) is not dict:
    raise ValueError("Invalid QCOM build source inventory")
  current_source = _runtime_parent_source(package, current_source)
  before, after = saved["sources"], current_source["sources"]
  if (set(before) != set(after) or saved["commands"] != current_source["commands"] or
      {key for key in before if before[key] != after[key]} != {COMPATIBILITY_SOURCE}):
    raise ValueError("QCOM source change is outside reviewed runtime compatibility")
  if manifest.get("source_sha256") != _inventory_signature(saved):
    raise ValueError("QCOM build source signature mismatch")
  sidecar = package / "compatibility.json"
  if not sidecar.is_file() or sidecar.is_symlink() or sidecar.stat().st_size > 65536:
    raise ValueError("QCOM runtime compatibility evidence is missing or invalid")
  evidence = json.loads(sidecar.read_text(), object_pairs_hook=_unique_pairs)
  if type(evidence) is not dict or set(evidence) != {
      "version", "method", "build_manifest_sha256", "compatible_source_sha256",
      "changed_source", "artifact_sha256", "paired_outputs", "evidence_files"}:
    raise ValueError("Invalid QCOM runtime compatibility evidence")
  files = evidence["evidence_files"]
  if (set(expected) not in (set(COMPATIBILITY_OUTPUTS), set(COMPATIBILITY_OUTPUTS) - {RETIRED_TARGET}) or type(files) is not dict or
      not 1 <= len(files) <= MAX_COMPATIBILITY_FILES):
    raise ValueError("Incomplete QCOM compatibility evidence files")
  evidence_dir = package / "compatibility"
  if not evidence_dir.is_dir() or evidence_dir.is_symlink():
    raise ValueError("Invalid QCOM compatibility evidence directory")
  for name, expected_sha in files.items():
    if type(name) is not str or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", name) is None:
      raise ValueError("Invalid QCOM compatibility evidence filename")
    path = evidence_dir / name
    if (not _valid_sha(expected_sha) or not path.is_file() or path.is_symlink() or
        not 0 < path.stat().st_size <= MAX_COMPATIBILITY_FILE_BYTES or _sha(path) != expected_sha):
      raise ValueError(f"QCOM compatibility evidence file mismatch: {name}")
  if any(type(entry) is not dict for entry in manifest["artifacts"].values()):
    raise ValueError("Invalid QCOM artifact entries")
  if (type(evidence["version"]) is not int or evidence["version"] != 1 or
      evidence["method"] != "paired-qcom-output-sha256" or
      evidence["build_manifest_sha256"] != _sha(manifest_path) or
      evidence["compatible_source_sha256"] != _inventory_signature(current_source) or
      evidence["changed_source"] != {"path": COMPATIBILITY_SOURCE,
                                     "build_sha256": before[COMPATIBILITY_SOURCE],
                                     "runtime_sha256": after[COMPATIBILITY_SOURCE]} or
      evidence["artifact_sha256"] != {name: entry.get("sha256") for name, entry in manifest["artifacts"].items()}):
    raise ValueError("QCOM runtime compatibility evidence is not bound to this package and source")
  observations = evidence["paired_outputs"]
  if type(observations) is not dict or set(observations) != set(expected):
    raise ValueError("QCOM runtime compatibility evidence lacks a stock target")
  for target, rows in observations.items():
    if type(rows) is not list or len(rows) != 3:
      raise ValueError(f"Invalid QCOM paired outputs: {target}")
    for index, row in enumerate(rows):
      if (type(row) is not dict or set(row) != {"input", "shape", "dtype", "build_sha256", "runtime_sha256"} or
          row["input"] != ("A" if index != 1 else "B") or
          row["shape"] != COMPATIBILITY_OUTPUTS[target][0] or
          row["dtype"] != COMPATIBILITY_OUTPUTS[target][1] or
          not _valid_sha(row["build_sha256"]) or row["build_sha256"] != row["runtime_sha256"]):
        raise ValueError(f"Invalid QCOM paired outputs: {target}")


def verify(package: Path, root: Path, target_name: str, actual_command: str) -> tuple[Path, str]:
  expected = commands(root.resolve())
  from tools.laptop_device_build.validate_artifacts import require_qcom_pickle
  # Authenticate the physical compiler command before normalizing historical paths.
  if target_name not in expected or shlex.split(actual_command) != shlex.split(expected[target_name]):
    raise ValueError(f"QCOM command recipe changed: {target_name}")
  manifest_path = package / "manifest.json"
  manifest = json.loads(manifest_path.read_text(), object_pairs_hook=_unique_pairs)
  if type(manifest) is not dict:
    raise ValueError("Invalid QCOM manifest")
  if type(manifest.get("artifacts")) is not dict:
    raise ValueError("Invalid QCOM artifact set")
  current_source = source_inventory(root)
  current_source, provenance_expected = _historical_inventory(manifest, current_source, expected, root.resolve())
  if manifest.get("source") != current_source and not (package / "compatibility.json").exists():
    saved = manifest.get("source", {})
    for section in ("sources", "commands"):
      old = saved.get(section, {}) if type(saved) is dict else {}
      if type(old) is not dict:
        old = {}
      new = current_source[section]
      for path in sorted(set(old) | set(new)):
        if old.get(path) != new.get(path):
          raise ValueError(f"QCOM {section} changed: {path}")
  if (type(manifest.get("version")) is not int or manifest["version"] != VERSION or
      type(manifest.get("artifacts")) is not dict or
      set(manifest["artifacts"]) != set(provenance_expected)):
    raise ValueError("QCOM package source or artifact set does not match this checkout")
  if manifest.get("source") == current_source:
    if manifest.get("source_sha256") != _inventory_signature(current_source):
      raise ValueError("QCOM package source signature mismatch")
  else:
    _verify_runtime_compatibility(package, manifest_path, manifest, current_source, provenance_expected)
  for name in expected:
    entry = manifest["artifacts"][name]
    path = package / "artifacts" / name
    if (type(entry) is not dict or not path.is_file() or path.is_symlink() or
        type(entry.get("size")) is not int or path.stat().st_size != entry["size"] or
        _sha(path) != entry.get("sha256")):
      raise ValueError(f"QCOM package artifact mismatch: {name}")
    require_qcom_pickle(path)
  return package / "artifacts" / target_name, manifest["artifacts"][target_name]["sha256"]


def import_artifact(package: Path, root: Path, target: Path, command: str) -> None:
  source, expected_sha = verify(package, root, target.name, command)
  with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".qcom-import-", delete=False) as output:
    temporary = Path(output.name)
    try:
      with source.open("rb") as original:
        shutil.copyfileobj(original, output)
      output.flush()
      os.fsync(output.fileno())
      if _sha(temporary) != expected_sha:
        raise ValueError(f"QCOM artifact changed during copy: {target.name}")
      os.replace(temporary, target)
    finally:
      temporary.unlink(missing_ok=True)


def preserve_stock_artifact(target: Path) -> None:
  from openpilot.common.file_chunker import materialize_file_chunked
  from openpilot.starpilot.models.runtime_bridge.runtime import _stock_bytes
  data = _stock_bytes(materialize_file_chunked(target))
  if target.exists():
    return
  fd, temporary = tempfile.mkstemp(prefix=f'.{target.name}.', dir=target.parent)
  try:
    with os.fdopen(fd, 'wb') as output:
      output.write(data)
      output.flush()
      os.fsync(output.fileno())
    os.replace(temporary, target)
  finally:
    Path(temporary).unlink(missing_ok=True)


def main() -> None:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("command", choices=("source-signature", "record"))
  parser.add_argument("root", type=Path)
  parser.add_argument("artifacts", nargs="?", type=Path)
  args = parser.parse_args()
  if args.command == "source-signature":
    print(source_signature(args.root))
  elif args.artifacts is None:
    parser.error("record requires artifact directory")
  else:
    print(json.dumps(record(args.root, args.artifacts), sort_keys=True))


if __name__ == "__main__":
  main()
