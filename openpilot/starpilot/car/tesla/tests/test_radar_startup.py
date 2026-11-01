import gc
import json

import pytest

from opendbc.car import car_helpers, structs
from opendbc.car.tesla.tests.test_preap_startup import exercise_startup
from opendbc.car.tesla.values import CAR
from openpilot.cereal import messaging
from openpilot.common.params import Params
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.selfdrive.car import card as card_module
from openpilot.selfdrive.car.card import Car
from openpilot.starpilot.car.tesla.radar_settings import DOCUMENT_KEY, encode
from openpilot.starpilot.vehicle_selection import KEY as SELECTION_KEY, encode as encode_selection


@pytest.mark.parametrize('mode', ('front', 'nosecone'))
@pytest.mark.parametrize('release', (False, True))
def test_auto_actual_factory_card_final_cp_and_frozen_context(mode, release, monkeypatch):
  exercise_startup(CAR.TESLA_MODEL_S_PREAP, True, release, False, monkeypatch,
                   radar_mode=mode, radar_offset=0.25)


@pytest.mark.parametrize('mode', ('front', 'nosecone'))
def test_manual_selection_remains_denied_at_actual_card_startup(mode, monkeypatch):
  monkeypatch.setenv('SIMULATION', '1')
  monkeypatch.setenv('REPLAY', '1')
  monkeypatch.setenv('AOL_REPLAY_RUNTIME', '1')
  monkeypatch.delenv('FINGERPRINT', raising=False)
  with OpenpilotPrefix():
    saved = Params()
    saved.put_bool('OpenpilotEnabledToggle', True, block=True)
    saved.put(SELECTION_KEY, json.loads(encode_selection(str(CAR.TESLA_MODEL_S_PREAP))), block=True)
    saved.put(DOCUMENT_KEY, json.loads(encode(mode, 0.25)), block=True)
    # The actual get_car function must apply the selection and mark it fixed.
    def fingerprint(can_recv, can_send, set_obd_multiplexing, cached_params, *, known_tesla_bus0=False):
      assert known_tesla_bus0
      return (None, {0: {}, 1: {}, 2: {}}, '0' * 17, [], structs.CarParams.FingerprintSource.can, False)
    monkeypatch.setattr(car_helpers, 'fingerprint', fingerprint)
    monkeypatch.setattr(card_module, 'can_comm_callbacks', lambda *args: (lambda *args: [], lambda frames: None))
    monkeypatch.setattr(card_module.messaging, 'recv_one_retry', lambda socket: messaging.new_message('can', 1))
    card = None
    try:
      card = Car()
      assert card.CP.carFingerprint == CAR.TESLA_MODEL_S_PREAP
      assert card.CP.fingerprintSource == structs.CarParams.FingerprintSource.fixed
      assert card.CP.dashcamOnly and card.CP.passive
      assert len(card.CP.safetyConfigs) == 1
      assert card.CP.safetyConfigs[0].safetyModel == structs.CarParams.SafetyModel.noOutput
      assert not card.aol_qualified and card.tesla_preap_radar_context is None
      assert card.RI.preap.radar_off_can
      with structs.CarParams.from_bytes(saved.get('CarParams')) as published:
        assert published.to_dict() == card.CP.to_dict()
    finally:
      del card
      gc.collect()
