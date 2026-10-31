"""Real settings authority for original-rack Prius SDSU and manual selection."""
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from opendbc.car.toyota.values import CAR
from openpilot.common.params import Params
from openpilot.starpilot.car.toyota.tests.test_prius_sdsu_filter import factory
from openpilot.starpilot.galaxy.settings import AuthorityContext, SettingsGateway, SettingsChanged, _qualified
from openpilot.starpilot.galaxy.vehicle_configuration import settings_configuration
from openpilot.starpilot.vehicle_selection import choices, encode


def gateway(params, cp, raw):
  current = SimpleNamespace(parked=True, cp=cp, raw=raw)
  source = SimpleNamespace(sample=lambda: AuthorityContext(current.parked, current.cp, current.raw))
  return current, SettingsGateway(params, Mock(wraps=source), clock=lambda: 10)


@pytest.mark.parametrize('manual', (False, True))
def test_real_gateway_sng_saves_without_auto_hold_authority(tmp_path, manual):
  params, physical = Params(str(tmp_path)), factory()
  params.put('VehicleSelection', json.loads(encode(str(CAR.TOYOTA_PRIUS) if manual else None)), block=True)
  cp, raw, _, _ = settings_configuration(params, physical, b'physical-prius')
  assert cp is not None and cp.safetyConfigs[0].safetyParam == physical.safetyConfigs[0].safetyParam
  assert cp.openpilotLongitudinalControl and not cp.alphaLongitudinalAvailable
  current, owner = gateway(params, cp, raw)
  assert not _qualified(AuthorityContext(True, cp, raw), 'vehicle')
  for value in ('On', 'Off'):
    page = owner.page('vehicle', 'session', b'generation')
    index = next(i for i, row in enumerate(page['rows']) if row['label'] == 'Stop-and-Go Hack')
    assert page['rows'][index]['available'], 'PRIUS_SNG_WRONG_AUTO_HOLD_GATE'
    intent = owner.preview(page['view'], index, 0, 'session', b'generation', value=value)
    assert owner.confirm(intent['intent'], 'session', b'generation')
    assert Path(params.get_param_path('SNGHack')).read_bytes() == (b'1' if value == 'On' else b'0')


def test_manual_prius_labels_distinguish_original_rack_and_retrofit():
  labels = {choice.platform: choice.label for choice in choices()}
  assert 'Prius Prime' in labels[CAR.TOYOTA_PRIUS], 'PRIUS_MANUAL_LABEL_AMBIGUOUS'
  assert '2016-20' in labels[CAR.TOYOTA_PRIUS]
  assert 'TSS2 EPS retrofit' in labels[CAR.TOYOTA_PRIUS_RETROFIT]


@pytest.mark.parametrize('change', ('onroad', 'profile', 'bytes', 'session'))
def test_pending_sng_choice_cannot_survive_authority_or_source_change(tmp_path, change):
  params, cp = Params(str(tmp_path)), factory()
  current, owner = gateway(params, cp, b'physical-prius')
  page = owner.page('vehicle', 'session', b'generation')
  index = next(i for i, row in enumerate(page['rows']) if row['label'] == 'Stop-and-Go Hack')
  intent = owner.preview(page['view'], index, 0, 'session', b'generation', value='On')
  if change == 'onroad':
    current.parked = False
  elif change == 'profile':
    current.cp = None
    current.raw = b'changed'
  elif change == 'bytes':
    params.put_bool('SNGHack', False, block=True)
  try:
    assert not owner.confirm(intent['intent'], 'session', b'generation', session_valid=lambda: change != 'session')
  except SettingsChanged:
    assert change != 'bytes'
  assert not params.get_bool('SNGHack')


def test_native_owner_sng_save_does_not_require_unrelated_auto_hold(tmp_path):
  from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner
  from openpilot.starpilot.ui.feature_settings_state import row_change
  params, cp = Params(str(tmp_path)), factory()
  owner = FeatureSettingsOwner(params, lambda group: group == 'parked_preferences',
                               vehicle_params=lambda: cp, vehicle_fingerprint=lambda: cp.carFingerprint)
  row = next(row for row in owner.snapshot('vehicle', parked=True, system_long=True,
                                          lateral_context=True, metric=False).rows if row.key == 'SNGHack')
  request = row_change(row)
  assert request is not None
  assert owner.apply(replace(request, confirmation=True))
  assert params.get_bool('SNGHack')
