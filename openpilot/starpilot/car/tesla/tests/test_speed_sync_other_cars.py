"""Nothing the Tesla set-speed and car-control work added acts for another car: every new behavior sits behind a gate
that is false for other fingerprints, for a Tesla without the cable, and for a Tesla whose switch is off."""
from pathlib import Path
import re
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from opendbc.car.car_helpers import interfaces
from opendbc.car.structs import car
from opendbc.car.tesla import speed_sync
from opendbc.car.tesla.carcontroller import CarController
from opendbc.car.tesla.values import DBC
from opendbc.car.values import PLATFORMS
from openpilot.common.params import Params
from openpilot.selfdrive.car.cruise import VCruiseHelper
from openpilot.starpilot.car.tesla import set_speed
from openpilot.starpilot.common import car_control_config
from openpilot.starpilot.controllers import cruise_action
from openpilot.starpilot.speed_limits import planner_host, tesla_set_speed_card
from openpilot.starpilot.tests.tesla_set_speed_fixture import switch, tesla
from openpilot.starpilot.ui.feature_settings_state import FeatureSettingsRequest
from openpilot.starpilot.ui.tesla_controls_feature import TeslaControlsFeature
from openpilot.starpilot.ui.tesla_rows import TeslaVehicleRows, TeslaWheelRows
from openpilot.starpilot.ui.tesla_set_speed_feature import TeslaSetSpeedFeature, addon_cable

ROOT = Path(__file__).resolve().parents[5]
MODELS = ('TESLA_MODEL_3', 'TESLA_MODEL_Y')
OTHER_PLATFORMS = sorted(name for name in PLATFORMS if name not in MODELS)
OTHER_TESLAS = ('TESLA_MODEL_X', 'TESLA_MODEL_S_HW1', 'TESLA_MODEL_S_PREAP')
EXAMPLES = ('HYUNDAI_IONIQ_5', 'TOYOTA_RAV4_TSS2', 'HONDA_CIVIC_BOSCH', 'CHEVROLET_BOLT_EUV', 'FORD_MAVERICK_MK1',
            'SUBARU_OUTBACK', 'VOLKSWAGEN_GOLF_MK7') + OTHER_TESLAS


class SwitchOn:
  """Saved settings with Comma can set speed on Tesla dashboard on, noting every setting that is opened."""
  def __init__(self, path):
    self.params = switch(Params(str(path)), True)
    self.opened = []

  def get_param_path(self, key=''):
    self.opened.append(key)
    return self.params.get_param_path(key)


def source(path):
  return (ROOT / path).read_text()


def squeezed(text):
  return re.sub(r'\s+', ' ', text)


def other(platform):
  return interfaces[platform].get_non_essential_params(platform)


def test_the_examples_are_real_platforms():
  assert set(EXAMPLES) <= set(PLATFORMS) and set(MODELS) <= set(PLATFORMS)
  assert speed_sync.MODELS == car_control_config.MODELS == MODELS


@pytest.mark.parametrize('platform', OTHER_PLATFORMS)
def test_no_other_platform_passes_any_gate_and_the_switch_is_never_read_for_it(tmp_path, platform):
  saved = SwitchOn(tmp_path)
  cp = other(platform)
  for longitudinal in (bool(cp.openpilotLongitudinalControl), True):
    cp.openpilotLongitudinalControl = longitudinal
    assert not speed_sync.eligible(cp) and not speed_sync.selected(cp)
    assert not cruise_action.tesla_set_speed(cp)
    assert not set_speed.active(saved, cp) and not car_control_config.comma_speed(saved, cp)
    assert not set_speed.Polled(saved)(cp)
    assert not car_control_config.vehicle_supported(cp)
    assert addon_cable(cp) is None
    assert cruise_action.eligible(cp) is bool(not cp.pcmCruise and longitudinal and not cp.passive and not cp.dashcamOnly and
                                              not cp.notCar), 'the software cruise rule other cars had'
  helper = VCruiseHelper(cp)
  card = SimpleNamespace(CP=cp, params=saved, v_cruise_helper=helper, slc_transport_available=True)
  with patch.object(tesla_set_speed_card.messaging, 'sub_sock', side_effect=AssertionError('no receiver for another car')), \
       patch.object(tesla_set_speed_card.messaging, 'recv_one_or_none', side_effect=AssertionError('nothing to read')):
    tesla_set_speed_card.refresh(card)
    card.slc_set_speed = tesla_set_speed_card.attach(card)
    tesla_set_speed_card.apply_commands(card, car.CarState.new_message(), None, 1, long_active=True)
  assert card.slc_set_speed is None and not tesla_set_speed_card.rule(card)
  assert helper.tesla_set_speed is False and helper.slc_pcm is False
  with patch.object(planner_host.messaging, 'pub_sock', side_effect=AssertionError('no command publisher for another car')):
    assert planner_host.SavedSlcLifecycle(saved.params, cp, {}).command_sock is None
  assert saved.opened == [], 'the switch is not read for a car that cannot own its set speed'


@pytest.mark.parametrize('platform', EXAMPLES)
def test_another_car_with_every_tesla_looking_field_set_still_passes_no_gate(tmp_path, platform):
  # The cable flag (8) and the scroll-frame bit (4096) are other brands' own flags; neither makes a car a Tesla Model 3/Y.
  saved = SwitchOn(tmp_path)
  cp = other(platform)
  cp.openpilotLongitudinalControl = True
  cp.pcmCruise = True
  cp.dashcamOnly = False
  cp.flags = int(cp.flags) | 8
  for config in cp.safetyConfigs:
    config.safetyParam = int(config.safetyParam) | 4096 | 1
  before = cp.as_reader().as_builder().to_bytes()
  assert not speed_sync.eligible(cp) and not speed_sync.selected(cp)
  assert not cruise_action.tesla_set_speed(cp) and not cruise_action.eligible(cp)
  assert not car_control_config.comma_speed(saved, cp) and not car_control_config.vehicle_supported(cp)
  assert not VCruiseHelper(cp).tesla_set_speed and saved.opened == []
  speed_sync.prepare(cp, True)
  speed_sync.prepare(cp, False)
  assert cp.as_reader().as_builder().to_bytes() == before


@pytest.mark.parametrize('make,on', [(lambda: tesla(selected=True), False), (lambda: tesla('TESLA_MODEL_Y', selected=True), False),
                                     (tesla, True), (lambda: tesla(cable=False, selected=True), True),
                                     (lambda: tesla(long=False, selected=True), True)])
def test_a_tesla_with_the_switch_off_or_without_the_frame_the_cable_or_longitudinal_control_owns_no_set_speed(tmp_path, make, on):
  params = switch(Params(str(tmp_path)), on)
  cp = make()
  assert not set_speed.active(params, cp) and not car_control_config.comma_speed(params, cp)
  helper = VCruiseHelper(cp)
  tesla_set_speed_card.refresh(SimpleNamespace(CP=cp, params=params, v_cruise_helper=helper))
  assert helper.tesla_set_speed is False
  bindings = car_control_config.effective_bindings(None, comma_speed=car_control_config.comma_speed(params, cp))
  assert 'right_wheel_up' not in bindings and 'right_wheel_down' not in bindings


@pytest.mark.parametrize('platform', EXAMPLES)
def test_other_cars_get_no_settings_rows_and_no_saved_changes(tmp_path, platform):
  cp = other(platform)
  params = Params(str(tmp_path))
  owner = SimpleNamespace(params=params, authority=lambda group: True, vehicle_params=lambda: cp,
                          vehicle_fingerprint=lambda: cp.carFingerprint)
  assert TeslaSetSpeedFeature(owner).rows(True) == () and TeslaSetSpeedFeature(owner).capability() is None
  assert TeslaControlsFeature(owner).rows() == []
  assert not TeslaVehicleRows(owner).set_speed.active()
  for key, value in (('TeslaSpeedSync', 'On'), ('tesla_input:right_wheel_left', 'Bookmark')):
    request = FeatureSettingsRequest(key, None, value, confirmation=True, vehicle_fingerprint=cp.carFingerprint,
                                     capability=(str(cp.carFingerprint), str(cp.brand), int(cp.flags), True, True, ()))
    assert not TeslaVehicleRows(owner).apply(request)
  assert not params.get_bool('TeslaSpeedSync') and params.get('CarControlAssignments') is None
  assert isinstance(TeslaWheelRows(owner).tesla.rows(), list) and not TeslaWheelRows(owner).tesla.rows()


@pytest.mark.parametrize('platform', OTHER_TESLAS)
def test_other_teslas_get_no_synchronizer(platform):
  cp = other(platform)
  assert CarController(DBC[platform], cp).speed_sync is None
  cp = tesla(platform, selected=True)
  assert CarController(DBC[platform], cp).speed_sync is None


def test_a_car_that_is_not_a_tesla_by_brand_is_not_one_by_name():
  cp = car.CarParams.new_message(brand='mock', carFingerprint='TESLA_MODEL_3', flags=8, pcmCruise=True, openpilotLongitudinalControl=True)
  cp.safetyConfigs = [car.CarParams.SafetyConfig.new_message(safetyParam=4097)]
  assert not speed_sync.eligible(cp) and not speed_sync.selected(cp) and not car_control_config.vehicle_supported(cp)
  assert addon_cable(cp) is None


# The gates themselves, as written in the code every car runs. A new use of the feature in these files must sit
# behind one of them; add it here once it has been checked.
GATES = {
  'openpilot/selfdrive/car/cruise.py': [
    'self.tesla_set_speed = False',
    'stock_software = subaru_software_selected(self.CP) or self.hyundai_software_cruise or self.tesla_set_speed',
    'self.slc_pcm = bool(slc_enabled and self.tesla_set_speed)',
    'if self.CP.pcmCruise and (not self.slc_pcm or slc_target_mps is None):',
    'if resume and self.v_cruise_initialized and not (self.slc_pcm and slc_target_mps is not None):',
  ],
  'openpilot/selfdrive/car/card.py': [
    'enabled=bool((host_control_enabled or cruise_helper.tesla_set_speed and host_enabled) and',
    'if getattr(self.CI.CC, "speed_sync", None) is not None:',
    'rule = tesla_set_speed_card.rule(self)',
  ],
  'openpilot/starpilot/speed_limits/tesla_set_speed_card.py': [
    'if selected(card.CP): card.v_cruise_helper.tesla_set_speed = set_speed.active(card.params, card.CP)',
    'if not (card.slc_transport_available and selected(card.CP)): return None',
    "receiver = getattr(card, 'slc_set_speed', None) if receiver is None: return",
    'if not helper.tesla_set_speed: receiver.commands.clear()',
    "return bool(getattr(card, 'slc_set_speed', None) is not None and card.v_cruise_helper.tesla_set_speed)",
    "if kind in ('commandApplied', 'commandRejected'):",
  ],
  'openpilot/starpilot/car/tesla/set_speed.py': [
    'return bool(selected(cp) and requested(params))',
    'if not selected(cp): return False',
  ],
  'openpilot/starpilot/speed_limits/planner_host.py': [
    "self.command_sock = messaging.pub_sock('slcCruiseCommand') if tesla_set_speed_selected(cp) else None",
    'if self.command_sock is not None and tesla_set_speed.active(self.params, self.cp):',
    'if output.command is not None and self.command_sock is not None:',
  ],
  'openpilot/starpilot/speed_limits/runtime.py': [
    'self.set_speed_rule = bool(self.settings.set_speed_rule and tesla_set_speed_selected(CP))',
    'command = self._command(result, frame, sm, now_ns, old_pending, action, adopt) if self.set_speed_rule else None',
  ],
  'openpilot/starpilot/controllers/cruise_action.py': [
    'return bool(cp is not None and (not cp.pcmCruise or tesla_set_speed(cp)) and cp.openpilotLongitudinalControl and',
    'not tesla_set_speed(cp)):',
    "if kind == \"cruiseStep\" and not getattr(helper, 'tesla_set_speed', False):",
    "if not (eligible(cp) and (not cp.pcmCruise or getattr(helper, 'tesla_set_speed', False)) and",
  ],
  'openpilot/starpilot/controllers/tesla_receiver.py': [
    'if not vehicle_supported(self.vehicle()):',
    'binding = effective_bindings(doc, comma_speed=comma_speed(self.params, cp)).get(name)',
  ],
  'openpilot/starpilot/controllers/tesla_input_worker.py': [
    'fingerprint = cp_fingerprint(cp) if vehicle_supported(cp) else None',
    'provenance = bool(fingerprint) and speed_sync_selected(cp)',
    'bindings = effective_bindings(doc, comma_speed=self.comma_speed(cp)) if doc else {}',
  ],
  'openpilot/starpilot/car/tesla_speed_sync_context.py': [
    'if controller is None or getattr(controller, "speed_sync", None) is None: return',
    'if type(stamp) is not int or stamp <= 0 or not helper.tesla_set_speed: return',
  ],
  'openpilot/starpilot/ui/runtime_app.py': [
    'if ui_state.CP is not None and (not ui_state.CP.pcmCruise or self._tesla_set_speed(ui_state.CP)):',
  ],
  'openpilot/starpilot/ui/tesla_speed_actions.py': [
    'if vehicle_supported(ui_state.CP):',
  ],
  'openpilot/starpilot/ui/tesla_controls_feature.py': [
    'if cp is None or cp.carFingerprint not in MODELS:',
  ],
  'openpilot/starpilot/ui/tesla_set_speed_feature.py': [
    "if cp is None or cp.brand != 'tesla' or cp.carFingerprint not in MODELS:",
    'if addon_cable(cp) is None:',
  ],
  'openpilot/starpilot/vehicle_preferences.py': [
    'prepare_speed_sync(cp, self.tesla_speed_sync)',
  ],
  'opendbc_repo/opendbc/car/tesla/speed_sync.py': [
    'return bool(cp.brand == "tesla" and cp.carFingerprint in MODELS and cp.openpilotLongitudinalControl and cp.pcmCruise and',
    'return bool(eligible(cp) and cp.safetyConfigs[0].safetyParam & SYNC_FLAG)',
    'if eligible(cp):',
  ],
  'opendbc_repo/opendbc/car/tesla/carcontroller.py': [
    'self.speed_sync = SpeedSynchronizer() if speed_sync_selected(CP) else None',
    'if self.speed_sync is not None:',
  ],
  'opendbc_repo/opendbc/car/tesla/interface.py': [
    'if self.CC is not None and self.CC.speed_sync is not None:',
  ],
  'opendbc_repo/opendbc/car/tesla/carstate.py': [
    'if self.CP.carFingerprint in (CAR.TESLA_MODEL_3, CAR.TESLA_MODEL_Y): update_dashboard_limit(',
  ],
}
# Every line of the files all cars share that uses the selection or the switch, apart from imports, definitions and
# comments.
SOFTWARE = 'stock_software = subaru_software_selected(self.CP) or self.hyundai_software_cruise or self.tesla_set_speed'
SHARED = {
  'openpilot/selfdrive/car/cruise.py': (r'tesla_set_speed|speed_sync', [
    'self.tesla_set_speed = False', SOFTWARE, 'self.slc_pcm = bool(slc_enabled and self.tesla_set_speed)', SOFTWARE]),
  'openpilot/selfdrive/car/card.py': (r'tesla_set_speed|speed_sync', [
    'self.slc_set_speed = tesla_set_speed_card.attach(self)',
    'enabled=bool((host_control_enabled or cruise_helper.tesla_set_speed and host_enabled) and',
    'tesla_set_speed_card.apply_commands(self, CS, observation, now_ns, long_active=slc_long_active, after_ns=floor_ns)',
    'rule = tesla_set_speed_card.rule(self)',
    'tesla_set_speed_card.write_receipt(record, kind, detail)',
    'if getattr(self.CI.CC, "speed_sync", None) is not None:',
    'tesla_set_speed_card.refresh(self)']),
  'openpilot/starpilot/controllers/cruise_action.py': (r'tesla_set_speed', [
    'Card applies a request only while the switch is on (helper.tesla_set_speed)."""',
    'return bool(cp is not None and (not cp.pcmCruise or tesla_set_speed(cp)) and cp.openpilotLongitudinalControl and',
    'not tesla_set_speed(cp)):',
    "if kind == \"cruiseStep\" and not getattr(helper, 'tesla_set_speed', False):",
    "if not (eligible(cp) and (not cp.pcmCruise or getattr(helper, 'tesla_set_speed', False)) and"]),
  'openpilot/starpilot/ui/runtime_app.py': (r'tesla_set_speed\(', [
    'if ui_state.CP is not None and (not ui_state.CP.pcmCruise or self._tesla_set_speed(ui_state.CP)):']),
  'openpilot/starpilot/vehicle_preferences.py': (r'prepare_speed_sync\(|tesla_speed_sync=speed_sync', [
    'tesla_screen_brake=screen_brake, tesla_cooperative=cooperative, tesla_speed_sync=speed_sync, toyota_auto_hold=toyota,',
    'prepare_speed_sync(cp, self.tesla_speed_sync)', 'prepare_speed_sync(cp, self.tesla_speed_sync)']),
  'openpilot/starpilot/speed_limits/planner_host.py': (r'tesla_set_speed', [
    "self.command_sock = messaging.pub_sock('slcCruiseCommand') if tesla_set_speed_selected(cp) else None",
    'if self.command_sock is not None and tesla_set_speed.active(self.params, self.cp):']),
  'openpilot/starpilot/speed_limits/runtime.py': (r'tesla_set_speed', [
    'self.set_speed_rule = bool(self.settings.set_speed_rule and tesla_set_speed_selected(CP))']),
}


@pytest.mark.parametrize('path', GATES)
def test_each_gate_is_still_in_place(path):
  text = squeezed(source(path))
  for gate in GATES[path]:
    assert squeezed(gate) in text, f'{path}: gate not found: {gate}'


@pytest.mark.parametrize('path', SHARED)
def test_shared_code_uses_the_selection_only_at_its_reviewed_gates(path):
  pattern, expected = SHARED[path]
  lines = [squeezed(line.split('  #')[0]).strip() for line in source(path).splitlines()
           if re.search(pattern, line) and not line.lstrip().startswith(('#', 'from ', 'import ', 'def '))]
  assert lines == expected, f'{path}: a use of the selection changed; check it is gated, then update this list'


def test_startup_is_the_only_place_that_writes_the_selection():
  writers = []
  for path in list((ROOT / 'openpilot').rglob('*.py')) + list((ROOT / 'opendbc_repo/opendbc').rglob('*.py')):
    if '/tests/' in path.as_posix() or path.name.startswith('test_'):
      continue
    text = path.read_text(errors='ignore')
    if re.search(r'\|=\s*(SYNC_FLAG|.*SCROLL_SPEED_SYNC)|prepare_speed_sync\(|speed_sync import .*\bprepare\b', text):
      writers.append(path.relative_to(ROOT).as_posix())
  assert sorted(writers) == ['opendbc_repo/opendbc/car/tesla/speed_sync.py', 'openpilot/starpilot/vehicle_preferences.py']


def test_only_cards_settings_thread_turns_ownership_on():
  writers = []
  for path in (ROOT / 'openpilot').rglob('*.py'):
    if '/tests/' in path.as_posix() or path.name.startswith('test_'):
      continue
    if re.search(r'\.tesla_set_speed\s*=[^=]', path.read_text(errors='ignore')):
      writers.append(path.relative_to(ROOT).as_posix())
  assert sorted(writers) == ['openpilot/selfdrive/car/cruise.py', 'openpilot/starpilot/speed_limits/tesla_set_speed_card.py']


def test_the_car_interface_does_not_select_it():
  text = source('opendbc_repo/opendbc/car/tesla/interface.py')
  assert 'SCROLL_SPEED_SYNC' not in text and 'EXPERIMENTAL_TEST_BUILD' not in text and 'PROTOCOL_QUALIFIED' not in text
  engine = source('opendbc_repo/opendbc/car/tesla/speed_sync.py')
  assert 'EXPERIMENTAL_TEST_BUILD' not in engine and 'PROTOCOL_QUALIFIED' not in engine


@pytest.mark.parametrize('name', ['match_speed_owner', 'software_cruise_requested', 'tesla_controls_requested', 'tesla_controls_active',
                                  'slc_sets_set_speed', 'TeslaIndependentSetSpeed', 'TeslaOverrideSetSpeed', 'TeslaGentleSlowing',
                                  'WHEEL_GESTURES', 'WheelPress', '_wheel_deferred', 'dash_speed', 'tesla_speed_sync_warning',
                                  'pedal_handover', 'PedalHandover', 'HandoverSample', 'gas_release_frame', 'tesla_vehicle_feature',
                                  'indicator_left_half', 'beam_pull', 'wiper_half'])
def test_removed_parts_are_gone_from_the_code(name):
  found = []
  roots = [ROOT / 'openpilot/starpilot', ROOT / 'openpilot/selfdrive', ROOT / 'openpilot/common', ROOT / 'openpilot/cereal',
           ROOT / 'opendbc_repo/opendbc/car/tesla', ROOT / 'opendbc_repo/opendbc/safety/modes']
  for root in roots:
    for path in root.rglob('*'):
      if (path.suffix not in ('.py', '.capnp', '.h', '.js') or '/tests/' in path.as_posix() or path.name.startswith('test_') or
          '/gen/' in path.as_posix()):
        continue
      if re.search(r'\b' + re.escape(name) + r'\b', path.read_text(errors='ignore')):
        found.append(path.relative_to(ROOT).as_posix())
  assert not found, f'{name} is still named in {found}'
