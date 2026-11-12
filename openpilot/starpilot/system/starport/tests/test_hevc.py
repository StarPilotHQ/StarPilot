"""H.265 is an opt-in for a car that offers nothing else; every car with an H.264 mode is chosen exactly as before."""
import itertools

import pytest

from openpilot.starpilot.system.starport import hw_encoder
from openpilot.starpilot.system.starport.hw_encoder import HEVC_AUD, normalize_access_unit_hevc
from openpilot.starpilot.system.starport.session import (CODEC_H264_BP, CODEC_H265, START_CODE, VIDEO_UI_CONFIG_REPLY,
                                                         VIDEO_UI_CONFIG_REQUEST, ProjectionSession, VideoMode, choose_video_mode,
                                                         codec_config, codec_config_hevc, hevc_keyframe_summary, wire_frame,
                                                         wire_frame_hevc)

SC = b"\x00\x00\x00\x01"


def config(resolution, codec, margin_w=0, margin_h=0, fps=2):
  return {1: [resolution], 2: [fps], 3: [margin_w], 4: [margin_h], 10: [codec]}


def channels(*configs):
  return [{"id": 3, "video_configs": list(configs), "display_type": 0}]


# What Bugsink showed one "AndroidAuto" unit offering: every mode H.265, none H.264 (enums 8, 7, 6 are portrait).
H265_ONLY = channels(config(8, 7, 288, 1378), config(7, 7, 0, 811), config(6, 7, 0, 541), config(3, 7, 0, 360))
H264_OFFERS = [
  channels(config(2, 3, 0, 240)),
  channels(config(1, 3), config(2, 3, 0, 240), config(3, 3, 0, 360)),
  channels(config(3, 3, 0, 360), config(2, 3, 0, 240, fps=1)),
  channels(config(1, 3)),
]


def test_without_the_setting_an_h265_only_car_is_refused_with_a_hint():
  with pytest.raises(ValueError, match="supported H.264 video mode.*Developer > StarPort"):
    choose_video_mode(H265_ONLY)
  with pytest.raises(ValueError, match="supported H.264 video mode.*Developer > StarPort"):
    choose_video_mode(H265_ONLY, False)


def test_with_the_setting_an_h265_only_car_gets_the_1080p_h265_mode_inside_the_known_resolutions():
  mode = choose_video_mode(H265_ONLY, True)
  assert (mode.codec, mode.width, mode.height, mode.margin_height) == (CODEC_H265, 1920, 1080, 360)
  assert mode.as_dict()["codec"] == "H.265"


@pytest.mark.parametrize("offer", H264_OFFERS)
def test_a_car_with_an_h264_mode_is_chosen_identically_whatever_the_setting(offer):
  without, with_setting = choose_video_mode(offer), choose_video_mode(offer, True)
  assert without == with_setting and without.codec == CODEC_H264_BP
  assert "codec" not in without.as_dict()  # H.264 reports and screen files are unchanged


def test_mixed_offers_never_prefer_h265_over_h264():
  for h264, h265 in itertools.product(H264_OFFERS, [H265_ONLY, channels(config(2, 7, 0, 240))]):
    mixed = [{"id": 3, "video_configs": h264[0]["video_configs"] + h265[0]["video_configs"]}]
    assert choose_video_mode(mixed, True) == choose_video_mode(h264)


def test_h265_prefers_the_smaller_known_resolution_like_h264():
  mode = choose_video_mode(channels(config(3, 7, 0, 360), config(2, 7, 0, 240)), True)
  assert (mode.codec, mode.width, mode.height) == (CODEC_H265, 1280, 720)


def au(*kinds):
  """An Annex B H.265 access unit with the given NAL types."""
  return b"".join(SC + bytes([kind << 1, 1]) + b"\x42" * 6 for kind in kinds)


def test_hevc_codec_config_and_wire_frame_use_the_two_byte_header():
  frame = au(35, 32, 33, 34, 19)
  assert START_CODE.findall(codec_config_hevc(frame)) == [SC] * 3
  assert b"".join(bytes([kind << 1, 1]) + b"\x42" * 6 for kind in (32, 33, 34)) == codec_config_hevc(frame).replace(SC, b"")
  assert wire_frame_hevc(frame, drop_config=True) == SC + bytes([19 << 1, 1]) + b"\x42" * 6
  assert wire_frame_hevc(frame, drop_config=False) == au(32, 33, 34, 19)  # the AUD is always dropped
  assert codec_config_hevc(au(35, 1)) == b""


def test_h264_functions_are_unchanged_by_the_h265_ones():
  h264 = SC + b"\x09\xf0" + SC + b"\x67" + b"\x01" * 5 + SC + b"\x68" + b"\x02" * 3 + SC + b"\x65" + b"\x03" * 9
  assert codec_config(h264) == SC + b"\x67" + b"\x01" * 5 + SC + b"\x68" + b"\x02" * 3
  assert wire_frame(h264, drop_config=True) == SC + b"\x65" + b"\x03" * 9


def test_hevc_access_units_are_checked_like_the_h264_ones():
  keyframe = normalize_access_unit_hevc(au(32, 33, 34, 20), keyframe=True)
  assert keyframe.startswith(HEVC_AUD) and keyframe.count(SC) == 5
  assert normalize_access_unit_hevc(au(35, 1), keyframe=False).startswith(HEVC_AUD)
  # The comma's encoder emits two slices per picture; both belong to the same picture.
  assert normalize_access_unit_hevc(au(32, 33, 34, 19, 19), keyframe=True).count(SC) == 6
  assert normalize_access_unit_hevc(au(1, 1), keyframe=False).count(SC) == 3
  for bad, key in ((au(32, 33, 34), True), (au(35), False), (au(33, 34, 19), True), (au(32, 33, 34, 1), True), (au(19, 1), False)):
    with pytest.raises(RuntimeError):
      normalize_access_unit_hevc(bad, keyframe=key)
  with pytest.raises(RuntimeError):
    normalize_access_unit_hevc(b"not annex b", keyframe=False)


def test_the_hevc_encoder_class_only_changes_what_h265_needs():
  h264, hevc = hw_encoder.HardwareH264Encoder, hw_encoder.HardwareHevcEncoder
  assert (h264.max_width, h264.max_height, h264.codec_options, h264.required_abi) == (1280, 720, 0, 2)
  assert (hevc.max_width, hevc.max_height, hevc.codec_options, hevc.required_abi) == (1920, 1080, hw_encoder.OPTION_HEVC, 4)
  assert hw_encoder.OPTION_HEVC & hw_encoder.OPTION_CBR == 0


def test_h265_never_falls_back_to_another_codec(monkeypatch, tmp_path):
  monkeypatch.setattr(hw_encoder, "LIBRARY", tmp_path / "missing.so")
  events = []
  with pytest.raises(RuntimeError, match="needs H.265 and the hardware encoder could not start it"):
    hw_encoder.create_hevc_encoder(1920, 1080, bitrate_kbps=6000, margin_height=360, log=lambda *a, **k: events.append(a))
  assert events == []  # nothing was announced as started


def test_video_mode_defaults_to_h264():
  assert VideoMode(3, 0, 1280, 720, 30, 0, 240).codec == CODEC_H264_BP


def ui_config_body():
  from openpilot.starpilot.system.starport.session import field

  def insets(top, bottom):
    return field(1, top) + field(2, bottom) + field(3, 0) + field(4, 0)
  return field(1, field(1, insets(0, 541)) + field(2, insets(0, 0)) + field(3, insets(0, 0)) + field(4, 0) + field(5, 1))


def video_session(codec):
  sent, events = [], []

  class VideoSession(ProjectionSession):
    def send(self, channel: int, kind: int, body: bytes = b"", encrypted: bool = True, control: bool = False,
             *, deadline: float | None = None) -> int:
      sent.append((channel, kind, body))
      return 1

  session = VideoSession.__new__(VideoSession)
  session.audio = session.nav = None
  session.mode = VideoMode(1, 2, 720, 1280, 60, 0, 541, codec=codec)
  session.ignored_counts = {}
  session._log = lambda name, **values: events.append(name)
  return session, sent, events


def test_h265_session_answers_the_ui_config_request_with_the_same_config():
  session, sent, events = video_session(CODEC_H265)
  body = ui_config_body()
  session.handle(1, VIDEO_UI_CONFIG_REQUEST, body)
  assert sent == [(1, VIDEO_UI_CONFIG_REPLY, body)]
  assert events == ["ui_config_replied"]


def test_h264_session_still_ignores_the_ui_config_request():
  session, sent, events = video_session(CODEC_H264_BP)
  session.handle(1, VIDEO_UI_CONFIG_REQUEST, ui_config_body())
  assert sent == [] and events == ["video_ignored"]


def test_keyframe_summary_reads_tier_and_level_through_emulation_prevention():
  sps_main = bytes([33 << 1, 1, 0x01, 0x01, 0x60, 0, 0, 3, 0, 0x90, 0, 0, 3, 0, 0, 3, 0, 123, 0xA0])
  sps_high = bytes([33 << 1, 1, 0x01, 0x21, 0x60, 0, 0, 3, 0, 0x90, 0, 0, 3, 0, 0, 3, 0, 150, 0xA0])
  frame = SC + bytes([32 << 1, 1, 5]) + SC + sps_main + SC + bytes([34 << 1, 1, 7]) + SC + bytes([19 << 1, 1, 9])
  assert hevc_keyframe_summary(frame) == {"nal_types": [32, 33, 34, 19], "tier": "main", "profile": 1, "level": 4.1}
  assert hevc_keyframe_summary(SC + sps_high)["tier"] == "high"
  assert hevc_keyframe_summary(SC + sps_high)["level"] == 5.0
