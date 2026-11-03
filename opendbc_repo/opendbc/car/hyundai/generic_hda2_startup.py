from copy import deepcopy
from typing import Any
import time

from opendbc.car.hyundai.canfd_stock_aol import qualified as stock_qualified, qualified_long
from opendbc.car.hyundai.ecu_continuity import ContinuousECUStartup, StartupKeeper
from opendbc.car.hyundai.ecu_startup import Outcome, stock_copy
from opendbc.car.hyundai.radar_hda2_startup import RadarHDA2Startup
from opendbc.car.hyundai.values import CAR, HyundaiFlags, is_blended

CARS = frozenset((
  CAR.GENESIS_G80_2ND_GEN_FL,
  CAR.HYUNDAI_IONIQ_5_N,
  CAR.HYUNDAI_KONA_2ND_GEN,
  CAR.HYUNDAI_KONA_HEV_2ND_GEN,
  CAR.HYUNDAI_SANTA_CRUZ_1ST_GEN,
  CAR.HYUNDAI_SANTA_CRUZ_2025,
  CAR.HYUNDAI_SONATA_2024,
  CAR.HYUNDAI_SONATA_HEV_2024,
  CAR.HYUNDAI_STARIA_4TH_GEN,
  CAR.HYUNDAI_TUCSON_2025,
  CAR.HYUNDAI_TUCSON_4TH_GEN,
  CAR.HYUNDAI_TUCSON_HEV_2025,
  CAR.HYUNDAI_TUCSON_PHEV_2025,
  CAR.KIA_CARNIVAL_2025,
  CAR.KIA_CARNIVAL_HEV_4TH_GEN,
  CAR.KIA_K4_2025,
  CAR.KIA_K5_2025,
  CAR.KIA_K8_HEV_1ST_GEN,
  CAR.KIA_NIRO_EV_2ND_GEN,
  CAR.KIA_NIRO_HEV_2ND_GEN,
  CAR.KIA_SORENTO_2024,
  CAR.KIA_SPORTAGE_5TH_GEN,
))

EXISTING_OWNERS_OR_DISTINCT_RECEIVERS = frozenset((
  CAR.HYUNDAI_KONA_EV_2ND_GEN,
  CAR.HYUNDAI_IONIQ_5,
  CAR.HYUNDAI_IONIQ_6,
  CAR.KIA_EV6,
  CAR.GENESIS_GV70_ELECTRIFIED_1ST_GEN,
  CAR.GENESIS_GV60_EV_1ST_GEN,
  CAR.HYUNDAI_IONIQ_5_PE,
  CAR.KIA_EV9,
  CAR.KIA_SORENTO_4TH_GEN,
  CAR.KIA_SORENTO_HEV_4TH_GEN,
  CAR.KIA_CARNIVAL_4TH_GEN,
  CAR.GENESIS_GV70_1ST_GEN,
  CAR.GENESIS_GV80,
))

def carnival_long(cp):
  return (cp.carFingerprint == CAR.KIA_CARNIVAL_2025 and qualified_long(cp) and
          cp.safetyConfigs[0].safetyParam in (0x20B4, 0x28B4))


STOCK_WORDS = (0x10, 0x11, 0x12, 0x90, 0x91, 0x92)


def required(cp):
  # Unreviewed future ordinary ECU-disable profiles must fail before publication.
  return (cp.carFingerprint not in EXISTING_OWNERS_OR_DISTINCT_RECEIVERS and not is_blended(cp) and len(cp.safetyConfigs) == 1 and
          cp.openpilotLongitudinalControl and bool(cp.flags & HyundaiFlags.CANFD) and
          not cp.flags & (HyundaiFlags.CANFD_CAMERA_SCC | HyundaiFlags.CANFD_ANGLE_STEERING))


def qualified(cp):
  allowed = (HyundaiFlags.CANFD | HyundaiFlags.EV | HyundaiFlags.HYBRID | HyundaiFlags.CCNC |
             HyundaiFlags.CANFD_LKA_STEER_MSG | HyundaiFlags.CANFD_LKA_STEER_MSG_ALT |
             HyundaiFlags.CANFD_ALT_GEARS | HyundaiFlags.CANFD_ALT_GEARS_2)
  if not required(cp) or cp.carFingerprint not in CARS:
    return False
  if carnival_long(cp):
    return cp.alphaLongitudinalAvailable and cp.alternativeExperience == 0
  factory = CAR(cp.carFingerprint)
  stock = stock_copy(cp)
  return (cp.alphaLongitudinalAvailable and cp.alternativeExperience == 0 and not cp.pcmCruise and
          bool(cp.flags & HyundaiFlags.CANFD_LKA_STEER_MSG) and not int(cp.flags) & ~int(allowed) and
          bool(cp.flags & HyundaiFlags.CCNC) == bool(factory.config.flags & HyundaiFlags.CCNC) and
          stock_qualified(stock) and stock.safetyConfigs[0].safetyParam in STOCK_WORDS and
          cp.safetyConfigs[0].safetyParam == (stock.safetyConfigs[0].safetyParam | 4 |
            (0x2000 if cp.carFingerprint in (CAR.KIA_CARNIVAL_2025, CAR.KIA_CARNIVAL_HEV_4TH_GEN) else 0)))


class GenericHDA2Startup(RadarHDA2Startup):
  def __init__(self, cp, callbacks, *, clock=time.monotonic, keeper_factory=StartupKeeper):
    if not qualified(cp):
      raise ValueError('Exact source-selected ordinary HDA-II LONG configuration required before publication')
    ContinuousECUStartup.__init__(self, cp, callbacks, address=0x730, bus=1,
                                  label='Ordinary HDA-II LONG', clock=clock, keeper_factory=keeper_factory)
    self.stock_cp = stock_copy(cp)

  def continuity_required(self):
    return required(self.cp)

  def finalize_aol_configuration(self, ci):
    if self.published or self.closed or ci is not self.ci or not self.ready:
      raise RuntimeError('Ordinary HDA-II finalization requires configured startup')
    if self.prepared_for(ci.CP):
      return
    long_aol = self.outcome in (Outcome.PENDING, Outcome.SENT_UNCONFIRMED) and qualified_long(ci.CP, marked_only=True)
    if not long_aol and (self.outcome not in (Outcome.STOCK_UNTOUCHED, Outcome.STOCK_RESTORED) or
                         not stock_qualified(ci.CP, marked_only=True)):
      raise RuntimeError('Ordinary HDA-II startup does not admit this AOL configuration')
    expected: Any = deepcopy(self.prepared_cp)
    configs = expected.get('safetyConfigs', [])
    allowed_words = (*tuple(word | 4 for word in STOCK_WORDS), 0x20B4) if long_aol else (*STOCK_WORDS, 0x20B0)
    if (len(configs) != 1 or configs[0]['safetyModel'] != 'hyundaiCanfd' or
        configs[0]['safetyParam'] not in allowed_words or expected['alternativeExperience'] != 0):
      raise RuntimeError('Ordinary HDA-II prepared stock profile cannot be finalized')
    configs[0]['safetyParam'] |= 0x800
    if long_aol:
      expected['alternativeExperience'] = 32
    if ci.CP.to_dict() != expected:
      raise RuntimeError('Ordinary HDA-II finalization changed unrelated CarParams')
    self.prepared_cp = expected

  def prepare_when_controls_ready(self, *, admission):
    if not carnival_long(self.cp):
      return self.prepare(admission=admission)
    if self.outcome is not None or self.closed:
      raise RuntimeError('Carnival startup preparation is single-use')
    if admission() is not True:
      return self.prepare(admission=lambda: False)
    self.admission = admission
    self.floor_ns = time.clock_gettime_ns(time.CLOCK_BOOTTIME)
    self.outcome = Outcome.PENDING
    self.prepared_cp = deepcopy(self.cp.to_dict())
    return self.cp

  def commit_pending(self, ci, *, sources_current, control_current):
    if (not carnival_long(ci.CP) or self.outcome is not Outcome.PENDING or self.closed or not self.published or
        ci is not self.ci or not self.prepared_for(ci.CP) or not sources_current or not control_current or
        self.admission is None or self.admission() is not True):
      raise RuntimeError('Carnival takeover requires current diagnostic, source and inactive control admission')
    try:
      success = self._disable(self.callbacks[0], self._send, bus=self.bus, addr=self.address,
                              com_cont_req=self.communication_control_request())
      if not success or not self.disable_attempted:
        raise RuntimeError('Carnival pending ECU disable was not sent successfully')
      self.outcome = Outcome.SENT_UNCONFIRMED
      self.floor_ns = time.clock_gettime_ns(time.CLOCK_BOOTTIME)
      from opendbc.car import make_tester_present_msg
      self.keeper = self.keeper_factory(
        lambda: self.callbacks[1]([make_tester_present_msg(self.address, self.bus, suppress_response=True)]), clock=self.clock)
      self.keeper.start()
    except BaseException:
      if self.keeper is not None:
        self.keeper.stop()
      try:
        restored = not (self.session_attempted or self.disable_attempted) or self._restore()
      except Exception:
        restored = False
      self.outcome = Outcome.ABORT_UNCERTAIN
      if not restored:
        raise RuntimeError('Carnival pending takeover failed; stock restoration unverified') from None
      raise

  def before_control(self, **context):
    if self.outcome is Outcome.PENDING:
      self.check()
      return False
    return super().before_control(**context)
