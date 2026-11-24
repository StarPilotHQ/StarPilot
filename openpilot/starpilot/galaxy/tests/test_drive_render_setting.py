"""Galaxy's Developer page saves the "3D drive render" choice in the onroad customization document."""
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

import pytest

from openpilot.common.params import Params
from openpilot.starpilot.galaxy import settings
from openpilot.starpilot.galaxy.settings import AuthorityContext, ContextSource, SettingsGateway
from openpilot.starpilot.ui import drive_render
from openpilot.starpilot.ui.appearance_owner import AppearanceOwner
from openpilot.starpilot.ui.feature_settings_state import FeatureSettingsRequest
from openpilot.starpilot.ui.onroad_customization import PARAM_KEY, decode_document, default_document


def gateway(params, device_type):
  context = SimpleNamespace(sample=lambda: AuthorityContext(True, None, None))
  return patch.object(settings.HARDWARE, 'get_device_type', return_value=device_type), SettingsGateway(params, cast(ContextSource, context))


def developer_row(page):
  index = next(index for index, row in enumerate(page['rows']) if row['label'] == drive_render.LABEL)
  return index, page['rows'][index]


def saved(params):
  return decode_document(Path(params.get_param_path(PARAM_KEY)).read_bytes())


def test_default_row_on_a_large_device(tmp_path):
  params = Params(str(tmp_path))
  device, gate = gateway(params, 'tizi')
  with device:
    _, row = developer_row(gate.page('developer', 'session', b'generation'))
  assert row['value'] == 'StarPort' and row['defaultValue'] == 'StarPort'
  assert row['choices'] == ['Off', 'StarPort', 'Comma display']
  assert row['available'] and 'next connection' in row['reason']
  assert params.get(PARAM_KEY) is None


@pytest.mark.parametrize('choice,stored', [('Off', 'off'), ('Comma display', 'device')])
def test_gateway_round_trip_keeps_the_layout(tmp_path, choice, stored):
  params = Params(str(tmp_path))
  document = default_document()
  document['layouts']['large']['steering_wheel']['enabled'] = False
  document['speedDisplayOffset'] = -1.7
  params.put(PARAM_KEY, document, block=True)
  device, gate = gateway(params, 'tici')
  with device:
    page = gate.page('developer', 'session', b'generation')
    index, _ = developer_row(page)
    intent = gate.preview(page['view'], index, 0, 'session', b'generation', value=choice)['intent']
    assert gate.confirm(intent, 'session', b'generation')
    assert developer_row(gate.page('developer', 'session', b'generation'))[1]['value'] == choice
  after = saved(params)
  assert after.pop(drive_render.FIELD) == stored and after == document
  assert drive_render.read(params) == stored
  # Choosing the default again removes the field, so the document matches one that never had it.
  owner = AppearanceOwner(params, lambda: True, large_display=True)
  row = owner.developer_snapshot().rows[1]
  assert owner.apply(FeatureSettingsRequest(row.key, row.source, 'StarPort'))
  assert saved(params) == document and drive_render.read(params) == 'starport'


def test_compact_device_never_offers_or_saves_the_comma_display(tmp_path):
  params = Params(str(tmp_path))
  device, gate = gateway(params, 'mici')
  with device:
    page = gate.page('developer', 'session', b'generation')
    index, row = developer_row(page)
    assert row['choices'] == ['Off', 'StarPort'] and 'Comma display' not in row['reason']
    with pytest.raises(settings.SettingsChanged):
      gate.preview(page['view'], index, 0, 'session', b'generation', value='Comma display')
  owner = AppearanceOwner(params, lambda: True)
  row = owner.developer_snapshot().rows[1]
  assert not owner.apply(FeatureSettingsRequest(row.key, row.source, 'Comma display'))
  assert params.get(PARAM_KEY) is None
  # A choice saved on a comma 3 reads as Off here, and the row can still change it.
  params.put(PARAM_KEY, {**default_document(), drive_render.FIELD: 'device'}, block=True)
  row = owner.developer_snapshot().rows[1]
  assert row.value == 'Off' and row.choices == ('Off', 'StarPort')
  assert owner.apply(FeatureSettingsRequest(row.key, row.source, 'StarPort'))
  assert drive_render.FIELD not in saved(params)


@pytest.mark.parametrize('bad', ['off', 'device', 'On', '', None, True, 1])
def test_unknown_value_never_writes(tmp_path, bad):
  params = Params(str(tmp_path))
  assert not AppearanceOwner(params, lambda: True, large_display=True).apply(FeatureSettingsRequest(drive_render.KEY, None, bad))
  assert params.get(PARAM_KEY) is None


def test_stale_source_driving_and_broken_document_never_write(tmp_path):
  params = Params(str(tmp_path))
  owner = AppearanceOwner(params, lambda: True, large_display=True)
  row = owner.developer_snapshot().rows[1]
  assert owner.apply(FeatureSettingsRequest(row.key, row.source, 'Off'))
  first = Path(params.get_param_path(PARAM_KEY)).read_bytes()
  assert not owner.apply(FeatureSettingsRequest(row.key, row.source, 'Comma display'))
  driving = AppearanceOwner(params, lambda: False, large_display=True)
  assert not driving.developer_snapshot().rows[1].available
  assert not driving.apply(FeatureSettingsRequest(row.key, first, 'Comma display'))
  assert Path(params.get_param_path(PARAM_KEY)).read_bytes() == first
  Path(params.get_param_path(PARAM_KEY)).write_bytes(b'{broken')
  broken = owner.developer_snapshot().rows[1]
  assert not broken.available and broken.value == 'Unavailable' and broken.choices == ()
  assert not owner.apply(FeatureSettingsRequest(row.key, b'{broken', 'Off'))
  assert Path(params.get_param_path(PARAM_KEY)).read_bytes() == b'{broken'
