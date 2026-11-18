import builtins
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np
from tinygrad import Tensor

from openpilot.starpilot.models import laboratory, laboratory_runner
from openpilot.starpilot.models.runner import CatalogModelState, SUPPORTED_VERSIONS
from openpilot.starpilot.models.tests import test_laboratory_runner as load_fixture


class TestCoherentOutput(unittest.TestCase):
  def test_allocation_order_and_kernel_flags(self):
    from jetlink.openpilot.warp import COHERENT_WRITEBACK
    output = Mock(device='QCOM', nbytes=64)
    memory = SimpleNamespace(meta=[SimpleNamespace(flags=COHERENT_WRITEBACK)])
    device = Mock()
    events = []
    output.ensure_allocated.side_effect = lambda: events.append('allocated')
    device._gpu_alloc.side_effect = lambda *a, **k: (events.append('coherent'), memory)[1]
    output.deallocate.side_effect = lambda: events.append('freed')
    output.allocate.side_effect = lambda **k: events.append('bound')
    with patch('tinygrad.device.Device', {'QCOM': device}):
      laboratory_runner._coherent_camera_output(output)
    self.assertEqual(events, ['allocated', 'coherent', 'freed', 'bound'])
    device._gpu_alloc.assert_called_once_with(64, flags=COHERENT_WRITEBACK)
    output.allocate.assert_called_once_with(opaque=memory)

  def test_refused_flags_keep_old_storage(self):
    from jetlink.openpilot.warp import COHERENT_WRITEBACK
    for flags in (0, COHERENT_WRITEBACK & ~(1 << 31), COHERENT_WRITEBACK & ~(3 << 26)):
      with self.subTest(flags=flags):
        output = Mock(device='QCOM', nbytes=64)
        memory = SimpleNamespace(meta=[SimpleNamespace(flags=flags)])
        device = Mock()
        device._gpu_alloc.return_value = memory
        with patch('tinygrad.device.Device', {'QCOM': device}), self.assertRaisesRegex(RuntimeError, 'kept back'):
          laboratory_runner._coherent_camera_output(output)
        device._gpu_free.assert_called_once_with(memory)
        output.deallocate.assert_not_called()
        output.allocate.assert_not_called()

  def test_missing_optional_allocator_refuses_before_touching_storage(self):
    original = builtins.__import__
    def missing(name, *args, **kwargs):
      if name == 'jetlink.openpilot.warp':
        raise ImportError('missing optional allocator')
      return original(name, *args, **kwargs)
    output = Mock()
    with patch('builtins.__import__', side_effect=missing), self.assertRaisesRegex(RuntimeError, 'unavailable'):
      laboratory_runner._coherent_camera_output(output)
    output.ensure_allocated.assert_not_called()

  def test_uncaptured_warps_keep_existing_execution(self):
    for warp in (lambda: None, SimpleNamespace(captured=None), SimpleNamespace(captured=SimpleNamespace(ret=(1, 2)))):
      model = CatalogModelState.__new__(CatalogModelState)
      model.warp_enqueue = warp
      callback = Mock()
      model.move_warp_output(callback)
      callback.assert_not_called()
      self.assertEqual(model.warp_copied, 'warp has no captured Tensor output')
      self.assertIs(model.warp_enqueue, warp)

  def test_non_qcom_tensor_is_unchanged(self):
    tensor = Tensor(np.arange(16, dtype=np.uint8), device='NPY').realize()
    model = CatalogModelState.__new__(CatalogModelState)
    model.warp_enqueue = SimpleNamespace(captured=SimpleNamespace(ret=tensor))
    callback = Mock()
    model.move_warp_output(callback)
    callback.assert_not_called()
    np.testing.assert_array_equal(tensor.numpy(), np.arange(16, dtype=np.uint8))
    self.assertEqual(model.warp_copied, 'a NPY buffer')


class TestCoherentFactory(unittest.TestCase):
  setUp = load_fixture.TestLaboratoryLoad.setUp
  factory = load_fixture.TestLaboratoryLoad.factory

  def test_all_supported_versions_apply_after_verification_before_warmup(self):
    original = laboratory.freeze_request(self.root)
    assert original is not None
    for version in sorted(SUPPORTED_VERSIONS):
      with self.subTest(version=version):
        request = deepcopy(original)
        for role in request['roles'].values():
          role['version'] = version
        events = []
        def factory(*args, events=events):
          model = self.factory(*args)
          model.move_warp_output = Mock(side_effect=lambda callback, events=events: events.append(('coherent', callback)))
          model.warmup = Mock(side_effect=lambda events=events: events.append(('warmup', None)))
          return model
        with patch('openpilot.starpilot.models.runtime_bridge.runtime.frozen_laboratory_request', return_value=request), \
             patch('openpilot.starpilot.models.runner.CatalogModelState', side_effect=factory):
          pair = laboratory_runner.load_laboratory_pair(8, 8, self.config, root=self.root)
        self.assertEqual(events, [('coherent', laboratory_runner._coherent_camera_output), ('warmup', None)] * 2)
        self.assertEqual(pair.lateral_role.behavior_version, version)
        self.assertEqual(pair.longitudinal_role.behavior_version, version)
        self.assertNotEqual(pair.lateral_role.artifact.sha256, pair.longitudinal_role.artifact.sha256)

  def test_original_dom_sources_retain_original_loading(self):
    request = laboratory.freeze_request(self.root)
    assert request is not None
    for role in request['roles'].values():
      role['source_contract']['_source_contract'] = 'dom_v25_protocol5'
      role['source_contract']['source_format'] = 'tinygrad_single_v1'
    models = []
    def factory(*args):
      model = self.factory(*args)
      model.move_warp_output = Mock()
      models.append(model)
      return model
    with patch('openpilot.starpilot.models.runtime_bridge.runtime.frozen_laboratory_request', return_value=request), \
         patch('openpilot.starpilot.models.runner.CatalogModelState', side_effect=factory):
      pair = laboratory_runner.load_laboratory_pair(8, 8, self.config, root=self.root)
    self.assertEqual(len(models), 2)
    for model in models:
      model.move_warp_output.assert_not_called()
      model.warmup.assert_called_once_with()
    self.assertEqual(pair.lateral_role.source_format, 'tinygrad_single_v1')
