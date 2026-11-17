import unittest
import pytest
from opendbc.can import CANPacker
from opendbc.car import structs
from opendbc.car.tesla.interface import CarInterface
from opendbc.car.tesla.carstate import CarState
from opendbc.car.tesla.carcontroller import CarController
from opendbc.car.tesla.hw1 import get_hw1_can_parsers
from opendbc.car.tesla.values import CAR, DBC
from opendbc.car.vehicle_model import VehicleModel, calc_slip_factor


class TestModelSHW1Host(unittest.TestCase):
  def test_hw1_params_and_model_are_distinct(self):
    for long in [False, True]:
      with self.subTest(long=long):
        cp = CarInterface.get_non_essential_params(CAR.TESLA_MODEL_S_HW1)
        cp = CarInterface._get_params(cp, CAR.TESLA_MODEL_S_HW1, {0: {}, 1: {}, 2: {}}, [], long, False, False)
        assert cp.safetyConfigs[0].safetyParam == 16 + int(long)
        assert cp.openpilotLongitudinalControl == long
        assert cp.pcmCruise
        model = VehicleModel(cp)
        assert cp.steerRatio == 15 and cp.wheelbase == pytest.approx(2.96)
        assert calc_slip_factor(model) == pytest.approx(-0.0005666493436310427)
        assert cp.radarUnavailable

  def test_hw1_literal_can_state_and_controller_stale_cancel(self):
    cp = CarInterface.get_non_essential_params(CAR.TESLA_MODEL_S_HW1)
    cp = CarInterface._get_params(cp, CAR.TESLA_MODEL_S_HW1, {0: {}, 1: {769: 8}, 2: {}}, [], True, False, False)
    cs = CarState(cp)
    parsers = get_hw1_can_parsers(cp)
    packer = CANPacker('tesla_can')
    messages = [
      packer.make_can_msg('DI_torque1', 0, {'DI_pedalPos': 1}),
      packer.make_can_msg('DI_torque2', 0, {'DI_gear': 4}),
      packer.make_can_msg('ESP_B', 0, {'ESP_vehicleSpeed': 72}),
      packer.make_can_msg('BrakeMessage', 0, {'driverBrakeStatus': 2}),
      packer.make_can_msg('EPAS_sysStatus', 0, {'EPAS_internalSAS': 12.5, 'EPAS_handsOnLevel': 1, 'EPAS_eacStatus': 2}),
      packer.make_can_msg('STW_ANGLHP_STAT', 0, {'StW_AnglHP_Spd': 3}),
      packer.make_can_msg('DI_state', 0, {'DI_cruiseState': 2, 'DI_speedUnits': 1, 'DI_hw1CruiseSet': 100}),
      packer.make_can_msg('GTW_carState', 0, {}),
      packer.make_can_msg('SDM1', 0, {'SDM_bcklDrivStatus': 1}),
      packer.make_can_msg('DAS_control', 2, {'DAS_accState': 4}),
      packer.make_can_msg('DAS_steeringControl', 2, {}),
    ]
    for parser in parsers.values():
      parser.update([(1000000000, messages)])
    ret = cs.update(parsers)
    assert ret.vEgoRaw == pytest.approx(20.0)
    assert ret.gasPressed and ret.brakePressed
    assert ret.steeringAngleDeg == pytest.approx(-12.5, abs=0.1)
    assert ret.steeringRateDeg == pytest.approx(-3.0, abs=0.1)
    assert ret.cruiseState.enabled and ret.cruiseState.speed == pytest.approx(100 / 3.6)
    assert not ret.seatbeltUnlatched
    cs.out = ret
    cc = structs.CarControl.new_message()
    cc.longActive = True
    cc.cruiseControl.cancel = True
    cc.actuators.accel = 2.0
    controller = CarController(DBC[cp.carFingerprint], cp)
    _, sends = controller.update(cc.as_reader(), cs, 0)
    assert {msg[0] for msg in sends} == {1160, 697}
    assert all(msg[2] == 0 for msg in sends)
    from opendbc.can import CANParser

    decoder = CANParser('tesla_can', [('DAS_control', 0)], 0)
    decoder.update([(2000000000, sends)])
    signals = decoder.vl['DAS_control']
    assert signals['DAS_accState'] == 13
    assert signals['DAS_accelMin'] == pytest.approx(0, abs=0.05)
    assert signals['DAS_accelMax'] == pytest.approx(0, abs=0.05)
    assert signals['DAS_setSpeed'] == pytest.approx(72.0)

  def test_hw1_all_cruise_status_words(self):
    for status in range(16):
      with self.subTest(status=status):
        cp = CarInterface.get_non_essential_params(CAR.TESLA_MODEL_S_HW1)
        cs = CarState(cp)
        parsers = get_hw1_can_parsers(cp)
        msg = CANPacker('tesla_can').make_can_msg('DI_state', 0, {'DI_cruiseState': status})
        for parser in parsers.values():
          parser.update([(1000000000, [msg])])
        ret = cs.update(parsers)
        assert ret.cruiseState.enabled == (status in (2, 3, 4, 6, 7))
        assert ret.cruiseState.available == (status in (1, 2, 3, 4, 6, 7))
        assert ret.accFaulted == (status == 5)

  def test_hw1_epas_fault_messages(self):
    for status in range(8):
      for error in range(16):
        with self.subTest(status=status, error=error):
          cp = CarInterface.get_non_essential_params(CAR.TESLA_MODEL_S_HW1)
          cs = CarState(cp)
          parsers = get_hw1_can_parsers(cp)
          msg = CANPacker('tesla_can').make_can_msg('EPAS_sysStatus', 0, {'EPAS_eacStatus': status, 'EPAS_eacErrorCode': error})
          for parser in parsers.values():
            parser.update([(1000000000, [msg])])
          ret = cs.update(parsers)
          assert ret.steerFaultPermanent == (status == 3)
          assert ret.steerFaultTemporary == (status == 0)
          assert ret.steeringDisengage == (status == 0 and error == 9)

  def test_hw1_alpha_off_cancel_burst_is_neutral(self):
    from opendbc.can import CANParser

    cp = CarInterface.get_non_essential_params(CAR.TESLA_MODEL_S_HW1)
    cp = CarInterface._get_params(cp, CAR.TESLA_MODEL_S_HW1, {0: {}, 1: {}, 2: {}}, [], False, False, False)
    cs = CarState(cp)
    cs.out = structs.CarState.new_message()
    cs.out.vEgo = 20
    cs.das_control = {'DAS_controlCounter': 7}
    cc = structs.CarControl.new_message()
    cc.cruiseControl.cancel = True
    cc.actuators.accel = 2.5
    controller = CarController(DBC[cp.carFingerprint], cp)
    decoder = CANParser('tesla_can', [('DAS_control', 0)], 0)
    for frame in range(20):
      _, sends = controller.update(cc.as_reader(), cs, frame * 10000000)
      long = [msg for msg in sends if msg[0] == 697]
      assert len(long) == 1
      decoder.update([(1000000000 + frame * 10000000, long)])
      signals = decoder.vl['DAS_control']
      assert signals['DAS_accState'] == 13
      assert signals['DAS_accelMin'] == pytest.approx(0, abs=0.05)
      assert signals['DAS_accelMax'] == pytest.approx(0, abs=0.05)
      assert signals['DAS_controlCounter'] == 0

  def test_actual_host_output_is_admitted_by_paired_native(self):
    for long in [False, True]:
      for cancel in [False, True]:
        with self.subTest(long=long, cancel=cancel):
          from opendbc.safety.tests import test_tesla_hw1 as native

          cp = CarInterface.get_non_essential_params(CAR.TESLA_MODEL_S_HW1)
          cp = CarInterface._get_params(cp, CAR.TESLA_MODEL_S_HW1, {0: {}, 1: {}, 2: {}}, [], long, False, False)
          cs = CarState(cp)
          cs.out = structs.CarState.new_message()
          cs.out.vEgo = 20
          cs.out.vEgoRaw = 20
          cs.das_control = {'DAS_controlCounter': 7}
          cs.hands_on_level = 0
          cc = structs.CarControl.new_message()
          cc.latActive = True
          cc.longActive = long
          cc.cruiseControl.cancel = cancel
          cc.actuators.accel = 1.0
          cc.actuators.steeringAngleDeg = 10.0
          controller = CarController(DBC[cp.carFingerprint], cp)
          native.reset(16 + int(long))
          native.lib.set_controls_allowed(True)
          native.lib.set_angle_meas(0, 0)
          for _ in range(6):
            native.rx('ESP_B', 0, {'ESP_vehicleSpeed': 72})
          native.lib.set_controls_allowed(True)
          for frame in range(20):
            _, sends = controller.update(cc.as_reader(), cs, frame * 10000000)
            for message in sends:
              assert native.lib.safety_tx_hook(native.packet(message)), (long, cancel, frame, message)

  def test_hw1_default_production_fw_match_is_unique(self):
    for version in [b'1016704-00-HAA' + b'\x00' * 10, b'\x10\x00A']:
      with self.subTest(version=version):
        from opendbc.car.fw_versions import match_fw_to_car

        fw = structs.CarParams.CarFw(ecu=structs.CarParams.Ecu.eps, address=1840, brand='tesla', fwVersion=version)
        exact, matches = match_fw_to_car([fw], '', log=False)
        assert exact and matches == {CAR.TESLA_MODEL_S_HW1}

  def test_unknown_or_malformed_eps_cannot_identify_hw1(self):
    for version in [b'', b'1016704-00-HAA', b'\x10\x00B', b'1016704-00-HAA' + b'\x00' * 9, b'1016704-00-HAA' + b'\x00' * 11]:
      with self.subTest(version=version):
        from opendbc.car.fw_versions import match_fw_to_car

        fw = structs.CarParams.CarFw(ecu=structs.CarParams.Ecu.eps, address=1840, brand='tesla', fwVersion=version)
        _, matches = match_fw_to_car([fw], '', log=False)
        assert CAR.TESLA_MODEL_S_HW1 not in matches

  def test_modern_tesla_firmware_cannot_identify_hw1(self):
    for model in [CAR.TESLA_MODEL_3, CAR.TESLA_MODEL_Y, CAR.TESLA_MODEL_X]:
      with self.subTest(model=model):
        from opendbc.car.tesla.fingerprints import FW_VERSIONS
        from opendbc.car.fw_versions import match_fw_to_car

        fw = [
          structs.CarParams.CarFw(ecu=key[0], address=key[1], subAddress=key[2] or 0, brand='tesla', fwVersion=versions[0])
          for key, versions in FW_VERSIONS[model].items()
        ]
        _, matches = match_fw_to_car(fw, '', log=False)
        assert CAR.TESLA_MODEL_S_HW1 not in matches

  def test_optional_belt_sources_fail_closed_and_sdm_has_priority(self):
    for sdm, rcm, expected in [(None, None, True), (1, None, False), (0, None, True), (None, 1, False), (None, 0, True), (1, 0, False), (0, 1, True)]:
      with self.subTest(sdm=sdm, rcm=rcm, expected=expected):
        cp = CarInterface.get_non_essential_params(CAR.TESLA_MODEL_S_HW1)
        cs = CarState(cp)
        parsers = get_hw1_can_parsers(cp)
        packer = CANPacker('tesla_can')
        messages = []
        if sdm is not None:
          messages.append(packer.make_can_msg('SDM1', 0, {'SDM_bcklDrivStatus': sdm}))
        if rcm is not None:
          messages.append(packer.make_can_msg('RCM_status', 0, {'RCM_buckleDriverStatus': rcm}))
        for parser in parsers.values():
          parser.update([(1000000000, messages)])
        assert cs.update(parsers).seatbeltUnlatched == expected
        for parser in parsers.values():
          parser.update([(2100000000, [])])
        assert cs.update(parsers).seatbeltUnlatched

  def test_bosch_radar_full_feed_geometry_fault_and_point_removal(self):
    from opendbc.car.tesla.radar_hw1 import HW1RadarInterface

    cp = CarInterface.get_non_essential_params(CAR.TESLA_MODEL_S_HW1)
    cp.radarUnavailable = False
    radar = HW1RadarInterface(cp)
    packer = CANPacker('tesla_radar_bosch_generated')

    def feed(time, tracked=True, mismatch=False, fault=False):
      messages = [packer.make_can_msg(769, 1, {'RADC_HWFail': fault})]
      for index in range(32):
        messages.append(
          packer.make_can_msg(
            784 + 3 * index,
            1,
            {'LongDist': 50 + index, 'LongSpeed': -2, 'LatDist': 1.5, 'LongAccel': 0.5, 'ProbExist': 75, 'Tracked': tracked, 'Meas': 1, 'Index': 0},
          )
        )
        messages.append(packer.make_can_msg(785 + 3 * index, 1, {'LatSpeed': 0.25, 'Index2': int(mismatch)}))
      return radar.update([(time, messages)])

    ret = feed(1000000000)
    assert len(ret.points) == 32 and (not ret.errors.canError) and (not ret.errors.radarFault)
    point = ret.points[0]
    assert (point.dRel, point.yRel, point.vRel) == pytest.approx((50, 1.5, -2))
    assert not feed(1125000000, mismatch=True).points
    assert not feed(1250000000, tracked=False).points
    assert feed(1375000000, fault=True).errors.radarFault
