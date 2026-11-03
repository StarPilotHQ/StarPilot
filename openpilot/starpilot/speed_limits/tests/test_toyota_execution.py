"""Supported Toyota variants retain native SET while SLC owns only a planner cap."""

import pytest

from opendbc.car import gen_empty_fingerprint
from opendbc.car.structs import car
from opendbc.car.toyota.interface import CarInterface
from opendbc.car.toyota.prius_sdsu import RETROFIT_EPS
from opendbc.car.toyota.values import CAR, ToyotaFlags
from openpilot.selfdrive.car.cruise import ButtonType, VCruiseHelper, SlcPendingConfirmation
from openpilot.starpilot.car.toyota.tests.test_pedal_caller_native import firmware


@pytest.fixture(params=[
  pytest.param((CAR.TOYOTA_COROLLA_TSS2, {}, False), id='native-tss2'),
  pytest.param((CAR.TOYOTA_PRIUS, {0: {0x2FF: 8}}, False), id='prius-sdsu'),
  pytest.param((CAR.TOYOTA_PRIUS, {0: {0x2FF: 4}}, False), id='prius-advert'),
  pytest.param((CAR.TOYOTA_PRIUS, {0: {0x2FF: 8}}, True), id='prius-eps-mod'),
  pytest.param((CAR.TOYOTA_PRIUS_RETROFIT, {0: {0x2FF: 8}}, True), id='prius-retrofit-sdsu'),
  pytest.param((CAR.TOYOTA_PRIUS_RETROFIT, {0: {0x2FF: 4}}, True), id='prius-retrofit-advert'),
  pytest.param((CAR.TOYOTA_COROLLA, {0: {0x2FF: 8}}, False), id='tssp-corolla'),
  pytest.param((CAR.TOYOTA_PRIUS_V, {0: {0x2FF: 8}}, False), id='tssp-prius-v'),
  pytest.param((CAR.LEXUS_IS, {0: {0x2FF: 8}}, False), id='tssp-alternate-main'),
  pytest.param((CAR.TOYOTA_RAV4, {2: {0x343: 8}}, False), id='tssp-dsu-bypass'),
  pytest.param((CAR.TOYOTA_CAMRY, {0: {0x2AA: 8}}, False), id='tssp-radar-filter'),
  pytest.param((CAR.TOYOTA_RAV4_TSS2_2022, {}, False), id='tss2-radar-disabled'),
])
def helper(request):
  candidate, sources, eps_mod = request.param
  fp = gen_empty_fingerprint()
  fp.update(sources)
  fw = firmware(CAR.TOYOTA_PRIUS if candidate == CAR.TOYOTA_PRIUS_RETROFIT else candidate)
  if eps_mod:
    for row in fw:
      if row.ecu == car.CarParams.Ecu.eps:
        row.fwVersion = RETROFIT_EPS
  cp = CarInterface.get_params(candidate, fp, fw, True, False, False)
  assert cp.pcmCruise and cp.openpilotLongitudinalControl and not cp.flags & ToyotaFlags.GAS_INTERCEPTOR
  result = VCruiseHelper(cp)
  result.v_cruise_kph = result.v_cruise_cluster_kph = 50.0
  return result



def state(native=60., cluster=None, *, button=None, pressed=True, stopped=False):
  return car.CarState(canValid=True, standstill=stopped,
    cruiseState={'available': True, 'enabled': True, 'speed': native / 3.6,
                 'speedCluster': (native if cluster is None else cluster) / 3.6, 'standstill': stopped},
    buttonEvents=[] if button is None else [car.CarState.ButtonEvent(type=button, pressed=pressed)])


@pytest.mark.parametrize('slc', [False, True])
def test_slc_keeps_native_cruise_speed_and_cluster_calibration(helper, slc):
  helper.update_v_cruise(state(70., 69.6), True, True, slc_enabled=slc)
  assert helper.v_cruise_kph == pytest.approx(70.)
  assert helper.v_cruise_cluster_kph == pytest.approx(69.6)
  assert not helper.initialize_v_cruise(state(), False, slc_target_mps=25., is_metric=True)
  assert helper.v_cruise_kph == pytest.approx(70.)
  assert not helper.adjust_v_cruise(ButtonType.accelCruise, state(), True)


@pytest.mark.parametrize('button', ['accelCruise', 'decelCruise'])
def test_confirmations_consume_software_action_but_native_set_remains_authoritative(helper, button):
  pending = SlcPendingConfirmation('drive', 1, 1)
  helper.update_v_cruise(state(button=button), True, True, pending, slc_enabled=True)
  assert helper.slc_consumed_button is not None
  assert helper.slc_consumed_button.button == ('accel' if button == 'accelCruise' else 'decel')
  helper.update_v_cruise(state(61., button=button), True, True, pending, slc_enabled=True)
  assert helper.slc_consumed_button is None
  assert helper.v_cruise_kph == pytest.approx(61.)
  assert helper.slc_cruise_change is None
  helper.update_v_cruise(state(61., button=button, pressed=False), True, True, slc_enabled=False)
  assert not helper.slc_suppressed_buttons and helper.slc_cruise_change is None
  assert helper.v_cruise_kph == pytest.approx(61.)


def test_native_standstill_resume_takes_precedence_over_confirmation(helper):
  pending = SlcPendingConfirmation('drive', 1, 1)
  helper.update_v_cruise(state(button='accelCruise', stopped=True), True, True, pending, slc_enabled=True)
  assert helper.slc_consumed_button is None
  assert helper.v_cruise_kph == pytest.approx(60.)


def test_factory_longitudinal_does_not_gain_slc_button_ownership(helper):
  helper.CP.openpilotLongitudinalControl = False
  helper.update_v_cruise(state(button='decelCruise'), True, True, SlcPendingConfirmation('drive', 1, 1), slc_enabled=True)
  assert helper.slc_consumed_button is None
  assert helper.v_cruise_kph == pytest.approx(60.)
