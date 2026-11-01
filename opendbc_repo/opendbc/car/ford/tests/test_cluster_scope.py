"""Dom CAN-FD cluster scope through actual factory, setting UI and controller."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from opendbc.can import CANParser
from opendbc.car import structs
from opendbc.car.ford.tests.test_mache_ui_owner import setup, UI
from opendbc.car.ford.values import CAR, FordFlags
from openpilot.common.params import Params
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.starpilot.car.ford.cluster_preferences import KEY, qualified
from openpilot.starpilot.controller_extensions import configure_controller
from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner
from openpilot.starpilot.ui.feature_settings_state import row_change

DOM_CANFD = (
  CAR.FORD_MUSTANG_MACH_E_MK1, CAR.FORD_ESCAPE_MK4_5, CAR.FORD_EXPEDITION_MK4,
  CAR.FORD_F_150_MK14, CAR.FORD_F_150_LIGHTNING_MK1, CAR.FORD_RANGER_MK2, CAR.FORD_MONDEO_MK5,
)


def test_scope_covers_every_dom_canfd_profile():
  assert set(DOM_CANFD) == {car for car in CAR if car.config.flags & FordFlags.CANFD and not car.config.flags & FordFlags.LKA_STEERING}


@pytest.mark.parametrize('identity', DOM_CANFD)
@pytest.mark.parametrize('aol', [False, True])
@pytest.mark.parametrize('enabled', [False, True])
def test_all_dom_profiles_have_shared_setting_and_correct_active_status(identity, aol, enabled):
  with OpenpilotPrefix():
    saved = Params()
    controller, state = setup(aol=aol, identity=identity)
    assert qualified(controller.CP)
    owner = FeatureSettingsOwner(saved, lambda group: True, vehicle_params=lambda: controller.CP,
                                 vehicle_fingerprint=lambda: str(identity))
    row = next(r for r in owner.snapshot('torque/bluepilot', parked=True, system_long=True, lateral_context=True, metric=False).rows if r.key == KEY)
    assert row.available and row.value == 'Off'
    if enabled:
      request = row_change(row, 1)
      assert request is not None
      assert owner.apply(request)
    configure_controller(SimpleNamespace(CP=controller.CP, CC=controller), saved)
    assert controller.hands_free_cluster_input.enabled == enabled
    command = structs.CarControl()
    for _ in range(10):
      _, frames = controller.update(command.as_reader(), state, controller.frame * 10_000_000)
      if identity == CAR.FORD_MUSTANG_MACH_E_MK1 and aol:
        assert not UI.intersection(address for address, _, _ in frames)
    command.latActive = True
    parser = CANParser('ford_lincoln_base_pt', [('ACCDATA_3', 5)], 0)
    statuses = []
    for _ in range(110):
      _, frames = controller.update(command.as_reader(), state, controller.frame * 10_000_000)
      for frame in frames:
        if frame[0] == 0x18A:
          parser.update([(controller.frame * 10_000_000, [frame])])
          statuses.append(parser.vl['ACCDATA_3']['Tja_D_Stat'])
    assert statuses and set(statuses) == {7 if enabled else 2}
    assert controller.cluster_extended_announced


@pytest.mark.parametrize('identity', [car for car in CAR if car not in DOM_CANFD])
def test_non_canfd_fords_do_not_show_cluster_setting(identity):
  with OpenpilotPrefix():
    saved = Params()
    controller, _ = setup(identity=identity)
    assert not qualified(controller.CP)
    configure_controller(SimpleNamespace(CP=controller.CP, CC=controller), saved)
    assert controller.hands_free_cluster_input is None
    owner = FeatureSettingsOwner(saved, lambda group: True, vehicle_params=lambda: controller.CP,
                                 vehicle_fingerprint=lambda: str(identity))
    assert KEY not in {r.key for r in owner.snapshot('torque/bluepilot', parked=True, system_long=True, lateral_context=True, metric=False).rows}


def test_invalid_profile_does_not_enable_display_preference():
  with OpenpilotPrefix():
    saved = Params()
    Path(saved.get_param_path(KEY)).write_bytes(b'1')
    controller, _ = setup()
    controller.CP.passive = True
    assert not qualified(controller.CP)
    configure_controller(SimpleNamespace(CP=controller.CP, CC=controller), saved)
    assert controller.hands_free_cluster_input is None
