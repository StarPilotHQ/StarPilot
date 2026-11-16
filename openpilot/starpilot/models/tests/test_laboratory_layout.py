from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from functools import lru_cache
import gc
import time
import unittest
import weakref

import numpy as np
from tinygrad import Tensor
from tinygrad.uop.ops import UOp

from openpilot.starpilot.models.laboratory_layout import WeakLayoutMemo

ORIGINAL = UOp.contiguous_view


@contextmanager
def installed(cache):
  def replay(self):
    return cache(self)
  UOp.contiguous_view = replay
  try:
    yield
  finally:
    UOp.contiguous_view = ORIGINAL


class TestWeakLayout(unittest.TestCase):
  def test_real_view_layouts_match_original(self):
    cache = WeakLayoutMemo(ORIGINAL)
    base = Tensor(np.arange(256, dtype=np.float32).reshape(16, 16), device='NPY').realize()
    views = [base, base.reshape(256), base[1:8], base.reshape(256)[3:91], base.transpose(), base[:, ::2]]
    for view in views:
      expected = ORIGINAL(view.uop)
      for _ in range(3):
        actual = cache(view.uop)
        self.assertEqual(actual, expected)
        if actual is not None:
          assert expected is not None
          self.assertIs(actual[0], expected[0])
    self.assertGreater(cache.hits, 0)

  def test_storage_rebind_is_fresh(self):
    cache = WeakLayoutMemo(ORIGINAL)
    base = Tensor(np.arange(16, dtype=np.float32), device='NPY').realize()
    view = base[2:8]
    with installed(cache):
      first = view.uop.buffer.ensure_allocated()
      np.testing.assert_array_equal(np.frombuffer(first.as_memoryview(), dtype=np.float32), np.arange(2, 8, dtype=np.float32))
      storage = base.uop.base.buffer
      storage.deallocate()
      storage.allocate(opaque=np.arange(16, dtype=np.float32) + 100)
      second = view.uop.buffer.ensure_allocated()
      self.assertIsNot(first, second)
      np.testing.assert_array_equal(np.frombuffer(second.as_memoryview(), dtype=np.float32), np.arange(2, 8, dtype=np.float32) + 100)

  def test_noncontiguous_buffers_still_refuse(self):
    cache = WeakLayoutMemo(ORIGINAL)
    view = Tensor(np.arange(32, dtype=np.float32), device='NPY').realize()[::2]
    with installed(cache):
      for _ in range(3):
        self.assertIsNone(view.uop.contiguous_view())
        with self.assertRaises(RuntimeError):
          _ = view.uop.buffer

  def test_capacity_is_bounded_with_live_graphs(self):
    cache = WeakLayoutMemo(ORIGINAL, capacity=16)
    base = Tensor(np.arange(512, dtype=np.float32), device='NPY').realize()
    views = [base[i:i + 32] for i in range(100)]
    for view in views:
      self.assertEqual(cache(view.uop), ORIGINAL(view.uop))
    self.assertEqual(len(cache.entries), 16)
    self.assertEqual(cache(views[0].uop), ORIGINAL(views[0].uop))
    self.assertEqual(len(cache.entries), 16)

  def test_closed_graph_not_retained(self):
    cache = WeakLayoutMemo(ORIGINAL)
    def run():
      base = Tensor(np.arange(37, dtype=np.float32), device='NPY').realize()
      view = base[7:19]
      cache(view.uop)
      return weakref.ref(view.uop)
    node = run()
    gc.collect()
    self.assertIsNone(node())
    self.assertEqual(len(cache.entries), 0)

  def test_previous_strong_cache_retains_closed_graph_until_clear(self):
    cache = lru_cache(maxsize=1024)(ORIGINAL)
    def run():
      view = Tensor(np.arange(41, dtype=np.float32), device='NPY').realize()[3:22]
      cache(view.uop)
      return weakref.ref(view.uop)
    node = run()
    gc.collect()
    self.assertIsNotNone(node())
    cache.cache_clear()
    gc.collect()
    self.assertIsNone(node())

  def test_replacement_graph_uses_current_layout(self):
    cache = WeakLayoutMemo(ORIGINAL)
    base = Tensor(np.arange(64, dtype=np.float32), device='NPY').realize()
    before = base[1:17]
    after = base[9:25]
    self.assertEqual(cache(before.uop), ORIGINAL(before.uop))
    self.assertEqual(cache(after.uop), ORIGINAL(after.uop))
    self.assertNotEqual(cache(before.uop)[1], cache(after.uop)[1])

  def test_parallel_lookups_and_eviction_preserve_results(self):
    cache = WeakLayoutMemo(ORIGINAL, capacity=8)
    base = Tensor(np.arange(128, dtype=np.float32), device='NPY').realize()
    views = [base[i:i + 16] for i in range(16)]
    expected = [ORIGINAL(view.uop) for view in views]
    def read(index):
      result = cache(views[index % 16].uop)
      self.assertEqual(result, expected[index % 16])
    with ThreadPoolExecutor(max_workers=4) as pool:
      list(pool.map(read, range(300)))
    self.assertLessEqual(len(cache.entries), 8)

  def test_original_failures_are_not_cached(self):
    def original(node):
      raise RuntimeError('original refusal')
    cache = WeakLayoutMemo(original)
    tensor = Tensor(np.arange(4), device='NPY').realize()
    for _ in range(2):
      with self.assertRaisesRegex(RuntimeError, 'original refusal'):
        cache(tensor.uop)
    self.assertEqual(len(cache.entries), 0)

  def test_layout_microbenchmark(self):
    cache = WeakLayoutMemo(ORIGINAL)
    base = Tensor(np.arange(4096, dtype=np.float32), device='NPY').realize()
    views = [base[i:i + 128].uop for i in range(32)]
    for node in views:
      self.assertEqual(cache(node), ORIGINAL(node))
    results = {}
    for label, operation in [('original', ORIGINAL), ('weak_memo', cache)]:
      began = time.perf_counter_ns()
      for i in range(2000):
        operation(views[i % 32])
      results[label] = (time.perf_counter_ns() - began) / 2000
    print('HOST_LAYOUT_NS_PER_CALL', results)

  def test_product_scope_restores_original_after_failure(self):
    cache = WeakLayoutMemo(ORIGINAL)
    with self.assertRaisesRegex(RuntimeError, 'frame failed'):
      with cache.installed():
        self.assertIsNot(UOp.contiguous_view, ORIGINAL)
        raise RuntimeError('frame failed')
    self.assertIs(UOp.contiguous_view, ORIGINAL)

  def test_product_scope_does_not_replace_foreign_hook(self):
    cache = WeakLayoutMemo(ORIGINAL)
    def foreign(self):
      return ORIGINAL(self)
    UOp.contiguous_view = foreign
    try:
      with cache.installed():
        self.assertIs(UOp.contiguous_view, foreign)
      self.assertIs(UOp.contiguous_view, foreign)
    finally:
      UOp.contiguous_view = ORIGINAL

  def test_actual_pair_scope_and_failure_clear_metadata(self):
    from openpilot.starpilot.models.tests import test_laboratory_runner as callers
    pair = callers.make_pair()
    cache = WeakLayoutMemo(ORIGINAL)
    pair._layout_cache = cache
    tensor = Tensor(np.arange(16), device='NPY').realize()
    cache(tensor.uop)
    self.assertTrue(cache.entries)
    pair.lateral.run_policy.side_effect = RuntimeError('frame failed')
    with self.assertRaisesRegex(RuntimeError, 'frame failed'):
      callers.TestLaboratoryFrame.run_frame(pair)
    self.assertIs(UOp.contiguous_view, ORIGINAL)
    self.assertFalse(cache.entries)
