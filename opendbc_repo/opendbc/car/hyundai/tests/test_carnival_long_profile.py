import pytest
from opendbc.car import Bus
from opendbc.car.hyundai.tests.test_ccnc_six import params
from opendbc.car.hyundai.values import CAR, HyundaiFlags
from opendbc.car.hyundai.canfd_stock_aol import qualified_long
from opendbc.car.hyundai.generic_hda2_startup import qualified
from opendbc.car.hyundai.carstate import CarState


def carnival():
  return params(CAR.KIA_CARNIVAL_2025, hda2=True, alt_buttons=True, alpha=True, adas=True, alt_lka=True)


@pytest.mark.parametrize('word,experience,expected', [
  (0x20B4,0,True), (0x28B4,32,True), (0x28B4,0,False), (0x20B4,32,False),
  (0x28B6,32,False), (0x28B0,32,False), (0x8B4,32,False), (0x28B5,32,False),
])
def test_exact_profile(word, experience, expected):
  cp = carnival()
  cp.safetyConfigs[0].safetyParam, cp.alternativeExperience = word, experience
  assert qualified_long(cp) == expected


@pytest.mark.parametrize('mutation', ['hybrid','other_car','passive','dashcam','wrong_model','angle','other_bus'])
def test_reject_other_configuration(mutation):
  cp = carnival()
  if mutation == 'hybrid':
    cp.flags = int(cp.flags | HyundaiFlags.HYBRID)
  elif mutation == 'other_car':
    cp.carFingerprint = CAR.KIA_CARNIVAL_HEV_4TH_GEN
  elif mutation == 'passive':
    cp.passive = True
  elif mutation == 'dashcam':
    cp.dashcamOnly = True
  elif mutation == 'angle':
    cp.flags = int(cp.flags | HyundaiFlags.CANFD_ANGLE_STEERING)
  elif mutation == 'other_bus':
    cp.safetyConfigs = [dict(safetyModel='noOutput'),dict(safetyModel='hyundaiCanfd',safetyParam=0x20B4)]
  else:
    cp.safetyConfigs[0].safetyModel = 'noOutput'
  assert not qualified(cp)


def test_takeover_health_binds_physical_sources_not_removed_scc():
  cp = carnival()
  parser = CarState(cp).get_can_parsers(cp)[Bus.pt]
  assert parser.message_states[0x1AA].frequency == 50
  assert 0x1CF not in parser.addresses
  assert not parser.message_states[0x1A0].frequency > 0
  for address in (0x100,0x175,0xA0,0xEA):
    assert parser.message_states[address].frequency > 0
