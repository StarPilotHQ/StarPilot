"""Small volatile modeld load receipt; no model artifact is deserialized here."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import time

from openpilot.common.hardware.hw import Paths
from openpilot.starpilot.models.catalog import BUNDLED_CURRENT, model_entries
from openpilot.starpilot.models.status import LaboratoryLoad, ModelLoad, ModelVariant
from openpilot.starpilot.models.catalog import ARTIFACT_ABI as LEGACY_ABI, COMPILER_REVISION as LEGACY_REVISION


MAX_RECEIPT_BYTES = 1024
MAX_ARTIFACT_BYTES = 2 * 1024 * 1024 * 1024
_PREFIX = re.compile(r"[A-Za-z0-9_-]{1,32}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_REASONS = {None, "chestnut-load-failed", "chestnut-run-stalled", "selected-load-failed"}


class ReceiptUnavailable(ValueError):
  pass


def receipt_path() -> Path:
  prefix = os.environ.get("OPENPILOT_PREFIX", "d")
  if _PREFIX.fullmatch(prefix) is None:
    raise ReceiptUnavailable("invalid IPC namespace")
  return Path(Paths.shm_path()) / f"starpilot_modeld_{prefix}" / "load-v1.json"


def _root_fd(path: Path, *, create: bool) -> int:
  if create:
    try:
      os.mkdir(path.parent, 0o700)
    except FileExistsError:
      pass
  fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
  try:
    info = os.fstat(fd)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
      raise ReceiptUnavailable("model receipt directory unavailable")
    return fd
  except BaseException:
    os.close(fd)
    raise


def process_start_ticks(pid: int, *, proc_root: Path = Path("/proc")) -> int:
  """Return Linux's immutable per-process start ticks; zero means unavailable."""
  if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
    return 0
  try:
    raw = (proc_root / str(pid) / "stat").read_bytes()
    tail = raw[raw.rfind(b")") + 2:].split()
    ticks = int(tail[19])
    return ticks if ticks > 0 else 0
  except (OSError, ValueError, IndexError):
    return 0


def _identity(info: os.stat_result) -> tuple[int, int, int, int, int]:
  return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


@dataclass(frozen=True)
class PreparedArtifact:
  identity: tuple[int, int, int, int, int]
  sha256: str


def artifact_identity(path: Path) -> tuple[int, int, int, int, int]:
  """Capture the installed file identity immediately before ModelState opens it."""
  try:
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC)
  except OSError as exc:
    raise ReceiptUnavailable("compiled artifact unavailable") from exc
  try:
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_size <= 0 or info.st_size > MAX_ARTIFACT_BYTES:
      raise ReceiptUnavailable("compiled artifact is not a bounded regular file")
    return _identity(info)
  finally:
    os.close(fd)


def artifact_digest(path: Path, expected_identity: tuple[int, int, int, int, int]) -> str:
  """Hash a bounded installed compiled artifact once, without following a link or FIFO."""
  try:
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC)
  except OSError as exc:
    raise ReceiptUnavailable("compiled artifact unavailable") from exc
  try:
    before = os.fstat(fd)
    if (not stat.S_ISREG(before.st_mode) or before.st_size <= 0 or before.st_size > MAX_ARTIFACT_BYTES or
        _identity(before) != expected_identity):
      raise ReceiptUnavailable("compiled artifact is not a bounded regular file")
    digest = hashlib.sha256()
    remaining = before.st_size
    while remaining:
      chunk = os.read(fd, min(1024 * 1024, remaining))
      if not chunk:
        raise ReceiptUnavailable("compiled artifact changed during hash")
      digest.update(chunk)
      remaining -= len(chunk)
    after = os.fstat(fd)
    named = os.stat(path, follow_symlinks=False)
    if _identity(before) != _identity(after) or _identity(before) != _identity(named):
      raise ReceiptUnavailable("compiled artifact changed during hash")
    return digest.hexdigest()
  except OSError as exc:
    raise ReceiptUnavailable("compiled artifact unavailable") from exc
  finally:
    os.close(fd)


def prepare_artifact(path: Path, expected_identity: tuple[int, int, int, int, int]) -> PreparedArtifact:
  """Complete bounded installed-file hashing before modeld's live loop starts."""
  return PreparedArtifact(expected_identity, artifact_digest(path, expected_identity))


def _pairs(items: list[tuple[str, object]]) -> dict[str, object]:
  result: dict[str, object] = {}
  for key, value in items:
    if key in result:
      raise ReceiptUnavailable("duplicate model receipt field")
    result[key] = value
  return result


def _positive_int(value: object) -> bool:
  return isinstance(value, int) and not isinstance(value, bool) and 0 < value <= (1 << 64) - 1


def _decode(raw: bytes) -> ModelLoad:
  entries = model_entries()
  try:
    record = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs)
  except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
    raise ReceiptUnavailable("malformed model receipt") from exc
  if not isinstance(record, dict) or set(record) != {"version", "pid", "processStartTicks", "loadedMonoNs",
                                                  "modelId", "variant", "artifactSha256", "fallbackReason"}:
    raise ReceiptUnavailable("invalid model receipt fields")
  if record["version"] != 1 or type(record["version"]) is not int:
    raise ReceiptUnavailable("unsupported model receipt")
  if not all(_positive_int(record[key]) for key in ("pid", "processStartTicks", "loadedMonoNs")):
    raise ReceiptUnavailable("invalid model process identity")
  if not isinstance(record["modelId"], str) or record["modelId"] not in entries:
    raise ReceiptUnavailable("unknown loaded model")
  try:
    variant = ModelVariant(record["variant"])
  except (ValueError, TypeError) as exc:
    raise ReceiptUnavailable("unknown model variant") from exc
  if record["modelId"] != BUNDLED_CURRENT and entries[record["modelId"]].uses_external_gpu != (variant is ModelVariant.CHESTNUT):
    raise ReceiptUnavailable("model receipt hardware mismatch")
  digest = record["artifactSha256"]
  if not isinstance(digest, str) or _DIGEST.fullmatch(digest) is None or record["fallbackReason"] not in _REASONS:
    raise ReceiptUnavailable("invalid model artifact identity")
  return ModelLoad(record["pid"], record["processStartTicks"], record["loadedMonoNs"],
                   record["modelId"], variant, digest, record["fallbackReason"])


def read_receipt(path: Path | None = None) -> ModelLoad | None:
  """Missing or malformed volatile state is unavailable, never a default identity."""
  try:
    path = receipt_path() if path is None else path
    root_fd = _root_fd(path, create=False)
  except (OSError, ReceiptUnavailable):
    return None
  try:
    fd = os.open(path.name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=root_fd)
  except (OSError, ReceiptUnavailable):
    os.close(root_fd)
    return None
  try:
    before = os.fstat(fd)
    if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_RECEIPT_BYTES:
      return None
    raw = os.read(fd, MAX_RECEIPT_BYTES + 1)
    after = os.fstat(fd)
    named = os.stat(path.name, dir_fd=root_fd, follow_symlinks=False)
    if len(raw) > MAX_RECEIPT_BYTES or _identity(before) != _identity(after) or _identity(before) != _identity(named):
      return None
    return _decode(raw)
  except (OSError, ReceiptUnavailable, TypeError):
    return None
  finally:
    os.close(fd)
    os.close(root_fd)


def write_receipt(load: ModelLoad, path: Path | None = None) -> None:
  """Replace one volatile record after the selected model has warmed successfully."""
  path = receipt_path() if path is None else path
  raw = json.dumps({"version": 1, "pid": load.pid, "processStartTicks": load.process_start_ticks,
                    "loadedMonoNs": load.loaded_mono_ns, "modelId": load.model_id,
                    "variant": load.variant.value, "artifactSha256": load.artifact_sha256,
                    "fallbackReason": load.fallback_reason}, separators=(",", ":"), sort_keys=True).encode()
  if len(raw) > MAX_RECEIPT_BYTES or _decode(raw) != load:
    raise ReceiptUnavailable("invalid model receipt")
  root_fd = _root_fd(path, create=True)
  temporary = f".load-{secrets.token_hex(8)}"
  try:
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                         0o600, dir_fd=root_fd)
    try:
      with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)
    except BaseException:
      os.unlink(temporary, dir_fd=root_fd)
      raise
    os.replace(temporary, path.name, src_dir_fd=root_fd, dst_dir_fd=root_fd)
  finally:
    try:
      os.unlink(temporary, dir_fd=root_fd)
    except FileNotFoundError:
      pass
    os.close(root_fd)


def recorded_model_message(load: ModelLoad) -> str:
  return json.dumps({'process': load.pid, 'msg': {'event': 'modeld.loaded', 'version': 1,
                    'pid': load.pid, 'processStartTicks': load.process_start_ticks,
                    'loadedMonoNs': load.loaded_mono_ns, 'modelId': load.model_id,
                    'variant': load.variant.value, 'artifactSha256': load.artifact_sha256,
                    'fallbackReason': load.fallback_reason}}, separators=(',', ':'))


def log_loaded_model(load: ModelLoad) -> None:
  """Record the actual initialized runner once per load or fallback in the rlog."""
  from openpilot.common.swaglog import cloudlog
  cloudlog.event("modeld.loaded", version=1, pid=load.pid, processStartTicks=load.process_start_ticks,
                 loadedMonoNs=load.loaded_mono_ns, modelId=load.model_id, variant=load.variant.value,
                 artifactSha256=load.artifact_sha256, fallbackReason=load.fallback_reason)


def logged_model_load(raw: str) -> ModelLoad | None:
  """Read our load event, never a requested selection or a free-form load message."""
  if len(raw) > 8192:
    return None
  try:
    record = json.loads(raw, object_pairs_hook=_pairs)
    payload = record.get("msg") if isinstance(record, dict) else None
    if not isinstance(payload, dict) or payload.get("event") != "modeld.loaded":
      return None
    payload = {key: value for key, value in payload.items() if key != "event"}
    load = _decode(json.dumps(payload).encode())
    return load if type(record.get("process")) is int and record["process"] == load.pid else None
  except (ReceiptUnavailable, ValueError, TypeError, RecursionError):
    return None


def clear_receipt(path: Path | None = None) -> None:
  """Invalidate a prior run without following the previous file or directory."""
  path = receipt_path() if path is None else path
  try:
    root_fd = _root_fd(path, create=False)
  except (OSError, ReceiptUnavailable):
    return
  try:
    os.unlink(path.name, dir_fd=root_fd)
  except FileNotFoundError:
    pass
  finally:
    os.close(root_fd)


def record_prepared(prepared: PreparedArtifact, variant: ModelVariant, fallback_reason: str | None = None,
                    *, model_id: str = BUNDLED_CURRENT, receipt_file: Path | None = None, pid: int | None = None,
                    start_ticks: int | None = None, now_mono_ns: int | None = None) -> ModelLoad:
  """Write a tiny receipt from a startup-verified artifact; never hash on fallback."""
  pid = os.getpid() if pid is None else pid
  start_ticks = process_start_ticks(pid) if start_ticks is None else start_ticks
  now_mono_ns = time.monotonic_ns() if now_mono_ns is None else now_mono_ns
  if not _positive_int(start_ticks) or not _positive_int(now_mono_ns):
    raise ReceiptUnavailable("process identity unavailable")
  load = ModelLoad(pid, start_ticks, now_mono_ns, model_id, variant,
                   prepared.sha256, fallback_reason)
  write_receipt(load, receipt_file)
  return load


MAX_PAIR_RECEIPT_BYTES = 4096


def pair_receipt_path(path: Path | None = None) -> Path:
  return (receipt_path() if path is None else path).with_name('load-v2.json')


def _pair_record(load: LaboratoryLoad) -> dict:
  return {'version': 2, 'pid': load.pid, 'processStartTicks': load.process_start_ticks,
          'loadedMonoNs': load.loaded_mono_ns, 'planSha256': load.plan_sha256, 'runtimeFamily': 'legacy9d',
          'targetRuntimeRevision': LEGACY_REVISION, 'fallbackArtifactAbi': LEGACY_ABI,
          'roles': {'lateral': {'modelId': load.lateral_id, 'behaviorVersion': load.lateral_version, 'artifactSha256': load.lateral_sha256,
                                 'sourceFormat': load.lateral_source_format, 'sourceCompilerRevision': load.lateral_source_compiler,
                                 'sourceManifestSha256': load.lateral_source_manifest},
                    'longitudinal': {'modelId': load.longitudinal_id, 'behaviorVersion': load.longitudinal_version,
                                     'artifactSha256': load.longitudinal_sha256, 'sourceFormat': load.longitudinal_source_format,
                                     'sourceCompilerRevision': load.longitudinal_source_compiler,
                                     'sourceManifestSha256': load.longitudinal_source_manifest}}}


def _decode_pair(raw: bytes) -> LaboratoryLoad:
  record = json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs)
  if (not isinstance(record, dict) or set(record) != {'version', 'pid', 'processStartTicks', 'loadedMonoNs',
                                                                   'planSha256', 'runtimeFamily', 'targetRuntimeRevision', 'fallbackArtifactAbi', 'roles'} or
      type(record['version']) is not int or record['version'] != 2 or record['runtimeFamily'] != 'legacy9d' or
      record['targetRuntimeRevision'] != LEGACY_REVISION or record['fallbackArtifactAbi'] != LEGACY_ABI or
      not all(_positive_int(record[k]) for k in ('pid', 'processStartTicks', 'loadedMonoNs')) or
      not isinstance(record['planSha256'], str) or _DIGEST.fullmatch(record['planSha256']) is None or
      not isinstance(record['roles'], dict) or set(record['roles']) != {'lateral', 'longitudinal'}):
    raise ReceiptUnavailable('invalid laboratory receipt')
  entries = model_entries()
  roles = record['roles']
  for role in roles.values():
    if (not isinstance(role, dict) or set(role) != {'modelId', 'behaviorVersion', 'artifactSha256',
                                                    'sourceFormat', 'sourceCompilerRevision', 'sourceManifestSha256'} or
        not isinstance(role['modelId'], str) or role['modelId'] not in entries or role['modelId'] == BUNDLED_CURRENT or
        role['behaviorVersion'] != entries[role['modelId']].version or
        not isinstance(role['artifactSha256'], str) or _DIGEST.fullmatch(role['artifactSha256']) is None):
      raise ReceiptUnavailable('invalid laboratory role')
    if not ((role['sourceFormat'] == LEGACY_ABI and role['sourceCompilerRevision'] == LEGACY_REVISION and
             role['sourceManifestSha256'] == '') or
            (role['sourceFormat'] == 'tinygrad_single_v1' and role['sourceCompilerRevision'] is None and
             isinstance(role['sourceManifestSha256'], str) and _DIGEST.fullmatch(role['sourceManifestSha256']) is not None)):
      raise ReceiptUnavailable('invalid laboratory source provenance')
  a, b = roles['lateral'], roles['longitudinal']
  if a['modelId'] == b['modelId'] or a['artifactSha256'] == b['artifactSha256']:
    raise ReceiptUnavailable('laboratory roles are not distinct')
  return LaboratoryLoad(record['pid'], record['processStartTicks'], record['loadedMonoNs'],
                        a['modelId'], a['behaviorVersion'], a['artifactSha256'],
                        b['modelId'], b['behaviorVersion'], b['artifactSha256'], record['planSha256'],
                        a['sourceFormat'], a['sourceCompilerRevision'], a['sourceManifestSha256'],
                        b['sourceFormat'], b['sourceCompilerRevision'], b['sourceManifestSha256'])


def read_pair_receipt(path: Path | None = None) -> LaboratoryLoad | None:
  path = pair_receipt_path(path)
  root_fd = fd = None
  try:
    root_fd = _root_fd(path, create=False)
    fd = os.open(path.name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=root_fd)
    before = os.fstat(fd)
    if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid() or
        stat.S_IMODE(before.st_mode) != 0o600 or not 0 < before.st_size <= MAX_PAIR_RECEIPT_BYTES):
      return None
    raw = os.read(fd, MAX_PAIR_RECEIPT_BYTES + 1)
    if (len(raw) > MAX_PAIR_RECEIPT_BYTES or _identity(before) != _identity(os.fstat(fd)) or
        _identity(before) != _identity(os.stat(path.name, dir_fd=root_fd, follow_symlinks=False))):
      return None
    return _decode_pair(raw)
  except (OSError, ValueError, TypeError, KeyError, RecursionError):
    return None
  finally:
    if fd is not None:
      os.close(fd)
    if root_fd is not None:
      os.close(root_fd)


def write_pair_receipt(load: LaboratoryLoad, path: Path | None = None) -> None:
  path = pair_receipt_path(path)
  raw = json.dumps(_pair_record(load), separators=(',', ':'), sort_keys=True).encode()
  if len(raw) > MAX_PAIR_RECEIPT_BYTES or _decode_pair(raw) != load:
    raise ReceiptUnavailable('invalid laboratory receipt')
  root_fd = _root_fd(path, create=True)
  temporary = f'.pair-{secrets.token_hex(8)}'
  try:
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=root_fd)
    with os.fdopen(fd, 'wb') as stream:
      stream.write(raw)
    os.replace(temporary, path.name, src_dir_fd=root_fd, dst_dir_fd=root_fd)
  finally:
    try:
      os.unlink(temporary, dir_fd=root_fd)
    except FileNotFoundError:
      pass
    os.close(root_fd)



class ModelReceiptOwner:
  """Best-effort modeld hook: diagnostic failures never stop inference."""

  def __init__(self, receipt_file: Path | None = None, report: Callable[[str], None] | None = None,
               record: Callable[[ModelLoad], None] = log_loaded_model):
    self.receipt_file = receipt_file
    self.report = report if report is not None else lambda _reason: None
    self.record = record
    self.load = None
    self.pair_load = None
    self._identity_since_ns = time.monotonic_ns()
    self._last_publication_ns = 0

  def start(self) -> None:
    self.load = None
    self.pair_load = None
    self._identity_since_ns = time.monotonic_ns()
    self._last_publication_ns = 0
    try:
      clear_receipt(self.receipt_file)
      clear_receipt(pair_receipt_path(self.receipt_file))
    except Exception:
      self.report("clear-failed")

  def capture(self, path: Path) -> tuple[int, int, int, int, int] | None:
    try:
      return artifact_identity(path)
    except Exception:
      self.report("artifact-unavailable")
      return None

  def prepare(self, path: Path, expected_identity: tuple[int, int, int, int, int] | None) -> PreparedArtifact | None:
    if expected_identity is None:
      self.report("artifact-identity-unavailable")
      return None
    try:
      return prepare_artifact(path, expected_identity)
    except Exception:
      self.report("artifact-digest-unavailable")
      return None

  def publish(self, publisher, now_ns: int) -> bool:
    """Repeat actual load identity so routes started after warm-up retain it."""
    if self._last_publication_ns and now_ns - self._last_publication_ns < 5_000_000_000:
      return False
    if self.pair_load is not None:
      try:
        from openpilot.cereal import messaging
        packet = messaging.new_message(None, valid=True, logMonoTime=now_ns)
        packet.logMessage = json.dumps({'process': os.getpid(), 'msg': {'event': 'modeld.loaded_pair',
                                      **_pair_record(self.pair_load)}}, separators=(',', ':'))
        publisher.send('modelIdentity', packet)
        self._last_publication_ns = now_ns
        return True
      except Exception:
        self.report('pair-publish-failed')
        return False
    try:
      from openpilot.cereal import messaging
      message = messaging.new_message(None, valid=True, logMonoTime=now_ns)
      message.logMessage = (recorded_model_message(self.load) if self.load is not None else
                            json.dumps({'msg': {'event': 'modeld.identity-unavailable',
                                                'sinceMonoNs': self._identity_since_ns}}))
      publisher.send('modelIdentity', message)
      self._last_publication_ns = now_ns
      return True
    except Exception:
      self.report('publication-failed')
      return False

  def loaded(self, prepared: PreparedArtifact | None, variant: ModelVariant,
             fallback_reason: str | None = None, *, model_id: str = BUNDLED_CURRENT) -> bool:
    # An unknown/failed replacement (including Jetlink handover) must retire
    # the previous volatile identity as well as the in-memory heartbeat.
    self.start()
    if prepared is None:
      self.report("prepared-artifact-unavailable")
      return False
    try:
      clear_receipt(pair_receipt_path(self.receipt_file))
      load = record_prepared(prepared, variant, fallback_reason, model_id=model_id, receipt_file=self.receipt_file)
    except Exception:
      self.report("record-failed")
      return False
    self.load = load
    self._last_publication_ns = 0
    try:
      self.record(load)
    except Exception:
      self.report("log-failed")
    return True


  def loaded_pair(self, pair, plan_sha256: str) -> bool:
    self.load = self.pair_load = None
    self._identity_since_ns = time.monotonic_ns()
    self._last_publication_ns = 0
    try:
      clear_receipt(self.receipt_file)
      clear_receipt(pair_receipt_path(self.receipt_file))
      a, b = pair.lateral_role, pair.longitudinal_role
      load = LaboratoryLoad(os.getpid(), process_start_ticks(os.getpid()), time.monotonic_ns(),
                            a.model_id, a.behavior_version, a.artifact.sha256,
                            b.model_id, b.behavior_version, b.artifact.sha256, plan_sha256,
                            a.source_format, a.source_compiler_revision, a.source_manifest_sha256,
                            b.source_format, b.source_compiler_revision, b.source_manifest_sha256)
      write_pair_receipt(load, self.receipt_file)
      self.pair_load = load
      return True
    except Exception:
      self.report('pair-record-failed')
      return False

