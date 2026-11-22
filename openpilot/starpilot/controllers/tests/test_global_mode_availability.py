"""Generic mode requests retain their original live source and acknowledgement guards."""
from unittest.mock import Mock

import pytest

from opendbc.car.car_helpers import interfaces
from opendbc.car.ford.values import CAR as FORD
from opendbc.car.honda.values import CAR as HONDA
from opendbc.car.mazda.values import CAR as MAZDA
from opendbc.car.nissan.values import CAR as NISSAN
from opendbc.car.subaru.values import CAR as SUBARU
from opendbc.car.toyota.values import CAR as TOYOTA
from opendbc.car.volkswagen.values import CAR as VW
from openpilot.cereal import messaging
from openpilot.starpilot.controllers.mode_actions import (
  ModeActionOwner, ModeActionPublisher, ModeIntent, SwitchbackStatusOwner,
  authority, producer_available, publish_switchback,
)
from openpilot.starpilot.controllers.tests.test_mode_actions import DRIVE, NOW, fixture, request


VEHICLES = (TOYOTA.TOYOTA_RAV4_TSS2, HONDA.HONDA_CIVIC, MAZDA.MAZDA_CX5,
            MAZDA.MAZDA_CX5_2022, MAZDA.MAZDA_CX9_2021,
            SUBARU.SUBARU_IMPREZA, FORD.FORD_ESCAPE_MK4, NISSAN.NISSAN_XTRAIL,
            VW.VOLKSWAGEN_GOLF_MK7)


def prepared(candidate):
  sm, _ = fixture()
  cp = interfaces[candidate].get_non_essential_params(candidate)
  # Lateral-only operation is allowed even when the car owns longitudinal.
  cp.openpilotLongitudinalControl = False
  sm['carControl'].longActive = sm['carControl'].enabled = False
  sm['selfdriveState'].enabled = False
  heartbeat = publish_switchback(None, ModeIntent(DRIVE, False, False), session='b' * 32,
                                sequence=1, now_ns=NOW, source_car_control_ns=sm.logMonoTime['carControl'])
  sm['slcState'] = heartbeat.slcState
  for name in ('seen', 'valid', 'alive'):
    getattr(sm, name)['slcState'] = True
  sm.logMonoTime['slcState'], sm.recv_time['slcState'] = NOW, NOW / 1e9
  return sm, cp


@pytest.mark.parametrize('candidate', VEHICLES)
def test_generic_switchback_publisher_owner_and_status_accept_actual_vehicle(candidate):
  sm, cp = prepared(candidate)
  sender, publisher, owner = Mock(), ModeActionPublisher(), ModeActionOwner()
  assert authority(sm, cp, NOW, 'switchback')
  assert producer_available(sm, now_ns=NOW)
  assert publisher.dispatch('switchback', sm, cp, sender, now_ns=NOW)
  event = messaging.log_from_bytes(sender.send.call_args.args[1].to_bytes())
  assert event.slcAction.controllerMode.carFingerprint == cp.carFingerprint
  assert owner.update(event, sm, cp, now_ns=NOW)
  assert not owner.update(event, sm, cp, now_ns=NOW)  # No duplicate toggle.
  intent = owner.sample('switchback', sm, cp, now_ns=NOW)
  assert intent.requested and intent.effective
  state = publish_switchback(None, intent, session='c' * 32, sequence=1,
                            now_ns=NOW, source_car_control_ns=sm.logMonoTime['carControl'])
  serialized = messaging.log_from_bytes(state.to_bytes())
  assert SwitchbackStatusOwner().sample(serialized, drive_id=DRIVE, now_ns=NOW)
  assert not SwitchbackStatusOwner().sample(serialized, drive_id=DRIVE, now_ns=NOW + 100_000_001)
  sm['carControl'].latActive = False
  paused = owner.sample('switchback', sm, cp, now_ns=NOW)
  assert paused.requested and not paused.effective
  assert not publisher.dispatch('traffic', sm, cp, sender, now_ns=NOW + 101_000_000)


@pytest.mark.parametrize('fault', ('passive', 'dashcamOnly', 'notCar', 'canInvalid', 'canTimeout',
                                 'latInactive', 'offroad', 'carStateStale', 'carStateInvalid',
                                 'carControlStale', 'carControlUnseen', 'deviceStale', 'producerStale',
                                 'producerWrongDrive'))
def test_generic_switchback_still_rejects_live_faults_and_missing_producer(fault):
  sm, cp = prepared(TOYOTA.TOYOTA_RAV4_TSS2)
  if fault in ('passive', 'dashcamOnly', 'notCar'):
    setattr(cp, fault, True)
  elif fault == 'canInvalid':
    sm['carState'].canValid = False
  elif fault == 'canTimeout':
    sm['carState'].canTimeout = True
  elif fault == 'latInactive':
    sm['carControl'].latActive = False
  elif fault == 'offroad':
    sm['deviceState'].started = False
  elif fault == 'carStateStale':
    sm.logMonoTime['carState'] = NOW - 250_000_001
  elif fault == 'carStateInvalid':
    sm.valid['carState'] = False
  elif fault == 'carControlStale':
    sm.logMonoTime['carControl'] = NOW - 250_000_001
  elif fault == 'carControlUnseen':
    sm.seen['carControl'] = False
  elif fault == 'deviceStale':
    sm.logMonoTime['deviceState'] = NOW - 1_000_000_001
  elif fault == 'producerStale':
    sm.logMonoTime['slcState'] = NOW - 100_000_001
  else:
    sm['slcState'].switchbackMode.driveStartMonoTime = DRIVE + 1
  sender = Mock()
  assert not ModeActionPublisher().dispatch('switchback', sm, cp, sender, now_ns=NOW)
  sender.send.assert_not_called()


def test_generic_mode_owner_rejects_wrong_identity_expiry_and_inactive_traffic():
  sm, cp = prepared(TOYOTA.TOYOTA_RAV4_TSS2)
  event = request(sm, cp, 'switchbackModeToggle')
  wrong = request(sm, cp, 'switchbackModeToggle')
  wrong.slcAction.controllerMode.carFingerprint = 'another-car'
  assert not ModeActionOwner().update(wrong, sm, cp, now_ns=NOW)
  assert not ModeActionOwner().update(event, sm, cp, now_ns=NOW + 250_000_001)
  assert not ModeActionOwner().update(request(sm, cp, 'trafficModeToggle'), sm, cp, now_ns=NOW)


def test_actual_dashcam_mazda_cannot_dispatch_generic_switchback():
  sm, cp = prepared(MAZDA.MAZDA_CX9)
  assert cp.dashcamOnly
  assert producer_available(sm, now_ns=NOW)
  assert not authority(sm, cp, NOW, 'switchback')
  sender = Mock()
  assert not ModeActionPublisher().dispatch('switchback', sm, cp, sender, now_ns=NOW)
  sender.send.assert_not_called()
