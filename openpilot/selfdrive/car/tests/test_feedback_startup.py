from unittest.mock import patch

from opendbc.car import structs
from openpilot.cereal import messaging
from openpilot.selfdrive.car.card import Car
from openpilot.starpilot.flm.learning_wire import write_source_receipt
from openpilot.starpilot.flm.runtime_adapter_registry import SourceState


def test_first_calibrated_feedback_uses_startup_preparation():
  card = Car.__new__(Car)
  card.CP = structs.CarParams.new_message(brand='gm', carFingerprint='CHEVROLET_BOLT_ACC_2022_2023')
  card.learning_feedback_factory = None
  card._prepare_feedback_capture()

  stamp = 10_000_000_000
  service = 'autoSteeringLearning'
  packet = messaging.new_message(service, valid=True, logMonoTime=stamp)
  source = SourceState(True, False, 'boot', 'a' * 64, stamp, stamp + 500_000_000,
                       card._feedback_cp_sha256, (0., 0., 0.))
  write_source_receipt(packet, source)
  sm = messaging.SubMaster([service])
  sm.update_msgs(stamp / 1e9, [packet.as_reader()])
  sm.alive[service] = sm.freq_ok[service] = True
  card.sm = sm

  with patch('openpilot.starpilot.flm.runtime_adapter_registry.car_params_digest', side_effect=AssertionError):
    capture = card._feedback_capture(stamp)
    assert capture is not None
    output = messaging.new_message('carOutput', valid=True, logMonoTime=stamp)
    companion = messaging.new_message('starpilotCarState', valid=True, logMonoTime=stamp)
    card.learning_feedback_writer(companion.starpilotCarState, capture.publication(output))
    assert companion.starpilotCarState.feedbackPublication.version == 1
    assert not companion.starpilotCarState.feedbackPublication.outputKnown
    assert card._feedback_capture(stamp + 500_000_001) is None
