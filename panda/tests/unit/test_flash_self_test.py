import ctypes
import os
from pathlib import Path
import re
import shlex
import struct
import subprocess
import zlib

import pytest


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope='module')
def flash_test(tmp_path_factory):
  directory = tmp_path_factory.mktemp('flash-self-test')
  declaration = re.search(
    r'(?:#ifdef STM32H7\n)?extern int _app_start\[[^\n]+\n(?:#else\nextern int _app_start\[[^\n]+\n#endif)?',
    (ROOT / 'board/main_comms.h').read_text(),
  ).group()
  source = (ROOT / 'board/drivers/self_test.h').read_text()
  functions = '\n'.join(re.search(r'^static [^\n]+ ' + name + r'\([^\n]*\) \{.*?^\}', source, re.M | re.S).group()
                        for name in ('flash_crc_len', 'flash_crc_stored', 'flash_test_next'))
  code = directory / 'flash.c'
  code.write_text('\n'.join([
    '#include <stdint.h>\n#include <stdbool.h>\n#include <string.h>\n#include "board/crypto/rsa.h"\n#define STM32H7',
    '#define FLASH_CRC_CHUNK 0x400U\n#define MIN(a,b) ((a)<(b)?(a):(b))', declaration,
    'int _app_start[sizeof(_app_start) / sizeof(int)];', (ROOT / 'board/crc.h').read_text(), functions,
    'void load(const void *p, unsigned n) { memset(_app_start, 0, sizeof(_app_start)); memcpy(_app_start, p, MIN(n,sizeof(_app_start))); crc32_init(); }',
    'unsigned capacity(void) { return sizeof(_app_start); }',
    'bool boot(void) { unsigned n=flash_crc_len(); return n && crc32((void *)_app_start,n)==flash_crc_stored(); }',
    'bool tick(void) { unsigned crc=0; return flash_test_next(&crc); }',
  ]))
  library = directory / 'flash.so'
  subprocess.run([*shlex.split(os.environ.get('CC', 'cc')), '-shared', '-fPIC', '-O2', '-I', str(ROOT), '-o', str(library), str(code)], check=True)
  result = ctypes.CDLL(str(library))
  result.load.argtypes = [ctypes.c_void_p, ctypes.c_uint]
  result.boot.restype = result.tick.restype = ctypes.c_bool
  return result


def signed_image(size):
  payload = struct.pack('<I', size + 12) + bytes(i % 251 for i in range(size - 4))
  return payload + struct.pack('<I', zlib.crc32(payload)) + b'VERS' + struct.pack('<I', 2) + bytes(128)


@pytest.mark.parametrize('size', (4096, 196608, 220000, 0xE0000 - 140))
@pytest.mark.parametrize('corrupt', (False, True))
def test_boot_and_incremental_crc(flash_test, size, corrupt):
  image = bytearray(signed_image(size))
  if corrupt:
    image[size - 1] ^= 1
  flash_test.load(bytes(image), len(image))
  assert flash_test.boot() is not corrupt
  ticks = [flash_test.tick() for _ in range((size + 1023) // 1024)]
  assert all(ticks[:-1])
  assert ticks[-1] is not corrupt


@pytest.mark.parametrize('length', (0, 11, 0xE0000 - 127, 0xE0000, 0xE0001, 0xFFFFFFFF))
def test_invalid_lengths(flash_test, length):
  image = struct.pack('<I', length) + bytes(128)
  flash_test.load(image, len(image))
  assert not flash_test.boot()
  assert not flash_test.tick()


def test_h7_flash_capacity(flash_test):
  assert flash_test.capacity() == 1024 * 1024 - 128 * 1024


@pytest.mark.parametrize('image', sorted((ROOT / 'board/obj').glob('panda_h7*.bin.signed')), ids=lambda p: p.name)
def test_shipped_h7_crc(flash_test, image):
  data = image.read_bytes()
  assert len(data) <= flash_test.capacity()
  flash_test.load(data, len(data))
  assert flash_test.boot()
