"""Comma can set speed on Tesla dashboard, the saved switch: followed while the car is on, read at most once a second
by the loops that ask often, and never read for a car that cannot own its set speed."""
import pytest
from opendbc.car.structs import car
from openpilot.common.params import Params
from openpilot.starpilot.car.tesla import set_speed
from openpilot.starpilot.common.car_control_config import comma_speed
from openpilot.starpilot.tests.tesla_set_speed_fixture import save, switch, tesla

OTHER_CARS = {
  'no car': lambda: None,
  'tesla started without the scroll frame': tesla,
  'model y started without the scroll frame': lambda: tesla('TESLA_MODEL_Y'),
  'tesla without the cable': lambda: tesla(cable=False, selected=True),
  'tesla without longitudinal control': lambda: tesla(long=False, selected=True),
  'model s': lambda: tesla('TESLA_MODEL_S_HW1', selected=True),
  'model x': lambda: tesla('TESLA_MODEL_X', selected=True),
  'hyundai': lambda: car.CarParams.new_message(brand='hyundai', carFingerprint='HYUNDAI_IONIQ_5', pcmCruise=True,
                                               openpilotLongitudinalControl=True, flags=8),
  'not car parameters': object,
}


class Reads:
  """Saved settings that count how often each one is opened."""
  def __init__(self, path):
    self.params = Params(str(path))
    self.opened = []

  def get_param_path(self, key=''):
    self.opened.append(key)
    return self.params.get_param_path(key)


def test_the_switch_is_one_saved_setting():
  assert set_speed.PARAM == 'TeslaSpeedSync'


@pytest.mark.parametrize('saved,safe,expected', [
  (True, None, True), (True, False, True),
  (None, None, False), (False, None, False),      # never chosen, or off
  (True, True, False),                            # Safe Mode
  (b'2', None, False), (b'', None, False), (b'1' * 9, None, False), (b'true', None, False),
  (True, b'maybe', False),                        # Safe Mode unreadable: treated as on
])
def test_requested_is_the_switch_on_and_safe_mode_off(tmp_path, saved, safe, expected):
  params = Params(str(tmp_path))
  save(params, 'TeslaSpeedSync', saved)
  save(params, 'SafeMode', safe)
  assert set_speed.requested(params) is expected
  for model in ('TESLA_MODEL_3', 'TESLA_MODEL_Y'):
    assert set_speed.active(params, tesla(model, selected=True)) is expected
    assert comma_speed(params, tesla(model, selected=True)) is expected


def test_active_follows_the_switch_at_once_in_both_directions(tmp_path):
  params = Params(str(tmp_path))
  cp = tesla(selected=True)
  assert not set_speed.active(params, cp)
  switch(params, True)
  assert set_speed.active(params, cp)
  save(params, 'SafeMode', True)
  assert not set_speed.active(params, cp)
  save(params, 'SafeMode', False)
  assert set_speed.active(params, cp)
  switch(params, False)
  assert not set_speed.active(params, cp)


@pytest.mark.parametrize('case', OTHER_CARS)
def test_no_other_car_is_active_and_nothing_is_read_for_it(tmp_path, case):
  reads = Reads(tmp_path)
  switch(reads.params, True)
  cp = OTHER_CARS[case]()
  assert set_speed.active(reads, cp) is False and comma_speed(reads, cp) is False
  polled = set_speed.Polled(reads, clock=lambda: 10.)
  assert polled(cp) is False and polled.read_at is None
  assert reads.opened == []


def test_unreadable_storage_is_off():
  class Broken:
    def get_param_path(self, key=''):
      raise OSError('storage gone')
  assert set_speed.active(Broken(), tesla(selected=True)) is False


def test_polled_reads_storage_at_most_once_a_second(tmp_path):
  reads = Reads(tmp_path)
  now = [100.]
  polled = set_speed.Polled(reads, clock=lambda: now[0])
  cp = tesla(selected=True)
  assert polled(cp) is False and reads.opened == ['TeslaSpeedSync'], 'off: Safe Mode is not even looked at'
  switch(reads.params, True)
  for _ in range(100):
    now[0] += .0099
    assert polled(cp) is False, 'still the value read under a second ago'
  assert reads.opened == ['TeslaSpeedSync']
  now[0] = 101.
  assert polled(cp) is True and reads.opened == ['TeslaSpeedSync', 'TeslaSpeedSync', 'SafeMode']
  switch(reads.params, False)
  now[0] = 101.999
  assert polled(cp) is True
  now[0] = 102.
  assert polled(cp) is False


def test_polled_reads_again_when_the_clock_steps_back(tmp_path):
  reads = Reads(tmp_path)
  now = [100.]
  polled = set_speed.Polled(reads, clock=lambda: now[0])
  cp = tesla(selected=True)
  assert polled(cp) is False
  switch(reads.params, True)
  now[0] = 50.
  assert polled(cp) is True


def test_polled_answers_per_car_from_one_read(tmp_path):
  reads = Reads(tmp_path)
  switch(reads.params, True)
  polled = set_speed.Polled(reads, clock=lambda: 5.)
  assert polled(tesla(selected=True)) is True
  count = len(reads.opened)
  assert polled(tesla()) is False and polled(None) is False and polled(tesla('TESLA_MODEL_Y', selected=True)) is True
  assert len(reads.opened) == count
