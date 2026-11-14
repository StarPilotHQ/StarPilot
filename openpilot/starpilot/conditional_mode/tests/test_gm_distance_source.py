"""Physical GM Traffic packets, gestures and current-drive ownership."""

from opendbc.can import CANPacker
from opendbc.car.gm.distance_button import GMDistanceButtons
from openpilot.starpilot.conditional_mode.manual import Button, ButtonTracker, Gesture, Press


def test_distance_original_counter_thresholds_and_no_repeated_release():
  packer = CANPacker('gm_global_a_powertrain_generated')
  for count, expected in ((1, [Press.SHORT]), (49, [Press.SHORT]), (50, [Press.LONG]),
                           (249, [Press.LONG]), (250, [Press.LONG, Press.VERY_LONG])):
    source, tracker = GMDistanceButtons(), ButtonTracker()
    gestures = []
    for tick, held in enumerate([False] + [True] * count + [False, False]):
      packet = packer.make_can_msg('ASCMSteeringButton', 0, {'DistanceButton': int(held)})
      gestures.extend(tracker.observe_distance_source(source.update([(1_000_000_000 + tick * 10_000_000, [packet])])))
    assert gestures == [Gesture(Button.DISTANCE, press) for press in expected]


def test_distance_loss_and_held_start_require_new_neutral():
  packer = CANPacker('gm_global_a_powertrain_generated')
  source, tracker = GMDistanceButtons(), ButtonTracker()
  def observe(stamp, held):
    packet = packer.make_can_msg('ASCMSteeringButton', 0, {'DistanceButton': int(held)})
    return tracker.observe_distance_source(source.update([(stamp, [packet])]))
  assert not observe(1_000_000_000, True)
  assert not observe(1_030_000_000, False)
  assert not observe(1_060_000_000, True)
  assert not tracker.observe_distance_source(source.update([]))
  assert not observe(1_090_000_000, False)
  assert not observe(1_120_000_000, True)
  assert observe(1_150_000_000, False) == (Gesture(Button.DISTANCE, Press.SHORT),)


def test_card_distance_packet_to_traffic_owner_and_launch_revocation(tmp_path, monkeypatch):
  from types import SimpleNamespace
  from openpilot.common.params import Params
  from openpilot.cereal import messaging
  from opendbc.car import structs
  from opendbc.car.gm.tests.test_bolt_pedal import params as pedal_params
  from opendbc.car.gm.values import CAR
  from openpilot.selfdrive.car.card import Car
  from openpilot.starpilot.conditional_mode.card_input import conditional_traffic_candidate
  from openpilot.starpilot.conditional_mode.runtime_settings import ConditionalSettingsOwner
  from openpilot.starpilot.conditional_mode.traffic import TrafficOwner
  from openpilot.starpilot.conditional_mode.traffic_launch import TrafficLaunchState
  from openpilot.starpilot.conditional_mode.tests.test_traffic import FakeSM, NOW, DRIVE

  params = Params(str(tmp_path))
  params.put('DistanceButtonControl', 6, block=True)
  cp = pedal_params(CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL, setting=True, pedal=True)
  source, tracker, owner, settings = GMDistanceButtons(), ButtonTracker(), TrafficOwner(), ConditionalSettingsOwner(params)
  sm = FakeSM()
  sm.payloads['deviceState'].startedMonoTime = DRIVE
  cs = structs.CarState(canValid=True)
  sent = []
  card = Car.__new__(Car)
  card.conditional_replay = True
  card.slc_cruise_event_id = card.traffic_event_sequence = 0
  card.slc_producer_session = 'a' * 32
  monkeypatch.setattr(card, 'pm', SimpleNamespace(send=lambda service, event: sent.append(event)), raising=False)
  packer = CANPacker('gm_global_a_powertrain_generated')
  for tick, held in enumerate((False, True, False)):
    now = NOW + tick * 30_000_000
    boot = now + 2_000_000_000
    sm.advance(now)
    sm.logMonoTime['carState'] = now
    packet = packer.make_can_msg('ASCMSteeringButton', 0, {'DistanceButton': int(held)})
    observation = source.update([(boot, [packet])])
    card.traffic_receipt = conditional_traffic_candidate(cs, tracker, params, settings, cp, sm, now_ns=now, distance=observation)
    assert card.traffic_receipt is not None
    cs_send = messaging.new_message('carState', valid=True)
    cs_send.logMonoTime = now
    card.publish_traffic_receipt(cs_send)
    verdict = owner.sample(sent[-1], params=params, settings=settings, sm=sm, cp=cp,
                           drive_id=DRIVE, now_mono_ns=now, now_boot_ns=boot)
    assert verdict.effective is (tick == 2)
  replayed = owner.sample(sent[-1], params=params, settings=settings, sm=sm, cp=cp,
                          drive_id=DRIVE, now_mono_ns=now, now_boot_ns=boot)
  assert replayed.effective is True  # Repeated toggle cannot toggle twice.
  sm.payloads['carControl'].longActive = False
  paused = owner.sample(None, params=params, settings=settings, sm=sm, cp=cp,
                        drive_id=DRIVE, now_mono_ns=now, now_boot_ns=boot)
  assert paused.effective is None
  sm.payloads['carControl'].longActive = True
  status = owner.attach(None, verdict, now_ns=now, drive_id=DRIVE, profile_target_ready=True, profile_reason='qualified')
  sm.payloads['slcState'] = status.slcState
  for table in (sm.seen, sm.valid, sm.alive):
    table['slcState'] = True
  sm.logMonoTime['slcState'] = now
  sm.recv_time['slcState'] = now / 1e9
  launch = TrafficLaunchState(params)
  assert launch.sample(sm, now_ns=now, boot_ns=boot, drive_id=DRIVE) is True
  assert launch.sample(sm, now_ns=now + 101_000_000, boot_ns=boot + 101_000_000, drive_id=DRIVE) is None
  sm.advance(now + 301_000_000)
  lost = owner.sample(None, params=params, settings=settings, sm=sm, cp=cp,
                      drive_id=DRIVE, now_mono_ns=now + 301_000_000, now_boot_ns=boot + 301_000_000)
  assert lost.effective is True
  assert lost.requested
  assert lost.source_boot_ns == 0
  sm.valid['carControl'] = False
  denied = owner.sample(None, params=params, settings=settings, sm=sm, cp=cp,
                        drive_id=DRIVE, now_mono_ns=now + 301_000_000, now_boot_ns=boot + 301_000_000)
  assert denied.effective is None
  # A saved assignment cannot create a new toggle after a source loss.
  assert conditional_traffic_candidate(cs, tracker, params, settings, cp, sm,
                                       now_ns=now + 301_000_000, distance=source.update([])) is None


def test_gm_traffic_planner_lifecycle_starts_factory_conditional_owner(tmp_path):
  from unittest.mock import Mock, patch
  from openpilot.common.params import Params
  from opendbc.car.gm.tests.test_bolt_pedal import params as pedal_params
  from opendbc.car.gm.values import CAR
  from openpilot.selfdrive.controls import plannerd

  params = Params(str(tmp_path))
  cp = pedal_params(CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL, setting=True, pedal=True)
  params.put('CarParams', cp.to_bytes(), block=True)
  class EndLoop(Exception):
    pass
  sm = Mock()
  sm.update.side_effect = EndLoop
  with (patch.object(plannerd, 'Params', return_value=params),
        patch('openpilot.common.params.Params', return_value=params), patch.object(plannerd, 'config_realtime_process'),
        patch.object(plannerd, 'ConditionalPlannerHost') as conditional,
        patch.object(plannerd.messaging, 'sub_sock') as sockets,
        patch.object(plannerd.messaging, 'SubMaster', return_value=sm) as subscriber,
        patch.object(plannerd.messaging, 'PubMaster') as publisher,
        patch.dict('os.environ', {}, clear=True)):
    try:
      plannerd.main()
    except EndLoop:
      pass
    else:
      raise AssertionError('planner loop did not start')
    conditional.assert_called_once()
    assert 'deviceState' in subscriber.call_args.args[0]
    assert 'slcState' in publisher.call_args.args[0]
    assert any(call.args[0] == 'slcCruiseEvent' for call in sockets.call_args_list)


def test_gm_map_cache_reads_edges_thresholds_epoch_and_one_second(tmp_path):
  from unittest.mock import patch
  from openpilot.common.params import Params
  from openpilot.starpilot.conditional_mode.manual import WheelMapCache, read_button_map
  params = Params(str(tmp_path))
  source, cache = GMDistanceButtons(), WheelMapCache(include_ioniq_media=False)
  packer = CANPacker('gm_global_a_powertrain_generated')
  with patch('openpilot.starpilot.conditional_mode.manual.read_button_map', wraps=read_button_map) as read:
    for tick in range(34):
      packet = packer.make_can_msg('ASCMSteeringButton', 0, {'DistanceButton': 0})
      now = 1_000_000_000 + tick * 30_000_000
      cache.sample(params, source.update([(now, [packet])]), now)
    assert read.call_count == 1
    now += 30_000_000
    cache.sample(params, source.update([(now, [packet])]), now)
    assert read.call_count == 2  # One-second audit while continuously neutral.
    params.put('DistanceButtonControl', 6, block=True)
    packet = packer.make_can_msg('ASCMSteeringButton', 0, {'DistanceButton': 1})
    now += 30_000_000
    assert cache.sample(params, source.update([(now, [packet])]), now).distance == 6
    assert read.call_count == 3
    for tick in range(1, 18):
      cache.sample(params, source.update([(now + tick * 30_000_000, [packet])]), now + tick * 30_000_000)
    assert read.call_count == 4  # First held duration boundary, not every physical packet.


def test_qualified_cc_and_factory_distance_source_reaches_traffic_owner(tmp_path):
  from openpilot.common.params import Params
  from opendbc.car import structs
  from opendbc.car.gm.profiles import profiles_supported
  from opendbc.car.gm.values import CAR
  from openpilot.starpilot.tests.test_gm_feature_runtime import configured
  from openpilot.starpilot.conditional_mode.card_input import conditional_traffic_candidate
  from openpilot.starpilot.conditional_mode.runtime_settings import ConditionalSettingsOwner
  from openpilot.starpilot.conditional_mode.tests.test_traffic import FakeSM, NOW, DRIVE

  params = Params(str(tmp_path))
  params.put('DistanceButtonControl', 6, block=True)
  packer = CANPacker('gm_global_a_powertrain_generated')
  for identity, alpha in ((CAR.CHEVROLET_VOLT_CC, False), (CAR.CHEVROLET_BOLT_CC_2017, False),
                          (CAR.CHEVROLET_BOLT_ACC_2022_2023, True)):
    cp = configured(params, identity, alpha=alpha)
    assert profiles_supported(cp)
    source, tracker = GMDistanceButtons(), ButtonTracker()
    settings = ConditionalSettingsOwner(params)
    sm = FakeSM()
    sm.payloads['deviceState'].startedMonoTime = DRIVE
    cs = structs.CarState(canValid=True)
    receipts = []
    for tick, held in enumerate((False, True, False)):
      now = NOW + tick * 30_000_000
      sm.advance(now)
      sm.logMonoTime['carState'] = now
      packet = packer.make_can_msg('ASCMSteeringButton', 0, {'DistanceButton': int(held)})
      observation = source.update([(now + 2_000_000_000, [packet])])
      receipts.append(conditional_traffic_candidate(cs, tracker, params, settings, cp, sm,
                                                   now_ns=now, distance=observation))
    assert all(receipt is not None for receipt in receipts)
    assert receipts[-1][0] is True
    assert receipts[0][0] is False
