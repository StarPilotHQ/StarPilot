"""Factory/parser/Card regressions for root's coordinated native runtime."""
from types import SimpleNamespace
import pytest
from opendbc.can import CANPacker
from opendbc.car import Bus, gen_empty_fingerprint
from opendbc.car.mazda.interface import CarInterface
from opendbc.car.mazda.values import CAR, DBC
from opendbc.car.mazda.tests.test_stock_startup import complete_frames, exercise_startup
from openpilot.starpilot.car.mazda import dashboard

SUPPORTED = (CAR.MAZDA_CX5, CAR.MAZDA_CX5_2022, CAR.MAZDA_CX9_2021)


@pytest.mark.parametrize('identity', list(CAR))
@pytest.mark.parametrize('alpha,release', ((False, False), (True, False), (False, True), (True, True)))
def test_all_factory_variants_preserve_stock_ownership(identity, alpha, release):
  cp = CarInterface.get_params(identity, gen_empty_fingerprint(), [], alpha, release, False)
  assert cp.dashcamOnly is (identity not in SUPPORTED)
  assert cp.pcmCruise and not cp.openpilotLongitudinalControl and cp.mass > 1000
  assert cp.steerActuatorDelay == pytest.approx(0.1) and cp.steerLimitTimer == pytest.approx(0.8)
  assert cp.safetyConfigs[0].safetyParam == 0 and len(cp.safetyConfigs) == 1
  assert type(CarInterface(cp).CC).__module__ == 'opendbc.car.mazda.carcontroller'


@pytest.mark.parametrize('identity', SUPPORTED)
@pytest.mark.parametrize('aol', (False, True))
def test_actual_card_composes_original_camera_observation(identity, aol, monkeypatch):
  from openpilot.cereal import messaging

  def probe(card, ci, controls, saved):
    mono, offset = [100_000_000_000], 2_000_000_000
    monkeypatch.setattr(dashboard.time, 'monotonic_ns', lambda: mono[0])
    monkeypatch.setattr(dashboard.time, 'clock_gettime_ns', lambda clock: mono[0] + offset)
    create = messaging.new_message
    def new_message(service=None, size=None, **kwargs):
      kwargs.setdefault('logMonoTime', mono[0])
      return create(service, size, **kwargs)
    monkeypatch.setattr(messaging, 'new_message', new_message)
    sent, send = [], card.pm.send
    def capture(service, event):
      if service == 'starpilotCarState':
        event = event.to_bytes()
        sent.append(messaging.log_from_bytes(event).as_builder())
      return send(service, event)
    monkeypatch.setattr(card.pm, 'send', capture)
    packer, frozen = CANPacker(DBC[identity][Bus.pt]), card.CP.to_dict()
    value = 0
    for tick in range(1, 80):
      mono[0] += 10_000_000
      rx = complete_frames(packer, card.CP, tick, main=False)
      if 41 <= tick <= 58:
        value = (list(range(16)) + [9, 0])[tick - 41]
        rx.append((0x35F, bytes((0, 0, 0, value << 4, 0, 0, 0, 0)), 2))
      state = ci.update([(mono[0] + offset, rx)])
      if tick < 41:
        continue
      assert state.canValid and not state.canTimeout
      sent.clear()
      card.state_publish(state, None)
      assert len(sent) == 1
      event = sent[0]
      assert event.valid and event.logMonoTime == mono[0]
      payload = event.starpilotCarState
      assert payload.dashboardStopSign == int(value == 9)
    assert card.CP.to_dict() == frozen == controls.CP.to_dict()
  exercise_startup(identity, False, False, aol, monkeypatch, probe=probe)


@pytest.mark.parametrize('bus,length', ((0, 8), (1, 8), (2, 4), (2, 7), (2, 9), (2, 64)))
def test_wrong_bus_and_dlc_cannot_create_camera_evidence(bus, length):
  cp = CarInterface.get_params(SUPPORTED[0], gen_empty_fingerprint(), [], False, False, False)
  ci = CarInterface(cp)
  ci.update([(100_000_000_000, [(0x35F, bytes(length), bus)])])
  assert ci.CS.dashboard_stop_sign is None


@pytest.mark.parametrize('age', (-1, 0, 150_000_000, 150_000_001))
@pytest.mark.parametrize('health', ('healthy', 'invalid', 'timeout'))
def test_composition_preserves_other_evidence(age, health, monkeypatch):
  from openpilot.cereal import messaging
  cp = CarInterface.get_params(SUPPORTED[0], gen_empty_fingerprint(), [], False, False, False)
  now, offset = 100_000_000_000, 2_000_000_000
  monkeypatch.setattr(dashboard.time, 'monotonic_ns', lambda: now)
  monkeypatch.setattr(dashboard.time, 'clock_gettime_ns', lambda clock: now + offset)
  message = messaging.new_message('starpilotCarState', valid=True, logMonoTime=now)
  message.starpilotCarState.sourceCarStateMonoTime = now - 1
  message.starpilotCarState.learningApplication.profileSequence = 7
  state = SimpleNamespace(canValid=health != 'invalid', canTimeout=health == 'timeout')
  parsed = SimpleNamespace(dashboard_stop_sign=(True, now + offset - age))
  assert dashboard.compose(cp, parsed, state, message, car_state_stamp=now) is message
  assert message.valid and message.logMonoTime == now
  assert message.starpilotCarState.sourceCarStateMonoTime == now - 1
  assert message.starpilotCarState.learningApplication.profileSequence == 7
  valid = health == 'healthy' and 0 <= age <= 150_000_000
  assert message.starpilotCarState.dashboardStopSign == int(valid)


@pytest.mark.parametrize('identity', list(CAR))
def test_all_six_dom_dashboard_identities_observe_without_new_actuation(identity, monkeypatch):
  cp = CarInterface.get_params(identity, gen_empty_fingerprint(), [], False, False, False)
  ci = CarInterface(cp)
  before = cp.to_dict()
  now, offset = 100_000_000_000, 2_000_000_000
  monkeypatch.setattr(dashboard.time, 'monotonic_ns', lambda: now)
  monkeypatch.setattr(dashboard.time, 'clock_gettime_ns', lambda clock: now + offset)
  packer = CANPacker(DBC[identity][Bus.pt])
  for tick in range(1, 41):
    source = now + offset - (40 - tick) * 10_000_000
    rx = complete_frames(packer, cp, tick, main=False)
    rx.append((0x35F, bytes((0, 0, 0, 0x90, 0, 0, 0, 0)), 2))
    state = ci.update([(source, rx)])
  assert state.canValid and not state.canTimeout
  assert ci.CS.dashboard_stop_sign == (True, now + offset)
  message = dashboard.compose(cp, ci.CS, state, None, car_state_stamp=now)
  assert message.starpilotCarState.dashboardStopSign == 1
  assert cp.to_dict() == before
  assert cp.dashcamOnly is (identity not in SUPPORTED)
  assert cp.pcmCruise and not cp.openpilotLongitudinalControl
