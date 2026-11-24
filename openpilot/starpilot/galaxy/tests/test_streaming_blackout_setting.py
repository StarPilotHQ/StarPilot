"""Galaxy's Developer page saves "Black screen while StarPort streams" in the onroad customization document."""
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

import pytest

from openpilot.common.params import Params
from openpilot.starpilot.galaxy import settings
from openpilot.starpilot.galaxy.settings import AuthorityContext, ContextSource, SettingsGateway
from openpilot.starpilot.ui import drive_render, streaming_blackout
from openpilot.starpilot.ui.appearance_owner import AppearanceOwner
from openpilot.starpilot.ui.feature_settings_state import FeatureSettingsRequest
from openpilot.starpilot.ui.onroad_customization import PARAM_KEY, decode_document, default_document


def gateway(params, device_type):
  context = SimpleNamespace(sample=lambda: AuthorityContext(True, None, None))
  return patch.object(settings.HARDWARE, 'get_device_type', return_value=device_type), SettingsGateway(params, cast(ContextSource, context))


def developer_row(page):
  index = next(index for index, row in enumerate(page['rows']) if row['label'] == streaming_blackout.LABEL)
  return index, page['rows'][index]


def owner_row(owner):
  return next(row for row in owner.developer_snapshot().rows if row.key == streaming_blackout.KEY)


def saved(params):
  return decode_document(Path(params.get_param_path(PARAM_KEY)).read_bytes())


@pytest.mark.parametrize('device_type', ['tici', 'tizi', 'mici'])
def test_every_comma_offers_it_off_by_default(tmp_path, device_type):
  params = Params(str(tmp_path))
  device, gate = gateway(params, device_type)
  with device:
    _, row = developer_row(gate.page('developer', 'session', b'generation'))
  assert row['value'] == 'Off' and row['defaultValue'] == 'Off' and row['choices'] == ['Off', 'On']
  assert row['available'] and 'alerts' in row['reason']
  assert params.get(PARAM_KEY) is None


def test_gateway_round_trip_keeps_the_layout_and_the_render_choice(tmp_path):
  params = Params(str(tmp_path))
  document = default_document()
  document['layouts']['large']['steering_wheel']['enabled'] = False
  document[drive_render.FIELD] = 'device'
  params.put(PARAM_KEY, document, block=True)
  device, gate = gateway(params, 'mici')
  with device:
    page = gate.page('developer', 'session', b'generation')
    index, _ = developer_row(page)
    preview = gate.preview(page['view'], index, 0, 'session', b'generation', value='On')
    assert 'driving screen' in preview['question']
    assert gate.confirm(preview['intent'], 'session', b'generation')
    assert developer_row(gate.page('developer', 'session', b'generation'))[1]['value'] == 'On'
  after = saved(params)
  assert after.pop(streaming_blackout.FIELD) is True and after == document
  # Off removes the field, so the document matches one that never had it.
  owner = AppearanceOwner(params, lambda: True)
  row = owner_row(owner)
  assert owner.apply(FeatureSettingsRequest(row.key, row.source, 'Off'))
  assert saved(params) == document


@pytest.mark.parametrize('bad', ['on', 'True', '', None, True, 1, 'Comma display'])
def test_unknown_value_never_writes(tmp_path, bad):
  params = Params(str(tmp_path))
  assert not AppearanceOwner(params, lambda: True).apply(FeatureSettingsRequest(streaming_blackout.KEY, None, bad))
  assert params.get(PARAM_KEY) is None


def test_stale_source_driving_and_broken_document_never_write(tmp_path):
  params = Params(str(tmp_path))
  owner = AppearanceOwner(params, lambda: True)
  row = owner_row(owner)
  assert owner.apply(FeatureSettingsRequest(row.key, row.source, 'On'))
  first = Path(params.get_param_path(PARAM_KEY)).read_bytes()
  assert not owner.apply(FeatureSettingsRequest(row.key, row.source, 'Off')), 'the source it was read from is stale'
  driving = AppearanceOwner(params, lambda: False)
  assert not owner_row(driving).available
  assert not driving.apply(FeatureSettingsRequest(row.key, first, 'Off'))
  assert Path(params.get_param_path(PARAM_KEY)).read_bytes() == first
  Path(params.get_param_path(PARAM_KEY)).write_bytes(b'{broken')
  broken = owner_row(owner)
  assert not broken.available and broken.value == 'Unavailable' and broken.choices == ()
  assert not owner.apply(FeatureSettingsRequest(row.key, b'{broken', 'On'))
  assert Path(params.get_param_path(PARAM_KEY)).read_bytes() == b'{broken'
