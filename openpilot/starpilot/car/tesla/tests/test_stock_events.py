from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from opendbc.car import structs
from opendbc.car.tesla.interface import CarInterface
from opendbc.car.tesla.tests.test_preap_stock import params
from opendbc.car.tesla.tests.preap_stream import PreAPStream
from openpilot.cereal import log
from openpilot.selfdrive.selfdrived.events import Events, ET
from openpilot.selfdrive.selfdrived.alertmanager import AlertManager
from openpilot.selfdrive.selfdrived.selfdrived import SelfdriveD
from openpilot.starpilot.controllers.mode_actions import SwitchbackCooldown
from openpilot.starpilot.longitudinal.force_stop_alert import HoldAlertState
from openpilot.starpilot.car.tesla.stock_events import StockCruiseConsumer, publish_stock_status


class Capture:
  def send(self, service, message):
    assert service == 'slcCruiseEvent'
    self.raw = message.to_bytes()


class Sources(dict):
  valid = {'carState': True}
  alive = {'carState': True}


def receive(cp, internal, state, *, previous=False, sequence=1, session='card', stamp=20_000_000_000):
  publisher = Capture()
  publish_stock_status(publisher, cp, internal, session=session, sequence=sequence, car_state_stamp=stamp, valid=True)
  consumer = StockCruiseConsumer(cp)
  consumer.session, consumer.previous = session, previous
  sm = Sources(carState=state)
  sm.logMonoTime = {'carState': stamp}
  with log.Event.from_bytes(publisher.raw) as envelope:
    return consumer.update(sm, envelope=envelope, now_ns=stamp, session=session)


@pytest.mark.parametrize('previous,current,text,sound', (
  (False, True, 'Tesla Cruise Engaged', 'engage'),
  (True, False, 'Tesla Cruise Disengaged', 'disengage'),
))
def test_serialized_stock_edges_reach_actual_alert_consumer(previous, current, text, sound):
  cp = params()
  state = structs.CarState(canValid=True)
  internal = SimpleNamespace(di_cruise_state='ENABLED' if current else 'OVERRIDE', cruiseEnabled=False, enableLongControl=False)
  alerts = receive(cp, internal, state, previous=previous)
  alert = next(a for a in alerts if a.alert_text_1 == text)
  assert alert.audible_alert == getattr(log.SelfdriveState.AudibleAlert, sound)


@pytest.mark.parametrize('requested,cruise,expected', ((True, 0, True), (True, 1, False), (True, 2, False), (False, 0, False)))
def test_actual_parser_not_armed_condition_and_serialized_alert(requested, cruise, expected):
  cp = params()
  ci, stream = CarInterface(cp), PreAPStream()
  ci.update([])
  for tick in range(1, 75):
    lever = 2 if requested and tick in (61, 71) else 0
    state = ci.update([(1_000_000_000 + tick * 10_000_000, stream.frames(tick, lever=lever, cruise=cruise))])
  alerts = receive(cp, ci.CS.preap, state)
  assert any(a.alert_text_1 == 'Arm Stock Cruise to Enable Speed Control' and a.event_type == ET.PERMANENT for a in alerts) == expected


def test_enabled_to_override_reports_disengage_without_fake_pcm_disable():
  cp = params()
  ci, stream = CarInterface(cp), PreAPStream()
  ci.update([])
  for tick in range(1, 65):
    ci.update([(1_000_000_000 + tick * 10_000_000, stream.frames(tick, cruise=2))])
  current = ci.update([(1_650_000_000, stream.frames(65, cruise=4))])
  assert current.cruiseState.enabled
  alerts = receive(cp, ci.CS.preap, current, previous=True)
  assert any(a.alert_text_1 == 'Tesla Cruise Disengaged' for a in alerts)


@pytest.mark.parametrize('mutation', ('invalid', 'wrong_cp', 'wrong_session', 'wrong_frame', 'expired', 'replay', 'can_error'))
def test_transport_rejects_unbound_or_stale_stock_receipt(mutation):
  cp = params()
  state = structs.CarState(canValid=True)
  publisher = Capture()
  stamp = 20_000_000_000
  publish_stock_status(publisher, cp, SimpleNamespace(di_cruise_state='ENABLED', cruiseEnabled=False, enableLongControl=False),
                       session='card', sequence=1, car_state_stamp=stamp, valid=True)
  consumer = StockCruiseConsumer(cp)
  sm = Sources(carState=state)
  sm.logMonoTime = {'carState': stamp}
  with log.Event.from_bytes(publisher.raw) as decoded:
    envelope = decoded.as_builder()
  session, now = 'card', stamp
  if mutation == 'invalid':
    envelope.valid = False
  elif mutation == 'wrong_cp':
    envelope.slcCruiseEvent.teslaStockCruise.cpFingerprint = 'wrong'
  elif mutation == 'wrong_session':
    session = 'other'
  elif mutation == 'wrong_frame':
    sm.logMonoTime['carState'] += 1
  elif mutation == 'expired':
    now += 100_000_001
  elif mutation == 'replay':
    consumer.session, consumer.sequence = 'card', 1
  elif mutation == 'can_error':
    state.canValid = False
  assert not consumer.update(sm, envelope=envelope.as_reader(), now_ns=now, session=session)


def test_actual_card_publisher_socket_reaches_stock_alert_consumer(monkeypatch):
  import time
  from openpilot.cereal import messaging
  from opendbc.car.tesla.tests.test_preap_startup import exercise_startup
  from opendbc.car.tesla.values import CAR

  def probe(card, ci, controls, saved):
    consumer = StockCruiseConsumer(card.CP)
    subscriber = messaging.sub_sock('carState', timeout=100, conflate=True)
    stream = PreAPStream()
    ci.update([])
    for tick in range(1, 65):
      state = ci.update([(1_000_000_000 + tick * 10_000_000, stream.frames(tick, cruise=1))])
    assert state.canValid and not state.cruiseState.enabled
    tick = 64
    for cruise, expected in ((2, 'Tesla Cruise Engaged'), (4, 'Tesla Cruise Disengaged')):
      observed = False
      for _ in range(30):
        tick += 1
        state = ci.update([(1_000_000_000 + tick * 10_000_000, stream.frames(tick, cruise=cruise))])
        card.state_publish(state, None)
        received = messaging.recv_one(subscriber)
        if received is None:
          time.sleep(0.01)
          continue
        sm = Sources(carState=received.carState)
        sm.logMonoTime = {'carState': received.logMonoTime}
        alerts = consumer.poll(sm, now_ns=received.logMonoTime, session=card.slc_producer_session)
        if any(alert.alert_text_1 == expected for alert in alerts):
          observed = True
          break
        time.sleep(0.01)
      assert observed, (cruise, expected)
      if cruise == 4:
        assert state.cruiseState.enabled
        assert not any(a.event_type in (ET.ENABLE, ET.USER_DISABLE) for a in alerts)

  exercise_startup(CAR.TESLA_MODEL_S_PREAP, False, False, False, monkeypatch, probe=probe)


@pytest.mark.parametrize('previous,current,not_armed,text,sound,duration,category', (
  (False, True, False, 'Tesla Cruise Engaged', 'engage', 80, ET.WARNING),
  (True, False, False, 'Tesla Cruise Disengaged', 'disengage', 80, ET.WARNING),
  (False, False, True, 'Arm Stock Cruise to Enable Speed Control', 'none', 20, ET.PERMANENT),
))
def test_bound_custom_receipt_reaches_selfdrived_alert_manager_without_core_events(
    previous, current, not_armed, text, sound, duration, category):
  from openpilot.cereal import messaging

  cp, state = params(), structs.CarState(canValid=True)
  internal = SimpleNamespace(di_cruise_state='ENABLED' if current else 'OFF',
                             cruiseEnabled=not_armed, enableLongControl=not_armed)
  alerts = receive(cp, internal, state, previous=previous)
  from openpilot.starpilot.aol.runtime import AxisDecision
  drive = SelfdriveD.__new__(SelfdriveD)
  drive.aol_axis_decision = AxisDecision()
  drive.CP, drive.enabled, drive.is_metric = cp, False, False
  drive.personality = log.LongitudinalPersonality.standard
  vars(drive)['state_machine'] = SimpleNamespace(current_alert_types=[category], soft_disable_timer=0)
  drive.events, drive.AM = Events(), AlertManager()
  drive.tesla_stock_alerts = alerts
  sm = Sources(deviceState=messaging.new_message('deviceState').deviceState)
  sm.frame = 1
  vars(drive)['sm'] = sm
  drive.params = Mock(get=lambda _key: None)
  drive.switchback_capable, drive.switchback_setting_ns = False, 20_000_000_000
  drive.switchback_cooldown_ns, drive.switchback_cooldown = 300_000_000_000, SwitchbackCooldown()
  drive.force_stop_hold_alert = HoldAlertState()
  with patch('openpilot.selfdrive.selfdrived.selfdrived.time.monotonic_ns', return_value=20_000_000_000):
    drive.update_alerts(state)
  alert = drive.AM.current_alert
  assert (alert.alert_text_1, alert.audible_alert, alert.duration) == (text, getattr(log.SelfdriveState.AudibleAlert, sound), duration)
  assert not drive.events.to_msg()
  drive.tesla_stock_alerts = []
  sm.frame += duration + 2
  with patch('openpilot.selfdrive.selfdrived.selfdrived.time.monotonic_ns', return_value=20_000_000_000):
    drive.update_alerts(state)
  assert drive.AM.current_alert.alert_text_1 == ''
