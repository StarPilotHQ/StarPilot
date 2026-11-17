"""Automatic Continental parsing ignores the retired saved selection."""
from pathlib import Path

import pytest
from opendbc.car import gen_empty_fingerprint
from opendbc.car.toyota.interface import CarInterface
from opendbc.car.toyota.radar_interface import RadarInterface
from opendbc.car.toyota.values import CAR, ToyotaFlags
from openpilot.common.params import Params
from openpilot.starpilot.vehicle_preferences import VehicleStartupPreferences


class FilesystemParams:
  def __init__(self, path):
    self.path = path

  def get_default_value(self, key):
    return Params().get_default_value(key)

  def get_param_path(self, key):
    return str(self.path / key)


def factory(candidate=CAR.TOYOTA_RAV4_TSS2_2022):
  return CarInterface.get_params(candidate, gen_empty_fingerprint(), [], False, False, False)



@pytest.mark.parametrize('candidate', [CAR.TOYOTA_RAV4_TSS2_2022, CAR.TOYOTA_RAV4_TSS2_2023])
@pytest.mark.parametrize('raw', [None, b'0', b'1', b'2', b'garbage'])
def test_retired_choice_cannot_disable_or_change_automatic_receiver(tmp_path, candidate, raw):
  params = FilesystemParams(tmp_path)
  if raw is not None:
    Path(params.get_param_path('ToyotaRadarMode')).write_bytes(raw)
  cp = CarInterface.get_params(candidate, gen_empty_fingerprint(), [], False, False, False)
  assert cp.flags & ToyotaFlags.ARS510_RADAR, 'AUTOMATIC_CONTINENTAL_RADAR'
  baseline = cp.to_dict()
  owner = VehicleStartupPreferences.read(params, enabled=True)
  owner.prepare(cp)
  owner.finalize(cp)
  assert cp.flags & ToyotaFlags.ARS510_RADAR, 'AUTOMATIC_CONTINENTAL_RADAR'
  assert not cp.flags & ToyotaFlags.ARS510_FUSED
  assert not cp.radarUnavailable and RadarInterface(cp).ars510 is not None
  assert cp.safetyConfigs[0].to_dict() == baseline['safetyConfigs'][0]
  assert cp.lateralTuning.to_dict() == baseline['lateralTuning']
  assert cp.openpilotLongitudinalControl == baseline['openpilotLongitudinalControl']


@pytest.mark.parametrize('candidate', [CAR.TOYOTA_RAV4_TSS2_2022, CAR.TOYOTA_RAV4_TSS2_2023])
def test_automatic_factory_card_and_radard_parse_live_target(candidate):
  from unittest.mock import patch
  from opendbc.can import CANPacker
  from opendbc.car.toyota.ars510_acc_target import RADAR_DBC
  from opendbc.car.toyota.tests.test_ars510 import acc_frames
  from openpilot.cereal import messaging
  from openpilot.common.prefix import OpenpilotPrefix
  from openpilot.selfdrive.car.card import Car
  from openpilot.selfdrive.controls.radard import RadarD

  with OpenpilotPrefix():
    params = Params()
    params.put_bool('OpenpilotEnabledToggle', True, block=True)
    cp = CarInterface.get_params(candidate, gen_empty_fingerprint(), [], False, False, False)
    assert cp.flags & ToyotaFlags.ARS510_RADAR, 'AUTOMATIC_CONTINENTAL_RADAR'
    host = Car(CI=CarInterface(cp), RI=RadarInterface(cp))
    published = {}
    try:
      with patch.object(host.pm, 'send', side_effect=lambda name, message: published.update({name: message.as_reader()})):
        packer = CANPacker(RADAR_DBC)
        for tick in range(50):
          packet = [(1_000_000_000 + tick * 20_000_000, acc_frames(packer, tick, 60000 + tick))]
          host.radar_publish(host.radar_update(packet))
        event = published['radarTracks']
        assert event.valid and not event.radarTracks.errors.canError
        assert len(event.radarTracks.points) == 1
        point = event.radarTracks.points[0]
        assert point.dRel == pytest.approx(35.) and point.vRel == pytest.approx(-2.)
        sm = messaging.SubMaster(['modelV2', 'carState'])
        model, state = messaging.new_message('modelV2', valid=True), messaging.new_message('carState', valid=True)
        model.modelV2.velocity.x = [20.]
        state.carState.vEgo = 20.
        sm.update_msgs(2., [model, state])
        radar = RadarD(radar_available=True)
        radar.update(sm, event.radarTracks)
        assert len(radar.tracks) == 1
        assert next(iter(radar.tracks.values())).dRel == pytest.approx(35.)
        for tick in range(50, 60):
          host.radar_publish(host.radar_update([(1_000_000_000 + tick * 20_000_000,
                                                acc_frames(packer, tick, 60000 + tick, present=False))]))
        assert not published['radarTracks'].radarTracks.points
    finally:
      host.vehicle_startup.close()


@pytest.mark.parametrize('candidate', [CAR.TOYOTA_RAV4_TSS2_2022, CAR.TOYOTA_RAV4_TSS2_2023])
def test_disabled_radar_withdraws_a_previous_receiver_without_changing_authority(candidate):
  from opendbc.car.toyota.ars510_preferences import configure_radar
  cp = CarInterface.get_params(candidate, gen_empty_fingerprint(), [], False, False, False)
  cp.flags |= int(ToyotaFlags.DISABLE_RADAR | ToyotaFlags.ARS510_FUSED)
  before = cp.to_dict()
  assert not configure_radar(cp)
  expected = before.copy()
  expected['flags'] &= ~int(ToyotaFlags.ARS510_RADAR | ToyotaFlags.ARS510_FUSED)
  expected['radarUnavailable'] = True
  assert cp.to_dict() == expected
  assert RadarInterface(cp).ars510 is None
