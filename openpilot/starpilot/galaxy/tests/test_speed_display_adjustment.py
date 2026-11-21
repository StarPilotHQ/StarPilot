import copy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock, patch

import pytest

from openpilot.common.params import Params
from openpilot.starpilot.galaxy.settings import AuthorityContext, ContextSource, SettingsChanged, SettingsGateway
from openpilot.starpilot.ui.appearance_owner import AppearanceOwner
from openpilot.starpilot.ui.feature_settings_state import FeatureSettingsRequest
from openpilot.starpilot.ui.onroad_customization import PARAM_KEY, decode_document, default_document, validate_document
from openpilot.starpilot.ui.onroad_large_widgets import CurrentSpeedHud
from openpilot.starpilot.ui.onroad_state import OnroadState, SpeedLimitObservation
from openpilot.starpilot.ui.raylib import rl
from openpilot.starpilot.ui.shell import ShellMode
from openpilot.starpilot.ui.tests.test_runtime_snapshot import NOW, RuntimeSnapshotAdapter, ui_fake


def test_real_gateway_global_slider_saves_restores_and_keeps_layout(tmp_path):
  params = Params(str(tmp_path))
  document = default_document()
  document['layouts']['large']['steering_wheel']['enabled'] = False
  params.put(PARAM_KEY, document, block=True)
  context = SimpleNamespace(sample=lambda: AuthorityContext(True, None, None))
  gateway = SettingsGateway(params, cast(ContextSource, context))
  page = gateway.page('developer', 'session', b'generation')
  index = next(index for index, row in enumerate(page['rows']) if row['label'] == 'Speed display adjustment')
  row = page['rows'][index]
  assert row['label'] == 'Speed display adjustment' and row['value'] == '0'
  assert row['available'] and row['minimum'] == -5 and row['maximum'] == 5 and row['step'] == .1
  intent = gateway.preview(page['view'], index, 0, 'session', b'generation', value='-1.7')['intent']
  assert gateway.confirm(intent, 'session', b'generation')
  assert AppearanceOwner(Params(str(tmp_path)), lambda: True).developer_snapshot().rows[0].value == '-1.7'
  saved = decode_document(Path(params.get_param_path(PARAM_KEY)).read_bytes())
  assert saved.pop('speedDisplayOffset') == -1.7 and saved == document


def test_default_and_expired_or_stale_intent(tmp_path):
  params = Params(str(tmp_path))
  owner = AppearanceOwner(params, lambda: True)
  assert owner.developer_snapshot().rows[0].value == '0' and params.get(PARAM_KEY) is None
  row = owner.developer_snapshot().rows[0]
  assert owner.apply(FeatureSettingsRequest(row.key, row.source, '2.0'))
  first = Path(params.get_param_path(PARAM_KEY)).read_bytes()
  assert not owner.apply(FeatureSettingsRequest(row.key, row.source, '-1.7'))
  assert not AppearanceOwner(params, lambda: False).apply(FeatureSettingsRequest(row.key, first, '0'))
  assert Path(params.get_param_path(PARAM_KEY)).read_bytes() == first
  assert owner.apply(FeatureSettingsRequest(row.key, first, '0'))
  assert params.get(PARAM_KEY) == default_document()


@pytest.mark.parametrize('bad', ['nan', 'inf', '-inf', '5.1', '-5.1', 'invalid', True, None])
def test_invalid_adjustment_never_writes(tmp_path, bad):
  params = Params(str(tmp_path))
  assert not AppearanceOwner(params, lambda: True).apply(FeatureSettingsRequest('SpeedDisplayOffset', None, bad))
  assert params.get(PARAM_KEY) is None


@pytest.mark.parametrize('bad', [True, '1', None, float('nan'), float('inf'), -5.1, 5.1])
def test_document_rejects_invalid_adjustment(bad):
  document = default_document()
  document['speedDisplayOffset'] = bad
  with pytest.raises(ValueError, match='speed display adjustment'):
    validate_document(document)


def test_malformed_saved_layout_is_not_overwritten(tmp_path):
  params = Params(str(tmp_path))
  path = Path(params.get_param_path(PARAM_KEY))
  path.write_bytes(b'{broken')
  owner = AppearanceOwner(params, lambda: True)
  assert not owner.developer_snapshot().rows[0].available
  assert not owner.apply(FeatureSettingsRequest('SpeedDisplayOffset', b'{broken', '1'))
  assert path.read_bytes() == b'{broken'


@pytest.mark.parametrize('metric,speed,adjustment,expected', [(True, 118 / 3.6, -1.7, '116'),
                                                            (False, 118 / 3.6, -1.7, '72'),
                                                            (True, 100 / 3.6, 5., '105'),
                                                            (False, 60 / 2.2369362921, 5., '63'),
                                                            (True, 0., -5., '0')])
def test_actual_speed_label_only_changes_display(metric, speed, adjustment, expected):
  state = OnroadState(False, True, speed, 120., SpeedLimitObservation(), metric=metric,
                      speed_display_offset_percent=adjustment)
  before = copy.deepcopy(state)
  fonts = Mock()
  fonts.vertical_ink.return_value = (0, 100)
  with patch('openpilot.starpilot.ui.onroad_large_widgets._center') as draw:
    CurrentSpeedHud(fonts).render(rl.Rectangle(0, 0, 1860, 1080), state)
  assert draw.call_args_list[0].args[1] == expected
  assert state == before and state.speed_mps == speed and state.cruise_kph == 120.
  assert replace(state, speed_display_offset_percent=0.).display_speed_mps == speed


def test_real_snapshot_keeps_raw_speed_and_cruise_and_projection_replace(tmp_path):
  params = Params(str(tmp_path))
  document = default_document()
  document['speedDisplayOffset'] = -1.7
  ui = ui_fake()
  ui.params = params
  ui.sm['carState'].vEgoCluster = 118 / 3.6
  baseline = RuntimeSnapshotAdapter(ui).build(ShellMode.ONROAD, now_ns=NOW).onroad
  params.put(PARAM_KEY, document, block=True)
  adapter = RuntimeSnapshotAdapter(ui)
  state = adapter.build(ShellMode.ONROAD, now_ns=NOW).onroad
  assert state.speed_mps == ui.sm['carState'].vEgoCluster
  assert state.display_speed_mps == pytest.approx(118 / 3.6 * .983)
  assert state.cruise_kph == baseline.cruise_kph
  assert ui.sm['carState'].vCruiseCluster == 80.
  projection = replace(state, customization=default_document())
  assert projection.display_speed_mps == state.display_speed_mps
  assert params.get(PARAM_KEY) == document


@pytest.mark.parametrize('condition', ['expired', 'changed-layout'])
def test_gateway_refuses_expired_session_or_concurrent_layout_edit(tmp_path, condition):
  params = Params(str(tmp_path))
  context = SimpleNamespace(sample=lambda: AuthorityContext(True, None, None))
  gateway = SettingsGateway(params, cast(ContextSource, context))
  page = gateway.page('developer', 'session', b'generation')
  index = next(index for index, row in enumerate(page['rows']) if row['label'] == 'Speed display adjustment')
  intent = gateway.preview(page['view'], index, 0, 'session', b'generation', value='-1.7')['intent']
  if condition == 'changed-layout':
    document = default_document()
    document['clock24Hour'] = True
    params.put(PARAM_KEY, document, block=True)
  before = params.get(PARAM_KEY)
  if condition == 'expired':
    with pytest.raises(SettingsChanged):
      gateway.confirm(intent, 'session', b'generation', session_valid=lambda: False)
  else:
    assert not gateway.confirm(intent, 'session', b'generation')
  assert params.get(PARAM_KEY) == before
