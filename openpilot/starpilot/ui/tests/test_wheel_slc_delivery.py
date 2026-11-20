"""Wheel intent survives UI freshness misses without extending its lifetime."""
from collections import deque
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import pytest

from opendbc.car.structs import car
from openpilot.cereal import messaging
from openpilot.common.params import Params
from openpilot.starpilot.controllers.wheel_actions import LIFETIME_NS, WheelConsumer, WheelPublisher
from openpilot.starpilot.ui.slc_action_dispatch import SlcActionDispatcher
from openpilot.starpilot.ui.tests.test_runtime_panel_actions import runtime_app
from openpilot.starpilot.ui.tests.test_runtime_snapshot import BOOT_OFFSET_NS, NOW, ui_fake


DRIVE = NOW - 2_000_000_000


class Publisher:
  def __init__(self):
    self.events = []

  def send(self, service, event):
    self.events.append((service, messaging.log_from_bytes(event.to_bytes())))


@pytest.fixture
def context(tmp_path):
  ui = ui_fake()
  ui.params = Params(str(tmp_path))
  ui.params.put("MainCruiseButtonControl", 10, block=True)
  ui.CP = car.CarParams(brand="hyundai", carFingerprint="GENESIS_GV60_EV_1ST_GEN",
                       openpilotLongitudinalControl=True)
  ui.sm["deviceState"].startedMonoTime = DRIVE
  ui.sm["carState"].canValid, ui.sm["carState"].canTimeout = True, False
  ui.sm.put("controlsState", NS(longControlState="pid"))
  ui.sm["slcState"].frameMonoTime = NOW
  ui.sm["slcState"].actionSequenceId = 0
  session = runtime_app.StarShellSession.__new__(runtime_app.StarShellSession)
  session.adapter = runtime_app.RuntimeSnapshotAdapter(ui)
  session._wheel_sock = object()
  session._wheel_pending = deque()
  session._wheel_consumer = WheelConsumer()
  output = Publisher()
  clock = [NOW]
  session.slc_actions = SlcActionDispatcher(output, session._live_slc_message, clock=lambda: clock[0])
  ingress = deque()
  favorite_actions = Mock(return_value={})
  session._native_favorite_actions = favorite_actions
  owner = WheelPublisher()

  def publish(action=10, publisher=owner, at=NOW):
    pub = Publisher()
    publisher.publish((("MainCruiseButtonControl" if action else "", action),), ui.CP, pub,
                      now_ns=at, drive_id=DRIVE, source_car_ns=at, source_control_ns=at)
    event = pub.events[0][1]
    ingress.append(event)
    return event

  def refresh(now):
    clock[0] = now
    for name in ui.sm.messages:
      ui.sm.logMonoTime[name] = now + (BOOT_OFFSET_NS if name == "pandaStates" else 0)
      ui.sm.recv_time[name] = now / 1e9
    ui.sm["slcState"].frameMonoTime = now

  def poll(now):
    with patch.object(runtime_app, "ui_state", ui), \
         patch.object(runtime_app.messaging, "recv_one_or_none", side_effect=lambda _: ingress.popleft() if ingress else None), \
         patch.object(session, "_favorite_authority", return_value=True), \
         patch.object(session, "_native_favorite_actions", side_effect=favorite_actions), \
         patch("openpilot.starpilot.ui.runtime_snapshot._clock_pair",
               return_value=(now, now + BOOT_OFFSET_NS, BOOT_OFFSET_NS)):
      session._poll_wheel(now)

  return NS(ui=ui, session=session, output=output, clock=clock, ingress=ingress,
            publish=publish, refresh=refresh, poll=poll, favorite_actions=favorite_actions)


@pytest.mark.parametrize("service", ("carState", "carControl", "controlsState", "selfdriveState", "slcState"))
@pytest.mark.parametrize("phase_ms", (1, 17, 33))
def test_one_press_retries_when_next_ui_frame_has_fresh_sources(context, service, phase_ms):
  c = context
  c.publish()
  c.ui.sm.logMonoTime[service] = NOW - 201_000_000
  c.poll(NOW)
  assert not c.output.events
  assert c.session._wheel_pending
  c.refresh(NOW + phase_ms * 1_000_000)
  c.poll(c.clock[0])
  assert len(c.output.events) == 1
  assert str(c.output.events[0][1].slcAction.kind) == "adopt"
  assert c.output.events[0][1].slcAction.wheelAdoptMonoTime == NOW
  assert not c.session._wheel_pending
  c.poll(c.clock[0])
  assert len(c.output.events) == 1


def test_second_dispatch_read_can_retry_without_consuming_press(context):
  c = context
  c.publish()
  c.clock[0] = NOW + 201_000_000
  c.poll(NOW)
  assert not c.output.events
  assert c.session._wheel_pending
  c.refresh(NOW + 220_000_000)
  c.poll(c.clock[0])
  assert len(c.output.events) == 1


def test_heartbeat_does_not_retire_a_waiting_action(context):
  c = context
  c.publish()
  c.publish(0, at=NOW + 10_000_000)
  c.ui.sm.logMonoTime["controlsState"] = NOW - 201_000_000
  c.poll(NOW + 10_000_000)
  assert not c.output.events
  c.refresh(NOW + 33_000_000)
  c.poll(c.clock[0])
  assert len(c.output.events) == 1
  assert c.session._wheel_consumer.sequence == 2


@pytest.mark.parametrize("delay", (LIFETIME_NS, LIFETIME_NS + 1, LIFETIME_NS * 2))
def test_wait_does_not_extend_original_wheel_expiry(context, delay):
  c = context
  c.publish()
  c.ui.sm.valid["controlsState"] = False
  c.poll(NOW)
  c.poll(NOW + LIFETIME_NS)
  assert not c.session._wheel_pending
  c.ui.sm.valid["controlsState"] = True
  c.refresh(NOW + delay)
  c.poll(c.clock[0])
  assert not c.output.events


@pytest.mark.parametrize("denial", ("mapping", "drive", "cp", "can", "ignition", "pending", "slc_session", "source_future"))
def test_waiting_action_revalidates_current_context(context, denial):
  c = context
  c.publish()
  c.ui.sm.valid["controlsState"] = False
  c.poll(NOW)
  c.ui.sm.valid["controlsState"] = True
  c.refresh(NOW + 33_000_000)
  if denial == "mapping":
    c.ui.params.put("MainCruiseButtonControl", 0, block=True)
  elif denial == "drive":
    c.ui.sm["deviceState"].startedMonoTime += 1
  elif denial == "cp":
    c.ui.CP.pcmCruise = True
  elif denial == "can":
    c.ui.sm["carState"].canValid = False
  elif denial == "ignition":
    c.ui.sm["pandaStates"][0].ignitionLine = False
  elif denial == "pending":
    c.ui.sm["slcState"].hasPending = True
  elif denial == "slc_session":
    c.ui.sm["slcState"].sessionId = ""
  else:
    c.ui.sm.logMonoTime["carState"] = c.clock[0] + 1
  c.poll(c.clock[0])
  assert not c.output.events
  if denial == "pending":
    assert not c.session._wheel_pending
    c.ui.sm["slcState"].hasPending = False
    c.poll(c.clock[0])
    assert not c.output.events


def test_new_producer_session_retires_old_waiting_action(context):
  c = context
  c.publish()
  c.ui.sm.valid["controlsState"] = False
  c.poll(NOW)
  c.publish(0, publisher=WheelPublisher(), at=NOW + 10_000_000)
  c.refresh(NOW + 33_000_000)
  c.poll(c.clock[0])
  assert not c.output.events
  assert not c.session._wheel_pending


def test_duplicate_action_is_published_once(context):
  c = context
  event = c.publish()
  c.ingress.append(event)
  c.poll(NOW)
  assert len(c.output.events) == 1


@pytest.mark.parametrize("brand,fingerprint,key", (("hyundai", "GENESIS_GV60_EV_1ST_GEN", "MainCruiseButtonControl"),
                                                   ("hyundai", "KIA_STINGER", "MainCruiseButtonControl"),
                                                   ("gm", "CHEVROLET_BOLT_EUV", "MainCruiseButtonControl"),
                                                   ("honda", "HONDA_CIVIC", "MainCruiseButtonControl")))
def test_unconsumed_preview_keeps_sequence_until_delivery(context, brand, fingerprint, key):
  c = context
  c.ui.CP.brand, c.ui.CP.carFingerprint = brand, fingerprint
  event = c.publish()
  consumer = c.session._wheel_consumer
  for _ in range(3):
    assert consumer.accept(event, c.ui.params, c.ui.CP, c.ui.sm, now_ns=NOW, consume=False).key == key
    assert consumer.sequence == 0
  assert consumer.accept(event, c.ui.params, c.ui.CP, c.ui.sm, now_ns=NOW).action == 10
  assert consumer.accept(event, c.ui.params, c.ui.CP, c.ui.sm, now_ns=NOW) is None


def test_future_source_wait_does_not_let_heartbeat_overtake(context):
  c = context
  c.publish(at=NOW + 10_000_000)
  c.publish(0, at=NOW + 20_000_000)
  c.poll(NOW + 20_000_000)
  assert not c.output.events
  c.refresh(NOW + 33_000_000)
  c.poll(c.clock[0])
  assert len(c.output.events) == 1
  assert c.session._wheel_consumer.sequence == 2


def test_existing_slc_duplicate_guard_still_rejects_a_second_press(context):
  c = context
  c.publish()
  c.poll(NOW)
  assert len(c.output.events) == 1
  c.refresh(NOW + 100_000_000)
  c.publish(at=c.clock[0])
  c.poll(c.clock[0])
  c.refresh(NOW + 350_000_001)
  c.poll(c.clock[0])
  assert len(c.output.events) == 1
  assert not c.session._wheel_pending


@pytest.mark.parametrize("delay,delivered", ((249_000_000, True), (250_000_000, True), (250_000_001, False)))
def test_fresh_retry_respects_original_inclusive_deadline(context, delay, delivered):
  c = context
  c.publish()
  c.ui.sm.valid["controlsState"] = False
  c.poll(NOW)
  c.ui.sm.valid["controlsState"] = True
  c.refresh(NOW + delay)
  c.poll(c.clock[0])
  assert bool(c.output.events) == delivered
  assert not c.session._wheel_pending


def test_adopt_does_not_resolve_unrelated_favorites_before_freshness_read(context):
  context.publish()
  context.poll(NOW)
  assert len(context.output.events) == 1
  context.favorite_actions.assert_not_called()
