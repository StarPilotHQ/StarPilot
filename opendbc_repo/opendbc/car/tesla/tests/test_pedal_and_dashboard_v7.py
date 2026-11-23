"""The Tesla longitudinal command and the dashboard speed limit: the real formatter, CAN parser and car state; no
vehicle output. The command is the stock one for every Tesla, pedal or not: nothing in it follows the set-speed work."""
import inspect
from types import SimpleNamespace

import pytest
from opendbc.can import CANPacker
from opendbc.car import gen_empty_fingerprint
from opendbc.car.structs import car
from opendbc.car.tesla.interface import CarInterface
from opendbc.car.tesla.carcontroller import CarController
from opendbc.car.tesla.carstate import CarState
from opendbc.car.tesla.speed_sync import prepare
from opendbc.car.tesla.teslacan import TeslaCAN
from opendbc.car.tesla.values import CAR, CANBUS, DBC, CarControllerParams
from opendbc.car.dashboard_speed_limit import Status
from openpilot.starpilot.speed_limits.runtime import dashboard_observation
from openpilot.starpilot.speed_limits.acceptance import ObservationKind

MODERN = (CAR.TESLA_MODEL_3, CAR.TESLA_MODEL_Y)


class Capture:
  """Stands in for the CAN packer: returns the values the formatter chose."""
  def make_can_msg(self, name, bus, values):
    return name, bus, dict(values)


def stock_command(acc_state, accel, counter, v_ego):
  """The longitudinal command as the stock formatter builds it."""
  return {'DAS_setSpeed': min(max(v_ego + accel, 0) * 3.6, 400), 'DAS_accState': acc_state, 'DAS_aebEvent': 0,
          'DAS_jerkMin': -4.9, 'DAS_jerkMax': 4.9, 'DAS_accelMin': accel, 'DAS_accelMax': max(accel, 0),
          'DAS_controlCounter': counter}


def test_the_formatter_takes_the_stock_arguments_and_keeps_no_state():
  parameters = list(inspect.signature(TeslaCAN.create_longitudinal_command).parameters.values())
  assert [(p.name, p.kind, p.default) for p in parameters] == [
    (name, inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.empty)
    for name in ('self', 'acc_state', 'accel', 'counter', 'v_ego', 'active')]
  assert sorted(vars(TeslaCAN(None, Capture()))) == ['CP', 'packer']
  assert (CarControllerParams.JERK_LIMIT_MIN, CarControllerParams.JERK_LIMIT_MAX) == (-4.9, 4.9)


@pytest.mark.parametrize('acc_state', [4, 13])
@pytest.mark.parametrize('accel', [-3.48, -.1, 0., .8, 2.])
@pytest.mark.parametrize('v_ego', [0., 1.5, 20., 45., 120.])
@pytest.mark.parametrize('active', [False, True])
def test_the_command_is_the_stock_one(acc_state, accel, v_ego, active):
  codec = TeslaCAN(None, Capture())
  for counter in range(16):
    name, bus, values = codec.create_longitudinal_command(acc_state, accel, counter % 8, v_ego, active)
    assert (name, bus) == ('DAS_control', CANBUS.party)
    assert values == pytest.approx(stock_command(acc_state, accel, counter % 8, v_ego)), 'the same on every call'
  assert set(values) == set(stock_command(acc_state, accel, 0, v_ego))


def controller(candidate, *, selected, long=True):
  fingerprint = gen_empty_fingerprint()
  fingerprint[CANBUS.autopilot_party][0x489] = 8
  fingerprint[CANBUS.vehicle][0x3DF] = 8
  cp = prepare(CarInterface.get_params(candidate, fingerprint, [], long, False, False), selected)
  cs = CarState(cp)
  cs.hands_on_level = 0
  cs.das_control = {'DAS_controlCounter': 3}
  cs.out = car.CarState.new_message(vEgo=20., vEgoRaw=20., canValid=True)
  unit = CarController(DBC[candidate], cp)
  unit.tesla_can = TeslaCAN(cp, Capture())
  return unit, cs


def commands(sends):
  return [message[2] for message in sends if message[0] == 'DAS_control']


@pytest.mark.parametrize('candidate', MODERN)
@pytest.mark.parametrize('selected', [False, True])
def test_the_real_controller_sends_the_stock_command_through_a_pedal_press_and_release(candidate, selected):
  unit, cs = controller(candidate, selected=selected)
  seen = []
  for frame in range(400):
    cs.out.gasPressed = 100 <= frame < 180
    cc = car.CarControl.new_message(enabled=True, longActive=not cs.out.gasPressed, actuators={'accel': .8})
    _, sends = unit.update(cc.as_reader(), cs, 1_000_000_000 + frame * 10_000_000)
    for values in commands(sends):
      assert values == pytest.approx(stock_command(4, .8, (frame // 4) % 8, 20.)), frame
      seen.append(frame)
  assert seen == list(range(0, 400, 4)), 'every fourth frame, pedal or not'


@pytest.mark.parametrize('candidate', MODERN)
def test_the_real_controller_cancels_with_the_stock_command_without_longitudinal_control(candidate):
  unit, cs = controller(candidate, selected=False, long=False)
  cc = car.CarControl.new_message(enabled=False, cruiseControl={'cancel': True})
  _, sends = unit.update(cc.as_reader(), cs, 1_000_000_000)
  assert commands(sends) == [pytest.approx(stock_command(13, 0, 4, 20.))], 'from the first frame, with the full jerk limits'
  _, sends = unit.update(car.CarControl.new_message().as_reader(), cs, 1_010_000_000)
  assert commands(sends) == []


@pytest.mark.parametrize('candidate', MODERN)
def test_the_car_state_and_controller_carry_nothing_for_a_pedal_hand_over(candidate):
  unit, cs = controller(candidate, selected=True)
  for holder in (unit, cs, unit.tesla_can):
    assert not [name for name in vars(holder) if 'handover' in name or 'gas_release' in name]
  with pytest.raises(ImportError):
    __import__('opendbc.car.tesla.pedal_handover')


@pytest.mark.parametrize('candidate', [CAR.TESLA_MODEL_3, CAR.TESLA_MODEL_Y])
@pytest.mark.parametrize('units,limit,expected', [(0, 30, 13.4112), (1, 50, 50 / 3.6), (0, 155, 0), (0, 0, 0)])
def test_actual_carstate_publishes_fused_limit(candidate, units, limit, expected):
  cp = CarInterface.get_non_essential_params(candidate)
  state = CarState(cp)
  parsers = state.get_can_parsers(cp)
  state.update(parsers)  # Register the production parser subscriptions.
  packers = {bus: CANPacker(parser.dbc.name) for bus, parser in parsers.items()}
  stamp = 0
  for i in range(30):
    stamp = 1_000_000_000 + i * 100_000_000
    for bus, parser in parsers.items():
      messages = []
      for address in sorted(parser.addresses):
        msg = parser.dbc.addr_to_msg[address].name
        values = {'DI_speedUnits': units} if msg == 'DI_state' else {'DAS_fusedSpeedLimit': limit} if msg == 'DAS_status' else {}
        messages.append(packers[bus].make_can_msg(msg, parser.bus, values))
      parser.update([stamp, messages])
    state.update(parsers)
  obs = state.dashboard_limit.observation
  assert obs.status == (Status.VALID if expected else Status.ABSENT if limit == 155 else Status.UNKNOWN)
  assert obs.speed_mps == pytest.approx(expected)
  evidence = SimpleNamespace(status=obs.status.value, speedMps=obs.speed_mps, observedMonoTime=obs.observed_ns,
                             validUntilMonoTime=obs.valid_until_ns, episode=obs.episode, producerSessionId='real-parser')
  result = dashboard_observation(evidence, car_fingerprint=str(candidate), session_id='drive', now_ns=stamp)
  assert result.kind == (ObservationKind.VALID if expected else ObservationKind.ABSENT if limit == 155 else ObservationKind.UNKNOWN)
  if expected:
    stale = dashboard_observation(evidence, car_fingerprint=str(candidate), session_id='drive', now_ns=obs.valid_until_ns + 1)
    assert stale.kind is ObservationKind.STALE
