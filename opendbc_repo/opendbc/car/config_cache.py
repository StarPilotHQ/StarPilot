"""Bounded reuse of static configuration predicates for exact CarParams snapshots."""
from functools import lru_cache, wraps

from capnp.lib.capnp import _DynamicStructBuilder, _DynamicStructReader
from opendbc.car.structs import CarParams


def cached_config(check):
  # Cache only boolean configuration predicates, never sources or permissions.
  @lru_cache(maxsize=8)
  def from_bytes(key, args, kwargs):
    with CarParams.from_bytes(key) as cp:
      return check(cp, *args, **dict(kwargs))

  @wraps(check)
  def cached(cp, *args, **kwargs):
    cp_type = type(cp)
    if cp_type is _DynamicStructBuilder or cp_type is _DynamicStructReader:
      try:
        if cp.schema == CarParams.schema:
          # Serializing a copy preserves the original builder's write state.
          snapshot = cp.copy() if cp_type is _DynamicStructBuilder else cp.as_builder()
          key = snapshot.to_bytes()
          if len(key) <= 64 * 1024:
            return from_bytes(key, args, tuple(sorted(kwargs.items())))
      except Exception:
        pass
    return check(cp, *args, **kwargs)

  cached.cache_clear = from_bytes.cache_clear
  cached.cache_info = from_bytes.cache_info
  return cached
