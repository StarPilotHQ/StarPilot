from collections import OrderedDict
from contextlib import contextmanager
from threading import RLock
import weakref


class WeakLayoutMemo:
  def __init__(self, original, capacity=1024):
    if capacity < 1:
      raise ValueError('Layout cache must be bounded and nonempty')
    self.original = original
    self.capacity = capacity
    self.entries = OrderedDict()
    self.lock = RLock()
    self.hits = self.misses = 0
    self.owner = weakref.ref(self)

  def __call__(self, node):
    owner = self.owner
    def removed(key):
      if (cache := owner()) is not None:
        with cache.lock:
          cache.entries.pop(key, None)
    key = weakref.ref(node, removed)
    with self.lock:
      if key in self.entries:
        entry = self.entries[key]
        if entry is None:
          self.entries.move_to_end(key)
          self.hits += 1
          return None
        base, offset = entry
        if (resolved := base()) is not None:
          self.entries.move_to_end(key)
          self.hits += 1
          return resolved, offset
        del self.entries[key]
      self.misses += 1
      result = self.original(node)
      self.entries[key] = None if result is None else (weakref.ref(result[0]), result[1])
      if len(self.entries) > self.capacity:
        self.entries.popitem(last=False)
      return result

  def clear(self):
    with self.lock:
      self.entries.clear()

  @contextmanager
  def installed(self):
    from tinygrad.uop.ops import UOp
    if UOp.contiguous_view is not self.original:
      yield
      return
    memo = self
    def replay(self):
      return memo(self)
    UOp.contiguous_view = replay
    try:
      yield
    finally:
      if UOp.contiguous_view is replay:
        UOp.contiguous_view = self.original
