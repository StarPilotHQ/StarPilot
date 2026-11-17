"""Jetlink borrows the small model's camera warp: the one JIT, its inputs and its output, which on a comma the small
model moved to memory the CPU reads cached when it loaded, and the link sends from where it is."""
import ctypes
from pathlib import Path
from types import MethodType, SimpleNamespace as NS
from unittest.mock import Mock, patch

import numpy as np
import pytest
from tinygrad import Tensor

from openpilot.starpilot.models import jetlink_adapter
from openpilot.starpilot.models.jetlink_warp import CatalogWarp
from openpilot.starpilot.models.runner import CatalogModelState

GEOMETRY = (16, 8)  # model_w, model_h
SHAPE = (2, 6, 4, 8)


class FakeBuffer:
  def __init__(self, live=True, device="CPU"):
    self.mapped = np.zeros(int(np.prod(SHAPE)), dtype=np.uint8)
    self.nbytes = self.mapped.nbytes
    self.allocator = NS(dev=NS(synchronize=Mock()))
    self.live, self.device = live, device

  def as_memoryview(self, allow_zero_copy=False):
    assert allow_zero_copy
    # tinygrad's fallback without a host mapping is a one-off copy
    return memoryview(self.mapped if self.live else self.mapped.copy())


def output(buffer):
  return NS(uop=NS(base=NS(buffer=buffer)), shape=SHAPE)


def small_model(descriptor_shape=SHAPE, descriptor=True, live=True, copied="a CPU buffer"):
  calls, buffers = [], [FakeBuffer(live)]
  npy = {"tfm": np.zeros((3, 3), np.float32), "big_tfm": np.zeros((3, 3), np.float32)}

  def enqueue(**kw):
    calls.append({**kw, "tfm_at_call": npy["tfm"].copy(), "big_tfm_at_call": npy["big_tfm"].copy()})
    # a flat frame warps to itself: the output takes the road frame's first byte
    buffers[-1].mapped[:] = ctypes.c_uint8.from_address(kw["frame"][1]).value
    return output(buffers[-1])

  small = NS(warp_copied=copied, camera_warp_descriptor=NS(output_shape=descriptor_shape, dtype=np.dtype(np.uint8)) if descriptor else None,
             warp_enqueue=enqueue, vision_input_names=("img", "big_img"), npy=npy,
             input_queues={"tfm": "tfm queue", "big_tfm": "big_tfm queue", "img_q": "history"},
             warp_input_keys=("tfm", "big_tfm"), _blob_cache={}, frame_buf_size=32, warp_device="QCOM")
  # the runner's own warp call, over this model's fakes
  small.warp_frames = MethodType(CatalogModelState.warp_frames, small)
  return small, calls, buffers


@pytest.fixture(autouse=True)
def blobs():
  with patch("tinygrad.Tensor.from_blob", side_effect=lambda ptr, shape, **kw: ("blob", ptr, shape, kw["device"])) as blob:
    yield blob


def frames(fill):
  return np.full(32, fill, np.uint8), np.full(32, fill + 1, np.uint8)


def test_warps_with_the_small_models_jit_inputs_and_cache():
  small, calls, buffers = small_model()
  warp = CatalogWarp(small, GEOMETRY)
  assert len(calls) == 2  # the warm-up and the live-read probe, before modeld goes realtime
  assert small._blob_cache == {}  # and their frames, freed, leave no blob behind
  tfm, big_tfm = np.full((3, 3), 2, np.float32), np.full((3, 3), 3, np.float32)
  road, wide = frames(7)
  warp.start(road.ctypes.data, wide.ctypes.data, tfm, big_tfm)
  call = calls[-1]
  assert call["tfm"] == "tfm queue" and call["big_tfm"] == "big_tfm queue" and "img_q" not in call
  assert (call["tfm_at_call"] == tfm).all() and (call["big_tfm_at_call"] == big_tfm).all()
  assert call["frame"] == small._blob_cache[("img", road.ctypes.data)] == ("blob", road.ctypes.data, (32,), "QCOM")
  assert call["big_frame"] == small._blob_cache[("big_img", wide.ctypes.data)]
  warp.wait()
  buffers[-1].allocator.dev.synchronize.assert_called()
  assert warp.output.nbytes == int(np.prod(SHAPE)) and set(bytes(warp.output)) == {7}
  road2, wide2 = frames(9)
  warp.start(road2.ctypes.data, wide2.ctypes.data, tfm, big_tfm)  # what was sent holds until wait()
  assert set(bytes(warp.output)) == {7}
  warp.wait()
  assert set(bytes(warp.output)) == {9}


@pytest.mark.parametrize("copied", [None, "the GPU driver kept back IO-coherent memory (flags 0x100c0000)"])
def test_a_moved_output_is_sent_from_where_it_is_and_any_other_copied_out(copied):
  small, _, buffers = small_model(copied=copied)
  warp = CatalogWarp(small, GEOMETRY)
  assert warp.sending == ("from coherent memory" if copied is None else f"copied out: {copied}")
  road, wide = frames(7)
  warp.start(road.ctypes.data, wide.ctypes.data, np.eye(3), np.eye(3))
  warp.wait()
  assert set(bytes(warp.output)) == {7}
  assert np.shares_memory(np.frombuffer(warp.output, np.uint8), buffers[-1].mapped) is (copied is None)


def runner_buffer(device):
  buffer = FakeBuffer(device=device)
  small = CatalogModelState.__new__(CatalogModelState)
  # The production move path admits only a captured Tensor, not an arbitrary output object.
  tensor = Mock(spec=Tensor, uop=NS(base=NS(buffer=buffer)))
  small.warp_enqueue = NS(captured=NS(ret=tensor))
  return small, buffer


def test_the_runner_moves_a_comma_warps_output():
  small, buffer = runner_buffer("QCOM")
  move = Mock()
  small.move_warp_output(move)
  move.assert_called_once_with(buffer)
  assert small.warp_copied is None
  refused = RuntimeError("the GPU driver kept back IO-coherent memory (flags 0x100c0000)")
  small.move_warp_output(Mock(side_effect=refused))
  assert small.warp_copied == str(refused)
  cpu, _ = runner_buffer("CPU")
  move = Mock()
  cpu.move_warp_output(move)
  move.assert_not_called()
  assert cpu.warp_copied == "a CPU buffer"


@pytest.mark.parametrize("captured", [None, NS(ret=None), NS(ret=output(FakeBuffer(device="QCOM")))])
def test_the_runner_refuses_missing_or_non_tensor_warp_output(captured):
  small = CatalogModelState.__new__(CatalogModelState)
  small.warp_enqueue = NS(captured=captured)
  move = Mock()
  small.move_warp_output(move)
  move.assert_not_called()
  assert small.warp_copied == "warp has no captured Tensor output"


def test_the_move_comes_before_warmup_links_the_warp():
  calls = []
  model = Mock(warmup=lambda: calls.append("warmup"), move_warp_output=lambda move: calls.append("move"))
  with patch("openpilot.starpilot.models.runner.CatalogModelState", return_value=model), \
       patch("openpilot.starpilot.models.runner.prepare_artifact", return_value=NS(sha256="s")), \
       patch("openpilot.starpilot.models.runner.artifact_identity", return_value="id"), \
       patch("openpilot.starpilot.models.manager.state_lock"):
    from openpilot.starpilot.models.runner import load_verified_model
    load_verified_model(8, 8, Path("m"), "v", False, "id", "s", root=Path("/r"), coherent_warp=Mock())
  assert calls == ["move", "warmup"]


@pytest.mark.parametrize("link_on,legacy,moved", [(False, False, False), (True, True, False), (True, False, True)])
def test_the_adapter_moves_the_output_only_for_the_link_off_legacy9d(link_on, legacy, moved):
  from jetlink.openpilot.warp import make_coherent
  with patch.object(jetlink_adapter, "link_on", return_value=link_on), \
       patch("openpilot.starpilot.models.runtime_bridge.runtime.selected_legacy", return_value=legacy):
    assert jetlink_adapter.coherent_warp() is (make_coherent if moved else None)


def test_refuses_an_output_it_cannot_read_live():
  small, _, _ = small_model(live=False)
  with pytest.raises(RuntimeError, match="stale frame"):
    CatalogWarp(small, GEOMETRY)


@pytest.mark.parametrize("kw", [{"descriptor": False}, {"descriptor_shape": (2, 6, 8, 16)}])
def test_refuses_a_warp_the_link_cannot_send(kw):
  small, _, _ = small_model(**kw)
  with pytest.raises(RuntimeError):
    CatalogWarp(small, GEOMETRY)


def adapter_warp(small, legacy):
  adapter = jetlink_adapter._adapter_class().__new__(jetlink_adapter._adapter_class())
  log = Mock()
  with patch.object(type(adapter), "log", log, create=True), \
       patch("openpilot.starpilot.models.runtime_bridge.runtime.selected_legacy", return_value=legacy), \
       patch("openpilot.starpilot.models.runner.CatalogModelState", NS):
    return adapter.model_warp(small, 1344, 760, *GEOMETRY), log


def test_adapter_loads_the_stock_warp_under_legacy9d():
  for kw in ({}, {"descriptor": False}):
    warp, _ = adapter_warp(small_model(**kw)[0], legacy=True)
    assert warp is None


def test_adapter_sends_the_small_models_warp_off_legacy9d():
  warp, _ = adapter_warp(small_model()[0], legacy=False)
  assert isinstance(warp, CatalogWarp)
  small, _, _ = small_model(descriptor=False)
  with pytest.raises(RuntimeError, match="policy pipeline"):
    adapter_warp(small, legacy=False)


@pytest.mark.parametrize("accepted", [False, True])
def test_coherent_allocation_requires_both_kernel_flags_before_replacing_output(accepted):
  from jetlink.openpilot.warp import COHERENT_WRITEBACK, make_coherent
  memory = NS(meta=[NS(flags=COHERENT_WRITEBACK if accepted else 0)])
  device = NS(_gpu_alloc=Mock(return_value=memory), _gpu_free=Mock())
  buffer = Mock(device="QCOM", nbytes=384)
  with patch("tinygrad.device.Device", {"QCOM": device}):
    if accepted:
      make_coherent(buffer)
    else:
      with pytest.raises(RuntimeError, match="kept back IO-coherent"):
        make_coherent(buffer)
  device._gpu_alloc.assert_called_once_with(384, flags=COHERENT_WRITEBACK)
  if accepted:
    device._gpu_free.assert_not_called()
    assert buffer.method_calls == [("deallocate", (), {}), ("allocate", (), {"opaque": memory})]
  else:
    device._gpu_free.assert_called_once_with(memory)
    buffer.deallocate.assert_not_called()
    buffer.allocate.assert_not_called()
