import random

import numpy as np

from openpilot.common.test import OpenpilotTestCase
import openpilot.cereal.messaging as messaging
from openpilot.cereal import log
from openpilot.common.params import Params
from openpilot.starpilot.schema_cache import put_cache
from openpilot.selfdrive.locationd.calibrationd import Calibrator, INPUTS_NEEDED, INPUTS_WANTED, BLOCK_SIZE, MIN_SPEED_FILTER, \
                                                         MAX_YAW_RATE_FILTER, SMOOTH_CYCLES, HEIGHT_INIT, MAX_ALLOWED_PITCH_SPREAD, MAX_ALLOWED_YAW_SPREAD


def process_messages(c, cam_odo_calib, cycles,
                     cam_odo_speed=MIN_SPEED_FILTER + 1,
                     carstate_speed=MIN_SPEED_FILTER + 1,
                     cam_odo_yr=0.0,
                     cam_odo_speed_std=1e-3,
                     cam_odo_height_std=1e-3):
  old_rpy_weight_prev = 0.0
  for _ in range(cycles):
    assert (old_rpy_weight_prev - c.old_rpy_weight < 1/SMOOTH_CYCLES + 1e-3)
    old_rpy_weight_prev = c.old_rpy_weight
    c.handle_v_ego(carstate_speed)
    c.handle_cam_odom([cam_odo_speed,
                       np.sin(cam_odo_calib[2]) * cam_odo_speed,
                       -np.sin(cam_odo_calib[1]) * cam_odo_speed],
                        [0.0, 0.0, cam_odo_yr],
                        [0.0, 0.0, 0.0],
                        [cam_odo_speed_std, cam_odo_speed_std, cam_odo_speed_std],
                        [0.0, 0.0, HEIGHT_INIT.item()],
                        [cam_odo_height_std, cam_odo_height_std, cam_odo_height_std])

class TestCalibrationd(OpenpilotTestCase):
  def test_unqualified_saved_params_are_not_loaded(self):
    msg = messaging.new_message('extrinsicsCalibration')
    msg.extrinsicsCalibration.validBlocks = 10
    msg.extrinsicsCalibration.rpyCalib = [0.0, 0.02, 0.01]
    raw = msg.to_bytes()
    params = Params()
    params.put("CalibrationParams", raw, block=True)
    c = Calibrator(param_put=True)

    self.assertEqual(c.valid_blocks, 0)
    np.testing.assert_allclose(c.rpy, np.zeros(3))
    self.assertEqual(params.get("CalibrationParams"), raw)

  def test_read_saved_params(self):
    msg = messaging.new_message('extrinsicsCalibration')
    msg.extrinsicsCalibration.validBlocks = random.randint(1, 10)
    msg.extrinsicsCalibration.rpyCalib = [random.random() for _ in range(3)]
    msg.extrinsicsCalibration.height = [random.random() for _ in range(1)]
    put_cache(Params(), "CalibrationParams", msg, block=True)
    c = Calibrator(param_put=True)

    np.testing.assert_allclose(msg.extrinsicsCalibration.rpyCalib, c.rpy)
    np.testing.assert_allclose(msg.extrinsicsCalibration.height, c.height)
    assert msg.extrinsicsCalibration.validBlocks == c.valid_blocks


  def test_calibration_basics(self):
    c = Calibrator(param_put=False)
    process_messages(c, [0.0, 0.0, 0.0], BLOCK_SIZE * (INPUTS_WANTED + 1))
    assert c.valid_blocks == INPUTS_WANTED
    np.testing.assert_allclose(c.rpy, np.zeros(3))
    np.testing.assert_allclose(c.height, HEIGHT_INIT)
    c.reset()


  def test_calibration_low_speed_reject(self):
    c = Calibrator(param_put=False)
    process_messages(c, [0.0, 0.0, 0.0], BLOCK_SIZE * INPUTS_WANTED, cam_odo_speed=MIN_SPEED_FILTER - 1)
    process_messages(c, [0.0, 0.0, 0.0], BLOCK_SIZE * INPUTS_WANTED, carstate_speed=MIN_SPEED_FILTER - 1)
    assert c.valid_blocks == 0
    np.testing.assert_allclose(c.rpy, np.zeros(3))
    np.testing.assert_allclose(c.height, HEIGHT_INIT)


  def test_calibration_yaw_rate_reject(self):
    c = Calibrator(param_put=False)
    process_messages(c, [0.0, 0.0, 0.0], BLOCK_SIZE * INPUTS_WANTED, cam_odo_yr=MAX_YAW_RATE_FILTER)
    assert c.valid_blocks == 0
    np.testing.assert_allclose(c.rpy, np.zeros(3))
    np.testing.assert_allclose(c.height, HEIGHT_INIT)


  def test_calibration_speed_std_reject(self):
    c = Calibrator(param_put=False)
    process_messages(c, [0.0, 0.0, 0.0], BLOCK_SIZE * INPUTS_WANTED, cam_odo_speed_std=1e3)
    assert c.valid_blocks == INPUTS_NEEDED
    np.testing.assert_allclose(c.rpy, np.zeros(3))


  def test_calibration_speed_std_height_reject(self):
    c = Calibrator(param_put=False)
    process_messages(c, [0.0, 0.0, 0.0], BLOCK_SIZE * INPUTS_WANTED, cam_odo_height_std=1e3)
    assert c.valid_blocks == INPUTS_NEEDED
    np.testing.assert_allclose(c.rpy, np.zeros(3))


  def test_calibration_auto_reset(self):
    c = Calibrator(param_put=False)
    process_messages(c, [0.0, 0.0, 0.0], BLOCK_SIZE * (INPUTS_NEEDED + 1))
    assert c.valid_blocks == INPUTS_NEEDED
    np.testing.assert_allclose(c.rpy, [0.0, 0.0, 0.0], atol=1e-3)
    process_messages(c, [0.0, MAX_ALLOWED_PITCH_SPREAD*0.9, MAX_ALLOWED_YAW_SPREAD*0.9], BLOCK_SIZE + 10)
    assert c.valid_blocks == INPUTS_NEEDED + 1
    assert c.cal_status == log.ExtrinsicsCalibration.Status.calibrated

    c = Calibrator(param_put=False)
    process_messages(c, [0.0, 0.0, 0.0], BLOCK_SIZE * (INPUTS_NEEDED + 1))
    assert c.valid_blocks == INPUTS_NEEDED
    np.testing.assert_allclose(c.rpy, [0.0, 0.0, 0.0])
    process_messages(c, [0.0, MAX_ALLOWED_PITCH_SPREAD*1.1, 0.0], BLOCK_SIZE + 10)
    assert c.valid_blocks == 0
    assert c.cal_status == log.ExtrinsicsCalibration.Status.recalibrating
    np.testing.assert_allclose(c.rpy, [0.0, MAX_ALLOWED_PITCH_SPREAD*1.1, 0.0], atol=1e-2)

    c = Calibrator(param_put=False)
    process_messages(c, [0.0, 0.0, 0.0], BLOCK_SIZE * (INPUTS_NEEDED + 1))
    assert c.valid_blocks == INPUTS_NEEDED
    np.testing.assert_allclose(c.rpy, [0.0, 0.0, 0.0])
    process_messages(c, [0.0, 0.0, MAX_ALLOWED_YAW_SPREAD*1.1], BLOCK_SIZE + 10)
    assert c.valid_blocks == 0
    assert c.cal_status == log.ExtrinsicsCalibration.Status.recalibrating
    np.testing.assert_allclose(c.rpy, [0.0, 0.0, MAX_ALLOWED_YAW_SPREAD*1.1], atol=1e-2)


class TestCalibrationIdentity(OpenpilotTestCase):
  def test_only_a_different_verified_real_car_resets_calibration(self):
    from unittest.mock import patch
    from opendbc.car.structs import car
    from openpilot.selfdrive.locationd import calibrationd

    class StartupCaptured(Exception):
      pass

    params = Params()
    saved = messaging.new_message('extrinsicsCalibration')
    saved.extrinsicsCalibration.validBlocks = 8
    saved.extrinsicsCalibration.rpyCalib = [0., .01, .02]
    saved.extrinsicsCalibration.height = [1.22]
    real_calibrator = calibrationd.Calibrator
    for previous, current, raw_previous, reset in (
      (('hyundai', 'A'), ('hyundai', 'A'), False, False),
      (('hyundai', 'A'), ('toyota', 'B'), False, True),
      (('mock', 'MOCK'), ('toyota', 'B'), False, False),
      (('toyota', 'B'), ('mock', 'MOCK'), False, False),
      (None, ('toyota', 'B'), False, False),
      (('hyundai', 'A'), ('toyota', 'B'), True, False),
    ):
      with self.subTest(previous=previous, current=current, raw_previous=raw_previous):
        params.remove('CarParamsPrevRoute')
        put_cache(params, 'CalibrationParams', saved, block=True)
        cp = car.CarParams.new_message(brand=current[0], carFingerprint=current[1])
        params.put('CarParams', cp.to_bytes(), block=True)
        if previous is not None:
          old = car.CarParams.new_message(brand=previous[0], carFingerprint=previous[1])
          if raw_previous:
            params.put('CarParamsPrevRoute', old.to_bytes(), block=True)
          else:
            put_cache(params, 'CarParamsPrevRoute', old, block=True)
        captured = []

        def capture(captured=captured, **kwargs):
          captured.append(real_calibrator(**kwargs))
          raise StartupCaptured

        with patch.object(calibrationd, 'config_realtime_process'), \
             patch.object(calibrationd.messaging, 'PubMaster'), \
             patch.object(calibrationd.messaging, 'SubMaster'), \
             patch.object(calibrationd, 'Calibrator', side_effect=capture):
          with self.assertRaises(StartupCaptured):
            calibrationd.main()
        self.assertEqual(captured[0].valid_blocks, 0 if reset else 8)
        self.assertEqual(params.get('CalibrationParams') is None, reset)
