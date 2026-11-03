#!/usr/bin/env python3
import hashlib
import json
import lzma
import os
from pathlib import Path
import struct
import subprocess
import time
from collections.abc import Generator

import requests

SPARSE_CHUNK_FMT = struct.Struct('H2xI4x')

AGNOS_MANIFEST_FILE = "openpilot/common/hardware/comma/agnos.json"


INCOMPATIBLE_C3_ABL = "29fd7ed1c012e599420764840f9f11286d34dbff4adaf102a447f06d8c5e0b35"
AGNOS_MARKER = Path("/AGNOS")
DEVICE_MODEL_PATH = Path("/sys/firmware/devicetree/base/model")


def check_manifest_compatibility(update: list[dict]) -> None:
  if not any(p.get('name') == 'abl' and p.get('hash_raw', '').lower() == INCOMPATIBLE_C3_ABL for p in update):
    return
  try:
    model = DEVICE_MODEL_PATH.read_text().rstrip('\0\n')
  except FileNotFoundError:
    # Non-device hosts have no device tree.
    if not AGNOS_MARKER.exists():
      return
    raise RuntimeError("Cannot identify hardware for AGNOS firmware update") from None
  if model == 'comma tici':
    raise RuntimeError("This AGNOS bootloader is incompatible with comma 3. No firmware write or slot activation was attempted.")
  if model not in ('comma tizi', 'comma mici'):
    raise RuntimeError("Cannot identify supported hardware for this AGNOS bootloader")


def phone_uplink_active() -> bool:
  try:
    from openpilot.starpilot.galaxy.companion import phone_uplink_active as active
  except ImportError:
    # launch_chffrplus.sh runs this file directly from the old tree before PYTHONPATH is set.
    try:
      with open('/run/starpilot-companion/status.json') as f:
        state = json.load(f)
      return state.get('active') is True and 0 <= time.monotonic() - state['updated'] < 15
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
      return False
  return active()


class StreamingDecompressor:
  def __init__(self, url: str) -> None:
    if phone_uplink_active():
      raise requests.ConnectionError("AGNOS downloads are disabled on the phone uplink")
    self.buf = b""

    self.req = requests.get(url, stream=True, headers={'Accept-Encoding': 'identity'}, timeout=60)
    self.it = self.req.iter_content(chunk_size=1024 * 1024)
    self.decompressor = lzma.LZMADecompressor(format=lzma.FORMAT_AUTO)
    self.eof = False
    self.sha256 = hashlib.sha256()

  def read(self, length: int) -> bytes:
    if phone_uplink_active():
      self.req.close()
      raise requests.ConnectionError("Phone uplink activated; AGNOS download stopped")
    while len(self.buf) < length and not self.eof:
      if self.decompressor.needs_input:
        self.req.raise_for_status()

        try:
          compressed = next(self.it)
        except StopIteration:
          self.eof = True
          break
      else:
        compressed = b''

      self.buf += self.decompressor.decompress(compressed, max_length=length)

      if self.decompressor.eof:
        self.eof = True
        break

    result = self.buf[:length]
    self.buf = self.buf[length:]

    self.sha256.update(result)
    return result


def unsparsify(f: StreamingDecompressor) -> Generator[bytes, None, None]:
  # https://source.android.com/devices/bootloader/images#sparse-format
  magic = struct.unpack("I", f.read(4))[0]
  assert(magic == 0xed26ff3a)

  # Version
  major = struct.unpack("H", f.read(2))[0]
  minor = struct.unpack("H", f.read(2))[0]
  assert(major == 1 and minor == 0)

  f.read(2)  # file header size
  f.read(2)  # chunk header size

  block_sz = struct.unpack("I", f.read(4))[0]
  f.read(4)  # total blocks
  num_chunks = struct.unpack("I", f.read(4))[0]
  f.read(4)  # crc checksum

  for _ in range(num_chunks):
    chunk_type, out_blocks = SPARSE_CHUNK_FMT.unpack(f.read(12))

    if chunk_type == 0xcac1:  # Raw
      # TODO: yield in smaller chunks. Yielding only block_sz is too slow. Largest observed data chunk is 252 MB.
      yield f.read(out_blocks * block_sz)
    elif chunk_type == 0xcac2:  # Fill
      filler = f.read(4) * (block_sz // 4)
      for _ in range(out_blocks):
        yield filler
    elif chunk_type == 0xcac3:  # Don't care
      yield b""
    else:
      raise Exception("Unhandled sparse chunk type")


# noop wrapper with same API as unsparsify() for non sparse images
def noop(f: StreamingDecompressor) -> Generator[bytes, None, None]:
  while len(chunk := f.read(1024 * 1024)) > 0:
    yield chunk


def get_target_slot_number() -> int:
  current_slot = subprocess.check_output(["abctl", "--boot_slot"], encoding='utf-8').strip()
  return 1 if current_slot == "_a" else 0


def slot_number_to_suffix(slot_number: int) -> str:
  assert slot_number in (0, 1)
  return '_a' if slot_number == 0 else '_b'


def get_partition_path(target_slot_number: int, partition: dict) -> str:
  path = f"/dev/disk/by-partlabel/{partition['name']}"

  if partition.get('has_ab', True):
    path += slot_number_to_suffix(target_slot_number)

  return path


def get_raw_hash(path: str, partition_size: int) -> str:
  raw_hash = hashlib.sha256()
  pos, chunk_size = 0, 1024 * 1024

  with open(path, 'rb+') as out:
    while pos < partition_size:
      n = min(chunk_size, partition_size - pos)
      raw_hash.update(out.read(n))
      pos += n

  return raw_hash.hexdigest().lower()


def verify_partition(target_slot_number: int, partition: dict[str, str | int], force_full_check: bool = False) -> bool:
  full_check = partition['full_check'] or force_full_check
  path = get_partition_path(target_slot_number, partition)

  if not isinstance(partition['size'], int):
    return False

  partition_size: int = partition['size']

  if not isinstance(partition['hash_raw'], str):
    return False

  partition_hash: str = partition['hash_raw']

  if full_check:
    return get_raw_hash(path, partition_size) == partition_hash.lower()
  else:
    with open(path, 'rb+') as out:
      out.seek(partition_size)
      return out.read(64) == partition_hash.lower().encode()


def clear_partition_hash(target_slot_number: int, partition: dict) -> None:
  path = get_partition_path(target_slot_number, partition)
  with open(path, 'wb+') as out:
    partition_size = partition['size']

    out.seek(partition_size)
    out.write(b"\x00" * 64)
    os.sync()


def extract_compressed_image(target_slot_number: int, partition: dict, cloudlog):
  path = get_partition_path(target_slot_number, partition)
  downloader = StreamingDecompressor(partition['url'])

  with open(path, 'wb+') as out:
    # Flash partition
    last_p = 0
    raw_hash = hashlib.sha256()
    f = unsparsify if partition['sparse'] else noop
    for chunk in f(downloader):
      raw_hash.update(chunk)
      out.write(chunk)
      p = int(out.tell() / partition['size'] * 100)
      if p != last_p:
        last_p = p
        print(f"Installing {partition['name']}: {p}", flush=True)

    if raw_hash.hexdigest().lower() != partition['hash_raw'].lower():
      raise Exception(f"Raw hash mismatch '{raw_hash.hexdigest().lower()}'")

    if downloader.sha256.hexdigest().lower() != partition['hash'].lower():
      raise Exception("Uncompressed hash mismatch")

    if out.tell() != partition['size']:
      raise Exception("Uncompressed size mismatch")

    os.sync()


def flash_partition(target_slot_number: int, partition: dict, cloudlog, standalone=False):
  cloudlog.info(f"Downloading and writing {partition['name']}")

  if verify_partition(target_slot_number, partition):
    cloudlog.info(f"Already flashed {partition['name']}")
    return

  # Clear hash before flashing in case we get interrupted
  full_check = partition['full_check']
  if not full_check:
    clear_partition_hash(target_slot_number, partition)

  path = get_partition_path(target_slot_number, partition)

  extract_compressed_image(target_slot_number, partition, cloudlog)

  # Write hash after successful flash
  if not full_check:
    with open(path, 'wb+') as out:
      out.seek(partition['size'])
      out.write(partition['hash_raw'].lower().encode())


def swap(manifest_path: str, target_slot_number: int, cloudlog) -> None:
  update = json.load(open(manifest_path))
  check_manifest_compatibility(update)
  for partition in update:
    if not partition.get('full_check', False):
      clear_partition_hash(target_slot_number, partition)

  while True:
    out = subprocess.check_output(f"abctl --set_active {target_slot_number}", shell=True, stderr=subprocess.STDOUT, encoding='utf8')
    if ("No such file or directory" not in out) and ("lun as boot lun" in out):
      cloudlog.info(f"Swap successful {out}")
      break
    else:
      cloudlog.error(f"Swap failed {out}")


def flash_agnos_update(manifest_path: str, target_slot_number: int, cloudlog, standalone=False) -> None:
  update = json.load(open(manifest_path))
  check_manifest_compatibility(update)

  cloudlog.info(f"Target slot {target_slot_number}")

  # set target slot as unbootable
  subprocess.run(f"abctl --set_unbootable {target_slot_number}", shell=True)

  for partition in update:
    success = False

    for retries in range(10):
      try:
        flash_partition(target_slot_number, partition, cloudlog, standalone)
        success = True
        break

      except requests.exceptions.RequestException:
        cloudlog.exception("Failed")
        cloudlog.info(f"Failed to download {partition['name']}, retrying ({retries})")
        time.sleep(10)

    if not success:
      cloudlog.info(f"Failed to flash {partition['name']}, aborting")
      raise Exception("Maximum retries exceeded")

  cloudlog.info(f"AGNOS ready on slot {target_slot_number}")


def verify_agnos_update(manifest_path: str, target_slot_number: int) -> bool:
  update = json.load(open(manifest_path))
  check_manifest_compatibility(update)
  return all(verify_partition(target_slot_number, partition) for partition in update)


if __name__ == "__main__":
  import argparse
  import logging

  parser = argparse.ArgumentParser(description="Flash and verify AGNOS update",
                                   formatter_class=argparse.ArgumentDefaultsHelpFormatter)

  parser.add_argument("--verify", action="store_true", help="Verify and perform swap if update ready")
  parser.add_argument("--swap", action="store_true", help="Verify and perform swap, downloads if necessary")
  parser.add_argument("manifest", help="Manifest json")
  args = parser.parse_args()

  logging.basicConfig(level=logging.INFO)

  target_slot_number = get_target_slot_number()
  if args.verify:
    if verify_agnos_update(args.manifest, target_slot_number):
      swap(args.manifest, target_slot_number, logging)
      exit(0)
    exit(1)
  elif args.swap:
    while not verify_agnos_update(args.manifest, target_slot_number):
      logging.error("Verification failed. Flashing AGNOS")
      flash_agnos_update(args.manifest, target_slot_number, logging, standalone=True)

    logging.warning(f"Verification succeeded. Swapping to slot {target_slot_number}")
    swap(args.manifest, target_slot_number, logging)
  else:
    flash_agnos_update(args.manifest, target_slot_number, logging, standalone=True)
