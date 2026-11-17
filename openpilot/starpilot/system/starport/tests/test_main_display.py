"""A car with a portrait main screen and a landscape cluster projects to the main screen (Polestar 2)."""
import pytest

from openpilot.starpilot.system.starport.display_profile import validate_screen
from openpilot.starpilot.system.starport.frame_source import FrameConsumer, FrameRequest
from openpilot.starpilot.system.starport.hw_encoder import HardwareH264Encoder, HardwareHevcEncoder
from openpilot.starpilot.system.starport.projection_geometry import projection_geometry
from openpilot.starpilot.system.starport.session import CODEC_H265, choose_video_mode
from openpilot.starpilot.system.starport.touch import InputConfig, TouchMapper


def config(resolution, margin_w=0, margin_h=0, codec=7):
  return {1: [resolution], 2: [1], 3: [margin_w], 4: [margin_h], 10: [codec]}


POLESTAR = [
  {"id": 1, "display_id": 0, "display_type": 0,
   "video_configs": [config(8, 288, 1378), config(7, 0, 811), config(6, 0, 541)]},
  {"id": 2, "display_id": 1, "display_type": 1, "video_configs": [config(3, 0, 360)]},
]


def test_polestar_projects_720x1280_on_the_main_screen_not_the_cluster():
  mode = choose_video_mode(POLESTAR, True)
  assert (mode.channel, mode.config_index, mode.codec) == (1, 2, CODEC_H265)
  assert (mode.width, mode.height, mode.margin_width, mode.margin_height) == (720, 1280, 0, 541)


def test_a_cluster_only_offer_is_refused_with_a_hint():
  with pytest.raises(ValueError, match="secondary display"):
    choose_video_mode([POLESTAR[0] | {"video_configs": [config(9)]}, POLESTAR[1]], True)


def test_portrait_frames_pass_every_size_check(tmp_path):
  validate_screen({"version": 1, "width": 720, "height": 1280, "margin_width": 0, "margin_height": 541, "fps": 30,
                   "config_index": 2})
  geometry = projection_geometry(720, 1280, 0, 541)
  assert (geometry.width, geometry.height, geometry.logical_width) == (720, 739, 1860)
  consumer = FrameConsumer(str(tmp_path / "frame"))
  try:
    consumer.configure(FrameRequest(720, 1280, 0, 541, 33333, 0))
    with pytest.raises(ValueError):
      consumer.configure(FrameRequest(1280, 1280, 0, 0, 33333, 0))
  finally:
    consumer.close()


@pytest.mark.parametrize("encoder", [HardwareH264Encoder, HardwareHevcEncoder])
def test_hardware_encoders_accept_portrait_and_reject_oversized(encoder, tmp_path):
  with pytest.raises(ValueError, match="Hardware encoder supports"):
    encoder(1280, 1280, library=tmp_path / "missing.so")
  with pytest.raises(OSError):  # past the size check, failing only on the missing library
    encoder(720, 1280, library=tmp_path / "missing.so")


def test_touch_reported_over_the_visible_area_maps_to_the_whole_picture():
  mapper = TouchMapper(InputConfig((), 1152, 1182), 720, 1280, 0, 541)
  assert mapper.content_space
  assert mapper._normalize(0, 0) == (0, 0)
  assert mapper._normalize(1152, 1182) == (1, 1)
  assert mapper._normalize(576, 591) == pytest.approx((0.5, 0.5))
  assert mapper._inside(0, 0) and mapper._inside(1151, 1181)


def test_touch_reported_over_the_whole_frame_is_unchanged():
  mapper = TouchMapper(InputConfig((), 1280, 720), 1280, 720, 0, 240)
  assert not mapper.content_space
  assert mapper._normalize(0, 120) == (0, 0)
  assert not mapper._inside(640, 100)
