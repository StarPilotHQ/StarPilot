"""Real CAN parser/Card publisher/RadarD path for Dom Camry clusters."""
import pytest
from unittest.mock import patch
from opendbc.can import CANPacker
from opendbc.car.toyota.interface import CarInterface
from opendbc.car.toyota.radar_interface import RadarInterface
from opendbc.car.toyota.values import CAR
from openpilot.cereal import messaging
from openpilot.selfdrive.car.card import Car
from openpilot.selfdrive.controls.radard import RadarD
from openpilot.common.prefix import OpenpilotPrefix


def setup(alpha=False):
  cp=CarInterface.get_params(CAR.TOYOTA_CAMRY,{0: {0x2FF: 4}, 1: {}, 2: {}},[],alpha,False,False)
  assert not cp.radarUnavailable
  return cp,RadarInterface(cp),CANPacker('toyota_radar_dsu_tssp'),CANPacker('toyota_nodsu_pt_generated')


def batch(radar,pt,tick,*,target=True,wheels=True,bus=1,omit=None,lateral=1.5):
  frames=[]
  if wheels:
    frames.append(pt.make_can_msg('WHEEL_SPEEDS',0,{f'WHEEL_SPEED_{c}':36. for c in ('FL','FR','RL','RR')}))
  if tick%10==0:
    for address in range(0x680,0x686):
      if address==omit:
        continue
      fields={'ID':3 if target and address==0x680 else 63,'LONG_DIST':30. if target and address==0x680 else 0.,
              'LAT_DIST':lateral,'SPEED':10.,'LAT_SPEED':.2 if lateral > 0 else -.2}
      frames.append(radar.make_can_msg(address,bus,fields))
  return [(1_000_000_000+tick*10_000_000,frames)]


@pytest.mark.parametrize('lateral', (-1.5, 1.5))
@pytest.mark.parametrize('alpha', (False, True))
def test_cluster_factory_can_card_and_radard(tmp_path, lateral, alpha):
  with OpenpilotPrefix():
    _check_card_and_radard(lateral, alpha)


def _check_card_and_radard(lateral, alpha):
  cp,ri,rp,pt=setup(alpha)
  assert cp.openpilotLongitudinalControl == alpha
  ci=CarInterface(cp)
  host=Car(CI=ci,RI=ri)
  published={}
  capture=patch.object(host.pm,'send',side_effect=lambda n,m:published.update({n:m.as_reader()}))
  capture.start()
  try:
    for tick in range(1,51):
      result=host.radar_update(batch(rp,pt,tick,lateral=lateral))
      host.radar_publish(result)
    rd=published['radarTracks'].radarTracks
    assert len(rd.points)==1 and not rd.errors.canError
    point=rd.points[0]
    assert point.dRel==pytest.approx(30.,abs=.03)
    assert point.yRel==pytest.approx(lateral,abs=.02), 'CAMRY_CLUSTER_SIDE_MIRRORED'
    assert point.deprecated.yvRel==pytest.approx(.2 if lateral > 0 else -.2,abs=.01)
    assert point.vRel==pytest.approx(10.-10.*cp.wheelSpeedFactor*.922,abs=.08)
    sm=messaging.SubMaster(['modelV2','carState'])
    model=messaging.new_message('modelV2',valid=True)
    model.modelV2.velocity.x=[10.]
    car=messaging.new_message('carState',valid=True)
    car.carState.vEgo=10.
    sm.update_msgs(1.,[model,car])
    fused=RadarD(radar_available=True)
    fused.update(sm,rd)
    assert len(fused.tracks)==1
    assert next(iter(fused.tracks.values())).dRel==pytest.approx(point.dRel)
    assert next(iter(fused.tracks.values())).yRel==pytest.approx(point.yRel)
    for tick in range(51,61):
      host.radar_publish(host.radar_update(batch(rp,pt,tick,target=False)))
    assert len(published['radarTracks'].radarTracks.points)==0
  finally:
    capture.stop()
    host.vehicle_startup.close()


@pytest.mark.parametrize('fault',['wheel_expiry','cluster_expiry','wrong_bus'])
def test_cluster_source_faults(fault):
  cp,ri,rp,pt=setup()
  for tick in range(1,51):
    rd=ri.update(batch(rp,pt,tick)) or locals().get('rd')
  assert rd is not None and not rd.errors.canError
  for tick in range(51,251):
    result=ri.update(batch(rp,pt,tick,wheels=fault!='wheel_expiry',bus=2 if fault=='wrong_bus' else 1,
                           omit=0x680 if fault=='cluster_expiry' else None))
    if result is not None:
      rd=result
  if fault=='wrong_bus':
    # No trigger can publish a new frame; the previous event remains old.
    assert result is None
  else:
    assert rd.errors.canError
  for tick in range(251,301):
    rd=ri.update(batch(rp,pt,tick)) or rd
  assert not rd.errors.canError
