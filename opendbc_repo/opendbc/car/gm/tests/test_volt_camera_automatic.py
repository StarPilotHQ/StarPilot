"""Recorded no-camera/no-ACC Volt discovery must retain distinct existing owners."""
from contextlib import ExitStack
from copy import deepcopy
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from opendbc.car import car_helpers, structs
from opendbc.car.can_definitions import CanData
from opendbc.car.gm.discovery import automatic_volt_candidate
from opendbc.car.gm.fingerprints import FINGERPRINTS
from opendbc.car.gm.interface import CarInterface
from opendbc.car.gm.values import CAR, GMFlags, is_volt_camera_removed


def recorded_fingerprint():
  value = json.loads((Path(__file__).parent / 'testdata' / 'volt_camera_f1_fingerprint.json').read_text())
  return {int(bus): {int(address): length for address, length in messages.items()} for bus, messages in value.items()}


def remaining():
  return {0: [CAR.CHEVROLET_VOLT, CAR.CHEVROLET_VOLT_CC]}


def discover(fingerprint):
  packets = [CanData(address, bytes(length), bus)
             for bus, messages in fingerprint.items() for address, length in messages.items()]
  return car_helpers.can_fingerprint(lambda wait_for_one=False: [packets])


@pytest.mark.parametrize('alpha', [False, True])
@pytest.mark.parametrize('release', [False, True])
def test_recorded_collision_reaches_conventional_cruise_owner(alpha, release):
  fp = recorded_fingerprint()
  with patch.object(car_helpers, 'automatic_volt_candidate', side_effect=lambda candidate, *_: candidate):
    old, _ = discover(fp)
  assert old is None  # The legacy gateway and CC pedal signatures both survive.
  selected, observed = discover(fp)
  assert selected == CAR.CHEVROLET_VOLT_CC and observed == fp
  cp = CarInterface.get_params(selected, observed, [], alpha, release, False)
  assert cp.dashcamOnly == release and cp.networkLocation == structs.CarParams.NetworkLocation.gateway
  assert not is_volt_camera_removed(cp)
  assert cp.openpilotLongitudinalControl == (not release)
  assert cp.pcmCruise == release
  assert cp.safetyConfigs[0].safetyParam == (0 if release else 0xC163)
  if not release:
    assert cp.flags & GMFlags.NO_ACCELERATOR_POS_MSG


@pytest.mark.parametrize('address,length', [(0xD3, 2), (0x130, 1), (0x17D, 6), (0x210, 4), (0x236, 5),
  (0x184, 8), (0x34A, 5), (0x1C4, 8), (0xC9, 8), (0x1E1, 7), (0xF1, 6)])
@pytest.mark.parametrize('malformed', [False, True])
def test_missing_or_malformed_source_cannot_resolve_collision(address, length, malformed):
  fp = recorded_fingerprint()
  for bus in (0, 2):
    if malformed:
      fp[bus][address] = length - 1
    else:
      del fp[bus][address]
  assert automatic_volt_candidate(None, fp, remaining()) is None


@pytest.mark.parametrize('case', ['analog_be', 'pedal', 'malformed_pedal', 'sascm', 'camera', 'radar',
  'camera_missing', 'camera_different', 'extra_bus', 'unknown_candidates', 'nonvolt_candidates', 'no_collision'])
def test_other_topologies_and_ambiguities_keep_normal_discovery(case):
  fp, candidates = recorded_fingerprint(), remaining()
  if case in ('analog_be', 'pedal', 'malformed_pedal', 'sascm', 'camera'):
    address, size = {'analog_be': (0xBE, 6), 'pedal': (0x201, 6), 'malformed_pedal': (0x201, 5),
                     'sascm': (0x2FF, 8), 'camera': (0x320, 6)}[case]
    fp[0][address] = fp[2][address] = size
  elif case == 'radar':
    fp[1][0x460] = 8
  elif case == 'camera_missing':
    fp[2] = {}
  elif case == 'camera_different':
    fp[2][0x184] = 7
  elif case == 'extra_bus':
    fp[3][0x184] = 8
  elif case == 'unknown_candidates':
    candidates[0] = []
  elif case == 'nonvolt_candidates':
    candidates[0].append(CAR.HOLDEN_ASTRA)
  else:
    candidates[0] = [CAR.CHEVROLET_VOLT]
  before = deepcopy(fp)
  assert automatic_volt_candidate(None, fp, candidates) is None
  assert fp == before


@pytest.mark.parametrize('mirror', [False, True])
def test_every_existing_gm_fingerprint_retains_its_original_result(mirror):
  for platform, variants in FINGERPRINTS.items():
    for index, observed in enumerate(variants):
      fp = {0: observed}
      if mirror:
        fp[2] = dict(observed)
      with patch.object(car_helpers, 'automatic_volt_candidate', side_effect=lambda candidate, *_: candidate):
        before, before_fp = discover(fp)
      after, after_fp = discover(fp)
      assert after == before and after_fp == before_fp, (platform, index)


@pytest.mark.parametrize('candidate', [CAR.CHEVROLET_VOLT, CAR.CHEVROLET_VOLT_CC,
  CAR.CHEVROLET_VOLT_ASCM, CAR.CHEVROLET_VOLT_2019, CAR.CHEVROLET_VOLT_CAMERA, CAR.HOLDEN_ASTRA])
@pytest.mark.parametrize('alpha', [False, True])
def test_identified_or_explicit_vehicle_is_never_reinterpreted(candidate, alpha):
  fp = recorded_fingerprint()
  assert automatic_volt_candidate(candidate, fp, remaining()) == candidate
  with patch.object(car_helpers, 'fingerprint', return_value=(CAR.CHEVROLET_VOLT_CAMERA, fp,
       '00000000000000000', [], structs.CarParams.FingerprintSource.can, True)):
    ci = car_helpers.get_car(lambda wait_for_one=False: [], lambda _: None, lambda _: None,
                             alpha, False, forced_candidate=candidate)
  assert ci.CP.carFingerprint == candidate
  assert ci.CP.fingerprintSource == structs.CarParams.FingerprintSource.fixed


def test_auto_card_and_controls_use_correct_profile_but_start_with_commands_off():
  from openpilot.common.params import Params
  from openpilot.common.prefix import OpenpilotPrefix
  from openpilot.selfdrive.car.card import Car
  from openpilot.selfdrive.controls.controlsd import Controls
  from openpilot.starpilot.lateral.tests.test_lane_runtime import feed
  from openpilot.starpilot.vehicle_selection import encode

  fp = recorded_fingerprint()
  packets = [CanData(address, bytes(size), bus) for bus, messages in fp.items() for address, size in messages.items()]

  def receive(wait_for_one=False):
    return [packets] if wait_for_one else []

  def identify(*args, **kwargs):
    return car_helpers.get_car(receive, *args[1:], **kwargs)
  with OpenpilotPrefix(), ExitStack() as cleanup:
    cleanup.enter_context(patch.dict(os.environ, {'SKIP_FW_QUERY': '1', 'FINGERPRINT': '', 'SIMULATION': '1', 'REPLAY': '1'}))
    settings = Params()
    cleanup.callback(settings._finalizer)
    settings.put_bool('OpenpilotEnabledToggle', True, block=True)
    settings.put_bool('AlwaysOnLateral', False, block=True)
    settings.put_bool('IsReleaseBranch', False, block=True)
    settings.put('VehicleSelection', json.loads(encode(None)), block=True)
    with patch('openpilot.selfdrive.car.card.messaging.recv_one_retry', return_value=SimpleNamespace(can=[1])), \
         patch('openpilot.selfdrive.car.card.get_car', side_effect=identify):
      card = Car()
    cleanup.callback(card.vehicle_startup.close)
    cleanup.callback(card.params._finalizer)
    assert card.CP.carFingerprint == CAR.CHEVROLET_VOLT_CC
    assert card.CP.fingerprintSource == structs.CarParams.FingerprintSource.can
    assert not card.CP.dashcamOnly and not card.CP.passive
    assert card.CP.safetyConfigs[0].safetyParam == 0xC163
    assert card.CP.openpilotLongitudinalControl
    controls = Controls()
    cleanup.callback(controls.params._finalizer)
    assert controls.CP.carFingerprint == card.CP.carFingerprint
    feed(controls, 1_000_000_000, 0, active=False, enabled=False, can_valid=False, can_timeout=True)
    command, _ = controls.state_control()
    assert not command.enabled and not command.latActive and not command.longActive
