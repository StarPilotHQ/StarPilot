from unittest.mock import Mock
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from openpilot.common.params import Params
from openpilot.starpilot.conditional_mode.manual_saved import SavedCodes, encode as encode_manual
from openpilot.starpilot.conditional_mode.preferences import decode_preferences
from openpilot.starpilot.galaxy.settings import AuthorityContext, SettingsChanged, SettingsGateway


def setup(tmp_path):
  params = Params(str(tmp_path))
  current = NS(parked=True, cp=None, raw=None)
  context = NS(sample=lambda: AuthorityContext(current.parked, current.cp, current.raw))
  return params, current, SettingsGateway(params, Mock(wraps=context), clock=lambda: 10)


def mode_row(page):
  return next(index for index, row in enumerate(page['rows']) if row['label'] == 'Driving Mode')


def test_absent_car_can_save_core_conditional_choice_for_later_drive(tmp_path):
  params, current, gateway = setup(tmp_path)
  page = gateway.page('conditional', 'session', b'generation')
  index = mode_row(page)
  assert page['rows'][index]['available']
  assert not any('Wheel assignment' in row['label'] for row in page['rows'])
  intent = gateway.preview(page['view'], index, 0, 'session', b'generation', value='Automatic')
  assert gateway.confirm(intent['intent'], 'session', b'generation')
  assert decode_preferences(Path(params.get_param_path('ConditionalModeConfig')).read_bytes()).mode.value == 'conditional_experimental'
  current.parked = False
  page = gateway.page('conditional', 'session', b'generation')
  assert page['rows'][index]['available']
  intent = gateway.preview(page['view'], index, 0, 'session', b'generation', value='Chill')
  assert gateway.confirm(intent['intent'], 'session', b'generation')
  assert decode_preferences(Path(params.get_param_path('ConditionalModeConfig')).read_bytes()).mode.value == 'stock'


def test_unsupported_car_can_save_core_but_not_use_stale_context(tmp_path):
  params, current, gateway = setup(tmp_path)
  current.cp = NS(carFingerprint='UNSUPPORTED', openpilotLongitudinalControl=False,
                  notCar=False, passive=False, dashcamOnly=False)
  current.raw = b'unsupported-car'
  page = gateway.page('conditional', 'session', b'generation')
  index = mode_row(page)
  assert page['rows'][index]['available']
  intent = gateway.preview(page['view'], index, 0, 'session', b'generation', value='Chill')
  current.raw = b'other-car-context'
  with pytest.raises(SettingsChanged):
    gateway.confirm(intent['intent'], 'session', b'generation')
  assert params.get('ConditionalModeConfig') is None
  page = gateway.page('conditional', 'session', b'generation')
  intent = gateway.preview(page['view'], mode_row(page), 0, 'session', b'generation', value='Chill')
  assert gateway.confirm(intent['intent'], 'session', b'generation')
  assert decode_preferences(Path(params.get_param_path('ConditionalModeConfig')).read_bytes()).mode.value == 'stock'


def test_changed_saved_document_rejects_confirm_without_overwrite(tmp_path):
  params, _, gateway = setup(tmp_path)
  page = gateway.page('conditional', 'session', b'generation')
  intent = gateway.preview(page['view'], mode_row(page), 0, 'session', b'generation', value='Automatic')
  path = Path(params.get_param_path('ConditionalModeConfig'))
  path.write_bytes(b'{"version":2}')
  assert not gateway.confirm(intent['intent'], 'session', b'generation')
  assert path.read_bytes() == b'{"version":2}'


def test_absent_car_numeric_intent_rejects_changed_units(tmp_path):
  params, _, gateway = setup(tmp_path)
  page = gateway.page('conditional/cem', 'session', b'generation')
  index = next(i for i, row in enumerate(page['rows']) if row['label'] == 'Speed threshold')
  assert page['rows'][index]['available'] and page['rows'][index]['unit'] == 'mph'
  intent = gateway.preview(page['view'], index, 0, 'session', b'generation', value='5')
  Path(params.get_param_path('IsMetric')).write_bytes(b'1')
  assert not gateway.confirm(intent['intent'], 'session', b'generation')
  assert params.get('ConditionalModeConfig') is None


@pytest.mark.parametrize('source', ('SafeMode', 'ConditionalManualState'))
def test_absent_car_manual_persistence_rejects_changed_source(tmp_path, source):
  params, _, gateway = setup(tmp_path)
  page = gateway.page('conditional/cem', 'session', b'generation')
  index = next(i for i, row in enumerate(page['rows']) if row['label'] == 'Remember manual choice')
  assert page['rows'][index]['available']
  intent = gateway.preview(page['view'], index, 0, 'session', b'generation', value='On')
  changed = b'1' if source == 'SafeMode' else encode_manual(SavedCodes(cem=2, ccm=1))
  Path(params.get_param_path(source)).write_bytes(changed)
  assert not gateway.confirm(intent['intent'], 'session', b'generation')
  assert params.get('ConditionalModeConfig') is None
  assert Path(params.get_param_path(source)).read_bytes() == changed
