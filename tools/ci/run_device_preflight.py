#!/usr/bin/env python3
"""Read-only parked-device prerequisites; never starts StarPilot or probes hardware."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import stat
import subprocess
import sys
import tomllib


SOURCE = Path(__file__).resolve().parents[2]
UI = Path("openpilot/starpilot/ui")
MANIFESTS = ("bitmap-fonts.json", "sora-brand-font.json", "home-assets.json", "settings-assets.json",
             "toggles-assets.json", "onroad-assets.json")
IMPORTS = ("raylib_nb", "capnp", "numpy", "cv2", "tinygrad", "openpilot.cereal.messaging")
MAX_MANIFEST = 1 << 20
MAX_ASSET = 16 << 20
PROBE_TIMEOUT = 8


def _read_regular(path: Path, limit: int) -> bytes:
  flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
  fd = os.open(path, flags)
  try:
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
      raise ValueError("not a bounded regular file")
    with os.fdopen(fd, "rb", closefd=False) as stream:
      data = stream.read(limit + 1)
    if len(data) != info.st_size or len(data) > limit:
      raise ValueError("file changed or exceeds limit")
    return data
  finally:
    os.close(fd)


def _check(status: str, detail: str) -> dict[str, str]:
  return {"status": status, "detail": detail}


def _policy(source: Path) -> tuple[dict, str]:
  launch = _read_regular(source / "launch_env.sh", MAX_MANIFEST).decode("utf-8")
  project = tomllib.loads(_read_regular(source / "pyproject.toml", MAX_MANIFEST).decode("utf-8"))
  version = re.findall(r'^[ \t]*export AGNOS_VERSION="([^"]+)"$', launch, re.MULTILINE)
  policy = re.findall(r'^[ \t]*export AGNOS_UPDATE_POLICY="([^"]+)"$', launch, re.MULTILINE)
  required_python = project["project"]["requires-python"]
  if version != ["19.8.1"] or policy != ["retain"]:
    raise ValueError("retained AGNOS source policy differs from reviewed trial")
  if required_python != ">= 3.12.3, < 3.13":
    raise ValueError("project Python requirement differs from reviewed trial")
  return {"agnos_version": version[0], "update_policy": policy[0],
          "requires_python": required_python}, hashlib.sha256(launch.encode()).hexdigest()


def _manifest_records(source: Path, name: str) -> list[dict]:
  data = json.loads(_read_regular(source / UI / name, MAX_MANIFEST))
  records = data["files"]
  if not isinstance(records, list) or not records:
    raise ValueError("empty asset manifest")
  names = set()
  for record in records:
    filename = record["file"]
    relative = Path(filename)
    if (not isinstance(filename, str) or relative.is_absolute() or
        any(part in ("", ".", "..") for part in relative.parts) or filename in names or
        not isinstance(record["bytes"], int) or isinstance(record["bytes"], bool) or
        not 0 <= record["bytes"] <= MAX_ASSET or
        not re.fullmatch(r"[0-9a-f]{64}", record["sha256"])):
      raise ValueError("invalid asset manifest entry")
    names.add(filename)
  return records


def _assets(source: Path, font_dir: Path | None) -> dict[str, dict]:
  result = {}
  for name in MANIFESTS:
    try:
      records = _manifest_records(source, name)
      if name == "bitmap-fonts.json":
        root = font_dir if font_dir is not None else source / UI / "assets/fonts"
      elif name == "sora-brand-font.json":
        root = source / UI / "assets/fonts"
      else:
        root = source / "openpilot/selfdrive/assets"
      for record in records:
        data = _read_regular(root / record["file"], record["bytes"])
        if len(data) != record["bytes"] or hashlib.sha256(data).hexdigest() != record["sha256"]:
          raise ValueError("missing or mismatched asset")
      result[name] = _check("pass", f"{len(records)} manifest-pinned files match")
    except (OSError, ValueError, KeyError, TypeError, UnicodeError, json.JSONDecodeError):
      result[name] = _check("fail", "missing, malformed or mismatched reviewed asset")
  return result


def _probe_import(source: Path, python: str, module: str) -> dict[str, str]:
  # The child imports one named library only. It creates no service, camera,
  # Params instance or model runner. Do not include child stderr in the report.
  program = """import importlib, pathlib, sys
source = pathlib.Path(sys.argv[1]).resolve()
vendor = {'openpilot': source / 'openpilot',
          'msgq': source / 'msgq_repo',
          'opendbc': source / 'opendbc_repo',
          'tinygrad': source / 'tinygrad_repo',
          'jetlink': source / 'jetlink_repo'}
sys.path[:0] = [str(source / path) for path in
                ('', 'msgq_repo', 'opendbc_repo', 'rednose_repo', 'teleoprtc_repo', 'tinygrad_repo', 'jetlink_repo')]
try: importlib.import_module(sys.argv[2])
except Exception as error:
 print(type(error).__name__)
 sys.exit(1)
for name, loaded in tuple(sys.modules.items()):
 prefix = name.split('.', 1)[0]
 if prefix in vendor:
  origin = getattr(loaded, '__file__', None)
  if origin is None or not pathlib.Path(origin).resolve().is_relative_to(vendor[prefix]):
   print('OriginMismatch')
   sys.exit(1)
"""
  env = {key: os.environ[key] for key in ("PATH", "LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH") if key in os.environ}
  env["PYTHONDONTWRITEBYTECODE"] = "1"
  try:
    run = subprocess.run([python, "-I", "-B", "-c", program, str(source), module],
                         cwd=source, env=env, capture_output=True, timeout=PROBE_TIMEOUT, check=False)
  except subprocess.TimeoutExpired:
    return _check("fail", "isolated import timed out")
  except OSError:
    return _check("fail", "Python executable unavailable")
  if run.returncode == 0:
    return _check("pass", "isolated import succeeded")
  error = (run.stdout or b"").decode("ascii", errors="ignore").strip()
  allowed = {"ImportError", "ModuleNotFoundError", "OSError", "RuntimeError", "ValueError", "SyntaxError", "OriginMismatch"}
  return _check("fail", f"isolated import failed: {error}" if error in allowed else "isolated import failed")


def collect(source: Path = SOURCE, font_dir: Path | None = None, *,
            python: str = sys.executable, os_version_file: Path = Path("/VERSION"),
            system: str | None = None, machine: str | None = None,
            python_version: tuple[int, int, int] | None = None,
            probe=_probe_import) -> dict:
  source = source.resolve()
  system = platform.system() if system is None else system
  machine = platform.machine() if machine is None else machine
  python_version = sys.version_info[:3] if python_version is None else python_version
  try:
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=source, capture_output=True,
                              text=True, timeout=3, check=True).stdout.strip()
  except (OSError, subprocess.SubprocessError):
    revision = "unavailable"
  checks: dict[str, dict] = {}
  try:
    policy, launch_sha = _policy(source)
    checks["source_policy"] = _check("pass", "retained AGNOS and Python source policy match")
  except (OSError, ValueError, KeyError, TypeError, UnicodeError):
    policy, launch_sha = None, None
    checks["source_policy"] = _check("fail", "retained AGNOS or Python source policy unavailable/mismatched")
  checks["python_version"] = _check("pass" if (3, 12, 3) <= python_version < (3, 13, 0) else "fail",
                                     ".".join(map(str, python_version)))
  if system == "Linux" and machine in ("aarch64", "arm64"):
    try:
      actual = _read_regular(os_version_file, 128).decode("ascii").strip()
      checks["target_agnos"] = _check("pass" if policy and actual == policy["agnos_version"] else "fail",
                                      "retained version matches" if policy and actual == policy["agnos_version"] else "version unavailable/mismatched")
    except (OSError, ValueError, UnicodeError):
      checks["target_agnos"] = _check("unavailable", "target OS marker unavailable")
  else:
    checks["target_agnos"] = _check("unavailable", "target-device OS check unavailable on this host")
  checks.update(_assets(source, font_dir))
  checks["runtime_imports"] = {module: probe(source, python, module) for module in IMPORTS}
  statuses = [item["status"] for key, item in checks.items() if key != "runtime_imports"]
  statuses.extend(item["status"] for item in checks["runtime_imports"].values())
  prerequisites = "fail" if "fail" in statuses else "incomplete" if "unavailable" in statuses else "pass"
  source_hashes = {}
  for relative in ("launch_env.sh", "pyproject.toml", *(str(UI / name) for name in MANIFESTS)):
    try:
      source_hashes[relative] = hashlib.sha256(_read_regular(source / relative, MAX_MANIFEST)).hexdigest()
    except (OSError, ValueError):
      source_hashes[relative] = None
  return {"schema_version": 1, "source_commit": revision, "launch_env_sha256": launch_sha,
          "source_files_sha256": source_hashes,
          "host": {"system": system, "machine": machine}, "checks": checks,
          "prerequisites": prerequisites,
          "device_qualification": "not_assessed",
          "scope": "Read-only prerequisites; no graphics, camera, CAN, model inference, hardware timing or driving check"}


def main(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--font-dir", type=Path, help="complete existing font directory; never copied")
  parser.add_argument("--json-output", type=Path, help="write a local JSON report")
  args = parser.parse_args(argv)
  report = collect(font_dir=args.font_dir)
  encoded = json.dumps(report, indent=2) + "\n"
  if args.json_output:
    args.json_output.write_text(encoded)
  else:
    print(encoded, end="")
  return {"pass": 0, "fail": 1, "incomplete": 2}[report["prerequisites"]]


if __name__ == "__main__":
  raise SystemExit(main())
