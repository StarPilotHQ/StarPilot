import time

import openpilot.cereal.messaging as messaging
from openpilot.common.swaglog import cloudlog

from openpilot.starpilot.controllers.toyota_cruise import ToyotaCruisePreference, capability as toyota_cruise_capability


class ResumePlanInputs:
  """Card-owned fresh plan evidence, independent of Controls' saved preference snapshot."""
  def __init__(self, *, boottime=False):
    self.boottime = boottime
    from openpilot.starpilot.longitudinal.inputs import ResumeFreshness
    self.sm = messaging.SubMaster(["deviceState", "carState", "longitudinalPlan"], frequency=25)
    self.freshness = ResumeFreshness()

  def update(self, now_ns):
    self.sm.update(0)
    clock_ns = time.clock_gettime_ns(time.CLOCK_BOOTTIME) if self.boottime else time.monotonic_ns()
    return (type(now_ns) is int and abs(clock_ns - now_ns) <= 150_000_000 and
            self.freshness.current(self.sm) and not self.sm['longitudinalPlan'].shouldStop)


def configure_controller(CI, params):
  cp = CI.CP
  controller = CI.CC
  from openpilot.starpilot.car.ford.cluster_preferences import FordClusterPreference, qualified as cluster_qualified
  if controller is not None and cluster_qualified(cp):
    controller.hands_free_cluster_input = FordClusterPreference(params)
  from opendbc.bluepilot_lateral.hosts.starpilot.safety_param import lateral_inputs_required
  if controller is not None and lateral_inputs_required(cp) and hasattr(controller, "bluepilot_inputs"):
    from openpilot.starpilot.car.ford.bluepilot import BluePilotInputs
    controller.bluepilot_inputs = BluePilotInputs(params)
  if controller is not None and cp.brand == 'gm':
    from opendbc.car.gm.values import (is_bolt_euv_longitudinal, is_volt_longitudinal,
                                     camera_acc_pedal_profile, volt_cc_pedal_profile)
    from opendbc.car.gm.suburban import stopping_decel_rate as suburban_stopping_decel_rate
    pedal = camera_acc_pedal_profile(cp) or volt_cc_pedal_profile(cp)
    if (is_bolt_euv_longitudinal(cp) or is_volt_longitudinal(cp) or suburban_stopping_decel_rate(cp) is not None or
        pedal is not None and pedal.longitudinal):
      from openpilot.starpilot.car.gm.tune_preferences import GradeCompensationPreference
      controller.long_pitch_input = GradeCompensationPreference(params)
  from opendbc.car.gm.truck_longitudinal import truck_tuning_supported
  if controller is not None and truck_tuning_supported(cp):
    from openpilot.starpilot.car.gm.tune_preferences import TruckTuningPreference
    controller.truck_tuning_input = TruckTuningPreference(cp, params)
  from opendbc.car.gm.long_tune import tune_options
  if controller is not None and 1 in tune_options(cp):
    from openpilot.starpilot.car.gm.tune_preferences import AccTunePreference
    controller.gm_acc_tune_input = AccTunePreference(cp, params)
  from opendbc.car.toyota import pedal
  if pedal.configured(cp):
    from openpilot.starpilot.car.toyota.pedal_preferences import PedalPreference
    from openpilot.starpilot.car.toyota.prius_preferences import filter_preference_enabled
    if not filter_preference_enabled(params):
      pedal.prepare_stock(cp)
    if controller is not None:
      controller.pedal_input = PedalPreference(cp, params)
  from opendbc.car.toyota.rav4h_filter import enabled as rav4h_filter_enabled, prepare_stock as prepare_rav4h_stock
  if rav4h_filter_enabled(cp):
    from openpilot.starpilot.car.toyota.rav4h_preferences import Rav4hFilterPreference
    from openpilot.starpilot.car.toyota.prius_preferences import filter_preference_enabled
    if not filter_preference_enabled(params):
      prepare_rav4h_stock(cp)
    elif controller is not None:
      controller.rav4h_filter_input = Rav4hFilterPreference(cp, params)
  from opendbc.car.toyota import dsu_bypass
  if dsu_bypass.enabled(cp):
    from openpilot.starpilot.car.toyota.bypass_preferences import BypassPreference
    from openpilot.starpilot.car.toyota.prius_preferences import filter_preference_enabled
    if not filter_preference_enabled(params):
      dsu_bypass.prepare_stock(cp)
    elif controller is not None:
      controller.bypass_input = BypassPreference(cp, params)
  from opendbc.car.toyota import advert_filter
  if advert_filter.enabled(cp):
    from openpilot.starpilot.car.toyota.advert_preferences import AdvertFilterPreference
    from openpilot.starpilot.car.toyota.prius_preferences import filter_preference_enabled
    if not filter_preference_enabled(params):
      advert_filter.prepare_stock(cp)
    elif controller is not None:
      controller.advert_filter_input = AdvertFilterPreference(cp, params)
  from opendbc.car.toyota.prius_longitudinal import enabled as prius_filter_enabled, prepare_stock as prepare_prius_stock
  if prius_filter_enabled(cp):
    from openpilot.starpilot.car.toyota.prius_preferences import PriusFilterPreference, filter_preference_enabled
    if not filter_preference_enabled(params):
      prepare_prius_stock(cp)
      if controller is not None:
        controller.prius_longitudinal = False
    elif controller is not None:
      controller.prius_filter_input = PriusFilterPreference(cp, params)
  from opendbc.car.gm.values import is_gm_auto_hold, is_volt_one_pedal, is_bolt_pedal_profile, camera_acc_pedal_profile, volt_cc_pedal_profile
  cc_pedal = volt_cc_pedal_profile(cp)
  camera_pedal = camera_acc_pedal_profile(cp) or cc_pedal
  if controller is not None and camera_pedal is not None and camera_pedal.longitudinal:
    from openpilot.starpilot.car.gm.camera import CameraPedalPreference
    controller.camera_pedal_input = CameraPedalPreference(cp, params)
    if cc_pedal is not None or camera_pedal.volt:
      controller.longitudinal_maneuver_input = CameraPedalPreference(cp, params, maneuver=True)
  if controller is not None and is_bolt_pedal_profile(cp):
    from openpilot.starpilot.car.gm.paddle import PADDLE_IDS, PaddlePreference
    if cp.carFingerprint in PADDLE_IDS:
      controller.maneuver_paddle_input = PaddlePreference(cp, params)
  if controller is not None and is_gm_auto_hold(cp) and controller.gm_auto_hold:
    from openpilot.starpilot.car.gm.auto_hold import AutoHoldPreference
    controller.gm_auto_hold_input = AutoHoldPreference(cp, params)
  if controller is not None and is_volt_one_pedal(cp) and controller.volt_one_pedal:
    from openpilot.starpilot.car.gm.auto_hold import AutoHoldPreference
    controller.volt_one_pedal_input = AutoHoldPreference(cp, params, "VoltOnePedalMode")
  from opendbc.car.gm.values import is_volt_longitudinal
  if controller is not None and is_volt_longitudinal(cp) and controller.volt_sng:
    try:
      from opendbc.car.gm.values import GMFlags, is_volt_ascm_longitudinal, is_volt_sdgm_profile
      controller.volt_sng_plan_input = ResumePlanInputs(
        boottime=(is_volt_sdgm_profile(cp, longitudinal=True) and cp.safetyConfigs[0].safetyParam == 0x5007 or
                  cp.alternativeExperience == 32 and not cp.flags & GMFlags.PEDAL_LONG and is_volt_ascm_longitudinal(cp)))
    except OSError:
      controller.volt_sng_plan_input = None
      cloudlog.exception('Optional Volt resume input transport unavailable')
  if controller is not None and toyota_cruise_capability(cp) is not None:
    controller.reverse_cruise_input = ToyotaCruisePreference(cp, params)
  if controller is not None and cp.brand == "honda" and getattr(controller, "nidec_interceptor", None) is not None:
    from openpilot.starpilot.car.honda.longitudinal import BoschLearningParams
    controller.interceptor_learning_params = BoschLearningParams(params, controller.nidec_interceptor)

  if controller is not None and cp.brand == "honda" and getattr(controller, "bosch_longitudinal", None) is not None:
    from openpilot.starpilot.car.honda.longitudinal import BoschLearningParams
    controller.bosch_learning_params = BoschLearningParams(params, controller.bosch_longitudinal)

  from opendbc.car.hyundai.g90_lead import eligible as g90_lead_eligible
  if controller is not None and g90_lead_eligible(cp):
    from openpilot.starpilot.longitudinal.g90_lead import G90LeadInputs
    try:
      controller.g90_lead_inputs = G90LeadInputs()
    except OSError:
      controller.g90_lead_inputs = None
      cloudlog.exception('Optional G90 lead input transport unavailable')

  from opendbc.car.hyundai.gv70_camera_lead import eligible as gv70_lead_eligible
  if controller is not None and gv70_lead_eligible(cp):
    from openpilot.starpilot.longitudinal.gv70_lead import GV70LeadInputs
    try:
      controller.gv70_lead_inputs = GV70LeadInputs()
    except OSError:
      controller.gv70_lead_inputs = None
      cloudlog.exception('Optional GV70 lead input transport unavailable')

  from opendbc.car.hyundai.ev9_longitudinal import qualified as ev9_long_qualified
  from opendbc.car.hyundai.ioniq5pe_long_profile import qualified as pe_long_qualified
  if controller is not None and (ev9_long_qualified(cp) or pe_long_qualified(cp)):
    from openpilot.starpilot.longitudinal.canfd_lead import CANFDLeadInputs
    try:
      controller.ev9_lead_inputs = CANFDLeadInputs()
    except OSError:
      controller.ev9_lead_inputs = None
      cloudlog.exception('Optional EV9 lead input transport unavailable')

  if controller is not None and getattr(controller, 'ioniq6_longitudinal', None) is not None:
    from openpilot.starpilot.longitudinal.canfd_lead import CANFDLeadInputs
    try:
      controller.ioniq6_lead_inputs = CANFDLeadInputs()
    except OSError:
      controller.ioniq6_lead_inputs = None
      cloudlog.exception('Optional Ioniq 6 lead input transport unavailable')

  if controller is not None and getattr(controller, 'torque_ev_scc_enabled', False):
    from openpilot.starpilot.longitudinal.canfd_lead import CANFDLeadInputs
    try:
      controller.torque_ev_lead_inputs = CANFDLeadInputs()
    except OSError:
      controller.torque_ev_lead_inputs = None
      cloudlog.exception('Optional torque EV lead input transport unavailable')
