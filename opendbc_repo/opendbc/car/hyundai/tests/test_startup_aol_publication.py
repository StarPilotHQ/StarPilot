"""Car-owned stock fallback stays exact through Card's AOL publication order."""
import ast
import inspect
import textwrap
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from opendbc.can.packer import CANPacker
from opendbc.car import CanData
from opendbc.car.hyundai.ecu_startup import Outcome
from opendbc.car.hyundai.ev6_startup import EV6Startup
from opendbc.car.hyundai.gv70_startup import GV70Startup
from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai.hyundaicanfd import hkg_can_fd_checksum
from opendbc.car.hyundai.ioniq6_handoff import TimestampedCanPacket
from openpilot.starpilot.tests.test_ev6_startup import params
from opendbc.car.hyundai.values import CAR
from openpilot.starpilot.vehicle_startup import VehicleStartupOwner
from openpilot.starpilot.car.hyundai.aol import policy_for


def card_publication_sequence(context):
  # Run the reached production statements, retaining their actual source order.
  from openpilot.selfdrive.car.card import Car, hyundai_aol_safety_config
  source=textwrap.dedent(inspect.getsource(Car.__init__))
  tree=ast.parse(source)
  statements=[]
  for statement in tree.body[0].body:
    if isinstance(statement,ast.Expr) and isinstance(statement.value,ast.Call):
      func=statement.value.func
      if (isinstance(func,ast.Attribute) and isinstance(func.value,ast.Attribute) and
          isinstance(func.value.value,ast.Name) and func.value.value.id=='self' and
          func.value.attr=='vehicle_startup' and
          func.attr in ('configure','finalize_aol_configuration','seal_publication')):
        statements.append(statement)
    if isinstance(statement,ast.If) and isinstance(statement.test,ast.Attribute) and statement.test.attr=='aol_qualified':
      statements.append(statement)
  calls=[statement.value.func.attr for statement in statements if isinstance(statement,ast.Expr)]
  assert calls==['configure','finalize_aol_configuration','seal_publication']
  exec(compile(ast.Module(body=statements,type_ignores=[]),'actual-card-publication-order','exec'),
       {'self':context,'aol_policy':policy_for(context.CP), 'HYUNDAI_CAR':CAR,
        'hyundai_aol_safety_config':hyundai_aol_safety_config})


@pytest.fixture(params=[(CAR.KIA_EV6,EV6Startup),(CAR.GENESIS_GV70_ELECTRIFIED_1ST_GEN,GV70Startup)])
def configured(request):
  car,kind=request.param
  cp=params(radar=True,car=car)
  owner=kind(cp,(lambda:[],lambda frames:None))
  owner._capture=lambda:None  # Actual no-capture fallback issues no ECU request.
  with patch.object(time,'CLOCK_BOOTTIME',getattr(time,'CLOCK_BOOTTIME',time.CLOCK_MONOTONIC),create=True):
    stock=owner.prepare(admission=lambda:True)
  ci=CarInterface(stock)
  ci.update([])  # Discover the actual lazily subscribed parser messages.
  packers={bus:CANPacker(parser.dbc.name) for bus,parser in ci.can_parsers.items()}
  def recv():
    frames=[]
    for bus,parser in ci.can_parsers.items():
      for address in parser.addresses:
        message=parser.dbc.addr_to_msg[address]
        frames.append(CanData(*packers[bus].make_can_msg(message.name,parser.bus,{})))
    return [TimestampedCanPacket(frames,time.clock_gettime_ns(time.CLOCK_BOOTTIME))]
  owner.callbacks=(recv,lambda frames:None)
  return owner,ci


def test_actual_card_stock_fallback_configure_mark_seal(configured):
  owner,ci=configured
  assert policy_for(ci.CP).intent_supported
  assert owner.outcome is Outcome.STOCK_UNTOUCHED
  assert ci.CP.safetyConfigs[0].safetyParam==0x11
  context=SimpleNamespace(CI=ci,CP=ci.CP,vehicle_startup=VehicleStartupOwner(owner),aol_qualified=True)
  with patch.object(time,'CLOCK_BOOTTIME',getattr(time,'CLOCK_BOOTTIME',time.CLOCK_MONOTONIC),create=True):
    card_publication_sequence(context)
  assert owner.published and owner.ready
  assert ci.CP.safetyConfigs[0].safetyParam==0x811
  assert ci.CP.alternativeExperience==0 and not ci.CP.openpilotLongitudinalControl
  assert owner.prepared_for(ci.CP)


@pytest.mark.parametrize('defect',['long','word','experience','tuning','passive'])
def test_finalization_rejects_every_nonmarker_change(configured,defect):
  owner,ci=configured
  with patch.object(time,'CLOCK_BOOTTIME',getattr(time,'CLOCK_BOOTTIME',time.CLOCK_MONOTONIC),create=True):
    owner.configure(ci)
  ci.CP.safetyConfigs[0].safetyParam|=0x800
  if defect=='long':
    owner.outcome=Outcome.SENT_UNCONFIRMED
  elif defect=='word':
    ci.CP.safetyConfigs[0].safetyParam=0x815
  elif defect=='experience':
    ci.CP.alternativeExperience=32
  elif defect=='tuning':
    ci.CP.longitudinalActuatorDelay+=.1
  else:
    ci.CP.passive=True
  with pytest.raises(RuntimeError):
    owner.finalize_aol_configuration(ci)
  assert not owner.published


@pytest.mark.parametrize('mode',['stock_off','long'])
def test_unchanged_configurations_seal_without_rebinding(configured,mode):
  owner,ci=configured
  if mode=='long':
    cp=params(radar=True,car=ci.CP.carFingerprint)
    sent=[]
    counter=0
    def capture():
      nonlocal counter
      counter+=1
      data=bytearray(32)
      data[2],data[4]=counter%256,7
      data[:2]=hkg_can_fd_checksum(0x51,None,data).to_bytes(2,'little')
      return [TimestampedCanPacket([CanData(0x51,bytes(data),0)],time.clock_gettime_ns(time.CLOCK_BOOTTIME))]
    owner=type(owner)(cp,(capture,sent.extend))
    def disable(recv,send,**kwargs):
      send([CanData(kwargs['addr'],b'\x03\x28\x83\x01'+bytes(4),kwargs['bus'])])
      return True
    # Capture real counter/checksum frames before the classified sending transport.
    with patch.object(owner,'_disable',side_effect=disable), patch.object(time,'CLOCK_BOOTTIME',
         getattr(time,'CLOCK_BOOTTIME',time.CLOCK_MONOTONIC),create=True):
      cp=owner.prepare(admission=lambda:True)
    assert owner.outcome is Outcome.SENT_UNCONFIRMED and sent
    ci=CarInterface(cp)
  before=ci.CP.to_dict()
  context=SimpleNamespace(CI=ci,CP=ci.CP,vehicle_startup=VehicleStartupOwner(owner),aol_qualified=False)
  with patch.object(time,'CLOCK_BOOTTIME',getattr(time,'CLOCK_BOOTTIME',time.CLOCK_MONOTONIC),create=True):
    card_publication_sequence(context)
  assert ci.CP.to_dict()==before and owner.published
  with pytest.raises(RuntimeError):
    owner.finalize_aol_configuration(ci)
