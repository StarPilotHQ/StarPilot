"""Wheel bindings follow all CarParams values rather than Cap'n Proto allocation layout."""
from collections import deque

import pytest

from opendbc.car import structs
from opendbc.car.hyundai.values import CAR, HyundaiFlags
from openpilot.cereal import messaging
from openpilot.starpilot.controllers import wheel_actions
from openpilot.starpilot.controllers.tests.test_native_wheel_callbacks import native_session  # noqa: F401
from openpilot.starpilot.controllers.tests.test_wheel_actions import DRIVE, NOW, Publisher, services


def equivalent_params():
  original = structs.CarParams(brand='hyundai', carFingerprint=CAR.HYUNDAI_IONIQ_5_PE,
                              flags=int(HyundaiFlags.CANFD | HyundaiFlags.EV), pcmCruise=True,
                              steerControlType='angle', safetyConfigs=[{'safetyModel': 'hyundaiCanfd', 'safetyParam': 0x5491}])
  rebuilt = structs.CarParams.new_message(**original.as_reader().to_dict())
  assert original.as_reader().to_dict() == rebuilt.as_reader().to_dict()
  assert original.to_bytes() != rebuilt.to_bytes()
  return original, rebuilt


def test_equivalent_serializations_keep_the_entire_vehicle_binding():
  original, rebuilt = equivalent_params()
  before = original.to_bytes(), rebuilt.to_bytes()
  assert wheel_actions.cp_fingerprint(original) == wheel_actions.cp_fingerprint(rebuilt.as_reader())
  assert before == (original.to_bytes(), rebuilt.to_bytes())


@pytest.mark.parametrize('field,value', [('pcmCruise', False), ('openpilotLongitudinalControl', True),
                                       ('flags', 0), ('alternativeExperience', 32), ('minSteerSpeed', 5.0),
                                       ('carFingerprint', CAR.HYUNDAI_IONIQ_6)])
def test_actual_configuration_changes_still_revoke_binding(field, value):
  original, changed = equivalent_params()
  setattr(changed, field, value)
  assert wheel_actions.cp_fingerprint(original) != wheel_actions.cp_fingerprint(changed)


def test_nested_safety_changes_and_mutation_of_cached_object_revoke_binding():
  original, changed = equivalent_params()
  before = wheel_actions.cp_fingerprint(changed)
  changed.safetyConfigs[0].safetyParam ^= 1
  assert wheel_actions.cp_fingerprint(changed) != before
  assert wheel_actions.cp_fingerprint(changed) != wheel_actions.cp_fingerprint(original)


def test_canonicalization_is_cached_by_full_bytes_with_a_bound():
  wheel_actions._cp_fingerprint.cache_clear()
  original, rebuilt = equivalent_params()
  for _ in range(100):
    wheel_actions.cp_fingerprint(original)
    wheel_actions.cp_fingerprint(rebuilt)
  info = wheel_actions._cp_fingerprint.cache_info()
  assert info.misses == 2 and info.hits == 198 and info.maxsize == 64
  for value in range(70):
    original.mass = value
    wheel_actions.cp_fingerprint(original)
  assert wheel_actions._cp_fingerprint.cache_info().currsize == 64


@pytest.mark.parametrize('enabled', [False, True])
def test_paddle_still_reaches_native_bookmark_after_card_materializes_defaults(native_session, monkeypatch, enabled):  # noqa: F811
  runtime, session, ui, _, calls = native_session
  original, rebuilt = equivalent_params()
  ui.CP = original
  ui.params.put('LeftPaddleButtonControl', 8, block=True)
  ui.params.put_bool('NostalgiaMode', False, block=True)
  ui.sm = services(original)
  ui.sm.recv_frame = dict.fromkeys(ui.sm, ui.started_frame + 1)
  ui.sm['carControl'].enabled = enabled
  owner, sender = wheel_actions.WheelPublisher(), Publisher()
  for cp in (original, rebuilt):
    owner.publish((('LeftPaddleButtonControl', 8),), cp, sender, now_ns=NOW, drive_id=DRIVE,
                  source_car_ns=NOW, source_control_ns=NOW)
  queue = deque(messaging.log_from_bytes(raw) for _, raw in sender.events)
  monkeypatch.setattr(runtime.messaging, 'recv_one_or_none', lambda _: queue.popleft() if queue else None)
  session._poll_wheel(NOW)
  assert calls == ['bookmark', 'bookmark']
