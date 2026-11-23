"""The three-finger tap starts on Always On Lateral on/off: its saved switch (TeslaAOLScreenTap) is on by default, and it
only does anything on a Model 3/Y with the CAN add-on cable and Always On Lateral turned on."""
import pytest
from opendbc.car.tesla.screen_button import ORDINARY_WORDS, SCREEN_WORDS
from opendbc.car.tesla.values import CAR, TeslaFlags
from openpilot.common.params import Params
from openpilot.starpilot.common.car_control_config import screen_reserved
from openpilot.starpilot.tests.tesla_set_speed_fixture import save, tesla
from openpilot.starpilot.vehicle_preferences import VehicleStartupPreferences

KEY = 'TeslaAOLScreenTap'
MODERN = (CAR.TESLA_MODEL_3, CAR.TESLA_MODEL_Y)


def started(tmp_path, cp, *, tap, aol=True):
  """The car parameters after startup read the saved settings. tap: 'default' saves the registry default, as the manager
  does for a setting never saved; True/False a saved choice; None leaves it unsaved."""
  params = Params(str(tmp_path))
  params.put_bool('AlwaysOnLateral', aol, block=True)
  if tap == 'default':
    params.put(KEY, params.get_default_value(KEY), block=True)
  else:
    save(params, KEY, tap)
  snapshot = VehicleStartupPreferences.read(params, enabled=True)
  snapshot.prepare(cp)
  snapshot.finalize(cp)
  return int(cp.safetyConfigs[0].safetyParam) & ~4096, cp


def test_the_saved_default_is_on():
  assert Params().get_default_value(KEY) is True


@pytest.mark.parametrize('candidate', MODERN)
def test_with_the_cable_the_default_puts_always_on_lateral_on_the_tap(tmp_path, candidate):
  word, cp = started(tmp_path, tesla(candidate), tap='default')
  assert word in SCREEN_WORDS and cp.flags & TeslaFlags.AOL_SCREEN_BUTTON and screen_reserved(cp)


@pytest.mark.parametrize('candidate', MODERN)
def test_without_the_cable_the_default_changes_nothing(tmp_path, candidate):
  word, cp = started(tmp_path, tesla(candidate, cable=False), tap='default')
  assert word in ORDINARY_WORDS and not cp.flags & TeslaFlags.AOL_SCREEN_BUTTON and not screen_reserved(cp)
  (tmp_path / 'off').mkdir()
  assert started(tmp_path / 'off', tesla(candidate, cable=False), tap=False)[0] == word


@pytest.mark.parametrize('tap', [False, None])
def test_a_tap_saved_off_or_never_saved_stays_off(tmp_path, tap):
  word, cp = started(tmp_path, tesla(), tap=tap)
  assert word in ORDINARY_WORDS and not screen_reserved(cp)


def test_it_needs_always_on_lateral_turned_on(tmp_path):
  word, cp = started(tmp_path, tesla(), tap='default', aol=False)
  assert word not in SCREEN_WORDS | ORDINARY_WORDS and not screen_reserved(cp)
