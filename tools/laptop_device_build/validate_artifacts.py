#!/usr/bin/env python3
"""Inspect device-build outputs without importing binaries or executing pickles."""

import argparse
from pathlib import Path


# Resolve this checkout's stdlib-only owner, even for an absolute CLI invocation
# with no PYTHONPATH or installed workspace package.
import importlib.util
_spec = importlib.util.spec_from_file_location("model_file_chunker", Path(__file__).resolve().parents[2] / "openpilot/common/file_chunker.py")
if _spec is None or _spec.loader is None:
  raise RuntimeError("Unable to load this checkout's model file chunker")
_chunk_owner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_chunk_owner)
materialize_file_chunked = _chunk_owner.materialize_file_chunked
import pickletools
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from openpilot.starpilot.models.catalog import DEFAULT_SMALL, DEFAULT_SMALL_SHA256, DEFAULT_SMALL_SIZE


EM_AARCH64 = 183
ELF_FILES = (
  "openpilot/starpilot/flm/_primitive_walk.so",
  "opendbc_repo/opendbc/can/_raw_value.so",
  "openpilot/common/libparams_c.so",
  "openpilot/selfdrive/pandad/pandad",
  "openpilot/system/loggerd/loggerd",
  "openpilot/system/loggerd/encoderd",
  "openpilot/starpilot/ui/native/_raylib_extra.so",
  "msgq_repo/msgq/ipc_pyx.so",
  "msgq_repo/msgq/visionipc/visionipc_pyx.so",
  "rednose_repo/rednose/helpers/ekf_sym_pyx.so",
  "openpilot/tools/replay/replay",
  "openpilot/tools/replay/libmedia_mux.so",
)
ARCHIVES = ("openpilot/cereal/libcereal.a", "openpilot/cereal/libsocketmaster.a")
MODEL_DIR = Path("openpilot/selfdrive/modeld/models")
# Both current fisheye camera configs in transformations/camera.py.
WARP_SIZES = ("1344x760", "1928x1208")
MODELS = ("dmonitoring_model_tinygrad.pkl",)
WARPS = tuple(f"{kind}_warp_{size}_tinygrad.pkl" for kind in ("driving", "dm") for size in WARP_SIZES)


def require_runtime_search_paths(data: bytes, name: str) -> None:
  phoff = struct.unpack_from("<Q", data, 32)[0]
  phentsize, phnum = struct.unpack_from("<HH", data, 54)
  headers = [struct.unpack_from("<IIQQQQQQ", data, phoff + i * phentsize) for i in range(phnum)]
  for header in headers:
    if header[0] != 2:  # PT_DYNAMIC
      continue
    offset, size = header[2], header[5]
    if offset + size > len(data) or size % 16:
      raise ValueError(f"{name}: malformed dynamic table")
    tags = []
    for pos in range(offset, offset + size, 16):
      tag, value = struct.unpack_from("<qQ", data, pos)
      if tag == 0:
        break
      tags.append((tag, value))
    paths = [value for tag, value in tags if tag in (15, 29)]  # DT_RPATH, DT_RUNPATH
    if not paths:
      continue
    table = dict(tags)
    address, length = table.get(5), table.get(10)  # DT_STRTAB, DT_STRSZ
    if address is None or length is None:
      raise ValueError(f"{name}: missing dynamic string table")
    loads = [h for h in headers if h[0] == 1 and h[3] <= address and address + length <= h[3] + h[5]]
    if len(loads) != 1:
      raise ValueError(f"{name}: invalid dynamic string table")
    start = loads[0][2] + address - loads[0][3]
    if start + length > len(data):
      raise ValueError(f"{name}: truncated dynamic string table")
    strings = data[start:start + length]
    for value in paths:
      end = strings.find(b"\0", value)
      if value >= length or end < 0:
        raise ValueError(f"{name}: invalid runtime search path")
      for path in strings[value:end].decode().split(":"):
        if path.startswith(("/work/", "/Users/", "/opt/tici-sysroot/")) or ".venv-linux-arm64" in path:
          raise ValueError(f"{name}: build-host runtime search path: {path}")


def require_aarch64_elf(data: bytes, name: str, *, relocatable: bool = False) -> None:
  if len(data) < 64 or data[:4] != b"\x7fELF" or data[4] != 2 or data[5] != 1:
    raise ValueError(f"{name}: expected little-endian ELF64")
  if struct.unpack_from("<H", data, 18)[0] != EM_AARCH64:
    raise ValueError(f"{name}: expected AArch64 ELF machine")
  e_type = struct.unpack_from("<H", data, 16)[0]
  e_version = struct.unpack_from("<I", data, 20)[0]
  phoff, shoff = struct.unpack_from("<QQ", data, 32)
  ehsize, phentsize, phnum, shentsize, shnum = struct.unpack_from("<HHHHH", data, 52)
  if e_version != 1 or ehsize != 64 or e_type not in ((1,) if relocatable else (2, 3)):
    raise ValueError(f"{name}: invalid ELF type/version/header")
  if relocatable:
    if not shoff or not shnum or shentsize < 64 or shoff + shentsize * shnum > len(data):
      raise ValueError(f"{name}: invalid ELF section table")
    sections = [struct.unpack_from("<IIQQQQ", data, shoff + i * shentsize) for i in range(shnum)]
    if any(section[1] not in (0, 8) and section[4] + section[5] > len(data) for section in sections):
      raise ValueError(f"{name}: object section extends beyond file")
    if not any(section[1] == 1 and section[5] > 0 for section in sections):
      raise ValueError(f"{name}: missing object code/data section")
  else:
    if not phoff or not phnum or phentsize < 56 or phoff + phentsize * phnum > len(data):
      raise ValueError(f"{name}: invalid ELF program table")
    if not any(struct.unpack_from("<I", data, phoff + i * phentsize)[0] == 1 and
               struct.unpack_from("<Q", data, phoff + i * phentsize + 32)[0] > 0 and
               struct.unpack_from("<Q", data, phoff + i * phentsize + 8)[0] +
               struct.unpack_from("<Q", data, phoff + i * phentsize + 32)[0] <= len(data)
               for i in range(phnum)):
      raise ValueError(f"{name}: missing bounded ELF load segment")
    require_runtime_search_paths(data, name)


def require_aarch64_archive(data: bytes, name: str) -> None:
  if not data.startswith(b"!<arch>\n"):
    raise ValueError(f"{name}: expected a regular ar archive")
  offset, objects = 8, 0
  while offset < len(data):
    header = data[offset:offset + 60]
    if len(header) != 60 or header[58:] != b"`\n":
      raise ValueError(f"{name}: malformed archive member")
    try:
      size = int(header[48:58].strip())
    except ValueError as exc:
      raise ValueError(f"{name}: invalid archive member size") from exc
    start, end = offset + 60, offset + 60 + size
    if end > len(data):
      raise ValueError(f"{name}: truncated archive member")
    member = data[start:end]
    if member.startswith(b"\x7fELF"):
      require_aarch64_elf(member, name, relocatable=True)
      objects += 1
    elif not header[:16].strip().startswith((b"/", b"__.SYMDEF")):
      raise ValueError(f"{name}: non-ELF object member")
    offset = end + (size % 2)
  if not objects or offset != len(data):
    raise ValueError(f"{name}: no complete AArch64 object members")


def pickle_opcodes(path: Path) -> bytes:
  with path.open("rb") as file:
    prefix = file.read(8)
    if prefix.startswith(b"\x80"):
      file.seek(0)
      return file.read(100_000_001)
    if len(prefix) != 8:
      raise ValueError(f"{path}: truncated model")
    size = struct.unpack("<q", prefix)[0]
    if not 0 < size <= 100_000_000:
      raise ValueError(f"{path}: invalid pickle opcode length")
    data = file.read(size)
    if len(data) != size:
      raise ValueError(f"{path}: truncated pickle opcodes")
    return data


def require_qcom_pickle(path: Path) -> None:
  name = path.name
  path = materialize_file_chunked(path)
  data = pickle_opcodes(path)
  if len(data) > 100_000_000:
    raise ValueError(f"{path}: pickle opcodes exceed inspection bound")
  try:
    ops = list(pickletools.genops(data))
    tokens = {arg for _, arg, _ in ops if isinstance(arg, str)}
  except (ValueError, EOFError) as exc:
    raise ValueError(f"{path}: malformed pickle opcode stream") from exc
  if not ops or ops[-1][0].name != "STOP" or not {"metadata", "run", "input_specs", "QCOM"}.issubset(tokens):
    raise ValueError(f"{path}: missing captured model/warp graph")
  if name in MODELS:
    if not {"output_specs", "output_slices", "input_shapes"}.issubset(tokens):
      raise ValueError(f"{path}: missing model input/output specification")
    if path.stat().st_size <= 8 + len(data):
      raise ValueError(f"{path}: missing out-of-band model buffers")
  # QCOM captures also serialize CPU host-dispatch/renderer nodes. A CPU token
  # therefore does not identify the device backend; require QCOM above and
  # reject explicitly captured foreign GPU backends here.
  if any(token in ("METAL", "AMD") or token.startswith(("METAL:", "AMD:", "USB+AMD")) for token in tokens):
    raise ValueError(f"{path}: foreign GPU captured backend")
  tinyjit_global = False
  captured_global = False
  for index, (op, _arg, _) in enumerate(ops):
    if op.name != "STACK_GLOBAL":
      continue
    names = [value for _, value, _ in ops[max(0, index - 4):index] if isinstance(value, str)]
    if names[-2:] == ["tinygrad.engine.jit", "_TinyJit"]:
      tinyjit_global = True
    if names and names[-1] == "CapturedJit":
      captured_global = True
  if not tinyjit_global or not captured_global:
    raise ValueError(f"{path}: missing TinyJit capture structure")


def validate(root: Path) -> None:
  from tools.laptop_device_build.validate_flm_native import validate as validate_flm_native
  validate_flm_native(root)
  for name in ELF_FILES:
    path = root / name
    require_aarch64_elf(path.read_bytes(), name)
  for name in ARCHIVES:
    path = root / name
    require_aarch64_archive(path.read_bytes(), name)

  model_dir = root / MODEL_DIR
  actual_warps = {p.name for p in model_dir.glob("driving_warp_*_tinygrad.pkl")}
  actual_warps.update(p.name for p in model_dir.glob("dm_warp_*_tinygrad.pkl"))
  if actual_warps != set(WARPS):
    raise ValueError(f"camera warp set differs from current camera configs: {sorted(actual_warps)}")
  for name in (*MODELS, *WARPS):
    require_qcom_pickle(model_dir / name)
  shipped = materialize_file_chunked(model_dir / f"{DEFAULT_SMALL}_driving_tinygrad.pkl", DEFAULT_SMALL_SHA256)
  if shipped.stat().st_size != DEFAULT_SMALL_SIZE:
    raise ValueError("Shipped default model size differs from the published artifact")


def main() -> None:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("root", type=Path)
  args = parser.parse_args()
  try:
    validate(args.root)
  except (OSError, ValueError) as exc:
    parser.exit(1, f"Device artifact validation failed: {exc}\n")
  print("Device artifacts: static AArch64 and QCOM capture structure checks passed; runtime inference remains unverified.")


if __name__ == "__main__":
  main()
