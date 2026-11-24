"""StarPort's car screen follows Galaxy's "3D drive render" choice: the render only for StarPort, else the camera."""
import json
from types import SimpleNamespace

import pytest

from openpilot.starpilot.system.starport import current_car_ui
from openpilot.starpilot.ui import drive_render
from openpilot.starpilot.ui.onroad_customization import default_document


def read_only_params(tmp_path):
  """Only what the projection's ProjectionParams offers that a saved-document read needs."""
  return SimpleNamespace(get_param_path=lambda key: str(tmp_path / key))


def save(tmp_path, choice):
  document = default_document()
  if choice is not None:
    document[drive_render.FIELD] = choice
  (tmp_path / 'OnroadCustomizations').write_text(json.dumps(document))


def test_unsaved_setting_keeps_the_render_on_the_car_screen(tmp_path):
  assert current_car_ui.saved_stream_content(read_only_params(tmp_path)) == 'birds_eye'


@pytest.mark.parametrize('choice,content', [(None, 'birds_eye'), ('starport', 'birds_eye'), ('off', 'full'), ('device', 'full')])
def test_each_choice_selects_the_car_screen_content(tmp_path, choice, content):
  save(tmp_path, choice)
  assert current_car_ui.saved_stream_content(read_only_params(tmp_path)) == content


def test_unreadable_setting_falls_back_to_the_default(tmp_path):
  (tmp_path / 'OnroadCustomizations').write_text('{broken')
  assert current_car_ui.saved_stream_content(read_only_params(tmp_path)) == 'birds_eye'


def test_projection_params_expose_what_the_read_needs():
  from openpilot.selfdrive.ui.ui_state import ProjectionParams
  assert callable(ProjectionParams.get_param_path)
