"""An obsolete pending Big camera switch must never paint its first frame."""
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import pytest

from openpilot.selfdrive.ui.onroad import augmented_road_view as road
from openpilot.selfdrive.ui.onroad import cameraview


class ViewWithoutResources(road.AugmentedRoadView):
  def close(self):
    pass


@pytest.mark.parametrize('experimental,speed', [(False, 1.), (True, road.ROAD_CAM_MIN_SPEED + 1.)])
def test_big_cancelled_wide_switch_never_consumes_unwanted_target_frame(experimental, speed):
  view = ViewWithoutResources.__new__(ViewWithoutResources)
  view._name = 'camerad'
  view._stream_type = road.NARROW_ROAD_CAM
  view._switching = False
  view._target_client = view._target_stream_type = None
  view.available_streams = [road.NARROW_ROAD_CAM, road.WIDE_CAM]
  current_frame = NS(idx=0)
  view.frame = current_frame
  view.client = Mock()
  view.client.is_connected.return_value = True
  view.client.recv.return_value = None
  pending = Mock()
  pending.recv.side_effect = AssertionError('Obsolete wide stream must not be consumed')
  sm = {'selfdriveState': NS(experimentalMode=True), 'carState': NS(vEgo=1.)}
  view._clients = {road.NARROW_ROAD_CAM: view.client, road.WIDE_CAM: pending}
  with patch.object(cameraview, 'VisionIpcClient', return_value=pending):
    view._switch_stream_if_needed(sm)
  assert view._switching and view._target_stream_type == road.WIDE_CAM
  # Policy returns to the already displayed narrow stream before wide arrives.
  sm['selfdriveState'].experimentalMode = experimental
  sm['carState'].vEgo = speed
  view._switch_stream_if_needed(sm)
  assert not view._switching and view._target_client is None
  assert view._target_stream_type is None
  assert view._poll_camera()
  assert view.frame is current_frame and view.stream_type == road.NARROW_ROAD_CAM
  pending.recv.assert_not_called()
  view.client.recv.assert_called_once_with(timeout_ms=0)


def test_big_keeps_still_requested_pending_switch():
  view = ViewWithoutResources.__new__(ViewWithoutResources)
  view._stream_type = road.NARROW_ROAD_CAM
  pending = view._target_client = Mock()
  view._target_stream_type = road.WIDE_CAM
  view._switching = True
  view.available_streams = [road.NARROW_ROAD_CAM, road.WIDE_CAM]
  view._switch_stream_if_needed({'selfdriveState': NS(experimentalMode=True), 'carState': NS(vEgo=1.)})
  assert view._target_client is pending and view._switching
