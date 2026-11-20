from types import SimpleNamespace
import time
import tempfile
import unittest
import uuid
from unittest.mock import patch

from openpilot.starpilot.models.catalog import BUNDLED_CURRENT
from openpilot.starpilot.models.runtime import ModelStatusSource, snapshot
from openpilot.starpilot.models.status import ModelHealth, ModelLoad, ModelVariant


class FakeSubMaster:
  def __init__(self):
    self.logMonoTime = {"managerState": 1_900_000_000, "modelV2": 1_990_000_000,
                        "drivingModelData": 1_995_000_000}
    self.valid = dict.fromkeys(self.logMonoTime, True)
    self.alive = dict(self.valid)
    self.messages = {
      "managerState": SimpleNamespace(processes=[SimpleNamespace(name="modeld", pid=421, running=True)]),
      "modelV2": SimpleNamespace(big=False),
      "drivingModelData": SimpleNamespace(),
    }

  def __getitem__(self, service):
    return self.messages[service]


class FakeParams:
  def __init__(self, chestnut=None):
    self.chestnut = chestnut

  def get(self, key):
    assert key == "ChestnutActive"
    return self.chestnut


class TestRuntimeSnapshot(unittest.TestCase):
  def setUp(self):
    self.sm = FakeSubMaster()
    self.load = ModelLoad(421, 54321, 1_800_000_000, BUNDLED_CURRENT, ModelVariant.SMALL, "a" * 64)

  def status(self, *, load=None, params=None, process_ticks=54321, now=2_000_000_000):
    return snapshot(self.sm, FakeParams() if params is None else params, now,
                    start_ticks=lambda _pid: process_ticks, read_load=lambda _path: self.load if load is None else load)

  def test_actual_manager_and_two_fresh_outputs_are_required(self):
    self.assertEqual(self.status().health, ModelHealth.ACTIVE)
    self.sm.valid["drivingModelData"] = False
    self.assertEqual(self.status().health, ModelHealth.STALE)
    self.sm.valid["drivingModelData"] = True
    self.sm.logMonoTime["managerState"] = 100
    self.assertEqual(self.status().health, ModelHealth.UNAVAILABLE)

  def test_conditional_switch_permission_requires_owned_publisher_and_both_fresh_outputs(self):
    conditional = SimpleNamespace(processPid=421, processStartTicks=54321, session='session', requestId='request',
                           phase='ready', activeModel='cheddar', highwayModel='cheddar', cityModel='city',
                           error='', canSwitch=True, sessionStartMonoTime=1_800_000_000,
                           frameMonoTime=1_990_000_000, transitionSeconds=.25, paused=False, targetMode='city', thresholdMph=50, unavailableReason='')
    self.sm.messages['conditionalModelState'] = conditional
    self.sm.logMonoTime['conditionalModelState'] = 1_995_000_000
    self.sm.valid['conditionalModelState'] = self.sm.alive['conditionalModelState'] = True
    source = ModelStatusSource(FakeParams(), sm=self.sm, clock=lambda: 2_000_000_000)
    with patch('openpilot.starpilot.models.runtime.process_start_ticks', return_value=54321):
      self.assertTrue(source.conditional_json()['canSwitch'])
      self.sm.valid['drivingModelData'] = False
      self.assertFalse(source.conditional_json()['canSwitch'])
      self.sm.valid['drivingModelData'] = True
      conditional.frameMonoTime = 1_799_000_000
      self.assertFalse(source.conditional_json()['canSwitch'])
      conditional.frameMonoTime = 1_990_000_000
      conditional.processStartTicks = 54322
      self.assertIsNone(source.conditional_json())
      conditional.processStartTicks = 54321
      self.sm.logMonoTime['conditionalModelState'] = 1_000_000_000
      self.assertIsNone(source.conditional_json())

  def test_pid_reuse_or_actual_variant_mismatch_does_not_claim_identity(self):
    self.assertEqual(self.status(process_ticks=54322).health, ModelHealth.IDENTITY_UNAVAILABLE)
    self.assertEqual(self.status(process_ticks=0).health, ModelHealth.IDENTITY_UNAVAILABLE)
    self.assertEqual(self.status(params=FakeParams(b"broken")).health, ModelHealth.ACTIVE)
    self.sm.messages["modelV2"].big = True
    self.assertEqual(self.status(params=FakeParams(b"1")).health, ModelHealth.FAILED)

  def test_native_typed_chestnut_parameter_matches_loaded_runner(self):
    from openpilot.common.params import Params
    with tempfile.TemporaryDirectory() as directory:
      params = Params(directory)
      params.put_bool("ChestnutActive", False, block=True)
      self.assertIs(params.get("ChestnutActive"), False)
      self.assertEqual(self.status(params=params).health, ModelHealth.ACTIVE)
      params.put_bool("ChestnutActive", True, block=True)
      self.sm.messages["modelV2"].big = True
      self.load = ModelLoad(421, 54321, 1_800_000_000, BUNDLED_CURRENT, ModelVariant.CHESTNUT, "a" * 64)
      self.assertEqual(self.status(params=params).health, ModelHealth.ACTIVE)
      params.put_bool("ChestnutActive", False, block=True)
      self.assertEqual(self.status(params=params).health, ModelHealth.ACTIVE)
      self.sm.messages["modelV2"].big = False
      self.assertEqual(self.status(params=params).health, ModelHealth.FAILED)

  def test_real_manager_and_model_output_ipc(self):
    from openpilot.cereal import messaging
    messaging.set_fake_prefix("model_status_" + uuid.uuid4().hex)
    publisher = messaging.PubMaster(["managerState", "modelV2", "drivingModelData"])
    subscriber = messaging.SubMaster(["managerState", "modelV2", "drivingModelData"])
    time.sleep(0.05)
    for name in ("managerState", "modelV2", "drivingModelData"):
      message = messaging.new_message(name)
      message.valid = True
      if name == "managerState":
        state = message.managerState.init("processes", 1)[0]
        state.name = "modeld"
        state.pid = 421
        state.running = True
      elif name == "modelV2":
        message.modelV2.big = False
      publisher.send(name, message)
      subscriber.update(100)
    self.assertTrue(all(subscriber.seen[name] for name in ("managerState", "modelV2", "drivingModelData")))
    load = ModelLoad(421, 54321, time.monotonic_ns() - 100_000_000, BUNDLED_CURRENT,
                     ModelVariant.SMALL, "a" * 64)
    observed = snapshot(subscriber, FakeParams(), time.monotonic_ns(),
                        start_ticks=lambda _pid: 54321, read_load=lambda _path: load)
    self.assertEqual(observed.health, ModelHealth.ACTIVE)


if __name__ == "__main__":
  unittest.main()
