"""Cold automatic Ford discovery must not put CAN2 on the unrelated OBD bus."""
from unittest import mock

import pytest

from opendbc.car import car_helpers, structs
from opendbc.car.can_definitions import CanData
from opendbc.car.ford.discovery import normal_bus_query_brand
from opendbc.car.ford.values import CAR
from opendbc.car.hyundai.discovery import NORMAL_BUS_LAYOUT, normal_bus_topology
from openpilot.selfdrive.car.tests.test_registered_ford_discovery import FordEcus, VIN

# Six original bus/address/DLC/payload samples from bfef.../00000003.
RECORDED = (
  (0, 130, '770814008b004000'),
  (0, 357, '50c0000000000000'),
  (0, 516, 'd1267d0000d10000'),
  (0, 535, '268f268b26a226aa'),
  (2, 970, '1e7d17f200000000'),
  (2, 982, '051f7b8f9a067ffc'),
)


MAVERICK_RECORDED = (
  (0, 130, '69081400b2004000'),
  (0, 357, '50c0000000000000'),
  (0, 516, 'f8727d00007c0000'),
  (0, 535, '052004a6050c048d'),
  (2, 970, '1e80080000000000'),
  (2, 979, '7d10007ce0801000'),
)


def recorded_frames(layout=RECORDED):
  return [CanData(address, bytes.fromhex(data), bus) for bus, address, data in layout]


def brand(frames):
  return normal_bus_query_brand(lambda **kwargs: [frames], normal_bus_topology)


def test_recorded_normal_harness_selects_ford_queries_only():
  assert brand(recorded_frames()) == 'ford'


@pytest.mark.parametrize('layout', [RECORDED, MAVERICK_RECORDED])
@pytest.mark.parametrize('missing', range(6))
def test_partial_layout_does_not_choose_ford(layout, missing):
  frames = recorded_frames(layout)
  del frames[missing]
  assert brand(frames) is None


@pytest.mark.parametrize('layout', [RECORDED, MAVERICK_RECORDED])
@pytest.mark.parametrize('bad', range(6))
def test_wrong_bus_dlc_and_returned_frames_do_not_choose_ford(layout, bad):
  for bus_delta, size in ((1, 8), (128, 8), (0, 7), (0, 12)):
    frames = recorded_frames(layout)
    original = frames[bad]
    frames[bad] = CanData(original.address, bytes(size), original.src + bus_delta)
    assert brand(frames) is None


def test_conflicting_dlc_cannot_be_overwritten_by_a_good_copy():
  frames = recorded_frames()
  assert brand([CanData(frames[0].address, bytes(7), frames[0].src), *frames]) is None


def test_existing_hyundai_and_unknown_layouts_keep_their_paths():
  frames = [CanData(address, bytes(length), bus) for (bus, address), length in NORMAL_BUS_LAYOUT.items()]
  assert brand(frames) == 'hyundai'
  assert brand([]) is None
  assert brand([*frames, *recorded_frames()]) == 'hyundai'


def test_query_configuration_must_still_allow_normal_ford_bus():
  with mock.patch('opendbc.car.ford.discovery.normal_bus0_queries', return_value=False):
    assert brand(recorded_frames()) is None


def test_shared_probe_uses_existing_read_bound():
  calls = []
  def recv(**kwargs):
    calls.append(kwargs)
    return []
  assert normal_bus_query_brand(recv, lambda recv: normal_bus_topology(recv, clock=lambda: 1.)) is None
  assert len(calls) == 24
  assert all(call == {'wait_for_one': True} for call in calls)


@pytest.mark.parametrize('firmware_known', [False, True])
@pytest.mark.parametrize('candidate', [CAR.FORD_MUSTANG_MACH_E_MK1, CAR.FORD_MAVERICK_MK1])
def test_cold_automatic_uses_real_vin_isotp_fw_and_identity_matching(firmware_known, candidate):
  firmware = {(0x706, b'\x22\xf1\x88'): b'RJ6T-14H102-BAE'.ljust(24, b'\0'),
              (0x730, b'\x22\xf1\x88'): b'LJ9C-14D003-FA'.ljust(24, b'\0'),
              (0x760, b'\x22\xf1\x88'): b'LK9C-2D053-CN'.ljust(24, b'\0'),
              (0x764, b'\x22\xf1\x88'): b'ML3T-14D049-AL'.ljust(24, b'\0')}
  layout = RECORDED
  if candidate == CAR.FORD_MAVERICK_MK1:
    firmware = {(address, b'\x22\xf1\x88'): version.ljust(24, b'\0') for address, version in (
      (0x706, b'NZ6T-14F397-AC'), (0x730, b'NZ6C-14D003-AL'),
      (0x760, b'PZ6C-2D053-EF'), (0x764, b'NZ6T-14D049-AA'))}
    layout = MAVERICK_RECORDED
  if not firmware_known:
    firmware = dict.fromkeys(firmware, b'unknown')
  transport = FordEcus(firmware=firmware, background=recorded_frames(layout))
  with mock.patch.dict(car_helpers.os.environ, {'FINGERPRINT': '', 'SKIP_FW_QUERY': '', 'DISABLE_FW_CACHE': ''}), \
       mock.patch.object(car_helpers.time, 'monotonic', side_effect=lambda: transport.clock), \
       mock.patch.object(car_helpers.time, 'sleep', side_effect=transport.sleep):
    ci = car_helpers.get_car(transport.recv, transport.send, transport.mux, False, False)
  assert transport.mux_calls and set(transport.mux_calls) == {False}
  assert transport.sent and all(frame.src in (0, 4) for frame in transport.sent)
  assert any(frame.dat[0] >> 4 == 3 for frame in transport.sent)
  if firmware_known:
    assert ci.CP.carFingerprint == candidate
    assert ci.CP.fingerprintSource == structs.CarParams.FingerprintSource.fw
    assert ci.CP.carVin == VIN
    assert {(fw.address, fw.fwVersion) for fw in ci.CP.carFw} == {(address, data) for (address, _), data in firmware.items()}
  else:
    assert ci.CP.carFingerprint != candidate
    assert not ci.CP.safetyConfigs or ci.CP.safetyConfigs[-1].safetyModel != structs.CarParams.SafetyModel.ford


def test_hyundai_mismatch_does_not_abort_remaining_ford_window():
  packets = iter([[CanData(0x1A0, bytes(8), 1)], recorded_frames()])
  def recv(**kwargs):
    return [next(packets, [])]
  assert normal_bus_query_brand(recv, normal_bus_topology) == 'ford'


def test_hyundai_early_exit_does_not_extend_outer_deadline():
  calls = []
  times = iter((1., 1.13))
  def recv(**kwargs):
    calls.append(kwargs)
    return []
  assert normal_bus_query_brand(recv, lambda recv: False, clock=lambda: next(times)) is None
  assert not calls
