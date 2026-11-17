import hashlib
import io
import json
import os
import platform
import re
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
VERSION = "1.7.12"
ARCHIVE_SHA256 = {
  "darwin_amd64": "5b44c3bc2255115c9b69e30efc0fecdf498fdb63c5d58e17084fd5f16324c644",
  "darwin_arm64": "aba9ced2dee8d27fecca3dc7feb1a7f9a52caefa1eb46f3271ea66b6e0e6953f",
  "linux_amd64": "8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8",
  "linux_arm64": "325e971b6ba9bfa504672e29be93c24981eeb1c07576d730e9f7c8805afff0c6",
}
# These two inherited jobs remain explicitly unavailable. No other diagnostic is waived.
DISABLED_JOBS = {
  ".github/workflows/tests.yaml": ("simulator_driving", "    if: false  # FIXME: Started to timeout recently"),
  ".github/workflows/ui_preview.yaml": ("preview", "    if: false # tmp disable due to GH API rate limiting flakiness"),
}


def actionlint() -> Path:
  machine = {"x86_64": "amd64", "aarch64": "arm64"}.get(platform.machine(), platform.machine())
  target = f"{platform.system().lower()}_{machine}"
  if target not in ARCHIVE_SHA256:
    raise RuntimeError(f"Unsupported actionlint platform: {target}")
  cache = ROOT / ".cache" / f"actionlint-{VERSION}-{target}"
  archive = cache / "release.tar.gz"
  binary = cache / "actionlint"
  cache.mkdir(parents=True, exist_ok=True)
  if not archive.exists():
    url = f"https://github.com/rhysd/actionlint/releases/download/v{VERSION}/actionlint_{VERSION}_{target}.tar.gz"
    for attempt in range(3):
      try:
        with urllib.request.urlopen(url, timeout=60) as response:
          data = response.read()
        break
      except (urllib.error.URLError, TimeoutError) as error:
        if (isinstance(error, urllib.error.HTTPError) and error.code not in (429, 500, 502, 503, 504)) or attempt == 2:
          raise
        time.sleep(1 << attempt)
    if hashlib.sha256(data).hexdigest() != ARCHIVE_SHA256[target]:
      raise RuntimeError("actionlint release checksum mismatch")
    with tempfile.NamedTemporaryFile(dir=cache, delete=False) as temporary:
      temporary.write(data)
      name = temporary.name
    os.replace(name, archive)
  data = archive.read_bytes()
  if hashlib.sha256(data).hexdigest() != ARCHIVE_SHA256[target]:
    raise RuntimeError("Cached actionlint release checksum mismatch")
  with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as release:
    member = release.extractfile("actionlint")
    if member is None:
      raise RuntimeError("actionlint release has no binary")
    expected = member.read()
  if not binary.exists() or binary.read_bytes() != expected:
    with tempfile.NamedTemporaryFile(dir=cache, delete=False) as temporary:
      temporary.write(expected)
      name = temporary.name
    os.chmod(name, 0o755)
    os.replace(name, binary)
  return binary


def main() -> int:
  binary = actionlint()
  if sys.argv[1:] == ["--install"]:
    return 0
  if sys.argv[1:]:
    raise RuntimeError("Usage: check_workflows.py [--install]")
  workflows = sorted(str(path.relative_to(ROOT)) for path in (ROOT / ".github/workflows").glob("*.yaml"))
  workflows += sorted(str(path.relative_to(ROOT)) for path in (ROOT / ".github/workflows").glob("*.yml"))
  if not workflows:
    raise RuntimeError("No workflows found")
  result = subprocess.run([str(binary), "-shellcheck=", "-pyflakes=", "-format", "{{json .}}", *workflows],
                          cwd=ROOT, text=True, capture_output=True)
  if result.returncode not in (0, 1) or result.stderr:
    raise RuntimeError(result.stderr or result.stdout or f"actionlint exited {result.returncode}")
  errors = json.loads(result.stdout or "[]")
  failed = False
  for error in errors:
    path = error["filepath"]
    lines = (ROOT / path).read_text().splitlines()
    line = lines[error["line"] - 1]
    preceding_jobs = [match.group(1) for text in lines[:error["line"] - 1]
                      if (match := re.fullmatch(r"  ([a-zA-Z_][a-zA-Z0-9_-]*):", text))]
    job = preceding_jobs[-1] if preceding_jobs else None
    if (error["kind"] == "if-cond" and error["message"] == 'constant expression "false" in condition. remove the if: section'
        and path in DISABLED_JOBS and (job, line) == DISABLED_JOBS[path]):
      continue
    print(f'{path}:{error["line"]}:{error["column"]}: {error["message"]}')
    failed = True
  return int(failed)


if __name__ == "__main__":
  try:
    sys.exit(main())
  except (OSError, RuntimeError, ValueError) as error:
    print(error, file=sys.stderr)
    sys.exit(1)
