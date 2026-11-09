"""Fresh typed synthetic envelopes: new full asset identity and independent STOP evidence."""
from types import SimpleNamespace as NS
import unittest
import hashlib
from openpilot.cereal import messaging
from openpilot.starpilot.speed_limits.vision import observation,producer,stop_observation
from openpilot.starpilot.speed_limits.vision.traffic_signs import TrafficSignModelCore
from openpilot.starpilot.speed_limits.vision.speed_face import bundle_model_id,MODEL_SHA256
BASE=100_000_000_000
OFFSET=2_000_000_000
OLD='52bb30f73bf0d166342f33d1b32b29b726bf036004e0359fef10ef7bf1f5556f:5d861067ab8f50986488f983cbc24f78527775d37af0b9740c5545d5f3173902'
class Input(dict):
 def __init__(self,event,now):
  super().__init__(slcVisionObservation=event.slcVisionObservation);self.seen={'slcVisionObservation':True};self.valid=self.seen.copy();self.alive=self.seen.copy();self.logMonoTime={'slcVisionObservation':int(event.logMonoTime)};self.recv_time={'slcVisionObservation':now/1e9};self.recv_frame={'slcVisionObservation':2}
def envelope(model_id=None):
 now=BASE+400_000_000;sample=stop_observation.StopEvidence('fresh_synthetic_camera','road',BASE,BASE+OFFSET,7,1344,760,(stop_observation.StopBox(.875,(780,200,840,260)),));captured=[]
 def send(service,event):
  assert service=='slcVisionObservation';event.logMonoTime=now
  if model_id is not None:event.slcVisionObservation.vision.modelId=model_id
  captured.append(messaging.log_from_bytes(event.to_bytes()))
 producer._send(NS(send=send),session=sample.session,status='unknown',stream=sample.stream,observed_ns=BASE,eof_boot_ns=BASE+OFFSET,frame_id=7,result=None,stop_sample=sample)
 return captured[0],sample,now
class TestSpeedFaceNativeBinding(unittest.TestCase):
 def test_full_three_asset_identity_wire_and_None_speed_valid_STOP(self):
  assets=(TrafficSignModelCore.DETECTOR_SHA256,TrafficSignModelCore.CLASSIFIER_SHA256,MODEL_SHA256,TrafficSignModelCore.STOP_PROJECTION_SHA256);expected=hashlib.sha256(('vision_joint_stop_projection_identity_v1\n'+'\n'.join(assets)+'\n').encode()).hexdigest()+':'+TrafficSignModelCore.CLASSIFIER_SHA256;self.assertEqual(tuple(TrafficSignModelCore.ASSET_MANIFEST),assets);self.assertEqual(observation.MODEL_ID,TrafficSignModelCore.MODEL_ID);self.assertEqual(expected,observation.MODEL_ID);self.assertNotEqual(expected,OLD)
  event,sample,now=envelope();self.assertEqual(str(event.slcVisionObservation.vision.modelId),expected);self.assertEqual(len(expected),129);self.assertEqual(event.slcVisionObservation.vision.supportCount,0);decoded=stop_observation.read_stop_evidence(Input(event,now),now_ns=now,now_boot_ns=now+OFFSET,drive_id=BASE-1_000_000_000);self.assertEqual(decoded,sample);speed=observation.vision_observation(event.slcVisionObservation.vision,receipt_mono_ns=now,now_mono_ns=now,now_boot_ns=now+OFFSET,car_fingerprint='synthetic',drive_session='synthetic');self.assertEqual(speed.kind,observation.acc.ObservationKind.UNKNOWN)
 def test_old_two_asset_identity_rejected_by_actual_typed_consumers(self):
  event,_,now=envelope(OLD);self.assertIsNone(stop_observation.read_stop_evidence(Input(event,now),now_ns=now,now_boot_ns=now+OFFSET,drive_id=BASE-1_000_000_000));speed=observation.vision_observation(event.slcVisionObservation.vision,receipt_mono_ns=now,now_mono_ns=now,now_boot_ns=now+OFFSET,car_fingerprint='synthetic',drive_session='synthetic');self.assertEqual(speed.kind,observation.acc.ObservationKind.UNKNOWN)

 def test_previous_three_asset_identity_rejected_by_actual_typed_consumers(self):
  previous=bundle_model_id(TrafficSignModelCore.DETECTOR_SHA256,TrafficSignModelCore.CLASSIFIER_SHA256);self.assertNotEqual(previous,observation.MODEL_ID);event,_,now=envelope(previous);self.assertIsNone(stop_observation.read_stop_evidence(Input(event,now),now_ns=now,now_boot_ns=now+OFFSET,drive_id=BASE-1_000_000_000));speed=observation.vision_observation(event.slcVisionObservation.vision,receipt_mono_ns=now,now_mono_ns=now,now_boot_ns=now+OFFSET,car_fingerprint='synthetic',drive_session='synthetic');self.assertEqual(speed.kind,observation.acc.ObservationKind.UNKNOWN)
