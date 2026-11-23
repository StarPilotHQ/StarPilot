"""Startup admits the scroll frame of Comma can set speed on Tesla dashboard: one safety-word bit in the car parameters
of every eligible Tesla Model 3/Y, whatever the switch says, so the switch itself works without a restart."""
from dataclasses import replace
from pathlib import Path

import pytest
from opendbc.car.car_helpers import interfaces
from opendbc.car.tesla.screen_button import SYNC_WORDS
from opendbc.car.tesla.speed_sync import SYNC_FLAG, eligible, prepare, selected
from opendbc.car.tesla.values import CAR
from opendbc.car.values import PLATFORMS
from openpilot.common.params import Params
from openpilot.starpilot.car.tesla import set_speed
from openpilot.starpilot.tests.tesla_set_speed_fixture import tesla
from openpilot.starpilot.vehicle_preferences import VehicleStartupPreferences

MODERN = (CAR.TESLA_MODEL_3, CAR.TESLA_MODEL_Y)
OFF, ON = VehicleStartupPreferences(), VehicleStartupPreferences(tesla_speed_sync=True)
OTHER_PLATFORMS = sorted(name for name in PLATFORMS if name not in ('TESLA_MODEL_3', 'TESLA_MODEL_Y'))


def raw(cp):
  return cp.as_reader().as_builder().to_bytes()


def word(cp):
  return int(cp.safetyConfigs[0].safetyParam)


def saved(tmp_path, **values):
  """Saved settings: True/False through the settings store, bytes written as they are, None left unset."""
  params = Params(str(tmp_path))
  for key, value in values.items():
    if isinstance(value, bytes):
      Path(params.get_param_path(key)).write_bytes(value)
    elif value is not None:
      params.put_bool(key, value, block=True)
  return params


def test_it_is_off_unless_read_on():
  assert OFF.tesla_speed_sync is False and ON.tesla_speed_sync is True


@pytest.mark.parametrize('enabled,safe,release,expected', [
  (True, None, None, True),
  (True, False, False, True),
  (False, None, None, False),         # StarPilot not enabled
  (True, True, None, False),          # Safe Mode
  (True, None, True, False),          # a release branch
  (True, True, True, False),
  (True, b'invalid', None, False),    # Safe Mode unreadable: treated as on
  (True, None, b'invalid', False),    # release marker unreadable: treated as a release
])
@pytest.mark.parametrize('switch', [None, False, True, b'2'])
def test_startup_admits_the_scroll_frame_whatever_the_switch_says(tmp_path, enabled, safe, release, expected, switch):
  params = saved(tmp_path, TeslaSpeedSync=switch, SafeMode=safe, IsReleaseBranch=release)
  snapshot = VehicleStartupPreferences.read(params, enabled=enabled)
  assert snapshot.tesla_speed_sync is expected
  for candidate in MODERN:
    cp = tesla(candidate)
    snapshot.prepare(cp)
    assert selected(cp) is expected and word(cp) == (4097 if expected else 1)
    snapshot.finalize(cp)
    assert selected(cp) is expected and word(cp) == (4097 if expected else 1)
    assert set_speed.active(params, cp) is bool(expected and switch is True), 'owning the set speed still needs the switch'


def test_startup_does_not_read_the_switch(tmp_path):
  params = saved(tmp_path, TeslaSpeedSync=True)
  reads = []

  class Spy:
    """The saved settings, noting which files are opened."""
    def __getattr__(self, name):
      return getattr(params, name)

    def get_param_path(self, key=''):
      reads.append(key)
      return params.get_param_path(key)
  assert VehicleStartupPreferences.read(Spy(), enabled=True).tesla_speed_sync
  assert reads and 'TeslaSpeedSync' not in reads and 'IsReleaseBranch' in reads and 'SafeMode' in reads


def test_the_admission_does_not_depend_on_the_other_tesla_switches(tmp_path):
  snapshot = VehicleStartupPreferences.read(saved(tmp_path), enabled=True)
  assert snapshot.tesla_speed_sync and not snapshot.tesla_cooperative and not snapshot.tesla_aol and not snapshot.tesla_screen
  params = saved(tmp_path, TeslaCoopSteering=True, AlwaysOnLateral=True, TeslaAOLScreenTap=True)
  snapshot = VehicleStartupPreferences.read(params, enabled=True)
  assert snapshot.tesla_speed_sync and snapshot.tesla_cooperative and snapshot.tesla_aol and snapshot.tesla_screen


@pytest.mark.parametrize('candidate', MODERN)
@pytest.mark.parametrize('step', ['prepare', 'finalize'])
def test_prepare_and_finalize_each_set_and_clear_the_bit(candidate, step):
  cp = tesla(candidate)
  before = raw(cp)
  getattr(ON, step)(cp)
  assert word(cp) == 4097 and selected(cp)
  getattr(ON, step)(cp)
  assert word(cp) == 4097, 'the same again'
  getattr(OFF, step)(cp)
  assert word(cp) == 1 and not selected(cp)
  assert raw(cp) == before, 'only that bit ever moved'


@pytest.mark.parametrize('candidate', MODERN)
def test_a_stale_bit_is_cleared_when_the_switch_is_off(candidate):
  cp = tesla(candidate, selected=True)
  OFF.finalize(cp)
  assert word(cp) == 1
  cp = tesla(candidate, selected=True)
  OFF.prepare(cp)
  assert word(cp) == 1


@pytest.mark.parametrize('candidate', MODERN)
@pytest.mark.parametrize('cable,long', [(False, True), (True, False), (False, False)])
def test_a_tesla_without_the_cable_or_longitudinal_control_is_never_selected(candidate, cable, long):
  cp = tesla(candidate, cable=cable, long=long)
  before = raw(cp)
  ON.prepare(cp)
  ON.finalize(cp)
  assert not selected(cp) and not word(cp) & SYNC_FLAG
  off = tesla(candidate, cable=cable, long=long)
  OFF.prepare(off)
  OFF.finalize(off)
  assert raw(cp) == raw(off) == before


@pytest.mark.parametrize('candidate', MODERN)
@pytest.mark.parametrize('coop', (False, True))
@pytest.mark.parametrize('aol,screen', [(False, False), (True, False), (True, True)])
@pytest.mark.parametrize('brake', (False, True))
def test_with_the_other_tesla_switches_the_word_is_one_the_panda_accepts(candidate, coop, aol, screen, brake):
  snapshot = replace(ON, tesla_cooperative=coop, tesla_aol=aol, tesla_screen=screen, tesla_screen_brake=brake)
  cp = tesla(candidate)
  for _ in range(2):
    snapshot.prepare(cp)
    assert word(cp) in SYNC_WORDS and selected(cp)
    snapshot.finalize(cp)
    assert word(cp) in SYNC_WORDS and selected(cp)
  expected = 4097 | (256 if coop else 0) | ((512 if screen else 2048) | (1024 if brake else 0) if aol else 0)
  assert word(cp) == expected
  without = tesla(candidate)
  replace(snapshot, tesla_speed_sync=False).prepare(without)
  replace(snapshot, tesla_speed_sync=False).finalize(without)
  assert word(without) == expected & ~SYNC_FLAG, 'the switch adds its bit and changes nothing else'
  cp.safetyConfigs[0].safetyParam = word(without)
  assert raw(cp) == raw(without)


def test_every_other_platform_exists_for_the_check_below():
  assert len(OTHER_PLATFORMS) > 100 and {'TESLA_MODEL_X', 'TESLA_MODEL_S_HW1', 'TESLA_MODEL_S_PREAP'} <= set(OTHER_PLATFORMS)


@pytest.mark.parametrize('platform', OTHER_PLATFORMS)
def test_every_other_car_is_left_exactly_as_without_the_switch(platform):
  results = []
  for snapshot in (OFF, ON):
    cp = interfaces[platform].get_non_essential_params(platform)
    assert not eligible(cp)
    prepared = snapshot.prepare(cp)
    snapshot.finalize(prepared)
    assert not selected(prepared)
    results.append(raw(prepared))
  assert results[0] == results[1]


@pytest.mark.parametrize('platform', ['HYUNDAI_IONIQ_5', 'TOYOTA_RAV4_TSS2', 'HONDA_CIVIC_BOSCH', 'TESLA_MODEL_X', 'TESLA_MODEL_S_HW1'])
def test_another_car_whose_safety_word_already_uses_the_same_bit_keeps_it(platform):
  # 4096 means something else to other brands' safety code: startup must neither clear nor read it.
  for snapshot in (OFF, ON):
    cp = interfaces[platform].get_non_essential_params(platform)
    cp.openpilotLongitudinalControl = True
    cp.safetyConfigs[0].safetyParam = int(cp.safetyConfigs[0].safetyParam) | SYNC_FLAG
    before = raw(cp)
    prepare(cp, snapshot.tesla_speed_sync)
    assert raw(cp) == before and not selected(cp) and not eligible(cp)
