# distutils: language = c++
# cython: language_level=3
import math
import struct
import json
import hashlib
import sys
from libcpp.string cimport string
from cpython.bytes cimport PyBytes_FromStringAndSize, PyBytes_AS_STRING, PyBytes_GET_SIZE
from cpython.unicode cimport PyUnicode_GET_LENGTH, PyUnicode_READ_CHAR

FALLBACK = object()
cdef Py_ssize_t LIMIT = 128 * 1024 * 1024
cdef int DEPTH = 32

cdef bint budget():
  cdef int frames = 0
  cdef object frame
  if sys.getrecursionlimit() < 512:
    return False
  frame = sys._getframe()
  while frame is not None and frames <= 64:
    frames += 1
    frame = frame.f_back
  return frames <= 64

cdef object limits(object item, int depth, dict memo, set active):
  cdef object kind = type(item), ident = id(item), cached, child, key, row, value
  cdef Py_ssize_t cost = 0, child_cost
  cdef int height = 0, maximum = -1
  if depth > DEPTH or ident in active:
    return FALLBACK
  cached = memo.get(ident)
  if cached is not None:
    return cached if depth + cached[1] <= DEPTH else FALLBACK
  if item is None: cost = 1
  elif kind is bool: cost = 2
  elif kind is int:
    value = 9 + (abs(item).bit_length() + 7) // 8
    if value > LIMIT: return FALLBACK
    cost = value
  elif kind is float:
    if not math.isfinite(item): return FALLBACK
    cost = 9
  elif kind is str or kind is bytes:
    value = 9 + len(item.encode('utf8', 'surrogatepass') if kind is str else item)
    if value > LIMIT: return FALLBACK
    cost = value
  elif kind is tuple or kind is list or kind is dict:
    if kind is dict:
      for key in item:
        if type(key) is not str: return FALLBACK
    active.add(ident)
    cost = 9
    try:
      if kind is dict:
        for key, child in item.items():
          for value in (key, child):
            row = limits(value, depth + 1, memo, active)
            if row is FALLBACK: return FALLBACK
            child_cost = row[0]
            if child_cost > LIMIT - cost: return FALLBACK
            cost += child_cost
            maximum = max(maximum, <int>row[1])
      else:
        for child in item:
          row = limits(child, depth + 1, memo, active)
          if row is FALLBACK: return FALLBACK
          child_cost = row[0]
          if child_cost > LIMIT - cost: return FALLBACK
          cost += child_cost
          maximum = max(maximum, <int>row[1])
    finally:
      active.remove(ident)
    height = 1 + maximum
  else: return FALLBACK
  if depth + height > DEPTH or cost > LIMIT: return FALLBACK
  row = (cost, height)
  memo[ident] = row
  return row

cdef object clone(object item):
  cdef object kind = type(item), child, key
  cdef dict mapping
  cdef list rows
  if kind is dict:
    mapping = {}
    for key, child in item.items(): mapping[key] = clone(child)
    return mapping
  if kind is tuple or kind is list:
    rows = []
    for child in item: rows.append(clone(child))
    return tuple(rows) if kind is tuple else rows
  return item

cdef object typed(object item):
  cdef object kind = type(item), child, key
  cdef list rows = []
  if item is None or kind is bool or kind is int or kind is str:
    return [kind.__name__, item]
  if kind is float: return ['float64', item.hex()]
  if kind is bytes: return ['bytes', item.hex()]
  if kind is tuple or kind is list:
    for child in item: rows.append(typed(child))
    return [kind.__name__, rows]
  for key in sorted(item): rows.append([key, typed(item[key])])
  return ['dict', rows]

def independent_primitive_graph(object value):
  if not budget() or limits(value, 0, {}, set()) is FALLBACK: return FALLBACK
  return clone(value)

cdef object shape(object item, int depth, dict memo, set active):
  cdef object kind = type(item), ident, cached, child, key, height
  cdef int maximum = -1
  if depth > DEPTH: return FALLBACK
  if item is None or kind is bool or kind is int or kind is str or kind is bytes: return 0
  if kind is float: return 0 if math.isfinite(item) else FALLBACK
  if kind is not tuple and kind is not list and kind is not dict: return FALLBACK
  ident = id(item)
  if ident in active: return FALLBACK
  cached = memo.get(ident)
  if cached is not None: return cached if depth + cached <= DEPTH else FALLBACK
  if kind is dict:
    for key in item:
      if type(key) is not str: return FALLBACK
  active.add(ident)
  try:
    for child in (item.values() if kind is dict else item):
      height = shape(child, depth + 1, memo, active)
      if height is FALLBACK: return FALLBACK
      maximum = max(maximum, <int>height)
  finally:
    active.remove(ident)
  height = 1 + maximum
  if depth + height > DEPTH: return FALLBACK
  memo[ident] = height
  return height

cdef bint large(object value, int depth):
  cdef object kind = type(value), child
  if kind is tuple or kind is list: return len(value) > 256
  if kind is dict and depth < 3:
    for child in value.values():
      if large(child, depth + 1): return True
  return False

cdef void update_digest(object digest, object value):
  cdef object kind = type(value), child, key, text
  cdef list batch
  cdef Py_ssize_t start, index = 0
  if (kind is tuple or kind is list) and len(value) > 256:
    digest.update(b'["tuple",[' if kind is tuple else b'["list",[')
    for start in range(0, len(value), 64):
      if start: digest.update(b',')
      batch = []
      for child in value[start:start + 64]: batch.append(typed(child))
      text = json.dumps(batch, separators=(',', ':'), allow_nan=False)
      digest.update(text[1:-1].encode())
    digest.update(b']]')
  elif kind is dict and large(value, 0):
    digest.update(b'["dict",[')
    for key in sorted(value):
      if index: digest.update(b',')
      digest.update(('[' + json.dumps(key, separators=(',', ':'), allow_nan=False) + ',').encode())
      update_digest(digest, value[key])
      digest.update(b']')
      index += 1
    digest.update(b']]')
  else:
    digest.update(json.dumps(typed(value), separators=(',', ':'), allow_nan=False).encode())

# One fixed-capacity native buffer, no Python token lists or typed-tree copy.
# Every byte is ASCII JSON. Admission remains the original full shape prepass.
cdef class JsonBuffer:
  cdef string data
  cdef object digest

  def __cinit__(self, object digest):
    self.digest = digest
    self.data.reserve(65536)

  cdef void flush(self) except *:
    if self.data.size():
      self.digest.update(PyBytes_FromStringAndSize(self.data.data(), self.data.size()))
      self.data.clear()

  cdef void put(self, char value) except *:
    self.data.push_back(value)
    if self.data.size() == 65536: self.flush()

  cdef void literal(self, const char* value, Py_ssize_t size) except *:
    cdef Py_ssize_t offset = 0, available, take
    while offset < size:
      available = 65536 - self.data.size()
      take = min(available, size - offset)
      self.data.append(value + offset, take)
      offset += take
      if self.data.size() == 65536: self.flush()

cdef void unicode_json(JsonBuffer output, object value) except *:
  cdef Py_ssize_t index, length
  cdef bytes encoded
  cdef unsigned int code, high, low
  cdef const char* digits = "0123456789abcdef"
  # A digest flush can release the GIL or reenter Python after shape(). A later
  # dict key may therefore no longer be an exact str. The old typed-tree JSON
  # path encodes such a raw key as a JSON value; preserve that safe behavior.
  if type(value) is not str:
    encoded = json.dumps(value, separators=(',', ':'), allow_nan=False).encode()
    output.literal(PyBytes_AS_STRING(encoded), PyBytes_GET_SIZE(encoded))
    return
  length = PyUnicode_GET_LENGTH(value)
  output.put(34)
  for index in range(length):
    code = PyUnicode_READ_CHAR(value, index)
    if code == 34 or code == 92:
      output.put(92)
      output.put(<char>code)
    elif code == 8: output.literal('\\b', 2)
    elif code == 9: output.literal('\\t', 2)
    elif code == 10: output.literal('\\n', 2)
    elif code == 12: output.literal('\\f', 2)
    elif code == 13: output.literal('\\r', 2)
    elif 32 <= code <= 126:
      output.put(<char>code)
    else:
      if code > 65535:
        high = 55296 + ((code - 65536) >> 10)
        low = 56320 + ((code - 65536) & 1023)
      else:
        high = code
        low = 0
      output.literal('\\u', 2)
      output.put(digits[(high >> 12) & 15])
      output.put(digits[(high >> 8) & 15])
      output.put(digits[(high >> 4) & 15])
      output.put(digits[high & 15])
      if code > 65535:
        output.literal('\\u', 2)
        output.put(digits[(low >> 12) & 15])
        output.put(digits[(low >> 8) & 15])
        output.put(digits[(low >> 4) & 15])
        output.put(digits[low & 15])
  output.put(34)

cdef void buffer_typed(JsonBuffer output, object value) except *:
  cdef object kind = type(value), child, key
  cdef bytes encoded
  cdef const unsigned char* raw
  cdef const char* digits = "0123456789abcdef"
  cdef Py_ssize_t index = 0, length, offset, take, byte_index
  cdef char hex_chunk[8192]
  if value is None: output.literal('["NoneType",null]', 17)
  elif kind is bool:
    if value: output.literal('["bool",true]', 13)
    else: output.literal('["bool",false]', 14)
  elif kind is int:
    output.literal('["int",', 7)
    # Exact int repr is also the C JSON encoder's decimal spelling and keeps
    # CPython's configured digit-limit exceptions. No subclass method can run.
    encoded = str(value).encode('ascii')
    output.literal(PyBytes_AS_STRING(encoded), PyBytes_GET_SIZE(encoded))
    output.put(93)
  elif kind is float:
    output.literal('["float64",', 11)
    unicode_json(output, value.hex())
    output.put(93)
  elif kind is str:
    output.literal('["str",', 7)
    unicode_json(output, value)
    output.put(93)
  elif kind is bytes:
    output.literal('["bytes","', 10)
    raw = <const unsigned char*>PyBytes_AS_STRING(value)
    length = PyBytes_GET_SIZE(value)
    for offset in range(0, length, 4096):
      take = min(<Py_ssize_t>4096, length - offset)
      for byte_index in range(take):
        hex_chunk[2 * byte_index] = digits[raw[offset + byte_index] >> 4]
        hex_chunk[2 * byte_index + 1] = digits[raw[offset + byte_index] & 15]
      output.literal(hex_chunk, 2 * take)
    output.literal('"]', 2)
  elif kind is tuple or kind is list:
    if kind is tuple: output.literal('["tuple",[', 10)
    else: output.literal('["list",[', 9)
    for child in value:
      if index: output.put(44)
      buffer_typed(output, child)
      index += 1
    output.literal(']]', 2)
  else:
    output.literal('["dict",[', 9)
    for key in sorted(value):
      if index: output.put(44)
      output.put(91)
      unicode_json(output, key)
      output.put(44)
      buffer_typed(output, value[key])
      output.put(93)
      index += 1
    output.literal(']]', 2)

def whole_body_digest(object value):
  cdef JsonBuffer output
  if not budget() or shape(value, 0, {}, set()) is FALLBACK: return FALLBACK
  digest = hashlib.sha256()
  if large(value, 0):
    output = JsonBuffer(digest)
    buffer_typed(output, value)
    output.flush()
  else:
    update_digest(digest, value)
  return digest.hexdigest()



cdef extern from "Python.h":
  int PY_MAJOR_VERSION
  int PY_MINOR_VERSION

def compiled_abi():
  return (PY_MAJOR_VERSION, PY_MINOR_VERSION, sizeof(void*) * 8, 'flm-exact-primitives-v1')


# Same complete typed key as live_tracking_probe._primitive. Only bounded
# worker contexts can load this extension; unsupported inputs use Python.
cdef object probe_key(object value, int depth, bint materialize):
  cdef object kind=type(value), child, key, result
  cdef list rows=[] if materialize else None
  if depth>40: return FALLBACK
  if value is None: return ('none',) if materialize else True
  if kind is bool or kind is int or kind is str or kind is bytes:
    if kind is int and value.bit_length()>512: return FALLBACK
    if (kind is str or kind is bytes) and len(value)>131072: return FALLBACK
    return (kind.__name__,value) if materialize else True
  if kind is float:
    if not math.isfinite(value): return FALLBACK
    return ('float64',struct.pack('!d',value)) if materialize else True
  if kind is tuple or kind is list:
    if len(value)>8192: return FALLBACK
    for child in value:
      result=probe_key(child,depth+1,materialize)
      if result is FALLBACK: return FALLBACK
      if materialize: rows.append(result)
    return (kind.__name__,tuple(rows)) if materialize else True
  if kind is dict and len(value)<=8192:
    for key in value:
      if type(key) is not str: return FALLBACK
    for key in sorted(value):
      result=probe_key(value[key],depth+1,materialize)
      if result is FALLBACK: return FALLBACK
      if materialize: rows.append((key,result))
    return ('dict',tuple(rows)) if materialize else True
  return FALLBACK


def probe_source_key(object value):
  if not budget(): return FALLBACK
  return probe_key(value,0,True)


def probe_source_valid(object value):
  if not budget(): return FALLBACK
  return probe_key(value,0,False)
