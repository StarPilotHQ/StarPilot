from dataclasses import replace
from unittest.mock import patch

from opendbc.car import structs
from openpilot.cereal import messaging
from openpilot.selfdrive.car.card import Car
from openpilot.starpilot.flm.feedback_wire import read_publication
from openpilot.starpilot.flm.learning_wire import read_source_receipt, write_source_receipt
from openpilot.starpilot.flm.runtime_adapter_registry import SourceState


def test_first_calibrated_feedback_uses_startup_preparation():
  card = Car.__new__(Car)
  card.CP = structs.CarParams.new_message(brand='gm', carFingerprint='CHEVROLET_BOLT_ACC_2022_2023')
  card.learning_feedback_factory = None
  card._prepare_feedback_capture()

  stamp = 10_000_000_000
  service = 'autoSteeringLearning'
  packet = messaging.new_message(service, valid=True, logMonoTime=stamp)
  source = SourceState(True, False, 'boot', 'a' * 64, stamp, stamp + 200_000_000,
                       card._feedback_cp_sha256, (0., 0., 0.))
  write_source_receipt(packet, source)
  sm = messaging.SubMaster([service])
  sm.update_msgs(stamp / 1e9, [packet.as_reader()])
  sm.alive[service] = sm.freq_ok[service] = True
  card.sm = sm

  with patch('openpilot.starpilot.flm.runtime_adapter_registry.car_params_digest', side_effect=AssertionError), \
       patch('openpilot.starpilot.flm.learning_wire.read_source_receipt', wraps=read_source_receipt) as decode:
    capture = card._feedback_capture(stamp)
    assert capture is not None
    assert card._feedback_capture(stamp + 10_000_000) is capture
    assert decode.call_count == 1
    output = messaging.new_message('carOutput', valid=True, logMonoTime=stamp)
    companion = messaging.new_message('starpilotCarState', valid=True, logMonoTime=stamp)
    row = capture.publication(output)
    card.learning_feedback_writer(companion.starpilotCarState, row)
    assert read_publication(companion.starpilotCarState) == row
    assert not row.output_known
    disabled = messaging.new_message(service, valid=True, logMonoTime=stamp)
    write_source_receipt(disabled, replace(source, enabled=False))
    sm.update_msgs((stamp + 20_000_000) / 1e9, [disabled.as_reader()])
    sm.alive[service] = sm.freq_ok[service] = True
    assert card._feedback_capture(stamp + 20_000_000) is None
    assert card._feedback_capture(stamp + 25_000_000) is None
    assert decode.call_count == 2

    sm.update_msgs((stamp + 30_000_000) / 1e9, [packet.as_reader()])
    sm.alive[service] = sm.freq_ok[service] = True
    assert card._feedback_capture(stamp + 30_000_000) is not None
    assert card._feedback_capture(source.expires_ns + 1) is None
    assert decode.call_count == 3
