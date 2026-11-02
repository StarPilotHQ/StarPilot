"""Stock ModelState.run method scope; no modeld import/bootstrap or native device.

Extract the proposed production method unchanged, bind it to a fixture class,
and exercise its real preprocessing and fault/callback/output order. This is
not a full-module, model-loading, GPU, or native memory-layout qualification.
"""
import __future__
import ast
from pathlib import Path
from types import SimpleNamespace
from typing import cast
import unittest
from unittest.mock import patch

import numpy as np

from openpilot.starpilot.models.runtime_bridge import runtime


def load_subject(device):
  path = Path(__file__).resolve().parents[3] / 'selfdrive/modeld/modeld.py'
  # parents[3] is the openpilot directory for a production models/tests path.
  tree = ast.parse(path.read_text())
  cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'ModelState')
  methods = [node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name in ('run', 'slice_outputs')]
  assert {node.name for node in methods} == {'run', 'slice_outputs'}
  original = {node.name: ast.dump(node, include_attributes=False) for node in methods}
  namespace = {'np': np, 'Device': device, 'selected_legacy': runtime.selected_legacy, 'SEND_RAW_PRED': False, 'cast': cast}
  module = ast.Module(body=[*methods], type_ignores=[])
  exec(compile(module, str(path), 'exec', flags=__future__.annotations.compiler_flag), namespace)
  # AST nodes are consumed unchanged; no rewritten expression or surrogate run.
  assert original == {node.name: ast.dump(node, include_attributes=False) for node in methods}
  subject = type('BoundStockModelStateRun', (), {name: namespace[name] for name in original})
  return subject, path


class TestStockModelErrorState(unittest.TestCase):
  def fixture(self, legacy, chestnut, flag):
    events = []
    native_forbidden = legacy or not chestnut

    class FlagView:
      def __getitem__(self, index):
        self_outer.assertEqual(index, 0)
        events.append('flag_read')
        return flag

    def host_view(*, fmt):
      self.assertEqual(fmt, 'q')
      events.append('host_view')
      return FlagView()

    class Registry:
      def __getitem__(self, name):
        if native_forbidden:
          raise AssertionError('legacy/non-Chestnut accessed nonexistent native error_state')
        self_outer.assertEqual(name, 'test-output')
        events.append('device_get')
        return SimpleNamespace(error_state=SimpleNamespace(host=SimpleNamespace(view=host_view)))

    class Output:
      @property
      def device(self):
        if native_forbidden:
          raise AssertionError('legacy/non-Chestnut inspected output.device for native error state')
        events.append('output_device')
        return 'test-output'

      def numpy(self):
        events.append('output_read')
        return np.array([[1., 2.]], dtype=np.float32)

    self_outer = self
    cls, path = load_subject(Registry())
    model = cls.__new__(cls)
    self.assertIs(model.run.__func__, cls.run)
    self.assertEqual(Path(model.run.__code__.co_filename).resolve(), path.resolve())
    model.chestnut = chestnut
    model.vision_input_names = ('img', 'big_img')
    model.frame_copy_size = 4
    model.frames = np.zeros((2, 4), dtype=np.uint8)
    model.npy = {'tfm': np.zeros((2, 3, 3), dtype=np.float32), 'desire': np.zeros(2, dtype=np.float32),
                 'traffic_convention': np.zeros(2, dtype=np.float32), 'action_t': np.zeros(2, dtype=np.float32)}
    model.prev_desire = np.zeros(2, dtype=np.float32)
    model.input_host = object()

    def copy_from(source):
      self.assertIs(source, model.input_host)
      events.append('upload')

    model.input_device = SimpleNamespace(copy_from=copy_from)
    model.input_queues = {}
    model.warp_inputs = {'fixture': object()}
    warped = object()

    def run_warp(**kwargs):
      self.assertEqual(kwargs, model.warp_inputs)
      events.append('warp')
      return warped

    def run_model(*, output_buffers, **queues):
      self.assertIs(output_buffers, model.outputs)
      self.assertIs(queues['new_img'], warped)
      events.append('model')

    model.run_warp, model.run_model = run_warp, run_model
    model.outputs = {'outputs': Output()}
    model.copy_inputs = lambda: model.input_device.copy_from(model.input_host)
    model.copy_outputs = lambda: model.outputs['outputs']
    model.output_slices = {'fixture': slice(0, 2)}
    parsed = {'fixture': np.array([[1., 2.]], dtype=np.float32)}

    def parse_outputs(slices):
      events.append('parse')
      self.assertEqual(set(slices), {'fixture'})
      np.testing.assert_array_equal(slices['fixture'], parsed['fixture'])
      return parsed

    model.parser = SimpleNamespace(parse_outputs=parse_outputs)
    bufs = {name: SimpleNamespace(data=bytes([1, 2, 3, 4])) for name in model.vision_input_names}
    transforms = {name: np.eye(3, dtype=np.float32) for name in model.vision_input_names}
    inputs = {'desire_pulse': np.array([0., 1.], dtype=np.float32),
              'traffic_convention': np.array([1., 0.], dtype=np.float32), 'action_t': np.array([.1, .2], dtype=np.float32)}
    selection = {'role': 'driving'} if legacy else None
    return SimpleNamespace(model=model, events=events, parsed=parsed, bufs=bufs, transforms=transforms,
                           inputs=inputs, selection=selection)

  def invoke(self, fixture):
    with patch.object(runtime, '_selection', fixture.selection):
      return fixture.model.run(fixture.bufs, fixture.transforms, fixture.inputs,
                               after_enqueue=lambda: fixture.events.append('callback'))

  def assert_preprocessing(self, fixture):
    np.testing.assert_array_equal(fixture.model.frames, [[1, 2, 3, 4], [1, 2, 3, 4]])
    np.testing.assert_array_equal(fixture.model.prev_desire, [0., 1.])
    np.testing.assert_array_equal(fixture.model.npy['traffic_convention'], fixture.inputs['traffic_convention'])

  def test_global_chestnut_fault_precedes_callback_and_output_read(self):
    fixture = self.fixture(legacy=False, chestnut=True, flag=7)
    with self.assertRaisesRegex(RuntimeError, '^chestnut failed with 7$'):
      self.invoke(fixture)
    self.assertEqual(fixture.events, ['upload', 'warp', 'model', 'output_device', 'device_get', 'host_view', 'flag_read'])
    self.assert_preprocessing(fixture)

  def test_global_chestnut_zero_flag_completes_in_order(self):
    fixture = self.fixture(legacy=False, chestnut=True, flag=0)
    self.assertIs(self.invoke(fixture), fixture.parsed)
    self.assertEqual(fixture.events, ['upload', 'warp', 'model', 'output_device', 'device_get', 'host_view', 'flag_read',
                                      'callback', 'output_read', 'parse'])
    self.assert_preprocessing(fixture)

  def test_pinned_legacy_chestnut_never_accesses_native_flag(self):
    fixture = self.fixture(legacy=True, chestnut=True, flag=7)
    self.assertIs(self.invoke(fixture), fixture.parsed)
    self.assertEqual(fixture.events, ['upload', 'warp', 'model', 'callback', 'output_read', 'parse'])
    self.assert_preprocessing(fixture)

  def test_non_chestnut_never_accesses_native_flag(self):
    fixture = self.fixture(legacy=False, chestnut=False, flag=7)
    self.assertIs(self.invoke(fixture), fixture.parsed)
    self.assertEqual(fixture.events, ['upload', 'warp', 'model', 'callback', 'output_read', 'parse'])
    self.assert_preprocessing(fixture)
