"""Saved Ford release intent is consumed before construction, with exact admission."""
from types import SimpleNamespace
import pytest
from opendbc.car import gen_empty_fingerprint
from opendbc.car.ford.interface import CarInterface
from opendbc.car.ford.values import CAR
from openpilot.starpilot.car.ford.longitudinal_preferences import prepare_release_long
from openpilot.starpilot.tests.ford_lifecycle_helpers import FORD_FACTORIES


def saved(tmp_path, alpha=b'1', safe=b'0'):
  for key, value in [('AlphaLongitudinalEnabled', alpha), ('SafeMode', safe)]:
    if value is not None:
      (tmp_path / key).write_bytes(value)
  return SimpleNamespace(get_param_path=lambda key: str(tmp_path / key))


@pytest.mark.parametrize('identity,base', FORD_FACTORIES)
@pytest.mark.parametrize('manual', [False, True])
@pytest.mark.parametrize('offset', [0, 4])
def test_release_long_changes_only_selected_owner_and_long_selection(identity, base, manual, offset, tmp_path):
  fp = gen_empty_fingerprint()
  if not manual:
    fp[offset][0x5A] = 8
  fp[offset + 2].update({0x3D6: 8, 0x186: 8})
  stock = CarInterface.get_params(identity, fp, [], False, True, False)
  stock.carVin = '0' * 17
  stock.fuzzyFingerprint = True
  before = stock.to_dict()
  selected = prepare_release_long(stock, saved(tmp_path), is_release=True, enabled=True)
  expected = stock.as_reader().as_builder()
  expected.openpilotLongitudinalControl = True
  expected.safetyConfigs[-1].safetyParam = base | 1
  assert stock.to_dict() == before
  assert selected.to_dict() == expected.to_dict()


@pytest.mark.parametrize('alpha,safe,release,enabled', [
  (None, b'0', True, True), (b'0', b'0', True, True), (b'1junk', b'0', True, True),
  (b'1', b'1', True, True), (b'1', b'bad', True, True), (b'1', b'0', False, True),
  (b'1', b'0', True, False),
])
def test_release_opt_in_off_invalid_safe_mode_debug_and_disabled_stay_unchanged(alpha, safe, release, enabled, tmp_path):
  fp = gen_empty_fingerprint()
  fp[0][0x5A] = 8
  cp = CarInterface.get_params(CAR.FORD_MUSTANG_MACH_E_MK1, fp, [], False, True, False)
  assert prepare_release_long(cp, saved(tmp_path, alpha, safe), is_release=release, enabled=enabled) is cp


@pytest.mark.parametrize('restriction', ['secoc', 'extra-panda', 'foreign-brand'])
def test_release_never_promotes_unowned_configuration(restriction, tmp_path):
  fp = gen_empty_fingerprint()
  fp[0][0x5A] = 8
  fp[2].update({0x3D6: 16 if restriction == 'secoc' else 8, 0x186: 16 if restriction == 'secoc' else 8})
  cp = CarInterface.get_params(CAR.FORD_MUSTANG_MACH_E_MK1, fp, [], False, True, False)
  if restriction == 'extra-panda':
    cp.safetyConfigs = [cp.safetyConfigs[0], cp.safetyConfigs[0]]
  elif restriction == 'foreign-brand':
    cp.brand = 'hyundai'
  assert prepare_release_long(cp, saved(tmp_path), is_release=True, enabled=True) is cp


class StopApproachFrame(dict):
  def __init__(self):
    from openpilot.starpilot.car.ford.stopped_lead import SOURCES
    super().__init__(carState=SimpleNamespace(vEgo=.8, canValid=True, canTimeout=False, accFaulted=False,
      gasPressed=False, brakePressed=False, gearShifter='drive', standstill=False),
      radarState=SimpleNamespace(leadOne=SimpleNamespace(present=True, radar=True, modelProb=.999,
        dRel=5.5, vLead=0., yRel=0.), leadTwo=SimpleNamespace(present=False)),
      modelV2=SimpleNamespace(), carControl=SimpleNamespace(longActive=True),
      controlsState=SimpleNamespace(forceDecel=False), selfdriveState=SimpleNamespace(enabled=True))
    self.valid = dict.fromkeys(SOURCES, True)
    self.alive = dict.fromkeys(SOURCES, True)
    self.logMonoTime = dict.fromkeys(SOURCES, 1_000_000_000)


def stopped_sample(owner, sm, **changes):
  args = {'now_ns': 1_000_000_000, 'drive_id': 1, 'target': -.05, 'stopping': False, 'accel_min': -3.5, 'active': True}
  args.update(changes)
  return owner.sample(sm, **args)


def primed_stop_approach():
  from openpilot.starpilot.car.ford.stopped_lead import RollingStoppedLead
  owner, sm = RollingStoppedLead(5.5), StopApproachFrame()
  assert stopped_sample(owner, sm) == (-.05, False)
  sm['carState'].vEgo = .4
  return owner, sm


def test_original_dom_rolling_stop_cap_and_stronger_braking_are_retained():
  owner, sm = primed_stop_approach()
  target, stopping = stopped_sample(owner, sm)
  # Dom's original6m STOP_DISTANCE-.5 +3m margin, with its unchanged brake terms.
  assert target == pytest.approx(-.21272727272727274) and stopping
  assert stopped_sample(owner, sm, target=-3.5, stopping=True) == (-3.5, True)


@pytest.mark.parametrize('defect', ['gas', 'brake', 'stale', 'future', 'invalid', 'off', 'disabled', 'force',
                                   'inactive', 'can', 'gear', 'nan', 'drive'])
def test_stop_approach_cannot_override_driver_invalid_source_or_new_drive(defect):
  owner, sm = primed_stop_approach()
  changes = {}
  if defect in ('gas', 'brake'):
    setattr(sm['carState'], defect + 'Pressed', True)
  elif defect == 'stale':
    sm.logMonoTime['radarState'] -= 250_000_001
  elif defect == 'future':
    sm.logMonoTime['radarState'] += 1
  elif defect == 'invalid':
    sm.valid['radarState'] = False
  elif defect == 'off':
    sm['carControl'].longActive = False
  elif defect == 'disabled':
    sm['selfdriveState'].enabled = False
  elif defect == 'force':
    sm['controlsState'].forceDecel = True
  elif defect == 'inactive':
    changes['active'] = False
  elif defect == 'can':
    sm['carState'].canValid = False
  elif defect == 'gear':
    sm['carState'].gearShifter = 'reverse'
  elif defect == 'nan':
    sm['radarState'].leadOne.dRel = float('nan')
  elif defect == 'drive':
    changes['drive_id'] = 2
  assert stopped_sample(owner, sm, **changes) == (-.05, False)
  assert not owner.rolling


@pytest.mark.parametrize('defect', ['moving', 'far', 'offcenter', 'uncertain', 'absent'])
def test_stop_approach_keeps_normal_moving_and_unqualified_leads_unchanged(defect):
  owner, sm = primed_stop_approach()
  lead = sm['radarState'].leadOne
  if defect == 'moving':
    lead.vLead = .450001
  elif defect == 'far':
    lead.dRel = 8.500001
  elif defect == 'offcenter':
    lead.yRel = 1.750001
  elif defect == 'uncertain':
    lead.radar, lead.modelProb = False, .949999
  else:
    lead.present = False
  assert stopped_sample(owner, sm) == (-.05, False)


def test_actual_standstill_and_normal_departure_keep_existing_release_owner():
  owner, sm = primed_stop_approach()
  assert stopped_sample(owner, sm)[1]
  sm['carState'].standstill, sm['carState'].vEgo = True, 0.
  assert stopped_sample(owner, sm, stopping=True) == (-.05, True)
  sm['carState'].standstill, sm['carState'].vEgo = False, .2
  sm['radarState'].leadOne.vLead = .2
  assert stopped_sample(owner, sm, target=.3) == (.3, False)
  owner.reset()
  assert stopped_sample(owner, sm, target=.3) == (.3, False)


def test_stopped_lead_owner_requires_existing_mache_open_long_configuration():
  from openpilot.starpilot.car.ford.stopped_lead import owner_for
  fp = gen_empty_fingerprint()
  fp[0][0x5A] = 8
  cp = CarInterface.get_params(CAR.FORD_MUSTANG_MACH_E_MK1, fp, [], True, False, False)
  assert owner_for(cp, 5.5) is not None
  for field, value in (('openpilotLongitudinalControl', False), ('passive', True), ('dashcamOnly', True),
                       ('notCar', True), ('brand', 'hyundai'), ('carFingerprint', CAR.FORD_F_150_MK14)):
    changed = cp.as_reader().as_builder()
    setattr(changed, field, value)
    assert owner_for(changed, 5.5) is None
