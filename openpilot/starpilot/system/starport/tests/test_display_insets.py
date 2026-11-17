"""Receiver-specific safe rectangles survive negotiation, IPC and touch mapping."""
import pytest

from openpilot.starpilot.system.starport.frame_source import FrameConsumer, FrameProducer, FrameRequest
from openpilot.starpilot.system.starport.session import choose_video_mode
from openpilot.starpilot.system.starport.touch import InputConfig, TouchMapper
from openpilot.starpilot.system.starport.wire import field, parse_fields


def mode_for(edges=None, *, margin_w=0, margin_h=231):
  config = field(1, 2) + field(2, 1) + field(3, margin_w) + field(4, margin_h)
  if edges is not None:
    config += field(11, field(1, b"".join(field(i, value) for i, value in enumerate(edges, 1))))
  return choose_video_mode([{"id": 7, "video_configs": [parse_fields(config)]}])


@pytest.mark.parametrize("edges", [(0, 231, 0, 0), (231, 0, 0, 0), (90, 141, 0, 0)])
def test_trax_asymmetric_insets_survive_real_negotiation_and_frame_handoff(tmp_path, edges):
  mode = mode_for(edges)
  assert (mode.width, mode.height, mode.content_height) == (1280, 720, 489)
  assert (mode.content_left, mode.content_top) == (edges[2], edges[0])
  assert mode.as_dict()["content_top"] == edges[0]
  request = FrameRequest(1280, 720, 0, 231, 33333, 3, mode.content_left, mode.content_top)
  path = str(tmp_path / "frame")
  consumer = FrameConsumer(path)
  producer = FrameProducer(path)
  try:
    consumer.configure(request)
    consumer.demand()
    restored = producer.pending_request()
    assert restored == request
    assert restored.content(1280, 489) == (0, edges[0] & ~1, 1280, 488)
    mapper = TouchMapper(InputConfig((), 1280, 720), 1280, 720, 0, 231,
                         content_left=mode.content_left, content_top=mode.content_top)
    assert mapper._normalize(0, edges[0]) == (0, 0)
    assert mapper._normalize(1280, edges[0] + 489) == (1, 1)
    assert mapper._inside(640, edges[0])
    assert not mapper._inside(640, edges[0] + 489)
  finally:
    producer.close()
    consumer.close()


@pytest.mark.parametrize("edges", [None, (0, 230, 0, 0), (0, 720, 0, 0), (0, 231, 1, 0)])
def test_legacy_or_inconsistent_insets_preserve_centered_layout(edges):
  mode = mode_for(edges)
  assert mode.content_top is None and mode.content_left is None
  assert "content_top" not in mode.as_dict()


def test_horizontal_insets():
  mode = mode_for((0, 0, 100, 8), margin_w=108, margin_h=0)
  assert (mode.content_left, mode.content_top) == (100, 0)
  mapper = TouchMapper(InputConfig((), 1920, 1080), 1920, 1080, 108, 0, content_left=100, content_top=0)
  assert mapper._normalize(100, 0) == (0, 0)
  assert not mapper._inside(99, 0)


@pytest.mark.parametrize("left,top", [(None, 0), (0, None), (-1, 0), (1, 0), (0, -1), (0, 232)])
def test_invalid_origin_rejected_before_shared_publication(tmp_path, left, top):
  consumer = FrameConsumer(str(tmp_path / "frame"))
  try:
    with pytest.raises(ValueError, match="origin"):
      consumer.configure(FrameRequest(1280, 720, 0, 231, 33333, 0, left, top))
  finally:
    consumer.close()
