from dataclasses import replace
import pytest
from opendbc.car import structs, gen_empty_fingerprint
from opendbc.car.toyota import rav4h_filter
from opendbc.car.toyota.interface import CarInterface
from opendbc.car.toyota.values import CAR
from openpilot.starpilot.car.toyota.legacy_lateral import below_engage_lateral_allowed
from openpilot.starpilot.car.toyota.tests.test_rav4h_caller_native import firmware
from openpilot.starpilot.aol.wire import IntentState, PandaSlot, SafetyState

NOW=1_000_000_000
SOURCE=NOW-1_000_000
SESSION='eps5-axis'


def evidence():
  fp=gen_empty_fingerprint()
  fp[0][0x2FF]=8
  cp=CarInterface.get_params(CAR.TOYOTA_HIGHLANDER,fp,firmware(CAR.TOYOTA_HIGHLANDER),False,False,False)
  assert rav4h_filter.prepare_stock(cp)
  cp.alternativeExperience=32
  cs=structs.CarState(canValid=True,vEgo=0.,gearShifter=structs.CarState.GearShifter.drive)
  cs.cruiseState.available=True
  intent=IntentState('card-producer',1,SOURCE,SOURCE,SOURCE+200_000_000,True,False,False,True,True)
  slot=PandaSlot(0,'actual-test-board',int(cp.safetyConfigs[0].safetyModel.raw),cp.safetyConfigs[0].safetyParam,32,False,False)
  native=SafetyState(1,True,SOURCE,SOURCE+200_000_000,slot.safetyModel,slot.safetyParam,False,False,False,False,slot.hardwareSerial,SESSION,SOURCE,(slot,))
  return cp,cs,intent,native


def allowed(cp,cs,intent,native,*,source=SOURCE,now=NOW,session=SESSION):
  return below_engage_lateral_allowed(cp,cs,intent,native,source_ns=source,now_ns=now,axis_session_id=session)


@pytest.mark.parametrize('native_requested,permission,expected',[(False,False,True),(True,True,True),(True,False,False)])
def test_low_speed_only_admits_request_or_actual_lateral_grant(native_requested,permission,expected):
  cp,cs,intent,native=evidence()
  native=replace(native,requestedLateral=native_requested,lateralAllowed=permission)
  assert allowed(cp,cs,intent,native)==expected
  assert cp.minEnableSpeed>0 and not native.longitudinalAllowed


@pytest.mark.parametrize('fault',['stale_source','future_source','future_intent','stale_native','wrong_session','missing_inventory',
  'rx_invalid','native_fault','wrong_native_word','missing_latch','paused_lateral','missing_settings','main_off','reverse',
  'door','can_invalid','can_timeout','temporary_eps','permanent_eps','acc_fault','stock_aeb','above_minimum','wrong_profile'])
def test_low_speed_exception_retains_source_and_native_denials(fault):
  cp,cs,intent,native=evidence()
  kwargs={}
  if fault=='stale_source':
    kwargs['now']=NOW+200_000_000
  elif fault=='future_source':
    kwargs['source']=NOW+1
  elif fault=='future_intent':
    intent=replace(intent,observedMonoTime=NOW+1)
  elif fault=='stale_native':
    native=replace(native,validUntilMonoTime=NOW-1)
  elif fault=='wrong_session':
    kwargs['session']='other-axis'
  elif fault=='missing_inventory':
    native=replace(native,pandaInventory=())
  elif fault=='rx_invalid':
    native=replace(native,pandaInventory=(replace(native.pandaInventory[0],safetyRxChecksInvalid=True),))
  elif fault=='native_fault':
    native=replace(native,compatible=False)
  elif fault=='wrong_native_word':
    native=replace(native,safetyParam=0x1049)
  elif fault=='missing_latch':
    intent=replace(intent,allowedLatch=False)
  elif fault=='paused_lateral':
    intent=replace(intent,pauseLateral=True)
  elif fault=='missing_settings':
    intent=replace(intent,settingsQualified=False)
  elif fault=='main_off':
    cs.cruiseState.available=False
  elif fault=='reverse':
    cs.gearShifter=structs.CarState.GearShifter.reverse
  elif fault=='door':
    cs.doorOpen=True
  elif fault=='can_invalid':
    cs.canValid=False
  elif fault=='can_timeout':
    cs.canTimeout=True
  elif fault=='temporary_eps':
    cs.steerFaultTemporary=True
  elif fault=='permanent_eps':
    cs.steerFaultPermanent=True
  elif fault=='acc_fault':
    cs.accFaulted=True
  elif fault=='stock_aeb':
    cs.stockAeb=True
  elif fault=='above_minimum':
    cs.vEgo=cp.minEnableSpeed+1
  elif fault=='wrong_profile':
    cp.carFingerprint=CAR.TOYOTA_HIGHLANDER_TSS2
  assert not allowed(cp,cs,intent,native,**kwargs)
