"""Finalized GM startup ownership and independent-axis caller lifecycle."""
import json
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from opendbc.can import CANPacker
from opendbc.car import Bus, structs
from opendbc.car.gm.aol import GM_AOL_WORDS, GM_BASE_GATEWAY_IDS, qualified_gm
from opendbc.car.gm.lateral import lane_centering_supported
from opendbc.car.gm.interface import CarInterface
from opendbc.car.gm.tests.test_bolt_cc import params as bolt_params, feed as feed_car, control, native
from opendbc.safety.tests.libsafety import libsafety_py
from opendbc.car.gm.tests.test_cc_gateway_stock import silverado_stock_params
from opendbc.car.gm.tests.test_bolt_factory_acc import factory_params
from opendbc.car.gm.tests.test_bolt_volt_configurations import ordinary_params
from opendbc.car.gm.tests.test_volt_camera_control import camera_params
from opendbc.car.gm.tests.test_volt_camera_removed import removed_params
from opendbc.car.gm.tests.test_volt_sdgm_control import sdgm_params
from opendbc.car.gm.values import CAR, DBC, ORDINARY_ASCM_CAR, ORDINARY_SDGM_CAR, ORDINARY_CAMERA_CAR, ORDINARY_CC_CAR, is_volt_one_pedal
from openpilot.cereal import messaging
from openpilot.common.params import Params
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.selfdrive.car.card import Car
from openpilot.selfdrive.controls.controlsd import Controls
from openpilot.starpilot.aol.runtime import decide_axes
from openpilot.starpilot.aol.transport_pause import TransportPause
from openpilot.starpilot.aol.vehicle import policy_for
from openpilot.starpilot.aol.wire import IntentState, SafetyState, encode_safety
from openpilot.starpilot.lateral.tests.test_lane_runtime import feed
from openpilot.starpilot.tests.test_volt_disable_longitudinal import configured
from openpilot.starpilot.vehicle_preferences import VehicleStartupPreferences
from openpilot.starpilot.aol.tests.gm_bolt_recovery import exercise_bolt_cancel_recovery


BOLT_IDS = (CAR.CHEVROLET_BOLT_CC_2017, CAR.CHEVROLET_BOLT_CC_2018_2021,
            CAR.CHEVROLET_BOLT_CC_2022_2023, CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL)


def _configurations():
  from opendbc.car.gm.tests.test_ordinary_cc import malibu_hybrid_params
  from opendbc.car.gm.startup_preferences import prepare_disable_longitudinal
  for pedal in (False, True):
    for removed in (False, True):
      for alternate in (False, True):
        for radar in ((False, True) if pedal else (False,)):
          cp = malibu_hybrid_params(pedal=pedal, removed=removed, alternate=alternate, radar=radar)
          yield cp
          disabled = cp.as_reader().as_builder()
          prepare_disable_longitudinal(disabled, True)
          yield disabled
  from opendbc.car.gm.tests.test_bolt_pedal import params as pedal_params
  for identity in BOLT_IDS:
    cp = pedal_params(identity, pedal=True, removed=True)
    yield cp
    disabled = cp.as_reader().as_builder()
    VehicleStartupPreferences(disable_bolt_long=True).prepare(disabled)
    yield disabled
  for identity in sorted(GM_BASE_GATEWAY_IDS):
    cp = ordinary_params(identity, radar=True)
    yield cp
    if identity == CAR.BUICK_LACROSSE:
      disabled = cp.as_reader().as_builder()
      VehicleStartupPreferences(disable_bolt_long=True).prepare(disabled)
      yield disabled
      held = cp.as_reader().as_builder()
      VehicleStartupPreferences(gm_auto_hold=True).prepare(held)
      yield held
  for identity in BOLT_IDS:
    for pedal in (False, True):
      for removed in (False, True):
        if identity == BOLT_IDS[-1] and pedal and removed:
          continue
        cp = bolt_params(identity, present=pedal, pedal=pedal, removed=removed)
        yield cp
        if pedal:
          disabled = cp.as_reader().as_builder()
          VehicleStartupPreferences(disable_bolt_long=True).prepare(disabled, fingerprints={2: {0x180: 4}})
          yield disabled
  for alpha in (False, True):
    yield factory_params(alpha=alpha)
    yield camera_params(alpha=alpha)
    yield removed_params(alpha=alpha)
    yield removed_params(alpha=alpha, alternate=True)
    for c9 in (False, True):
      yield sdgm_params(alpha=alpha, brake_c9=c9)
    for c9 in (False, True):
      for radar in (False, True):
        yield ordinary_params(CAR.CHEVROLET_VOLT_ASCM, alpha=alpha, sascm=True,
                              accelerator=not c9, radar=radar)
  for variant in ('be', 'f1', 'cc'):
    cp = configured(variant)
    yield cp
    disabled = cp.as_reader().as_builder()
    VehicleStartupPreferences(disable_bolt_long=True).prepare(disabled)
    yield disabled

  from opendbc.car.gm.tests.test_ascm_intercept import params as intercept_params
  from opendbc.car.gm.tests.test_ordinary_camera import params as ordinary_camera
  from opendbc.car.gm.tests.test_ordinary_camera_removed import params as ordinary_removed
  from opendbc.car.gm.tests.test_conventional_pedal import params as conventional_pedal
  from opendbc.car.gm.tests.test_silverado_cc_pedal import params as silverado_pedal
  from opendbc.car.gm.tests.test_camera_acc_pedal import params as camera_pedal
  for identity in sorted(ORDINARY_ASCM_CAR | ORDINARY_SDGM_CAR):
    for alpha in (False, True):
      for c9 in (False, True):
        for radar in (False, True):
          yield intercept_params(identity, sascm=True, accelerator=not c9, radar=radar, alpha=alpha)
  for identity in sorted(ORDINARY_CAMERA_CAR):
    for alpha in (False, True):
      for analog in (False, True):
        yield ordinary_camera(identity, alpha=alpha, be=analog)
        yield ordinary_removed(identity, alpha=alpha, analog=analog)
    for camera in (False, True):
      for be in (False, True):
        for release in (False, True):
          yield camera_pedal(identity, camera=camera, be=be, release=release)
  from opendbc.car.gm.tests.test_ordinary_cc import malibu_f1_params
  for release in (False, True):
    cp = malibu_f1_params(release=release)
    yield cp
    disabled = cp.as_reader().as_builder()
    VehicleStartupPreferences(disable_bolt_long=True).prepare(disabled)
    yield disabled
  for identity in sorted(ORDINARY_CC_CAR):
    yield intercept_params(identity)
    for removed in (False, True):
      for disabled in (False, True):
        yield conventional_pedal(identity, removed=removed, disabled=disabled)
  from opendbc.car.gm.tests.test_camera_acc_pedal import fingerprint as pedal_fingerprint
  from opendbc.car.gm.tests.test_volt_transitions import (volt_ascm_pedal_params, volt_cc_pedal_params,
                                                       volt_gateway_pedal_params, volt_sdgm_pedal_params)
  for camera in (False, True):
    for be in (False, True):
      observed = pedal_fingerprint(camera=camera, be=be)
      observed[0].update({0xBD: 7, 0x232: 8})
      for release in (False, True):
        yield CarInterface.get_params(CAR.CHEVROLET_VOLT_CAMERA, observed, [], False, release, False)
        yield volt_gateway_pedal_params(camera=camera, be=be, release=release)
  for be in (False, True):
    for radar in (False, True):
      for release in (False, True):
        yield volt_ascm_pedal_params(be=be, radar=radar, release=release)
        yield volt_sdgm_pedal_params(be=be, radar=radar, release=release)
        for removed in (False, True):
          yield volt_cc_pedal_params(be=be, radar=radar, release=release, removed=removed)
  for removed in (False, True):
    for disabled in (False, True):
      yield silverado_pedal(removed=removed, disabled=disabled)


def configurations():
  from opendbc.car.gm.tests.test_volt_cc_f1 import params as f1_params
  yield f1_params()
  yield silverado_stock_params()
  for cp in _configurations():
    yield cp
    selected = cp.as_reader().as_builder()
    VehicleStartupPreferences(gm_auto_hold=True).prepare(selected)
    if selected.safetyConfigs[0].safetyParam != cp.safetyConfigs[0].safetyParam:
      yield selected
    for paired in (False, True):
      selected = cp.as_reader().as_builder()
      VehicleStartupPreferences(volt_one_pedal=True, gm_auto_hold=paired).prepare(selected)
      if is_volt_one_pedal(selected):
        yield selected


class TestGmAol(unittest.TestCase):
  def test_actual_final_cp_registry_and_isolation(self):
    words = set()
    for cp in configurations():
      with self.subTest(identity=cp.carFingerprint, word=hex(cp.safetyConfigs[0].safetyParam)):
        self.assertTrue(qualified_gm(cp))
        words.add(cp.safetyConfigs[0].safetyParam)
        self.assertTrue(policy_for(cp).normal_runtime_supported)
        for field in ('passive', 'dashcamOnly', 'notCar'):
          denied = cp.as_reader().as_builder()
          setattr(denied, field, True)
          self.assertFalse(qualified_gm(denied))
        denied = cp.as_reader().as_builder()
        denied.safetyConfigs[0].safetyModel = 'noOutput'
        self.assertFalse(qualified_gm(denied))
        denied = cp.as_reader().as_builder()
        denied.alternativeExperience = 33
        self.assertFalse(qualified_gm(denied))
    self.assertEqual(words, GM_AOL_WORDS, {"missing": GM_AOL_WORDS - words, "unexpected": words - GM_AOL_WORDS})

  def test_base_gateway_aol_preserves_tuning_and_lane_capability(self):
    for identity in GM_BASE_GATEWAY_IDS:
      cp = ordinary_params(identity, radar=True)
      with self.subTest(identity=identity):
        self.assertTrue(qualified_gm(cp))
        self.assertFalse(lane_centering_supported(cp))
        selected = cp.as_reader().as_builder()
        selected.alternativeExperience = 32
        self.assertTrue(qualified_gm(selected))
        self.assertFalse(lane_centering_supported(selected))
        for field, value in (('pcmCruise', True), ('radarUnavailable', True), ('alphaLongitudinalAvailable', True),
                             ('networkLocation', 'fwdCamera'), ('transmissionType', 'direct'), ('flags', 1)):
          denied = cp.as_reader().as_builder()
          setattr(denied, field, value)
          self.assertFalse(qualified_gm(denied))
        denied = cp.as_reader().as_builder()
        denied.safetyConfigs[0].safetyParam = 0x80
        self.assertEqual(qualified_gm(denied), identity == CAR.BUICK_LACROSSE)
        denied = cp.as_reader().as_builder()
        denied.openpilotLongitudinalControl = False
        self.assertEqual(qualified_gm(denied), identity == CAR.BUICK_LACROSSE)

  @staticmethod
  def card(cp, settings):
    settings.put_bool('OpenpilotEnabledToggle', True, block=True)
    def get_car(*args, pre_create_hook, **kwargs):
      selected = pre_create_hook(cp.as_reader().as_builder(), cp.carFingerprint, {}, [])
      return CarInterface(selected)
    with patch('openpilot.selfdrive.car.card.messaging.recv_one_retry', return_value=SimpleNamespace(can=[1])), \
         patch('openpilot.selfdrive.car.card.get_car', side_effect=get_car):
      return Car()

  def test_automatic_bolt_identity_reaches_real_card_factory_and_native(self):
    from opendbc.car.can_definitions import CanData
    from opendbc.car import car_helpers
    from opendbc.car.gm.values import CarControllerParams
    from opendbc.car.gm.pedal_capability import automatic_bolt_candidate
    from opendbc.car.mock.values import CAR as MOCK
    from opendbc.car.gm.tests.test_bolt_cc import setup
    from openpilot.starpilot.vehicle_selection import encode

    # Recorded pre-controller PT fingerprints: 2020 nonACC pedal and Zik Gen2
    # ACC pedal. DLCs are literal; stock370 bytes are the first recorded true
    # camera observation, not a fabricated native/control grant.
    gen1 = {
      170: 8, 188: 8, 189: 7, 190: 6, 193: 8, 197: 8, 201: 8, 209: 7, 211: 2,
      241: 6, 257: 8, 288: 5, 298: 8, 304: 1, 308: 4, 309: 8, 311: 8, 313: 8,
      320: 3, 322: 7, 328: 1, 352: 5, 353: 3, 368: 3, 381: 8, 384: 4, 386: 8,
      388: 8, 390: 7, 407: 7, 417: 7, 419: 1, 451: 8, 452: 8, 453: 6, 454: 8,
      456: 8, 463: 3, 479: 3, 481: 7, 485: 8, 489: 8, 493: 8, 495: 4, 497: 8,
      499: 3, 500: 6, 501: 8, 503: 2, 508: 8, 513: 6, 528: 5, 532: 6, 546: 7,
      550: 8, 554: 3, 558: 8, 560: 8, 562: 8, 564: 5, 566: 7, 567: 5, 568: 1,
      569: 3, 608: 8, 609: 6, 610: 6, 611: 6, 612: 8, 613: 8, 647: 3, 707: 8,
      711: 6, 717: 5, 753: 5, 761: 7, 800: 6, 810: 8, 840: 5, 842: 5, 844: 8,
      848: 4, 866: 4, 869: 4, 872: 1, 961: 8, 967: 4, 969: 8, 975: 2, 977: 8,
      979: 7, 985: 5, 988: 6, 989: 8, 995: 7, 1001: 8, 1005: 6, 1009: 8, 1013: 3,
      1017: 8, 1019: 2, 1020: 8, 1022: 1, 1105: 5, 1187: 4, 1217: 8, 1221: 5, 1223: 3,
      1225: 7, 1227: 4, 1233: 8, 1236: 8, 1243: 3, 1249: 8, 1257: 6, 1265: 8, 1275: 3,
      1279: 4, 1280: 4, 1300: 8, 1322: 6, 1323: 4, 1328: 4, 1904: 7, 1905: 7, 1906: 7,
      1907: 7, 1912: 7, 1913: 7, 1922: 7, 1927: 7, 2020: 8, 2028: 8,
    }
    gen2 = {
      189: 7, 190: 7, 193: 8, 197: 8, 201: 8, 209: 7, 211: 3, 241: 6, 288: 5,
      289: 8, 298: 8, 304: 3, 309: 8, 311: 8, 313: 8, 320: 4, 322: 7, 328: 1,
      352: 5, 381: 8, 384: 4, 386: 8, 388: 8, 451: 8, 452: 8, 453: 6, 458: 5,
      463: 3, 479: 3, 481: 7, 485: 8, 489: 8, 497: 8, 500: 6, 501: 8, 513: 6,
      528: 5, 532: 6, 560: 8, 562: 8, 566: 8, 608: 8, 609: 6, 610: 6, 611: 6,
      612: 8, 613: 8, 707: 8, 715: 8, 717: 5, 753: 5, 761: 7, 789: 5, 800: 6,
      810: 8, 840: 5, 842: 5, 844: 8, 848: 4, 869: 4, 880: 6, 977: 8, 1001: 8,
      1017: 8, 1020: 8, 1217: 8, 1221: 5, 1233: 8, 1249: 8, 1265: 8, 1280: 4, 1296: 4,
      1300: 8, 1930: 7,
    }
    safety = libsafety_py.libsafety
    release = safety.set_safety_hooks(int(structs.CarParams.SafetyModel.allOutput), 0) != 0
    stock_acc = (0x370, bytes.fromhex('000221820000'), 2)

    def receive_for(pt, camera_messages):
      packets = [CanData(address, bytes(length), 0) for address, length in pt.items()]
      packets += [CanData(0x180, bytes(4), 2), CanData(0x320, bytes(pt.get(0x320, 8)), 2)]
      packets += [CanData(address, data, bus) for address, data, bus in camera_messages]
      return lambda wait_for_one=False: [packets] if wait_for_one else []

    # Unknown, mixed stock modes, wrong bus/echo and malformed lengths cannot
    # manufacture ACC capability from the Gen2 alias.
    for camera in ([], [(0x370, bytes(6), 2)], [(0x370, b'\0\4' + bytes(4), 2)],
                   [(0x370, b'\0\5' + bytes(4), 2)], [stock_acc, (0x370, b'\0\4' + bytes(4), 2)],
                   [(stock_acc[0], stock_acc[1], 0)], [(stock_acc[0], stock_acc[1], 130)],
                   [(stock_acc[0], stock_acc[1][:5], 2)]):
      with self.subTest(camera=camera):
        candidate, fp = car_helpers.can_fingerprint(receive_for(gen2, camera))
        self.assertIsNone(candidate)
        self.assertEqual(fp[0], gen2)
    fp = {0: gen2}
    self.assertEqual(automatic_bolt_candidate(CAR.CHEVROLET_BOLT_CC_2022_2023, fp, {b'\0\4' + bytes(4)}),
                     CAR.CHEVROLET_BOLT_CC_2022_2023)
    malformed = {0: dict(gen2)}
    malformed[0][0x201] = 5
    self.assertIsNone(automatic_bolt_candidate(CAR.CHEVROLET_BOLT_CC_2018_2021, malformed, {stock_acc[1]}))
    malformed[0][0x201] = 6
    malformed[0][0x236] = 7
    self.assertIsNone(automatic_bolt_candidate(CAR.CHEVROLET_BOLT_CC_2018_2021, malformed, {stock_acc[1]}))
    no_pedal = {0: {address: length for address, length in gen2.items() if address != 0x201}}
    for alias in (CAR.CHEVROLET_BOLT_CC_2017, CAR.CHEVROLET_BOLT_CC_2018_2021, CAR.CHEVROLET_BOLT_EUV):
      self.assertIsNone(automatic_bolt_candidate(alias, no_pedal, set()))
      self.assertIsNone(automatic_bolt_candidate(alias, no_pedal, {b'\0\4' + bytes(4), b'\0\5' + bytes(4)}))
    self.assertEqual(automatic_bolt_candidate(CAR.CHEVROLET_BOLT_ACC_2022_2023, no_pedal, set()),
                     CAR.CHEVROLET_BOLT_ACC_2022_2023)
    self.assertIsNone(automatic_bolt_candidate(None, fp, {stock_acc[1]}))
    self.assertEqual(automatic_bolt_candidate(MOCK.MOCK, fp, {stock_acc[1]}), MOCK.MOCK)

    def startup(pt, forced, camera):
      with OpenpilotPrefix(), patch.dict(os.environ, {'SKIP_FW_QUERY': '1', 'FINGERPRINT': ''}):
        settings = Params()
        settings.put_bool('OpenpilotEnabledToggle', True, block=True)
        settings.put_bool('AlwaysOnLateral', True, block=True)
        settings.put_bool('AlphaLongitudinalEnabled', True, block=True)
        settings.put_bool('IsReleaseBranch', release, block=True)
        settings.put('VehicleSelection', json.loads(encode(None if forced is None else forced.value)), block=True)
        receive = receive_for(pt, camera)

        def discover(*args, **kwargs):
          return car_helpers.get_car(receive, *args[1:], **kwargs)

        with patch('openpilot.selfdrive.car.card.messaging.recv_one_retry', return_value=SimpleNamespace(can=[1])), \
             patch('openpilot.selfdrive.car.card.get_car', side_effect=discover):
          card = Car()
        self.assertTrue(settings.get_bool('FirmwareQueryDone'))
        self.assertFalse(card.ci_initialized)
        self.assertEqual(card.CP.alternativeExperience, 32)
        return card.CP

    stock_pt = dict(gen2)
    del stock_pt[0x201]
    cases = ((gen1, None, CAR.CHEVROLET_BOLT_CC_2018_2021, 0x9D, []),
             (gen2, None, CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL, 0x1CD, [stock_acc]),
             (gen2, CAR.CHEVROLET_BOLT_CC_2017, CAR.CHEVROLET_BOLT_CC_2017, 0xBD, [stock_acc]),
             (gen2, CAR.CHEVROLET_BOLT_CC_2018_2021, CAR.CHEVROLET_BOLT_CC_2018_2021, 0x9D, [stock_acc]),
             (gen2, CAR.CHEVROLET_BOLT_CC_2022_2023, CAR.CHEVROLET_BOLT_CC_2022_2023, 0x19D, [stock_acc]),
             (stock_pt, CAR.CHEVROLET_BOLT_ACC_2022_2023, CAR.CHEVROLET_BOLT_ACC_2022_2023,
              5 if release else 7, [stock_acc]),
             (gen2, CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL,
              CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL, 0x1CD, [stock_acc]))
    for pt, forced, identity, word, camera in cases:
      with self.subTest(forced=forced, identity=identity, release=release):
        cp = startup(pt, forced, camera)
        self.assertEqual(cp.carFingerprint, identity)
        self.assertEqual(cp.safetyConfigs[0].safetyParam, word)
        self.assertEqual(cp.fingerprintSource, structs.CarParams.FingerprintSource.fixed if forced else
                         structs.CarParams.FingerprintSource.can)
        self.assertTrue(cp.flags & 16)  # actual recorded BSM presence, not a manual flag assignment
        self.assertFalse(cp.dashcamOnly)
        self.assertFalse(cp.passive)
        self.assertAlmostEqual(cp.lateralTuning.torque.latAccelFactor, 1.2 if identity == CAR.CHEVROLET_BOLT_CC_2017 else 2.)
        self.assertAlmostEqual(cp.lateralTuning.torque.friction, .21 if identity == CAR.CHEVROLET_BOLT_CC_2017 else .13)
        self.assertEqual(CarControllerParams(cp).STEER_MAX, 450 if identity == CAR.CHEVROLET_BOLT_CC_2017 else 300)
        pedal = 0x201 in pt
        self.assertEqual(cp.openpilotLongitudinalControl, pedal or not release)
        self.assertEqual(cp.pcmCruise, not pedal and release)
        setup(cp)
        safety.set_alternative_experience(cp.alternativeExperience)
        self.assertEqual(safety.get_current_safety_mode(), int(structs.CarParams.SafetyModel.gm))
        self.assertEqual(safety.get_current_safety_param(), word)
        self.assertFalse(safety.get_controls_allowed())

  def test_marked_gm_transport_survives_master_off_restart(self):
    from openpilot.starpilot.feature_runtime import enabled
    with OpenpilotPrefix():
      settings = Params()
      settings.put_bool('AlwaysOnLateral', False, block=True)
      for cp in configurations():
        cp = cp.as_reader().as_builder()
        with self.subTest(identity=cp.carFingerprint, word=cp.safetyConfigs[0].safetyParam):
          cp.alternativeExperience = 0
          self.assertFalse(policy_for(cp).full_axis_runtime_required)
          self.assertFalse(enabled(settings, cp, 'aol', {'AOL_REPLAY_RUNTIME': '0'}))
          cp.alternativeExperience = 32
          self.assertTrue(policy_for(cp).full_axis_runtime_required)
          self.assertTrue(enabled(settings, cp, 'aol', {'AOL_REPLAY_RUNTIME': '0'}))
      marked = silverado_stock_params()
      marked.alternativeExperience = 32
      card = self.card(marked, settings)
      self.assertEqual(card.CP.alternativeExperience, 0)
      if card.aol_card_intent is not None:
        self.assertFalse(card.aol_card_intent.allowed_latch)

  def test_silverado_stock_card_publishes_lateral_only_aol_owner(self):
    with OpenpilotPrefix(), patch.dict(os.environ, {'SIMULATION': '1', 'AOL_REPLAY_RUNTIME': '0'}):
      settings = Params()
      settings.put_bool('AlwaysOnLateral', True, block=True)
      card = self.card(silverado_stock_params(), settings)
      self.assertFalse(card.CP.dashcamOnly)
      self.assertTrue(card.CP.pcmCruise)
      self.assertFalse(card.CP.openpilotLongitudinalControl)
      self.assertEqual(card.CP.safetyConfigs[0].safetyParam, 16)
      self.assertEqual(card.CP.alternativeExperience, 32)
      self.assertIsNotNone(card.aol_card_intent)
      cs = structs.CarState(canValid=True, gearShifter='drive', vEgo=20.)
      cs.cruiseState.available = True
      cs.cruiseState.enabled = False
      card.aol_card_intent.update(cs)
      self.assertTrue(card.aol_card_intent.allowed_latch)
      cs.gearShifter = 'reverse'
      card.aol_card_intent.update(cs)
      self.assertTrue(card.aol_card_intent.allowed_latch)
      cs.cruiseState.available = False
      card.aol_card_intent.update(cs)
      self.assertFalse(card.aol_card_intent.allowed_latch)

  def test_bolt_main_intent_ignores_unavailable_lkas_assignment(self):
    from openpilot.starpilot.aol.intent import read_settings
    from opendbc.car.gm.tests.test_bolt_pedal import params as pedal_params
    with OpenpilotPrefix(), patch.dict(os.environ, {'SIMULATION': '1', 'AOL_REPLAY_RUNTIME': '0'}):
      settings = Params()
      settings.put_bool('AlwaysOnLateral', True, block=True)
      settings.put('LKASButtonControl', 9, block=True)
      cp = pedal_params(CAR.CHEVROLET_BOLT_CC_2018_2021, pedal=True, camera=True)
      self.assertEqual(cp.safetyConfigs[0].safetyParam, 0x9D)
      selected = self.card(cp, settings)
      owner = selected.aol_card_intent
      cs = structs.CarState(canValid=True, gearShifter='drive', vEgo=20.)
      cs.cruiseState.available = True
      for _ in range(2):
        owner.settings = read_settings(settings)
        owner.update(cs)
        self.assertTrue(owner.allowed_latch)
        self.assertEqual(settings.get('LKASButtonControl'), 9)
      settings.put('MainCruiseButtonControl', 9, block=True)
      owner = self.card(cp, settings).aol_card_intent
      owner.update(cs)
      self.assertTrue(owner.allowed_latch)
      self.assertEqual(settings.get('MainCruiseButtonControl'), 9)
      cs.buttonEvents = [structs.CarState.ButtonEvent(type='mainCruise', pressed=True)]
      owner.update(cs)
      self.assertTrue(owner.allowed_latch)
      owner.update(cs, fault_active=True)
      self.assertFalse(owner.allowed_latch)

  def test_actual_card_startup_default_off_and_main_level_fault_recovery(self):
    with OpenpilotPrefix(), patch.dict(os.environ, {'SIMULATION': '1', 'AOL_REPLAY_RUNTIME': '0'}):
      settings = Params()
      settings.put_bool('AlwaysOnLateral', False, block=True)  # Explicit Off survives new-install On enrollment.
      cp = factory_params(alpha=False)
      ordinary = self.card(cp, settings)
      self.assertIsNone(ordinary.aol_card_intent)
      self.assertEqual(ordinary.CP.alternativeExperience, 0)
      settings.put_bool('AlwaysOnLateral', True, block=True)
      selected = self.card(cp, settings)
      self.assertEqual(selected.CP.safetyConfigs[0].safetyParam, cp.safetyConfigs[0].safetyParam)
      self.assertEqual(selected.CP.alternativeExperience, 32)
      self.assertFalse(selected.aol_card_intent.explicit_latch)
      cs = structs.CarState(canValid=True, gearShifter='drive', vEgo=20.)
      cs.cruiseState.available = True
      selected.aol_card_intent.update(cs)
      self.assertTrue(selected.aol_card_intent.allowed_latch)
      cs.steerFaultTemporary = True
      selected.aol_card_intent.update(cs, fault_active=False)
      self.assertTrue(selected.aol_card_intent.allowed_latch)
      cs.steerFaultTemporary = False
      cs.gearShifter = 'reverse'
      selected.aol_card_intent.update(cs, fault_active=False)
      self.assertTrue(selected.aol_card_intent.allowed_latch)
      self.assertFalse(selected.aol_card_intent.output(cs)[0])
      cs.gearShifter = 'drive'
      cs.steerFaultPermanent = True
      selected.aol_card_intent.update(cs, fault_active=True)
      self.assertFalse(selected.aol_card_intent.allowed_latch)
      cs.steerFaultPermanent = False
      selected.aol_card_intent.update(cs, fault_active=False)
      self.assertTrue(selected.aol_card_intent.allowed_latch)
      cs.cruiseState.available = False
      cs.canValid = False
      selected.aol_card_intent.update(cs, fault_active=False)
      cs.cruiseState.available = True
      cs.canValid = True
      selected.aol_card_intent.update(cs, fault_active=False)
      self.assertTrue(selected.aol_card_intent.allowed_latch)
      cs.cruiseState.available = False
      selected.aol_card_intent.update(cs, fault_active=False)
      cs.cruiseState.available = True
      selected.aol_card_intent.update(cs, fault_active=False)
      self.assertTrue(selected.aol_card_intent.allowed_latch)

  def test_actual_bolt_card_lagging_preserves_intent_but_withdraws_both_axes(self):
    from opendbc.car.gm.tests.test_bolt_pedal import params as pedal_params
    from openpilot.cereal import log
    from openpilot.selfdrive.selfdrived.events import Events, ET
    from openpilot.starpilot.aol.intent import disarming_fault
    from openpilot.starpilot.nostalgia import aol_no_entry

    with OpenpilotPrefix(), patch.dict(os.environ, {'SIMULATION': '1', 'AOL_REPLAY_RUNTIME': '0'}):
      settings = Params()
      settings.put_bool('AlwaysOnLateral', True, block=True)
      selected = self.card(pedal_params(CAR.CHEVROLET_BOLT_CC_2018_2021, pedal=True, camera=True), settings)
      self.assertEqual(selected.CP.safetyConfigs[0].safetyParam, 0x9D)
      cs = structs.CarState(canValid=True, gearShifter='drive', vEgo=20.)
      cs.cruiseState.available = True
      owner = selected.aol_card_intent
      owner.update(cs)
      self.assertTrue(owner.allowed_latch)
      events = Events()
      events.add(log.OnroadEvent.EventName.selfdrivedLagging)
      self.assertTrue(events.contains(ET.NO_ENTRY))
      self.assertTrue(events.contains(ET.SOFT_DISABLE))
      self.assertFalse(disarming_fault(events.to_msg(), cs))  # SOFT withdraws output while retaining driver intent.
      for standard in (False, True):
        with patch.object(selected, 'sm', {'onroadEvents': events.to_msg()}):
          fault = selected.aol_disarming_fault(cs, 1_000_000_000, 1_000_000_001)
        self.assertFalse(fault)
        owner.update(cs, fault_active=fault)
        self.assertTrue(owner.allowed_latch)
        allowed, pause_lat, pause_long = owner.output(cs)
        intent = SimpleNamespace(allowedLatch=allowed, pauseLateral=pause_lat, pauseLongitudinal=pause_long)
        native = SimpleNamespace(requestedLateral=True, requestedLongitudinal=True,
                                 lateralAllowed=True, longitudinalAllowed=True)
        blocked = decide_axes(standard_lateral=standard, standard_longitudinal=standard, intent=intent,
          native=native, car_state=cs, initialized=True, model_ready=True,
          no_entry=aol_no_entry(events.names, cs, paddle_only_cancel=False), immediate_disable=False,
          dm_lockout=False, pause_brake_mps=0.)
        self.assertFalse(blocked.desired_lateral or blocked.desired_longitudinal)
        self.assertFalse(blocked.lateral_active or blocked.longitudinal_active)
      events.clear()
      with patch.object(selected, 'sm', {'onroadEvents': events.to_msg()}):
        owner.update(cs, fault_active=selected.aol_disarming_fault(cs, 1_100_000_000, 1_100_000_001))
      self.assertTrue(owner.allowed_latch)  # No main cycle, standard engagement, or gesture.
      allowed, pause_lat, pause_long = owner.output(cs)
      intent = SimpleNamespace(allowedLatch=allowed, pauseLateral=pause_lat, pauseLongitudinal=pause_long)
      for native, expected_active in ((None, False), (SimpleNamespace(requestedLateral=False,
          requestedLongitudinal=False, lateralAllowed=True, longitudinalAllowed=True), False),
          (SimpleNamespace(requestedLateral=True, requestedLongitudinal=False,
                           lateralAllowed=True, longitudinalAllowed=False), True)):
        recovered = decide_axes(standard_lateral=False, standard_longitudinal=False, intent=intent,
          native=native, car_state=cs, initialized=True, model_ready=True, no_entry=False,
          immediate_disable=False, dm_lockout=False, pause_brake_mps=0.)
        self.assertTrue(recovered.desired_lateral)
        self.assertEqual(recovered.lateral_active, expected_active)
        self.assertFalse(recovered.desired_longitudinal or recovered.longitudinal_active)
      events.add(log.OnroadEvent.EventName.selfdrivedLagging)
      denied = selected.CP.as_reader().as_builder()
      denied.passive = True
      with patch.object(selected, 'CP', denied), patch.object(selected, 'sm', {'onroadEvents': events.to_msg()}):
        self.assertTrue(selected.aol_disarming_fault(cs, 1_200_000_000, 1_200_000_001))

  def test_actual_bolt_card_mixed_faults_block_until_explicit_healthy_observation(self):
    from opendbc.car.gm.tests.test_bolt_pedal import params as pedal_params
    from openpilot.cereal import log
    from openpilot.selfdrive.selfdrived.events import Events

    with OpenpilotPrefix(), patch.dict(os.environ, {'SIMULATION': '1', 'AOL_REPLAY_RUNTIME': '0'}):
      settings = Params()
      settings.put_bool('AlwaysOnLateral', True, block=True)
      for extra in (log.OnroadEvent.EventName.controlsMismatch, log.OnroadEvent.EventName.steerUnavailable, None):
        with self.subTest(extra=extra):
          selected = self.card(pedal_params(CAR.CHEVROLET_BOLT_CC_2018_2021, pedal=True, camera=True), settings)
          cs = structs.CarState(canValid=True, gearShifter='drive', vEgo=20.)
          cs.cruiseState.available = True
          owner = selected.aol_card_intent
          owner.update(cs)
          events = Events()
          events.add(log.OnroadEvent.EventName.selfdrivedLagging)
          if extra is not None:
            events.add(extra)
          else:
            cs.steerFaultPermanent = True
          with patch.object(selected, 'sm', {'onroadEvents': events.to_msg()}):
            fault = selected.aol_disarming_fault(cs, 1_000_000_000, 1_000_000_001)
          self.assertTrue(fault)
          owner.update(cs, fault_active=fault)
          self.assertFalse(owner.allowed_latch)
          cs.steerFaultPermanent = False
          owner.update(cs, fault_active=False)
          self.assertTrue(owner.allowed_latch)
          cs.canValid = False
          cs.cruiseState.available = False
          owner.update(cs, fault_active=False)
          cs.canValid = True
          cs.cruiseState.available = True
          owner.update(cs, fault_active=False)
          self.assertTrue(owner.allowed_latch)
          cs.cruiseState.available = False
          owner.update(cs, fault_active=False)
          cs.cruiseState.available = True
          owner.update(cs, fault_active=False)
          self.assertTrue(owner.allowed_latch)

  def test_actual_controls_axis_receipt_and_fault_gates(self):
    with OpenpilotPrefix(), patch.dict(os.environ, {'SIMULATION': '1', 'REPLAY': '1', 'AOL_REPLAY_RUNTIME': '0'}):
      settings = Params()
      settings.put_bool('AlwaysOnLateral', True, block=True)
      selected = self.card(factory_params(alpha=False), settings)
      controls = Controls()
      cases = ((None, True), ('temporary', False), (None, True), ('reverse', False),
               ('brake_allowed', True), ('brake_paused', False), ('regen', True),
               ('permanent', False), (None, True), ('off', False), ('on', True))
      for tick, (fault, expected) in enumerate(cases):
        now = 1_000_000_000 + tick * 10_000_000
        feed(controls, now, tick, active=False, enabled=False)
        cs = structs.CarState(canValid=True, vEgo=20., gearShifter='drive')
        cs.cruiseState.available = fault != 'off'
        cs.steerFaultTemporary = fault == 'temporary'
        cs.steerFaultPermanent = fault == 'permanent'
        cs.gearShifter = 'reverse' if fault == 'reverse' else 'drive'
        cs.brakePressed = fault in ('brake_allowed', 'brake_paused')
        cs.regenBraking = fault == 'regen'
        selected.aol_card_intent.update(cs, fault_active=cs.steerFaultPermanent)
        intent = IntentState('card', tick + 1, now, now, now + 30_000_000,
                             selected.aol_card_intent.allowed_latch, False, False, True, True)
        native = SafetyState(1, True, now, now + 200_000_000, int(structs.CarParams.SafetyModel.gm),
                             selected.CP.safetyConfigs[0].safetyParam, True, False, True, False, 'panda', 'gm-test')
        decision = decide_axes(standard_lateral=False, standard_longitudinal=False, intent=intent, native=native,
                               car_state=cs, initialized=True, model_ready=True, no_entry=False,
                               immediate_disable=False, dm_lockout=False,
                               pause_brake_mps=25. if fault == 'brake_paused' else 0.)
        axis = messaging.new_message('aolAxisState', valid=True, logMonoTime=now)
        axis.aolAxisState.qualified = True
        axis.aolAxisState.nativeAcknowledged = decision.native_acknowledged
        axis.aolAxisState.desiredLateral = decision.desired_lateral
        axis.aolAxisState.lateralActive = decision.lateral_active
        axis.aolAxisState.sessionId = 'gm-test'
        axis.aolAxisState.observedMonoTime = now
        axis.aolAxisState.validUntilMonoTime = now + 30_000_000
        receipt = messaging.new_message('aolSafetyWire', 0, valid=True, logMonoTime=now)
        receipt.aolSafetyWire = encode_safety(native)
        state = messaging.new_message('carState', valid=True, logMonoTime=now)
        state.carState = cs
        controls.sm.update_msgs((now + 1_000) / 1e9, [axis.as_reader(), receipt.as_reader(), state.as_reader()])
        command, _ = controls.state_control()
        self.assertEqual(command.latActive, expected)
        self.assertFalse(command.longActive)


  def test_base_gateway_actual_card_controls_parser_and_native_axes(self):
    cases = [(identity, False, False) for identity in GM_BASE_GATEWAY_IDS]
    cases += [(CAR.BUICK_LACROSSE, True, False), (CAR.BUICK_LACROSSE, False, True)]
    safety = libsafety_py.libsafety
    for identity, held, disabled in cases:
      with self.subTest(identity=identity, held=held, disabled=disabled), OpenpilotPrefix(), \
           patch.dict(os.environ, {'SIMULATION': '1', 'REPLAY': '1', 'AOL_REPLAY_RUNTIME': '0'}):
        settings = Params()
        settings.put_bool('AlwaysOnLateral', False, block=True)  # Test the preserved explicit-Off startup branch.
        settings.put_bool('GMAutoHold', held, block=True)
        settings.put_bool('DisableOpenpilotLongitudinal', disabled, block=True)
        factory = ordinary_params(identity, radar=True)
        default = self.card(factory, settings)
        self.assertIsNone(default.aol_card_intent)
        self.assertEqual(default.CP.alternativeExperience, 0)
        settings.put_bool('AlwaysOnLateral', True, block=True)
        selected = self.card(factory, settings)
        self.assertEqual(selected.CP.safetyConfigs[0].safetyParam, default.CP.safetyConfigs[0].safetyParam)
        self.assertEqual(selected.CP.lateralTuning.to_dict(), default.CP.lateralTuning.to_dict())
        self.assertEqual(selected.CP.alternativeExperience, 32)
        self.assertEqual(selected.CP.openpilotLongitudinalControl, not disabled)
        self.assertFalse(selected.CP.pcmCruise)
        self.assertEqual(selected.CP.safetyConfigs[0].safetyParam, 0x80 if held else 0)
        self.assertEqual(selected.CP.lateralTuning.to_dict(), factory.lateralTuning.to_dict())
        self.assertFalse(lane_centering_supported(selected.CP))
        controls = Controls()
        self.assertEqual(controls.CP.lateralTuning.to_dict(), factory.lateralTuning.to_dict())
        self.assertEqual(controls.CP.lateralTuning.which(), factory.lateralTuning.which())
        ci = selected.CI
        packer = CANPacker(DBC[identity][Bus.pt])
        safety.set_alternative_experience(32)
        self.assertEqual(safety.set_safety_hooks(structs.CarParams.SafetyModel.gm, selected.CP.safetyConfigs[0].safetyParam), 0)
        safety.init_tests()
        seen_lateral = seen_longitudinal = False
        for tick in range(150):
          now = 1_000_000_000 + tick * 10_000_000
          ordinary = 41 <= tick < 60 and not disabled
          main = not 20 <= tick < 30
          gas, brake, reverse = 70 <= tick < 80, 80 <= tick < 90, 90 <= tick < 100
          _, packets = feed_car(SimpleNamespace(update=lambda _: None), packer, now, counter=tick % 4,
                                active=ordinary, speed=20., camera=False)
          packets = [packet for packet in packets if packet[0] not in (0xC9, 0x1C4, 0xBE, 0x1E1, 0x1F5, 0xBD)]
          packets += [packer.make_can_msg('AcceleratorPedal2', 0, {'CruiseState': 2 if ordinary else 0,
                                                                  'AcceleratorPedal2': 30 if gas else 0}),
                      packer.make_can_msg('ECMAcceleratorPos', 0, {'BrakePedalPos': 20 if brake else 0}),
                      packer.make_can_msg('ECMPRDNL2', 0, {'PRNDL2': 2 if reverse else 4}),
                      packer.make_can_msg('ASCMSteeringButton', 0, {'ACCButtons': 3 if tick == 40 else 6 if tick == 60 else 1,
                                                                  'RollingCounter': tick % 4})]
          if not 100 <= tick < 140:
            packets.append(packer.make_can_msg('ECMEngineStatus', 0, {'CruiseMainOn': int(main)}))
          ci.update([(now - 1_000_000, packets)])
          cs = ci.update([(now, packets)])
          for packet in packets:
            native('rx', packet, now // 1000)
          safety.safety_tick()
          safety.set_aol_test_heartbeat(True)
          selected.aol_card_intent.update(cs, now_ns=now, standard_enabled=ordinary)
          intent = IntentState('card', tick + 1, now, now, now + 30_000_000,
                               selected.aol_card_intent.allowed_latch, False, False, True, True)
          wanted = decide_axes(standard_lateral=ordinary, standard_longitudinal=ordinary, intent=intent, native=None,
                               car_state=cs, initialized=True, model_ready=True, no_entry=False,
                               immediate_disable=False, dm_lockout=False, pause_brake_mps=25.)
          safety.aol_set_host_request(int(wanted.desired_lateral) | (int(wanted.desired_longitudinal) << 1))
          safety.set_timer(now // 1000 + 1)
          mask = safety.aol_get_permission_mask()
          receipt_state = SafetyState(1, True, now, now + 200_000_000, int(structs.CarParams.SafetyModel.gm),
                                      selected.CP.safetyConfigs[0].safetyParam,
                                      bool(mask & 1), bool(mask & 2),
                                      wanted.desired_lateral, wanted.desired_longitudinal, 'panda', 'gm-gateway-test')
          decision = decide_axes(standard_lateral=ordinary, standard_longitudinal=ordinary, intent=intent, native=receipt_state,
                                 car_state=cs, initialized=True, model_ready=True, no_entry=False,
                                 immediate_disable=False, dm_lockout=False, pause_brake_mps=25.)
          feed(controls, now, tick, active=ordinary, enabled=ordinary)
          axis = messaging.new_message('aolAxisState', valid=True, logMonoTime=now)
          axis.aolAxisState.qualified = True
          axis.aolAxisState.nativeAcknowledged = True
          axis.aolAxisState.desiredLateral = decision.desired_lateral
          axis.aolAxisState.desiredLongitudinal = decision.desired_longitudinal
          axis.aolAxisState.lateralActive = decision.lateral_active
          axis.aolAxisState.longitudinalActive = decision.longitudinal_active
          axis.aolAxisState.sessionId = 'gm-gateway-test'
          axis.aolAxisState.observedMonoTime = now
          axis.aolAxisState.validUntilMonoTime = now + 30_000_000
          receipt = messaging.new_message('aolSafetyWire', 0, valid=True, logMonoTime=now)
          from dataclasses import replace
          transport_receipt = receipt_state
          if tick == 11:
            transport_receipt = replace(receipt_state, safetyParam=5)
          elif tick == 12:
            transport_receipt = replace(receipt_state, axisSessionId='wrong-session')
          elif tick == 13:
            transport_receipt = replace(receipt_state, observedMonoTime=now + 1)
          elif tick == 14:
            transport_receipt = replace(receipt_state, validUntilMonoTime=now - 1)
          receipt.aolSafetyWire = encode_safety(transport_receipt)
          state = messaging.new_message('carState', valid=True, logMonoTime=now)
          state.carState = cs
          controls.sm.update_msgs((now + 1_000) / 1e9, [axis.as_reader(), receipt.as_reader(), state.as_reader()])
          command, _ = controls.state_control()
          self.assertEqual(command.latActive, decision.lateral_active and tick not in (11, 12, 13, 14))
          self.assertEqual(command.longActive, ordinary)
          self.assertEqual(bool(safety.get_controls_allowed()), 41 <= tick < 60)
          if not main or brake or reverse or 131 <= tick < 140:
            self.assertFalse(command.latActive)
          if tick == 10:
            self.assertTrue(command.latActive)
          command.actuators.torque = .02 if command.latActive else 0.
          _, messages = ci.apply(command.as_reader(), now + 2)
          for message in messages:
            self.assertTrue(native('tx', message, now // 1000 + 1), (tick, message))
          seen_lateral |= command.latActive and not command.longActive
          seen_longitudinal |= command.longActive
        self.assertTrue(seen_lateral)
        self.assertEqual(seen_longitudinal, not disabled)
        safety.set_alternative_experience(0)

  def test_camera_interceptor_actual_controls_and_native_axis_ownership(self):
    from opendbc.car.gm.tests.test_camera_acc_pedal import params
    from opendbc.car.gm.tests.test_cc_gateway_stock import pt_frames
    from opendbc.car.gm import gmcan
    safety = libsafety_py.libsafety
    release = safety.set_safety_hooks(structs.CarParams.SafetyModel.allOutput, 0) != 0
    for camera in (False, True):
      for be in (False, True):
        with self.subTest(camera=camera, be=be), OpenpilotPrefix(), \
             patch.dict(os.environ, {'SIMULATION': '1', 'REPLAY': '1', 'AOL_REPLAY_RUNTIME': '0'}):
          settings = Params()
          settings.put_bool('AlwaysOnLateral', True, block=True)
          settings.put_bool('IsReleaseBranch', release, block=True)
          factory = params(camera=camera, be=be, release=release)
          selected = self.card(factory, settings)
          cp, ci = selected.CP, selected.CI
          self.assertEqual(cp.alternativeExperience, 32)
          controls = Controls()
          self.assertEqual(controls.CP.to_dict(), cp.to_dict())
          safety.set_alternative_experience(32)
          self.assertEqual(safety.set_safety_hooks(structs.CarParams.SafetyModel.gm, cp.safetyConfigs[0].safetyParam), 0)
          safety.init_tests()
          packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
          loopback = []
          for tick in range(70):
            now = 1_000_000_000 + tick * 10_000_000
            ordinary = tick >= 42 and not release
            packets = pt_frames(packer, counter=tick % 4, acc_cruise=2 if ordinary else 0)
            packets = [packet for packet in packets if packet[0] not in (0xBE, 0xF1, 0x1E1)]
            packets += [packer.make_can_msg('ECMAcceleratorPos' if be else 'EBCMBrakePedalPosition', 0, {}),
                        packer.make_can_msg('ASCMSteeringButton', 0, {'ACCButtons': 3 if tick == 40 else 1, 'RollingCounter': tick % 4})]
            sensor = bytearray.fromhex('0279012a0000')
            sensor[4] = tick % 16
            sensor[5] = gmcan.pedal_crc(sensor)
            packets.append((0x201, bytes(sensor), 0))
            if camera:
              packets += [packer.make_can_msg('ASCMLKASteeringCmd', 2, {'RollingCounter': tick % 4}),
                          packer.make_can_msg('AEBCmd', 2, {}),
                          packer.make_can_msg('ASCMActiveCruiseControlStatus', 2, {'ACCCruiseState': 2 if ordinary else 0})]
            safety.set_timer(now // 1000)
            for packet in packets:
              self.assertTrue(native('rx', packet, now // 1000))
            safety.safety_tick()
            state = ci.update([(now, packets + loopback)])
            selected.aol_card_intent.update(state, now_ns=now, standard_enabled=ordinary)
            requested = (1 if selected.aol_card_intent.allowed_latch else 0) | (2 if ordinary else 0)
            safety.set_aol_test_heartbeat(True)
            safety.aol_set_host_request(requested)
            safety.set_timer(now // 1000 + 1)
            mask = safety.aol_get_permission_mask()
            feed(controls, now, tick, active=ordinary, enabled=ordinary)
            axis = messaging.new_message('aolAxisState', valid=True, logMonoTime=now)
            axis.aolAxisState.qualified = axis.aolAxisState.nativeAcknowledged = True
            axis.aolAxisState.desiredLateral = bool(requested & 1)
            axis.aolAxisState.desiredLongitudinal = bool(requested & 2)
            axis.aolAxisState.lateralActive = bool(mask & 1)
            axis.aolAxisState.longitudinalActive = bool(mask & 2)
            axis.aolAxisState.sessionId = 'camera-interceptor'
            axis.aolAxisState.observedMonoTime, axis.aolAxisState.validUntilMonoTime = now, now + 30_000_000
            receipt = messaging.new_message('aolSafetyWire', 0, valid=True, logMonoTime=now)
            receipt.aolSafetyWire = encode_safety(SafetyState(1, True, now, now + 200_000_000,
              int(structs.CarParams.SafetyModel.gm), cp.safetyConfigs[0].safetyParam,
              bool(mask & 1), bool(mask & 2), bool(requested & 1), bool(requested & 2), 'panda', 'camera-interceptor'))
            actual = messaging.new_message('carState', valid=True, logMonoTime=now)
            actual.carState = state
            controls.sm.update_msgs((now + 1_000) / 1e9, [axis.as_reader(), receipt.as_reader(), actual.as_reader()])
            command, lateral_log = controls.state_control()
            controls.publish(command, lateral_log)
            if tick >= 42:
              self.assertTrue(command.latActive,
                              (tick, mask, requested, state.canValid, safety.safety_config_valid(), selected.CP.safetyConfigs[0].safetyParam))
              self.assertEqual(command.longActive, not release)
            _, commands = ci.apply(command.as_reader(), now + 2)
            loopback = [(addr, data, 128) for addr, data, _ in commands if addr == 0x180]
            for packet in commands:
              self.assertTrue(native('tx', packet, now // 1000 + 1), (tick, packet))
          safety.set_alternative_experience(0)

  def test_actual_card_forwards_shared_disarming_events(self):
    class IntentUpdated(Exception):
      pass
    with OpenpilotPrefix(), patch.dict(os.environ, {'SIMULATION': '1', 'AOL_REPLAY_RUNTIME': '0'}):
      settings = Params()
      settings.put_bool('AlwaysOnLateral', True, block=True)
      selected = self.card(factory_params(alpha=False), settings)
      cs = structs.CarState(canValid=True, vEgo=20., gearShifter='drive')
      cs.cruiseState.available = True
      selected.aol_card_intent.update(cs)
      self.assertTrue(selected.aol_card_intent.allowed_latch)
      now = 1_000_000_000
      events = messaging.new_message('onroadEvents', 1, valid=True, logMonoTime=now)
      events.onroadEvents[0].name = 'controlsMismatch'
      events.onroadEvents[0].immediateDisable = True
      control = messaging.new_message('carControl', valid=True, logMonoTime=now)
      selected.sm.update_msgs(now / 1e9, [events.as_reader(), control.as_reader()])
      with patch('openpilot.selfdrive.car.card.messaging.drain_sock_raw', return_value=[b'fixture']), \
           patch('openpilot.selfdrive.car.card.can_capnp_to_list', return_value=[]), \
           patch('openpilot.selfdrive.car.card.time.monotonic_ns', return_value=now), \
           patch('openpilot.selfdrive.car.card.REPLAY', False), \
           patch.object(selected.CI, 'update', return_value=cs), \
           patch.object(selected.RI, 'update', return_value=None), \
           patch.object(selected.sm, 'update'), \
           patch.object(selected, 'observe_distance_personality', side_effect=IntentUpdated):
        with self.assertRaises(IntentUpdated):
          selected.state_update()
      self.assertFalse(selected.aol_card_intent.allowed_latch)
      selected.aol_card_intent.update(cs, fault_active=False)
      self.assertTrue(selected.aol_card_intent.allowed_latch)


  def test_actual_controller_inactive_cruise_brake_and_regen_lateral_only(self):
    safety = libsafety_py.libsafety
    release = safety.set_safety_hooks(structs.CarParams.SafetyModel.allOutput, 0) != 0
    cases = [bolt_params(BOLT_IDS[0]), configured('cc'), camera_params(alpha=False),
             camera_params(alpha=True), removed_params(alpha=False), removed_params(alpha=True),
             sdgm_params(alpha=False), sdgm_params(alpha=True)]
    for variant in ('be', 'f1'):
      cp = configured(variant)
      VehicleStartupPreferences(disable_bolt_long=True).prepare(cp)
      cases.append(cp)
    for cp in cases:
      with self.subTest(identity=cp.carFingerprint, word=hex(cp.safetyConfigs[0].safetyParam)):
        cp.alternativeExperience = 32
        ci = CarInterface(cp)
        packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
        safety.init_tests()
        safety.set_alternative_experience(32)
        safety.set_safety_hooks(structs.CarParams.SafetyModel.gm, cp.safetyConfigs[0].safetyParam)
        safety.set_aol_test_heartbeat(True)
        supported = not release or cp.safetyConfigs[0].safetyParam not in (20, 0x4007, 0x5007, 0xC151)
        requested = []
        for tick in range(18):
          now = 1_000_000_000 + tick * 10_000_000
          out, sources = feed_car(ci, packer, now, counter=tick % 4, active=False, brake=True, regen=True, eps_status=1)
          sources = [source for source in sources if source[0] not in (0x1C4, 0xBE, 0xF1)]
          sources.extend([packer.make_can_msg('AcceleratorPedal2', 0, {'CruiseState': 0}),
                          packer.make_can_msg('ECMAcceleratorPos', 0, {'BrakePedalPos': 12}),
                          packer.make_can_msg('EBCMBrakePedalPosition', 0, {'BrakePedalPosition': 6}),
                          packer.make_can_msg('ASCMActiveCruiseControlStatus', 2, {'ACCCruiseState': 0})])
          out = ci.update([(now + 1, sources)])
          for source in sources:
            native('rx', source, now // 1000)
          safety.safety_tick()
          safety.aol_set_host_request(1)
          permission = safety.aol_get_permission_mask()
          cc = control(enabled=False, long_active=False)
          cc.latActive = bool(permission & 1)
          ci.CC.frame = tick
          _, messages = ci.apply(cc.as_reader(), now + 2)
          self.assertFalse(cc.longActive)
          for message in messages:
            if message[0] == 0x180:
              requested.append(bool(message[1][0] & 8))
              self.assertEqual(native('tx', message, now // 1000 + 1), supported)
        self.assertTrue(out.canValid)
        self.assertFalse(out.cruiseState.enabled)
        self.assertTrue(out.brakePressed)
        self.assertEqual(any(requested), supported)


  def test_calibration_pause_retains_intent_and_recovers_without_new_main_edge(self):
    from openpilot.selfdrive.selfdrived.selfdrived import SelfdriveD
    from openpilot.starpilot.aol.tests.test_runtime import sample_car_state
    from openpilot.selfdrive.selfdrived.state import StateMachine
    from openpilot.selfdrive.selfdrived.events import Events, EventName, ET
    from openpilot.starpilot.aol.runtime import AxisDecision
    from openpilot.starpilot.aol.wire import encode_intent

    for calibration_event in (EventName.calibrationInvalid, EventName.calibrationIncomplete, EventName.calibrationRecalibrating):
      with self.subTest(calibration=calibration_event), OpenpilotPrefix(), patch.dict(
          os.environ, {'SIMULATION': '1', 'REPLAY': '1', 'AOL_REPLAY_RUNTIME': '0'}):
        settings = Params()
        settings.put_bool('AlwaysOnLateral', True, block=True)
        selected = self.card(factory_params(alpha=False), settings)
        controls = Controls()
        sd = SelfdriveD.__new__(SelfdriveD)
        sd.CP, sd.initialized, sd.aol_replay = selected.CP, True, True
        sd.aol_session_id, sd.aol_axis_decision = 'calibration-test', AxisDecision()
        sd.aol_authority_lost = False
        sd.aol_transport_pause = TransportPause(sd.CP, sd.aol_session_id)
        sd.aol_dm_lateral_inhibit, sd.aol_settings = False, selected.aol_settings
        sd.nostalgia_paddle_cancel = False
        sd.state_machine, sd.events = StateMachine(), Events()
        sd.sm = messaging.SubMaster(['aolIntentWire', 'aolSafetyWire', 'modelV2',
                                     'extrinsicsCalibration', 'driverMonitoringState', 'pandaStates'])
        cs = structs.CarState(canValid=True, vEgo=20., gearShifter='drive')
        for tick, (phase, expected) in enumerate((('unbuckled', True), ('unbuckled', True), ('healthy', True), ('invalid', False), ('recovered', True),
                                                  ('held', True), ('unmapped_lkas', True), ('unmapped_main', True),
                                                  ('release_lkas', True), ('main_off', False), ('fault_edge', False),
                                                  ('recovered_again', True), ('main_off', False), ('new_main', True),
                                                  ('temporary', False), ('healthy', True), ('reverse', False),
                                                  ('healthy', True), ('model_stale', False), ('healthy', True))):
          now = 1_000_000_000 + tick * 10_000_000
          cs.cruiseState.available = phase != 'main_off'
          cs.seatbeltUnlatched = phase == 'unbuckled'
          cs.buttonEvents = []
          if phase in ('unmapped_lkas', 'unmapped_main', 'release_lkas'):
            cs.buttonEvents = [structs.CarState.ButtonEvent(
              type='mainCruise' if phase == 'unmapped_main' else 'lkas', pressed=phase != 'release_lkas')]
          cs.steerFaultTemporary = phase == 'temporary'
          cs.gearShifter = 'reverse' if phase == 'reverse' else 'drive'
          events = Events()
          if cs.seatbeltUnlatched:
            events.add(EventName.seatbeltNotLatched)
          if phase == 'invalid':
            events.add(calibration_event)
          if phase == 'fault_edge':
            events.add(EventName.overheat)
          onroad = messaging.new_message('onroadEvents', 0, valid=True, logMonoTime=now)
          onroad.onroadEvents = events.to_msg()
          selected.sm.update_msgs(now / 1e9, [onroad.as_reader()])
          selected.aol_card_intent.update(cs)
          selected.observe_aol_calibration(cs, now, False)
          self.assertEqual(selected.aol_card_intent.allowed_latch,
                           phase != 'main_off')
          intent = messaging.new_message('aolIntentWire', 0, valid=True, logMonoTime=now)
          allowed, pause_lat, pause_long = selected.aol_card_intent.output(cs)
          intent.aolIntentWire = encode_intent(IntentState('card', tick + 1, now, now, now + 30_000_000,
                                                          allowed, pause_lat, pause_long, True, True))
          native = SafetyState(1, True, now, now + 200_000_000, int(structs.CarParams.SafetyModel.gm),
                               selected.CP.safetyConfigs[0].safetyParam, expected, False, expected, False,
                               'panda', 'calibration-test')
          receipt = messaging.new_message('aolSafetyWire', 0, valid=True, logMonoTime=now)
          receipt.aolSafetyWire = encode_safety(native)
          model = messaging.new_message('modelV2', valid=phase != 'model_stale', logMonoTime=now)
          calibration = messaging.new_message('extrinsicsCalibration', valid=True, logMonoTime=now)
          calibration.extrinsicsCalibration.calStatus = 'uncalibrated' if phase == 'invalid' else 'calibrated'
          monitoring = messaging.new_message('driverMonitoringState', valid=True, logMonoTime=now)
          sd.sm.update_msgs(now / 1e9, [intent.as_reader(), receipt.as_reader(), model.as_reader(),
                                       calibration.as_reader(), monitoring.as_reader()])
          sd.aol_car_state_log_ns, sd.enabled, sd.active = now, False, False
          sd.events = events
          if cs.seatbeltUnlatched:
            sd.events.add(EventName.seatbeltNotLatched)
            self.assertTrue(sd.events.contains(ET.NO_ENTRY))
            self.assertTrue(sd.events.contains(ET.SOFT_DISABLE))
          with patch('openpilot.selfdrive.selfdrived.selfdrived.REPLAY', True), \
               patch.object(sd, 'data_sample', side_effect=lambda sd=sd, cs=cs, now=now: sample_car_state(sd, cs, now)), patch.object(sd, 'update_events'), \
               patch.object(sd, 'update_alerts'), patch.object(sd, 'update_conditional_mode'), \
               patch.object(sd, 'publish_selfdriveState'):
            sd.step()
          self.assertEqual(sd.aol_axis_decision.lateral_active, expected, phase)
          feed(controls, now, tick, active=False, enabled=False)
          axis = messaging.new_message('aolAxisState', valid=True, logMonoTime=now)
          axis.aolAxisState.qualified = axis.aolAxisState.nativeAcknowledged = True
          axis.aolAxisState.desiredLateral = sd.aol_axis_decision.desired_lateral
          axis.aolAxisState.lateralActive = sd.aol_axis_decision.lateral_active
          axis.aolAxisState.sessionId = 'calibration-test'
          axis.aolAxisState.observedMonoTime = now
          axis.aolAxisState.validUntilMonoTime = now + 30_000_000
          state = messaging.new_message('carState', valid=True, logMonoTime=now)
          state.carState = cs
          controls.sm.update_msgs((now + 1_000) / 1e9, [axis.as_reader(), receipt.as_reader(), state.as_reader()])
          command, _ = controls.state_control()
          self.assertEqual(command.latActive, expected, phase)
          self.assertFalse(command.longActive)

  def test_actual_selfdrived_gas_override_keeps_longitudinal_request(self):
    self.exercise_actual_selfdrived_override()

  def test_sustained_unbuckling_keeps_aol_through_standard_soft_disable(self):
    self.exercise_actual_selfdrived_override(unlatched=True)

  def exercise_actual_selfdrived_override(self, *, unlatched=False):
    from openpilot.selfdrive.selfdrived.selfdrived import SelfdriveD
    from openpilot.starpilot.aol.tests.test_runtime import sample_car_state
    from openpilot.selfdrive.selfdrived.state import StateMachine
    from openpilot.selfdrive.selfdrived.events import Events, EventName
    from openpilot.starpilot.aol.runtime import AxisDecision
    from openpilot.starpilot.aol.wire import encode_intent
    from openpilot.starpilot.aol.intent import AolSettings
    from openpilot.cereal import log
    with OpenpilotPrefix(), patch.dict(os.environ, {'SIMULATION': '1', 'REPLAY': '1'}):
      cp = configured('be')
      cp.alternativeExperience = 32
      now = 1_000_000_000
      cs = structs.CarState(canValid=True, vEgo=20., gearShifter='drive', gasPressed=True)
      cs.cruiseState.available = cs.cruiseState.enabled = True
      sd = SelfdriveD.__new__(SelfdriveD)
      sd.CP, sd.initialized, sd.aol_replay = cp, True, True
      sd.enabled = sd.active = True
      sd.aol_car_state_log_ns = now
      sd.aol_session_id = 'gm-test'
      sd.aol_authority_lost = False
      sd.aol_transport_pause = TransportPause(sd.CP, sd.aol_session_id)
      sd.aol_axis_decision = AxisDecision()
      sd.aol_dm_lateral_inhibit = False
      sd.aol_settings = AolSettings(True, 0., 0, 0, (0, 0, 0), (0, 0, 0))
      sd.nostalgia_paddle_cancel = False
      sd.state_machine = StateMachine()
      sd.state_machine.state = log.SelfdriveState.OpenpilotState.enabled
      sd.events = Events()
      sd.events.add(EventName.gasPressedOverride)
      sd.sm = messaging.SubMaster(['aolIntentWire', 'aolSafetyWire', 'modelV2',
                                   'extrinsicsCalibration', 'driverMonitoringState', 'pandaStates'])
      intent = messaging.new_message('aolIntentWire', 0, valid=True, logMonoTime=now)
      intent.aolIntentWire = encode_intent(IntentState('card', 1, now, now, now + 30_000_000,
                                                      True, False, False, True, True))
      receipt = messaging.new_message('aolSafetyWire', 0, valid=True, logMonoTime=now)
      receipt.aolSafetyWire = encode_safety(SafetyState(1, True, now, now + 200_000_000,
        int(structs.CarParams.SafetyModel.gm), cp.safetyConfigs[0].safetyParam,
        True, True, True, True, 'panda', 'gm-test'))
      model = messaging.new_message('modelV2', valid=True, logMonoTime=now)
      calibration = messaging.new_message('extrinsicsCalibration', valid=True, logMonoTime=now)
      calibration.extrinsicsCalibration.calStatus = 'calibrated'
      monitoring = messaging.new_message('driverMonitoringState', valid=True, logMonoTime=now)
      controls = None
      if unlatched:
        params = Params()
        params.put_bool('OpenpilotEnabledToggle', True, block=True)
        params.put_bool('AlwaysOnLateral', True, block=True)
        params.put('CarParams', cp.to_bytes(), block=True)
        controls = Controls()
      for tick in range(325 if unlatched else 1):
        now = 1_000_000_000 + tick * 10_000_000
        cs.gasPressed = not unlatched
        cs.seatbeltUnlatched = unlatched and tick > 0
        sd.events.clear()
        if cs.seatbeltUnlatched:
          sd.events.add(EventName.seatbeltNotLatched)
        elif not unlatched:
          sd.events.add(EventName.gasPressedOverride)
        intent.aolIntentWire = encode_intent(IntentState('card', tick + 1, now, now, now + 30_000_000,
                                                       True, False, False, True, True))
        receipt.aolSafetyWire = encode_safety(SafetyState(1, True, now, now + 200_000_000,
          int(structs.CarParams.SafetyModel.gm), cp.safetyConfigs[0].safetyParam,
          True, not cs.seatbeltUnlatched, True, not cs.seatbeltUnlatched, 'panda', 'gm-test'))
        for event in (intent, receipt, model, calibration, monitoring):
          event.logMonoTime = now
        sd.aol_car_state_log_ns = now
        sd.sm.update_msgs(now / 1e9, [intent.as_reader(), receipt.as_reader(), model.as_reader(),
                                     calibration.as_reader(), monitoring.as_reader()])
        with patch('openpilot.selfdrive.selfdrived.selfdrived.REPLAY', True), \
             patch.object(sd, 'data_sample', side_effect=lambda sd=sd, cs=cs, now=now: sample_car_state(sd, cs, now)), patch.object(sd, 'update_events'), \
             patch.object(sd, 'update_alerts'), patch.object(sd, 'update_conditional_mode'), \
             patch.object(sd, 'publish_selfdriveState'):
          sd.step()
        self.assertTrue(sd.aol_axis_decision.lateral_active)
        self.assertEqual(sd.aol_axis_decision.desired_longitudinal, not cs.seatbeltUnlatched)
        if unlatched:
          self.assertEqual(sd.enabled, tick <= 300)
          feed(controls, now, tick, active=sd.active, enabled=sd.enabled)
          axis = messaging.new_message('aolAxisState', valid=True, logMonoTime=now)
          axis.aolAxisState.qualified = axis.aolAxisState.nativeAcknowledged = True
          axis.aolAxisState.desiredLateral = axis.aolAxisState.lateralActive = True
          axis.aolAxisState.desiredLongitudinal = axis.aolAxisState.longitudinalActive = not cs.seatbeltUnlatched
          axis.aolAxisState.sessionId = 'gm-test'
          axis.aolAxisState.observedMonoTime, axis.aolAxisState.validUntilMonoTime = now, now + 30_000_000
          state = messaging.new_message('carState', valid=True, logMonoTime=now)
          state.carState = cs
          controls.sm.update_msgs((now + 1_000) / 1e9, [axis.as_reader(), receipt.as_reader(), state.as_reader()])
          command, lateral_log = controls.state_control()
          controls.publish(command, lateral_log)
          self.assertTrue(command.latActive)
          self.assertEqual(command.longActive, not cs.seatbeltUnlatched)
          self.assertEqual(command.cruiseControl.cancel, cs.cruiseState.enabled and (not command.enabled or not cp.pcmCruise))
        else:
          self.assertTrue(sd.enabled)

  def test_existing_vehicle_factories_preserve_intent_lifecycle(self):
    from opendbc.car import gen_empty_fingerprint
    from opendbc.car.honda.interface import CarInterface as HondaInterface
    from opendbc.car.honda.values import CAR as HondaCars
    from openpilot.starpilot.lateral.tests.test_lane_runtime import ioniq_candidate
    from openpilot.starpilot.aol.intent import AolSettings, AolCardIntent
    from openpilot.starpilot.aol.vehicle import create_intent
    honda = HondaInterface.get_params(HondaCars.HONDA_ACCORD, gen_empty_fingerprint(), [], True, False, False)
    _, hyundai = ioniq_candidate()
    # Existing Card startup adds AOL bit 11 inside the qualified Ioniq namespace.
    hyundai.safetyConfigs[-1].safetyParam |= 0x0800
    settings = AolSettings(True, 0., 0, 0, (0, 0, 0), (0, 0, 0))
    for cp in (honda, hyundai):
      policy = policy_for(cp)
      self.assertTrue(policy.intent_supported, (cp.carFingerprint, cp.safetyConfigs[-1].safetyParam))
      selected = create_intent(cp, settings, policy)
      from openpilot.starpilot.car.hyundai.aol import independent_cruise_buttons_supported
      reference = AolCardIntent(settings, explicit_latch=policy.explicit_latch,
                                independent_cruise_buttons=cp.brand == 'hyundai' and independent_cruise_buttons_supported(cp))
      self.assertIs(type(selected), AolCardIntent)
      for main, permanent, temporary, gear in ((True, False, False, 'drive'),
                                             (True, False, True, 'drive'),
                                             (True, False, False, 'reverse'),
                                             (True, True, False, 'drive'),
                                             (True, False, False, 'drive'),
                                             (False, False, False, 'drive'),
                                             (True, False, False, 'drive')):
        cs = structs.CarState(canValid=True, gearShifter=gear, steerFaultPermanent=permanent,
                              steerFaultTemporary=temporary)
        cs.cruiseState.available = main
        selected.update(cs, fault_active=permanent)
        reference.update(cs, fault_active=permanent)
        self.assertEqual(selected.__dict__.keys(), reference.__dict__.keys())
        for key, value in vars(selected).items():
          expected = vars(reference)[key]
          self.assertEqual(vars(value) if hasattr(value, '__dict__') else value,
                           vars(expected) if hasattr(expected, '__dict__') else expected, key)
        self.assertEqual(selected.output(cs), reference.output(cs))

  def test_bolt_cancel_lateral_recovery(self):
    for identity in (CAR.CHEVROLET_BOLT_CC_2017, CAR.CHEVROLET_BOLT_CC_2018_2021,
                     CAR.CHEVROLET_BOLT_CC_2022_2023, CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL):
      for removed in (False, True):
        with self.subTest(identity=identity, removed=removed):
          self.exercise_bolt_cancel_recovery(identity, removed)

  def test_bolt_cancel_fault_rearm(self):
    for critical in ('controlsMismatch', 'steerUnavailable', 'permanentEPS'):
      with self.subTest(critical=critical):
        self.exercise_bolt_cancel_recovery(CAR.CHEVROLET_BOLT_CC_2018_2021, False, critical=critical)

  def test_bolt_communications_pause_preserves_intent(self):
    for event in ('commIssueAvgFreq', 'commIssue'):
      with self.subTest(event=event):
        self.exercise_bolt_cancel_recovery(CAR.CHEVROLET_BOLT_CC_2018_2021, False, communications=event)

  def test_bolt_communications_preserves_critical_rearm(self):
    for critical in ('controlsMismatch', 'steerUnavailable', 'permanentEPS'):
      with self.subTest(critical=critical):
        self.exercise_bolt_cancel_recovery(CAR.CHEVROLET_BOLT_CC_2018_2021, False,
                                          critical=critical, communications='commIssueAvgFreq')

  def test_bolt_pedal_cancel_braking_and_physical_paddle_recovery(self):
    for identity in (CAR.CHEVROLET_BOLT_CC_2017, CAR.CHEVROLET_BOLT_CC_2018_2021,
                     CAR.CHEVROLET_BOLT_CC_2022_2023):
      with self.subTest(identity=identity):
        self.exercise_bolt_cancel_recovery(identity, False, pedal_scene=True)

  def test_bolt_pedal_current_inventory_and_published_control_join(self):
    exercise_bolt_cancel_recovery(self, CAR.CHEVROLET_BOLT_CC_2018_2021, False,
                                 pedal_scene=True, inventory_publish=True)

  def exercise_bolt_cancel_recovery(self, identity, removed, *, critical=None, communications: str | None = None, pedal_scene=False):
    exercise_bolt_cancel_recovery(self, identity, removed, critical=critical,
                                 communications=communications, pedal_scene=pedal_scene)


  def test_volt_brake_lateral_recovery(self):
    from dataclasses import replace
    from openpilot.selfdrive.car.car_events import CarEvents
    from openpilot.selfdrive.selfdrived.selfdrived import SelfdriveD
    from openpilot.selfdrive.selfdrived.state import StateMachine
    from openpilot.selfdrive.selfdrived.events import Events, EventName, ET
    from openpilot.starpilot.aol.runtime import AxisDecision
    from openpilot.starpilot.aol.wire import encode_intent, decode_intent
    from openpilot.starpilot.nostalgia import aol_no_entry
    from opendbc.car.gm.tests.test_ascm_intercept import params as ascm_params
    from opendbc.car.gm.values import GMFlags

    with OpenpilotPrefix(), patch.dict(os.environ, {'SIMULATION': '1', 'REPLAY': '1', 'AOL_REPLAY_RUNTIME': '0'}):
      settings = Params()
      for key in ('AlwaysOnLateral', 'AlphaLongitudinalEnabled', 'GMAutoHold', 'VoltOnePedalMode'):
        settings.put_bool(key, True, block=True)
      settings.put('AolBrakePauseSpeedMps', 10., block=True)
      selected = self.card(ascm_params(CAR.CHEVROLET_VOLT_ASCM, sascm=True, alpha=True, accelerator=True, radar=True), settings)
      cp, ci = selected.CP, selected.CI
      self.assertEqual(cp.safetyConfigs[0].safetyParam, 0xD114)
      self.assertEqual(cp.alternativeExperience, 32)
      self.assertFalse(cp.flags & GMFlags.PEDAL_LONG)
      self.assertTrue(cp.openpilotLongitudinalControl)
      self.assertFalse(cp.pcmCruise)
      self.assertEqual(selected.aol_settings.pause_brake_mps, 10.)
      controls = Controls()
      self.assertEqual(controls.CP.to_dict(), cp.to_dict())
      sd = SelfdriveD.__new__(SelfdriveD)
      sd.CP, sd.initialized, sd.aol_replay = cp, True, True
      sd.enabled = sd.active = False
      sd.aol_session_id, sd.aol_axis_decision = 'volt-brake', AxisDecision()
      sd.aol_authority_lost = False
      sd.aol_transport_pause = TransportPause(sd.CP, sd.aol_session_id)
      sd.aol_dm_lateral_inhibit, sd.aol_settings = False, selected.aol_settings
      sd.nostalgia_paddle_cancel = False
      sd.state_machine, sd.events = StateMachine(), Events()
      sd.sm = messaging.SubMaster(['aolIntentWire', 'aolSafetyWire', 'modelV2',
                                   'extrinsicsCalibration', 'driverMonitoringState'])
      car_events = CarEvents(cp)
      packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
      safety = libsafety_py.libsafety
      safety.set_alternative_experience(32)
      self.assertEqual(safety.set_safety_hooks(structs.CarParams.SafetyModel.gm, 0xD114), 0)
      safety.init_tests()
      resumed = paused = 0
      previous_sd_onroad = None
      ctx = {'identity': str(cp.carFingerprint), 'word': '0xd114', 'phase': 'prime'}
      try:
        # Warm all selected parser/native sources, with stock cruise inactive.
        for warmup in range(20):
          stamp = 800_000_000 + warmup * 10_000_000
          _, frames = feed_car(SimpleNamespace(update=lambda _: None), packer, stamp,
                               counter=warmup % 4, active=False, speed=8., camera=True)
          frames = [frame for frame in frames if frame[0] != 0x184]
          frames.append(packer.make_can_msg('PSCMStatus', 0, {'LKATorqueDeliveredStatus': 1}))
          frames.append(packer.make_can_msg('ASCMActiveCruiseControlStatus', 2, {'ACCCruiseState': 0}))
          previous = ci.update([(stamp, frames)])
          for frame in frames:
            self.assertTrue(native('rx', frame, stamp // 1000), (frame, ctx))
          safety.safety_tick()
        self.assertTrue(previous.canValid, msg=ctx)
        self.assertTrue(safety.safety_config_valid(), msg=ctx)
        self.assertFalse(safety.get_controls_allowed(), msg=ctx)
        for tick in range(146):
          now = 1_000_000_000 + tick * 10_000_000
          brake = any(start <= tick < start + 12 for start in (20, 60, 100))
          fault = tick == 138
          main = tick not in (140, 141)
          ctx.update(phase='scenario', tick=tick, brake=brake, fault=fault, main=main)
          _, packets = feed_car(SimpleNamespace(update=lambda _: None), packer, now,
                                counter=tick % 4, active=False, speed=8., camera=True)
          packets = [frame for frame in packets if frame[0] not in (0xC9, 0x1C4, 0xBE, 0x184)]
          packets += [packer.make_can_msg('PSCMStatus', 0, {'LKATorqueDeliveredStatus': 1}),
                      packer.make_can_msg('ASCMActiveCruiseControlStatus', 2, {'ACCCruiseState': 0}),
                      packer.make_can_msg('ECMEngineStatus', 0, {'CruiseMainOn': int(main)}),
                      packer.make_can_msg('AcceleratorPedal2', 0, {'CruiseState': 0}),
                      packer.make_can_msg('ECMAcceleratorPos', 0, {'BrakePedalPos': 30 if brake else 0})]
          cs = ci.update([(now, packets)])
          self.assertTrue(cs.canValid, msg=ctx)
          self.assertEqual(cs.brakePressed, brake, msg=ctx)
          self.assertFalse(cs.cruiseState.enabled, msg=ctx)
          self.assertEqual(cs.cruiseState.available, main, msg=ctx)
          for frame in packets:
            self.assertTrue(native('rx', frame, now // 1000), (frame, ctx))
          safety.safety_tick()
          # The native test heartbeat isolates transport negotiation.
          safety.set_aol_test_heartbeat(True)
          events = car_events.update(cs, previous, structs.CarControl())
          if cs.brakePressed and (not previous.brakePressed or not cs.standstill):
            events.add(EventName.pedalPressed)
          if fault:
            events.add(EventName.controlsMismatch)
          ctx['events'] = [int(event) for event in events.names]
          self.assertEqual(EventName.pedalPressed in events.names, brake, msg=ctx)
          self.assertEqual(events.contains(ET.IMMEDIATE_DISABLE), fault, msg=ctx)
          onroad = messaging.new_message('onroadEvents', 0, valid=True, logMonoTime=now)
          onroad.onroadEvents = events.to_msg()
          card_onroad = onroad.as_reader() if fault or previous_sd_onroad is None else previous_sd_onroad
          selected.sm.update_msgs(now / 1e9, [card_onroad])
          disarm = selected.aol_disarming_fault(cs, int(card_onroad.logMonoTime), now)
          self.assertEqual(disarm, tick in (136, 138, 139), msg=ctx)
          selected.aol_card_intent.update(cs, fault_active=disarm, now_ns=now, standard_enabled=False)
          expected_latch = main and not disarm
          self.assertEqual(selected.aol_card_intent.allowed_latch, expected_latch, msg=ctx)
          self.assertEqual(selected.aol_card_intent._main_cycle_required, disarm, msg=ctx)
          allowed, pause_lat, pause_long = selected.aol_card_intent.output(cs)
          intent_value = IntentState('card', tick + 1, now, now, now + 200_000_000,
                                     allowed, pause_lat, pause_long, selected.aol_qualified,
                                     selected.aol_card_intent.allowed_latch)
          intent = decode_intent(encode_intent(intent_value))
          self.assertIsNotNone(intent, msg=ctx)
          self.assertEqual(intent.allowedLatch, expected_latch, msg=ctx)
          self.assertFalse(intent.pauseLateral, msg=ctx)
          self.assertFalse(intent.pauseLongitudinal, msg=ctx)
          no_entry = aol_no_entry(events.names, cs, paddle_only_cancel=False, allow_below_engage_speed=True)
          immediate_disable = events.contains(ET.IMMEDIATE_DISABLE)
          wanted = decide_axes(native=None, standard_lateral=False, standard_longitudinal=False,
                               intent=intent, car_state=cs, initialized=True, model_ready=True,
                               no_entry=no_entry, immediate_disable=immediate_disable,
                               dm_lockout=False, pause_brake_mps=10.)
          expected_lat = not brake and expected_latch
          self.assertEqual(wanted.desired_lateral, expected_lat, msg=ctx)
          self.assertFalse(wanted.desired_longitudinal, msg=ctx)
          safety.aol_set_host_request(int(wanted.desired_lateral))
          mask = safety.aol_get_permission_mask()
          ctx['native_permission_mask'] = int(mask)
          self.assertEqual(mask, int(expected_lat), msg=ctx)
          self.assertFalse(safety.get_controls_allowed(), msg=ctx)
          receipt_state = SafetyState(1, True, now, now + 200_000_000, int(structs.CarParams.SafetyModel.gm),
                                     0xD114, bool(mask & 1), False, wanted.desired_lateral, False, 'panda', 'volt-brake')
          receipt = messaging.new_message('aolSafetyWire', 0, valid=True, logMonoTime=now)
          transport = replace(receipt_state, validUntilMonoTime=now - 1) if tick == 135 else receipt_state
          receipt.aolSafetyWire = encode_safety(transport)
          intent_message = messaging.new_message('aolIntentWire', 0, valid=True, logMonoTime=now)
          intent_message.aolIntentWire = encode_intent(intent_value)
          model = messaging.new_message('modelV2', valid=True, logMonoTime=now)
          calibration = messaging.new_message('extrinsicsCalibration', valid=True, logMonoTime=now)
          calibration.extrinsicsCalibration.calStatus = 'calibrated'
          monitoring = messaging.new_message('driverMonitoringState', valid=True, logMonoTime=now)
          sd.sm.update_msgs(now / 1e9, [intent_message.as_reader(), receipt.as_reader(), model.as_reader(),
                                       calibration.as_reader(), monitoring.as_reader()])
          sd.aol_car_state_log_ns = now
          sd.conditional_car_state_valid = cs.canValid and not cs.canTimeout
          sd.events = events
          with patch('openpilot.selfdrive.selfdrived.selfdrived.REPLAY', True), \
               patch.object(sd, 'data_sample', return_value=cs), patch.object(sd, 'update_events'), \
               patch.object(sd, 'update_alerts'), patch.object(sd, 'update_conditional_mode'), \
               patch.object(sd, 'publish_selfdriveState'):
            sd.step()
          actual_lat = expected_lat and tick != 135
          actual_onroad = messaging.new_message('onroadEvents', 0, valid=True, logMonoTime=now)
          actual_onroad.onroadEvents = sd.events.to_msg()
          previous_sd_onroad = actual_onroad.as_reader()
          self.assertFalse(sd.enabled or sd.active, msg=ctx)
          self.assertEqual(sd.aol_axis_decision.lateral_active, actual_lat, msg=ctx)
          self.assertFalse(sd.aol_axis_decision.longitudinal_active, msg=ctx)
          if tick == 135:
            self.assertTrue(sd.aol_authority_lost, msg=ctx)
            self.assertIn(EventName.controlsMismatch, sd.events.names, msg=ctx)
            self.assertFalse(sd.aol_axis_decision.native_acknowledged, msg=ctx)
          if tick == 136:
            self.assertFalse(sd.aol_axis_decision.lateral_active, msg=ctx)
            self.assertFalse(selected.aol_card_intent.allowed_latch, msg=ctx)
          if tick == 137:
            self.assertTrue(selected.aol_card_intent.allowed_latch, msg=ctx)
            self.assertTrue(sd.aol_axis_decision.lateral_active, msg=ctx)
          if tick == 142:
            self.assertTrue(sd.aol_axis_decision.lateral_active, msg=ctx)
            self.assertFalse(selected.aol_card_intent._main_cycle_required, msg=ctx)
          feed(controls, now, tick, active=sd.active, enabled=sd.enabled)
          axis = messaging.new_message('aolAxisState', valid=True, logMonoTime=now)
          axis.aolAxisState.qualified = True
          axis.aolAxisState.nativeAcknowledged = sd.aol_axis_decision.native_acknowledged
          axis.aolAxisState.desiredLateral = sd.aol_axis_decision.desired_lateral
          axis.aolAxisState.desiredLongitudinal = sd.aol_axis_decision.desired_longitudinal
          axis.aolAxisState.lateralActive = sd.aol_axis_decision.lateral_active
          axis.aolAxisState.longitudinalActive = sd.aol_axis_decision.longitudinal_active
          axis.aolAxisState.sessionId = 'volt-brake'
          axis.aolAxisState.observedMonoTime = now
          axis.aolAxisState.validUntilMonoTime = now + 30_000_000
          state = messaging.new_message('carState', valid=True, logMonoTime=now)
          state.carState = cs
          controls.sm.update_msgs((now + 1_000) / 1e9, [axis.as_reader(), receipt.as_reader(), state.as_reader()])
          command, _ = controls.state_control()
          self.assertEqual(command.latActive, actual_lat, msg=ctx)
          self.assertFalse(command.longActive, msg=ctx)
          command.actuators.torque = .02 if command.latActive else 0.
          _, messages = ci.apply(command.as_reader(), now + 2)
          for frame in messages:
            self.assertTrue(native('tx', frame, now // 1000 + 1), (frame, ctx))
          if brake:
            paused += 1
          if tick in (32, 72, 112):
            self.assertTrue(command.latActive, msg=ctx)
            resumed += 1
          if tick == 134:
            # These are policy-unit boundaries; the three cycles use real 8m/s CAN.
            probe = cs.as_reader().as_builder()
            probe.brakePressed = True
            for speed in (9.99, 10., 10.01):
              probe.vEgo = speed
              self.assertEqual(decide_axes(native=receipt_state, standard_lateral=False, standard_longitudinal=False,
                                           intent=intent, car_state=probe, initialized=True, model_ready=True,
                                           no_entry=no_entry, immediate_disable=immediate_disable,
                                           dm_lockout=False, pause_brake_mps=10.).desired_lateral,
                               speed >= 10., (speed, ctx))
          previous = cs
        self.assertEqual(resumed, 3)
        self.assertEqual(paused, 36)
      finally:
        safety.set_alternative_experience(0)
        selected.vehicle_startup.close()

  def test_volt_ascm_transient_acc_real_parser_card_controls_native(self):
    self.exercise_volt_ascm_acc_intent()

  def test_volt_ascm_mixed_critical_acc_fault_requires_main_cycle(self):
    for critical in ('controlsMismatch', 'steerUnavailable', 'permanentEPS'):
      with self.subTest(critical=critical):
        self.exercise_volt_ascm_acc_intent(critical=critical)

  def exercise_volt_ascm_acc_intent(self, *, critical=None):
    from openpilot.cereal import log
    from openpilot.selfdrive.car.car_events import CarEvents
    from openpilot.selfdrive.selfdrived.selfdrived import SelfdriveD
    from openpilot.selfdrive.selfdrived.state import StateMachine
    from openpilot.selfdrive.selfdrived.events import Events, EventName, ET
    from openpilot.starpilot.aol.runtime import AxisDecision
    from openpilot.starpilot.aol.wire import encode_intent
    from opendbc.can import CANParser
    from opendbc.car.gm.bolt_cc import button_bytes
    from opendbc.car.gm.values import AccState, CruiseButtons, GMFlags
    from opendbc.car.gm.tests.test_ascm_intercept import params as ascm_params

    with OpenpilotPrefix(), patch.dict(os.environ, {'SIMULATION': '1', 'REPLAY': '1', 'AOL_REPLAY_RUNTIME': '0'}):
      settings = Params()
      for key in ('AlwaysOnLateral', 'AlphaLongitudinalEnabled', 'GMAutoHold', 'VoltOnePedalMode'):
        settings.put_bool(key, True, block=True)
      factory = ascm_params(CAR.CHEVROLET_VOLT_ASCM, sascm=True, alpha=True, accelerator=True, radar=True)
      selected = self.card(factory, settings)
      cp, ci = selected.CP, selected.CI
      self.assertEqual(cp.safetyConfigs[0].safetyParam, 0xD114)
      self.assertEqual(cp.alternativeExperience, 32)
      self.assertTrue(cp.openpilotLongitudinalControl)
      self.assertFalse(cp.pcmCruise)
      self.assertFalse(cp.flags & GMFlags.PEDAL_LONG)
      controls = Controls()
      self.assertEqual(controls.CP.to_dict(), cp.to_dict())
      sd = SelfdriveD.__new__(SelfdriveD)
      sd.CP, sd.initialized, sd.aol_replay = cp, True, True
      sd.enabled = sd.active = False
      sd.aol_session_id, sd.aol_axis_decision = 'ascm-acc-fault', AxisDecision()
      sd.aol_authority_lost = False
      sd.aol_transport_pause = TransportPause(sd.CP, sd.aol_session_id)
      sd.aol_dm_lateral_inhibit, sd.aol_settings = False, selected.aol_settings
      sd.nostalgia_paddle_cancel = False
      sd.state_machine, sd.events = StateMachine(), Events()
      sd.state_machine.state = log.SelfdriveState.OpenpilotState.enabled
      sd.sm = messaging.SubMaster(['aolIntentWire', 'aolSafetyWire', 'modelV2',
                                   'extrinsicsCalibration', 'driverMonitoringState'])
      car_events = CarEvents(cp)
      previous = structs.CarState()
      packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
      output = CANParser(DBC[cp.carFingerprint][Bus.pt], [('ASCMGasRegenCmd', float('nan'))], 0)
      brake_output = CANParser(DBC[cp.carFingerprint][Bus.chassis], [('EBCMFrictionBrakeCmd', float('nan'))], 2)
      neutral_gas_seen = neutral_brake_seen = False
      safety = libsafety_py.libsafety
      safety.set_alternative_experience(32)
      self.assertEqual(safety.set_safety_hooks(structs.CarParams.SafetyModel.gm, 0xD114), 0)
      safety.init_tests()
      try:
        # The recorded window is already engaged: precede it with real physical SET release.
        for offset, counter, button in ((-20_000_000, 2, CruiseButtons.DECEL_SET),
                                        (-10_000_000, 3, CruiseButtons.UNPRESS)):
          prime_now = 102_289_000_000 + offset
          _, prime_packets = feed_car(SimpleNamespace(update=lambda _: None), packer, prime_now,
                                      counter=counter, active=True, speed=20., camera=True)
          prime_packets = [packet for packet in prime_packets if packet[0] not in (0x1C4, 0x1E1)]
          prime_packets += [packer.make_can_msg('AcceleratorPedal2', 0, {'CruiseState': AccState.ACTIVE}),
                            (0x1E1, button_bytes(button, counter), 0)]
          previous = ci.update([(prime_now, prime_packets)])
          for packet in prime_packets:
            self.assertTrue(native('rx', packet, prime_now // 1000), packet)
          safety.safety_tick()
        self.assertTrue(safety.get_controls_allowed())
        # Fault begins102.389s and clears102.469s: eight100Hz samples, no button/MAIN edge.
        for tick in range(36):
          now = 102_289_000_000 + tick * 10_000_000
          fault = 10 <= tick < 18
          main = tick not in (26, 27)
          ordinary = tick < 10
          _, packets = feed_car(SimpleNamespace(update=lambda _: None), packer, now,
                                counter=tick % 4, active=ordinary, speed=20., camera=True)
          packets = [packet for packet in packets if packet[0] not in (0x1C4, 0x3D1, 0x184)]
          packets += [packer.make_can_msg('AcceleratorPedal2', 0, {'CruiseState': AccState.FAULTED if fault else AccState.ACTIVE if ordinary else 0}),
                      packer.make_can_msg('ECMEngineStatus', 0, {'CruiseMainOn': int(main)}),
                      packer.make_can_msg('PSCMStatus', 0, {'LKATorqueDeliveredStatus': 3 if critical == 'permanentEPS' and fault else 2 if tick == 20 else 1}),
                      packer.make_can_msg('ASCMActiveCruiseControlStatus', 2, {'ACCCruiseState': 0})]
          self.assertFalse(any(address == 0x201 for address, _, _ in packets))
          cs = ci.update([(now, packets)])
          self.assertTrue(cs.canValid, tick)
          self.assertEqual(cs.accFaulted, fault)
          self.assertEqual(cs.cruiseState.available, main)
          for packet in packets:
            self.assertTrue(native('rx', packet, now // 1000), (tick, packet))
          safety.safety_tick()
          safety.set_aol_test_heartbeat(True)
          events = car_events.update(cs, previous, structs.CarControl())
          if fault and critical in ('controlsMismatch', 'steerUnavailable'):
            events.add(getattr(EventName, critical))
          self.assertEqual(EventName.accFaulted in events.names, fault)
          if fault:
            self.assertTrue(events.contains(ET.IMMEDIATE_DISABLE))
          onroad = messaging.new_message('onroadEvents', 0, valid=True, logMonoTime=now)
          onroad.onroadEvents = events.to_msg()
          selected.sm.update_msgs(now / 1e9, [onroad.as_reader()])
          disarm = selected.aol_disarming_fault(cs, now, now)
          self.assertEqual(disarm, bool(fault and critical), (tick, critical))
          selected.aol_card_intent.update(cs, fault_active=disarm, now_ns=now, standard_enabled=ordinary)
          expected_latch = main and not disarm
          self.assertEqual(selected.aol_card_intent.allowed_latch, expected_latch, (tick, critical))
          allowed, pause_lat, pause_long = selected.aol_card_intent.output(cs)
          intent_state = IntentState('card', tick + 1, now, now, now + 30_000_000,
                                     allowed, pause_lat, pause_long, True, True)
          desired = decide_axes(standard_lateral=ordinary, standard_longitudinal=ordinary,
                                intent=intent_state, native=None, car_state=cs, initialized=True,
                                model_ready=True, no_entry=events.contains(ET.NO_ENTRY),
                                immediate_disable=events.contains(ET.IMMEDIATE_DISABLE),
                                dm_lockout=False, pause_brake_mps=5.)
          safety.aol_set_host_request(int(desired.desired_lateral) | (int(desired.desired_longitudinal) << 1))
          mask = safety.aol_get_permission_mask()
          receipt_state = SafetyState(1, True, now, now + 200_000_000,
                                     int(structs.CarParams.SafetyModel.gm), 0xD114,
                                     bool(mask & 1), bool(mask & 2), desired.desired_lateral,
                                     desired.desired_longitudinal, 'panda', 'ascm-acc-fault')
          intent = messaging.new_message('aolIntentWire', 0, valid=True, logMonoTime=now)
          intent.aolIntentWire = encode_intent(intent_state)
          receipt = messaging.new_message('aolSafetyWire', 0, valid=True, logMonoTime=now)
          receipt.aolSafetyWire = encode_safety(receipt_state)
          model = messaging.new_message('modelV2', valid=True, logMonoTime=now)
          calibration = messaging.new_message('extrinsicsCalibration', valid=True, logMonoTime=now)
          calibration.extrinsicsCalibration.calStatus = 'calibrated'
          monitoring = messaging.new_message('driverMonitoringState', valid=True, logMonoTime=now)
          sd.sm.update_msgs(now / 1e9, [intent.as_reader(), receipt.as_reader(), model.as_reader(),
                                       calibration.as_reader(), monitoring.as_reader()])
          sd.aol_car_state_log_ns = now
          sd.conditional_car_state_valid = cs.canValid and not cs.canTimeout
          sd.events = events
          with patch('openpilot.selfdrive.selfdrived.selfdrived.REPLAY', True), \
               patch.object(sd, 'data_sample', return_value=cs), patch.object(sd, 'update_events'), \
               patch.object(sd, 'update_alerts'), patch.object(sd, 'update_conditional_mode'), \
               patch.object(sd, 'publish_selfdriveState'):
            sd.step()
          expected_lat = expected_latch and not fault and tick != 20
          self.assertEqual(sd.aol_axis_decision.lateral_active, expected_lat, (tick, critical))
          self.assertEqual(sd.aol_axis_decision.longitudinal_active, ordinary, (tick, critical))
          if tick >= 10:
            self.assertFalse(sd.enabled)  # Clearing ACC fault never silently resumes longitudinal.
          feed(controls, now, tick, active=sd.active, enabled=sd.enabled)
          axis = messaging.new_message('aolAxisState', valid=tick != 23, logMonoTime=now)
          axis.aolAxisState.qualified = axis.aolAxisState.nativeAcknowledged = True
          axis.aolAxisState.desiredLateral = sd.aol_axis_decision.desired_lateral
          axis.aolAxisState.desiredLongitudinal = sd.aol_axis_decision.desired_longitudinal
          axis.aolAxisState.lateralActive = sd.aol_axis_decision.lateral_active
          axis.aolAxisState.longitudinalActive = sd.aol_axis_decision.longitudinal_active
          axis.aolAxisState.sessionId = 'ascm-acc-fault'
          axis.aolAxisState.observedMonoTime = now
          axis.aolAxisState.validUntilMonoTime = now + 30_000_000
          state = messaging.new_message('carState', valid=True, logMonoTime=now)
          state.carState = cs
          controls.sm.update_msgs((now + 1_000) / 1e9, [axis.as_reader(), receipt.as_reader(), state.as_reader()])
          command, _ = controls.state_control()
          self.assertEqual(command.latActive, expected_lat and tick != 23, (tick, critical))
          self.assertEqual(command.longActive, ordinary, (tick, critical))
          command.actuators.torque = .02 if command.latActive else 0.
          _, messages = ci.apply(command.as_reader(), now + 2)
          output.update([(now, messages)])
          brake_output.update([(now, messages)])
          if fault:
            if any(address == 0x2CB for address, _, _ in messages):
              self.assertEqual(output.vl['ASCMGasRegenCmd']['GasRegenCmd'], -650.)
              self.assertFalse(output.vl['ASCMGasRegenCmd']['GasRegenCmdActive'])
              neutral_gas_seen = True
            if any(address == 0x315 for address, _, _ in messages):
              self.assertEqual(brake_output.vl['EBCMFrictionBrakeCmd']['FrictionBrakeCmd'], 0)
              neutral_brake_seen = True
          for message in messages:
            self.assertTrue(native('tx', message, now // 1000 + 1), (tick, message))
          previous = cs
        self.assertTrue(neutral_gas_seen)
        self.assertTrue(neutral_brake_seen)
        stale_now = now + 2_000_000_000
        for _ in range(10):
          stale_now += 10_000_000
          stale_cs = ci.update([(stale_now, [])])
        self.assertFalse(stale_cs.canValid)
        safety.set_timer(stale_now // 1000)
        safety.safety_tick()
        stale_intent = IntentState('card', 100, stale_now, stale_now, stale_now + 30_000_000,
                                   selected.aol_card_intent.allowed_latch, False, False, True, True)
        stale_decision = decide_axes(standard_lateral=False, standard_longitudinal=False,
                                     intent=stale_intent, native=receipt_state, car_state=stale_cs,
                                     initialized=True, model_ready=True, no_entry=False,
                                     immediate_disable=False, dm_lockout=False, pause_brake_mps=5.)
        self.assertFalse(stale_decision.lateral_active)
        self.assertFalse(stale_decision.longitudinal_active)
        safety.aol_set_host_request(0)
        feed(controls, stale_now, 100, active=False, enabled=False, can_valid=False, can_timeout=True)
        # Last axis/receipt are deliberately not restamped: their exact bytes have expired.
        stale_state = messaging.new_message('carState', valid=False, logMonoTime=stale_now)
        stale_state.carState = stale_cs
        controls.sm.update_msgs(stale_now / 1e9, [stale_state.as_reader()])
        withdrawn, _ = controls.state_control()
        self.assertFalse(withdrawn.latActive)
        self.assertFalse(withdrawn.longActive)
        _, neutral_messages = ci.apply(withdrawn.as_reader(), stale_now + 2)
        for message in neutral_messages:
          self.assertTrue(native('tx', message, stale_now // 1000 + 1), message)
      finally:
        safety.set_alternative_experience(0)
        selected.vehicle_startup.close()

  def test_transient_acc_intent_exception_stays_inside_nonpedal_ascm_long(self):
    from openpilot.selfdrive.selfdrived.events import Events, EventName
    from opendbc.car.gm.tests.test_volt_transitions import volt_ascm_pedal_params
    cases = (ordinary_params(CAR.CHEVROLET_VOLT_ASCM, alpha=False, sascm=True, accelerator=True, radar=True),
             volt_ascm_pedal_params(be=True, radar=True, sascm=True, alpha=True),
             ordinary_params(CAR.GMC_ACADIA_ASCM, alpha=True, sascm=True, accelerator=True, radar=True))
    for factory in cases:
      with self.subTest(identity=factory.carFingerprint, flags=factory.flags), OpenpilotPrefix(), \
           patch.dict(os.environ, {'SIMULATION': '1', 'REPLAY': '1', 'AOL_REPLAY_RUNTIME': '0'}):
        settings = Params()
        settings.put_bool('AlwaysOnLateral', True, block=True)
        selected = self.card(factory, settings)
        try:
          cs = structs.CarState(canValid=True, gearShifter='drive', vEgo=20., accFaulted=True)
          cs.cruiseState.available = True
          events = Events()
          events.add(EventName.accFaulted)
          onroad = messaging.new_message('onroadEvents', 0, valid=True, logMonoTime=1_000_000_000)
          onroad.onroadEvents = events.to_msg()
          selected.sm.update_msgs(1., [onroad.as_reader()])
          self.assertTrue(selected.aol_disarming_fault(cs, 1_000_000_000, 1_000_000_000))
          selected.aol_card_intent.update(cs, fault_active=True)
          cs.accFaulted = False
          selected.aol_card_intent.update(cs, fault_active=False)
          self.assertTrue(selected.aol_card_intent.allowed_latch)
        finally:
          selected.vehicle_startup.close()

  def test_sdgm_optional_saved_selection_actual_card_controls_cap(self):
    from opendbc.car.gm.values import CarControllerParams, is_gm_auto_hold, is_volt_one_pedal, volt_sdgm_accepted_accel_max
    from openpilot.starpilot.saved_source import read_saved
    for c9 in (False, True):
      for held, one_pedal, word in ((True, False, 0x5487 if c9 else 0x5087),
                                    (False, True, 0xD108 if c9 else 0xD107),
                                    (True, True, 0xD118 if c9 else 0xD117)):
        with self.subTest(word=hex(word)), OpenpilotPrefix(), \
             patch.dict(os.environ, {'SIMULATION': '1', 'REPLAY': '1', 'AOL_REPLAY_RUNTIME': '0'}):
          settings = Params()
          for key, value in (('AlwaysOnLateral', True), ('AlphaLongitudinalEnabled', True),
                             ('DisableOpenpilotLongitudinal', False), ('SafeMode', False),
                             ('GMAutoHold', held), ('VoltOnePedalMode', one_pedal)):
            settings.put_bool(key, value, block=True)
          saved = {key: read_saved(settings, key, 8) for key in ('GMAutoHold', 'VoltOnePedalMode')}
          selected = self.card(sdgm_params(brake_c9=c9), settings)
          try:
            cp = selected.CP
            self.assertEqual(cp.safetyConfigs[0].safetyParam, word)
            self.assertTrue(cp.openpilotLongitudinalControl)
            self.assertFalse(cp.pcmCruise)
            self.assertEqual(cp.alternativeExperience, 32)
            self.assertTrue(is_gm_auto_hold(cp))
            self.assertEqual(is_volt_one_pedal(cp), one_pedal)
            self.assertEqual(selected.CI.CC.gm_auto_hold, held)
            self.assertEqual(selected.CI.CC.volt_one_pedal, one_pedal)
            self.assertEqual(CarControllerParams(cp).MAX_GAS, 2041)
            self.assertEqual(selected.CI.CC.params.MAX_GAS, 2041)
            controls = Controls()
            self.assertEqual(controls.CP.to_dict(), cp.to_dict())
            upper = volt_sdgm_accepted_accel_max(cp, 20.)
            limits = controls.CI.get_pid_accel_limits(cp, 20., 20.)
            self.assertEqual(limits[1], upper)
            cs = structs.CarState(vEgo=20., canValid=True, gearShifter='drive')
            controls.LoC.update(True, cs, upper + 10., False, limits)
            self.assertEqual(controls.LoC.pid.pos_limit, upper)
            self.assertLessEqual(controls.LoC.last_output_accel, upper)
            self.assertEqual({key: read_saved(settings, key, 8) for key in saved}, saved)
          finally:
            selected.vehicle_startup.close()
