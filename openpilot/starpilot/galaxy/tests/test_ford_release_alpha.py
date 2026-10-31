"""Ford release opt-in reaches Galaxy's real capability and confirmation owner."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from opendbc.car import gen_empty_fingerprint
from opendbc.car.car_helpers import interfaces
from openpilot.common.params import Params
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.starpilot.aol.vehicle import policy_for
from openpilot.starpilot.galaxy.settings import AuthorityContext, ContextSource, SettingsChanged, SettingsGateway
from openpilot.starpilot.tests.ford_lifecycle_helpers import FORD_FACTORIES


def fixture(identity, *, manual, offset, marked=False):
  params = Params()
  params.put_bool('IsReleaseBranch', True, block=True)
  fp = gen_empty_fingerprint()
  fp[offset + 2].update({0x3D6: 8, 0x186: 8})
  if not manual:
    fp[offset][0x5A] = 8
  cp = interfaces[identity].get_params(identity, fp, [], False, False, True)
  if marked:
    policy = policy_for(cp)
    cp.alternativeExperience |= policy.alternative_experience_addition
    cp.safetyConfigs[-1].safetyParam |= policy.safety_param_addition
  state = SimpleNamespace(parked=True, cp=cp, raw=cp.to_bytes())
  gateway = SettingsGateway(params, Mock(spec=ContextSource, sample=lambda: AuthorityContext(state.parked, state.cp, state.raw)), clock=lambda: 10)
  return params, state, gateway


def row(gateway):
  page = gateway.page('vehicle', 'session', b'generation')
  index = next(i for i, item in enumerate(page['rows']) if item['label'] == 'Alpha Longitudinal')
  return page, index


@pytest.mark.parametrize('identity', [identity for identity, _ in FORD_FACTORIES])
@pytest.mark.parametrize('manual,offset,marked', [(False, 0, False), (True, 0, True), (False, 4, True), (True, 4, False)])
def test_release_ford_exact_configs_reach_real_galaxy_capability(identity, manual, offset, marked):
  with OpenpilotPrefix():
    params, state, gateway = fixture(identity, manual=manual, offset=offset, marked=marked)
    try:
      original = state.cp.to_dict()
      page, index = row(gateway)
      assert page['rows'][index]['available'] and not params.get_bool('AlphaLongitudinalEnabled')
      assert state.cp.to_dict() == original
    finally:
      gateway.close()


@pytest.mark.parametrize('manual,offset', [(False, 0), (True, 0), (False, 4), (True, 4)])
def test_release_ford_real_preview_confirm_on_off_and_parked_revocation(manual, offset):
  with OpenpilotPrefix():
    params, state, gateway = fixture('FORD_MUSTANG_MACH_E_MK1', manual=manual, offset=offset)
    try:
      original = state.cp.to_dict()
      for choice in ('On', 'Off'):
        page, index = row(gateway)
        intent = gateway.preview(page['view'], index, 0, 'session', b'generation', value=choice)
        assert gateway.confirm(intent['intent'], 'session', b'generation')
        assert params.get_bool('AlphaLongitudinalEnabled') == (choice == 'On')
        assert state.cp.to_dict() == original
      page, index = row(gateway)
      intent = gateway.preview(page['view'], index, 0, 'session', b'generation', value='On')
      state.parked = False
      with pytest.raises(SettingsChanged):
        gateway.confirm(intent['intent'], 'session', b'generation')
      assert not params.get_bool('AlphaLongitudinalEnabled')
    finally:
      gateway.close()


@pytest.mark.parametrize('failure', ['legacy', 'wrong-owner', 'automatic-manual-marker', 'passive', 'dashcam', 'not-car'])
def test_release_ford_capability_rejects_invalid_native_owner_and_passive_config(failure):
  with OpenpilotPrefix():
    params, state, gateway = fixture('FORD_MUSTANG_MACH_E_MK1', manual=False, offset=0)
    try:
      if failure == 'legacy':
        state.cp.safetyConfigs[-1].safetyParam = 18
      elif failure == 'wrong-owner':
        state.cp.safetyConfigs[-1].safetyModel = 'noOutput'
      elif failure == 'automatic-manual-marker':
        state.cp.safetyConfigs[-1].safetyParam |= 256
      else:
        setattr(state.cp, {'not-car': 'notCar', 'dashcam': 'dashcamOnly'}.get(failure, failure), True)
      state.raw = state.cp.to_bytes()
      page = gateway.page('vehicle', 'session', b'generation')
      assert not any(item['label'] == 'Alpha Longitudinal' for item in page['rows'])
      assert not params.get_bool('AlphaLongitudinalEnabled')
    finally:
      gateway.close()
