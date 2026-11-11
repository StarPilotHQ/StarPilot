"""Four-byte radar filter button evidence reaches existing mapping/personality owners."""
import pytest
from opendbc.car import Bus, structs
from opendbc.car.toyota import radar_filter
from opendbc.car.toyota.carstate import CarState
from opendbc.car.toyota.interface import CarInterface
from opendbc.car.toyota.values import CAR, ToyotaFlags
from opendbc.car.toyota.toyotacan import toyota_checksum
from openpilot.cereal import messaging
from openpilot.starpilot.aol.vehicle import policy_for
from openpilot.starpilot.controllers.wheel_actions import WheelPublisher
from openpilot.starpilot.car.toyota.tests.test_pedal_parent_configurations import sources
from openpilot.starpilot.car.toyota.tests.test_wheel_actions import feature, native_session as native_session
from openpilot.starpilot.controllers.tests.test_wheel_actions import Publisher, DRIVE, NOW

CARS = (CAR.TOYOTA_CAMRY, CAR.TOYOTA_CHR, CAR.TOYOTA_CHR_TSS2, CAR.TOYOTA_RAV4_TSS2_2022)


def factory(identity=CAR.TOYOTA_CAMRY, *, stock=False, topology='2FF4'):
  fp, fw = sources(identity, topology, 'catalog')
  cp = CarInterface.get_params(identity, fp, fw, True, False, False)
  if stock:
    assert radar_filter.prepare_stock(cp)
  return cp


def advertisement(pressed):
  data = bytearray([128 if pressed else 0, 0, 0x50, 0])
  data[-1] = toyota_checksum(0x2FF, None, data)
  return bytes(data)


class Buttons:
  def __init__(self, cp):
    self.owner = CarState(cp)
    self.parsers = CarState.get_can_parsers(cp)

  def sample(self, pressed=False, *, now=NOW, bus=0, data=None):
    self.parsers[Bus.pt].update([(now, [(0x2FF, advertisement(pressed) if data is None else data, bus)])])
    result = self.owner.update(self.parsers)
    # Auxiliary edge bench; full recorded CAN replay separately verifies transport health.
    result.canValid, result.canTimeout = True, False
    return result


@pytest.mark.parametrize('identity', CARS)
@pytest.mark.parametrize('stock', (False, True))
def test_factory_exposes_only_discovered_four_byte_buttons(tmp_path, identity, stock):
  from openpilot.common.params import Params
  cp = factory(identity, stock=stock)
  assert cp.flags & ToyotaFlags.FILTER_BUTTONS
  assert radar_filter.configured(cp) and policy_for(cp).runtime_supported
  assert cp.safetyConfigs[0].safetyParam == (0x1249 if stock else 0x1049)
  rows = {r.key: r for r in feature(Params(str(tmp_path)), cp).rows()}
  assert {'wheel:DistanceButtonControl', 'wheel:LongDistanceButtonControl',
          'wheel:VeryLongDistanceButtonControl'} <= rows.keys()
  assert ('wheel:LKASButtonControl' in rows) == (identity != CAR.TOYOTA_CAMRY)
  assert 'wheel:CancelButtonControl' not in rows
  main = rows['wheel:MainCruiseButtonControl']
  assert main.value == 'Factory cruise / AOL' and not main.available and not main.choices
  assert not feature(Params(str(tmp_path)), cp).choices('MainCruiseButtonControl')
  if not stock:
    assert 'Cycle personality' in rows['wheel:DistanceButtonControl'].choices


@pytest.mark.parametrize('identity', CARS)
def test_twoaa_does_not_invent_four_byte_button_receiver(tmp_path, identity):
  from openpilot.common.params import Params
  cp = factory(identity, topology='2AA8')
  assert not cp.flags & ToyotaFlags.FILTER_BUTTONS
  rows = {r.key for r in feature(Params(str(tmp_path)), cp).rows()}
  assert 'wheel:DistanceButtonControl' not in rows


@pytest.mark.parametrize('bus,data', [(1, advertisement(True)), (2, advertisement(True)),
                                     (130, advertisement(True)), (0, bytes([128]) + bytes(7))])
def test_foreign_bus_echo_and_eight_byte_stream_do_not_press_distance(bus, data):
  bench = Buttons(factory())
  assert not bench.sample().buttonEvents
  assert not bench.sample(now=NOW + 10_000_000, bus=bus, data=data).buttonEvents


def test_recorded_camry_button_shape_reaches_saved_personality_once(native_session, monkeypatch):
  runtime, session, ui, _, _ = native_session
  cp = ui.CP = factory()
  ui.has_longitudinal_control = True
  ui.params.put('LongitudinalPersonality', 1, block=True)
  bench, source = Buttons(cp), WheelPublisher(ui.params)
  source.observe(ui.params, cp, bench.sample(now=NOW - 20_000_000), now_ns=NOW - 20_000_000, drive_id=DRIVE)
  source.observe(ui.params, cp, bench.sample(True, now=NOW - 10_000_000), now_ns=NOW - 10_000_000, drive_id=DRIVE)
  commands = source.observe(ui.params, cp, bench.sample(False), now_ns=NOW, drive_id=DRIVE)
  assert commands == (('DistanceButtonControl', 1),), 'RADAR_FILTER_DISTANCE_PRESS_DROPPED'
  sender = Publisher()
  source.publish(commands, cp, sender, now_ns=NOW, drive_id=DRIVE, source_car_ns=NOW, source_control_ns=NOW)
  def save(value):
    ui.params.put('LongitudinalPersonality', value, block=True)
    return True
  session._native_favorite_actions = lambda: session.native_favorite_actions(lambda: True, save, lambda: True)
  queue = [messaging.log_from_bytes(sender.events[-1][1])]
  monkeypatch.setattr(runtime.messaging, 'recv_one_or_none', lambda _: queue.pop(0) if queue else None)
  session._poll_wheel(NOW)
  assert ui.params.get('LongitudinalPersonality') == 0
  session._poll_wheel(NOW)
  assert ui.params.get('LongitudinalPersonality') == 0


@pytest.mark.parametrize('stock', (False, True))
def test_camry_stock_lda_does_not_press_lkas(tmp_path, stock):
  from opendbc.can import CANPacker
  from opendbc.car.toyota.values import DBC
  from openpilot.common.params import Params
  from openpilot.starpilot.controllers.wheel_actions import button_reported
  cp = factory(stock=stock)
  bench = Buttons(cp)
  camera = CANPacker(DBC[cp.carFingerprint][Bus.pt])
  for index, lda in enumerate((0, 1, 2, 0, 1, 0)):
    now = NOW + index * 10_000_000
    bench.parsers[Bus.cam].update([(now, [camera.make_can_msg('LKAS_HUD', 2, {'LDA_ON_MESSAGE': lda})])])
    state = bench.sample(index == 2, now=now)
    assert not any(event.type == structs.CarState.ButtonEvent.Type.lkas for event in state.buttonEvents), 'CAMRY_LDA_IS_NOT_LKAS_BUTTON'
    if index in (2, 3):
      assert any(event.type == structs.CarState.ButtonEvent.Type.gapAdjustCruise and event.pressed == (index == 2) for event in state.buttonEvents)
  assert not button_reported(cp, 'LKASButtonControl')
  rows = {row.key for row in feature(Params(str(tmp_path)), cp).rows()}
  assert 'wheel:LKASButtonControl' not in rows
  assert 'wheel:DistanceButtonControl' in rows
  assert radar_filter.configured(cp) and policy_for(cp).runtime_supported
  assert cp.safetyConfigs[0].safetyParam == (0x1249 if stock else 0x1049)
