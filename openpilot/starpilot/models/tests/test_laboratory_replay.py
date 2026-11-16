import gc
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import weakref

import numpy as np
from tinygrad import Tensor, TinyJit, Context
from tinygrad.engine.jit import JitError

from openpilot.starpilot.models.laboratory_replay import AdmittedReplay, enable_replay
from openpilot.starpilot.models.tests import test_laboratory_runner as pair_tests
from openpilot.starpilot.models.tests.test_shared_camera_warp import make_runner


class TestAdmittedReplay(unittest.TestCase):
  @staticmethod
  def ready():
    x = Tensor(np.arange(8, dtype=np.float32)).realize()
    jit = TinyJit(lambda x: (x + 1).realize())
    jit(x=x)
    jit(x=x)
    replay = AdmittedReplay(jit)
    replay(x=x)
    return x, jit, replay

  def test_current_data_is_read_on_every_cached_call(self):
    x, jit, replay = self.ready()
    for offset in (3, 19, -4):
      x.assign(Tensor(np.arange(8, dtype=np.float32) + offset)).realize()
      np.testing.assert_array_equal(replay(x=x).numpy(), np.arange(8, dtype=np.float32) + offset + 1)
    self.assertEqual(len(replay.cache), 1)
    self.assertEqual(jit.cnt, 6)

  def test_hit_skips_only_repeated_input_preparation(self):
    x, jit, replay = self.ready()
    from tinygrad.engine import jit as module
    with patch.object(module, '_prepare_jit_inputs', side_effect=AssertionError('metadata recomputed')):
      np.testing.assert_array_equal(replay(x=x).numpy(), np.arange(8) + 1)
    self.assertEqual(jit.cnt, 4)

  def test_changed_inputs_preserve_original_admission_and_results(self):
    values = ({'other': Tensor(np.zeros(8, dtype=np.float32)).realize()},
              {'x': Tensor(np.arange(9, dtype=np.float32)).realize()},
              {'x': Tensor(np.arange(8, dtype=np.int32)).realize()})
    for kwargs in values:
      with self.subTest(kwargs=kwargs):
        _, jit, replay = self.ready()
        count = jit.cnt
        try:
          expected = jit(**kwargs).numpy().copy()
        except JitError as error:
          with self.assertRaisesRegex(JitError, str(error).split(':')[0]):
            replay(**kwargs)
          self.assertEqual(jit.cnt, count)
          self.assertEqual(len(replay.cache), 0)
        else:
          np.testing.assert_array_equal(replay(**kwargs).numpy(), expected)

  def test_duplicate_inputs_are_still_rejected(self):
    x = Tensor.ones(8).realize()
    y = Tensor.zeros(8).realize()
    jit = TinyJit(lambda x, y: (x + y).realize())
    jit(x=x, y=y)
    jit(x=x, y=y)
    replay = AdmittedReplay(jit)
    replay(x=x, y=y)
    with self.assertRaisesRegex(JitError, 'duplicate'):
      replay(x=x, y=x)
    self.assertEqual(len(replay.cache), 0)

  def test_replaced_uop_uses_original_validation(self):
    x, jit, replay = self.ready()
    x.uop = Tensor(np.arange(9, dtype=np.float32) + 17).realize().uop
    from tinygrad.engine import jit as module
    expected = jit(x=x).numpy().copy()
    with patch.object(module, '_prepare_jit_inputs', wraps=module._prepare_jit_inputs) as prepare:
      np.testing.assert_array_equal(replay(x=x).numpy(), expected)
      self.assertGreaterEqual(prepare.call_count, 1)

  def test_capture_replacement_invalidates_bindings(self):
    x, jit, replay = self.ready()
    other = TinyJit(lambda x: (x * 3).realize())
    other(x=x)
    other(x=x)
    jit.captured = other.captured
    np.testing.assert_array_equal(replay(x=x).numpy(), np.arange(8) * 3)
    self.assertEqual(len(replay.cache), 1)

  def test_capture_reset_uses_normal_capture_again(self):
    x, jit, replay = self.ready()
    replay.reset()
    self.assertEqual(len(replay.cache), 0)
    np.testing.assert_array_equal(replay(x=x).numpy(), np.arange(8) + 1)
    self.assertEqual(len(replay.cache), 0)
    self.assertEqual(jit.cnt, 1)

  def test_failed_native_call_does_not_increment_or_retain_bindings(self):
    x, jit, replay = self.ready()
    count = jit.cnt
    with patch.object(type(jit.captured), '__call__', side_effect=RuntimeError('native failure')):
      with self.assertRaisesRegex(RuntimeError, 'native failure'):
        replay(x=x)
    self.assertEqual(jit.cnt, count)
    self.assertEqual(len(replay.cache), 0)

  def test_disabled_jit_and_capture_use_original_path(self):
    x, jit, replay = self.ready()
    with Context(JIT=0):
      np.testing.assert_array_equal(replay(x=x).numpy(), np.arange(8) + 1)
    with patch('tinygrad.engine.jit.capturing', [object()]):
      with patch.object(type(jit), '__call__', return_value='original') as original:
        self.assertEqual(replay(x=x), 'original')
        original.assert_called_once()

  def test_cache_is_bounded_and_clear_releases_input_tensors(self):
    x, jit, replay = self.ready()
    for _ in range(40):
      replay(x=Tensor.ones(8).realize())
    self.assertEqual(len(replay.cache), 32)
    value = Tensor.ones(8).realize()
    ref = weakref.ref(value)
    replay(x=value)
    del value
    gc.collect()
    self.assertIsNotNone(ref())
    replay.clear()
    gc.collect()
    self.assertIsNone(ref())

  def test_enable_only_wraps_actual_jits(self):
    x, jit, replay = self.ready()
    policy = Mock()
    model = SimpleNamespace(warp_enqueue=jit, run_policy=policy)
    enable_replay(model)
    self.assertIsInstance(model.warp_enqueue, AdmittedReplay)
    self.assertIs(model.run_policy, policy)
    self.assertEqual(model._laboratory_replays, (model.warp_enqueue,))

  def test_actual_model_reset_clears_bindings_before_new_queues(self):
    model = make_runner()
    cache = Mock()
    model._laboratory_replays = (cache,)
    old = model.input_queues
    model._reset_state()
    cache.clear.assert_called_once()
    self.assertIsNot(model.input_queues, old)

  def test_actual_pair_failure_clears_both_roles(self):
    for stage in ('enqueue', 'action'):
      pair = pair_tests.make_pair()
      caches = [Mock(), Mock()]
      pair.lateral._laboratory_replays = (caches[0],)
      pair.longitudinal._laboratory_replays = (caches[1],)
      if stage == 'enqueue':
        pair.lateral.run_policy.side_effect = RuntimeError('policy failure')
      else:
        pair.longitudinal._parse.return_value['action'] = np.zeros((1, 3))
      with self.assertRaises((RuntimeError, ValueError)):
        pair_tests.TestLaboratoryFrame.run_frame(pair)
      self.assertTrue(pair.failed)
      for cache in caches:
        self.assertGreaterEqual(cache.clear.call_count, 1)

  def test_reallocated_input_storage_uses_current_bytes(self):
    x, jit, replay = self.ready()
    values = np.arange(8, dtype=np.float32) + 31
    buffer = x.uop.base.buffer
    buffer.deallocate()
    buffer.allocate()
    buffer.copy_from(Tensor(values).realize().uop.base.buffer)
    np.testing.assert_array_equal(replay(x=x).numpy(), values + 1)

  def test_mutated_capture_contract_is_not_hidden_by_cache(self):
    x, jit, replay = self.ready()
    jit.captured.expected_names = ['other']
    count = jit.cnt
    with self.assertRaises(JitError):
      replay(x=x)
    self.assertEqual(jit.cnt, count)
    self.assertEqual(len(replay.cache), 0)

  def test_replaced_capture_linear_invalidates_bindings(self):
    x, jit, replay = self.ready()
    from tinygrad.engine import jit as module
    original = jit.captured._linear
    jit.captured._linear = original.replace(src=(*original.src, *original.src))
    self.assertIsNot(jit.captured._linear, original)
    jit.captured.__dict__.pop('linear', None)
    with patch.object(module, '_prepare_jit_inputs', wraps=module._prepare_jit_inputs) as prepare:
      np.testing.assert_array_equal(replay(x=x).numpy(), np.arange(8) + 1)
      self.assertGreaterEqual(prepare.call_count, 1)

  def test_deallocated_input_returns_to_original_admission(self):
    x, jit, replay = self.ready()
    x.uop.base.buffer.deallocate()
    with patch.object(type(jit), '__call__', side_effect=JitError('original input admission')) as original:
      with self.assertRaisesRegex(JitError, 'original input admission'):
        replay(x=x)
      original.assert_called_once()
    self.assertEqual(len(replay.cache), 0)
