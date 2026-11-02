"""Selected StarPilot torque behavior outside the upstream controller."""

from openpilot.starpilot.lateral.controller_selection import ControllerMode


class TorqueExtension:
  def __init__(self, policy, *, parameter_factor=None):
    self.policy = policy
    self.parameter_factor = parameter_factor
    self.ratio_scale = getattr(policy, 'steer_ratio_scale', None)

  def transform_torque_parameters(self, factor, offset, friction):
    if self.parameter_factor is not None:
      factor *= self.parameter_factor
    return factor, offset, friction

  def vehicle_model_ratio(self, ratio, speed):
    return ratio if self.ratio_scale is None else ratio * self.ratio_scale(speed)

  def update(self, active, cs, vm, params, safety_limited, curvature, curvature_limited, delay):
    return self.policy.update(active, cs, vm, params, safety_limited, curvature, curvature_limited, delay)



def create_extension(parent, cp, mode, selected, *, turn_assist=False):
  if mode != ControllerMode.STARPILOT:
    return None
  from openpilot.starpilot.lateral.hkg_vehicle_policy import HKGVehicleTorquePolicy, PROFILES, policy_for
  if selected in PROFILES:
    if policy_for(cp) != selected:
      raise ValueError('HKG torque selection does not match CarParams')
    policy = HKGVehicleTorquePolicy(parent, cp)
    return TorqueExtension(policy, parameter_factor=policy.FACTOR_MULT)
  if selected == 'generic':
    from openpilot.starpilot.lateral.generic_torque_policy import GenericTorquePolicy
    return TorqueExtension(GenericTorquePolicy(parent, cp))
  if selected == 'ioniq6':
    from openpilot.starpilot.lateral.ioniq6_policy import Ioniq6TorquePolicy
    policy = Ioniq6TorquePolicy(parent, cp, turn_assist=turn_assist)
    return TorqueExtension(policy, parameter_factor=policy.FACTOR_MULT)
  if selected == 'genesis_gv70_electrified':
    from openpilot.starpilot.lateral.genesis_gv70_policy import GenesisGV70TorquePolicy, supported_cp
    if supported_cp(cp):
      return TorqueExtension(GenesisGV70TorquePolicy(parent, cp))
  if selected == 'genesis_g70_2020':
    from openpilot.starpilot.lateral.genesis_g70_policy import GenesisG70TorquePolicy, supported_cp
    if supported_cp(cp):
      return TorqueExtension(GenesisG70TorquePolicy(parent, cp))
  if selected == 'prius':
    from openpilot.starpilot.lateral.prius_policy import PriusTorquePolicy
    return TorqueExtension(PriusTorquePolicy(parent, cp))
  if selected == 'rav4_tss2':
    from openpilot.starpilot.lateral.rav4_tss2_policy import Rav4TSS2TorquePolicy, supported_cp
    if supported_cp(cp):
      return TorqueExtension(Rav4TSS2TorquePolicy(parent, cp))
  if selected == 'corolla_tss2':
    from openpilot.starpilot.lateral.corolla_tss2_policy import CorollaTSS2TorquePolicy, supported_cp
    if supported_cp(cp):
      return TorqueExtension(CorollaTSS2TorquePolicy(parent, cp))
  if selected in ('ordinary_camera', 'silverado_cc'):
    from openpilot.starpilot.lateral.camera_policy import CameraTorquePolicy
    return TorqueExtension(CameraTorquePolicy(parent, cp))
  if selected == 'suburban':
    from openpilot.starpilot.lateral.suburban_policy import SuburbanTorquePolicy
    return TorqueExtension(SuburbanTorquePolicy(parent, cp))
  if selected == 'ordinary_cc':
    from openpilot.starpilot.lateral.ordinary_cc_policy import OrdinaryCcTorquePolicy
    return TorqueExtension(OrdinaryCcTorquePolicy(parent, cp))
  if selected == 'ordinary_sdgm':
    from openpilot.starpilot.lateral.sdgm_policy import SdgmTorquePolicy
    return TorqueExtension(SdgmTorquePolicy(parent, cp))
  if selected == 'ordinary_ascm':
    from openpilot.starpilot.lateral.ascm_policy import AscmTorquePolicy
    return TorqueExtension(AscmTorquePolicy(parent, cp))
  if selected == 'sonata_hybrid':
    from openpilot.starpilot.lateral.sonata_hybrid_policy import SonataHybridTorquePolicy
    policy = SonataHybridTorquePolicy(parent, cp)
    return TorqueExtension(policy, parameter_factor=policy.FACTOR_MULT)
  if selected == 'palisade':
    from openpilot.starpilot.lateral.palisade_policy import PalisadeTorquePolicy
    policy = PalisadeTorquePolicy(parent, cp)
    return TorqueExtension(policy, parameter_factor=policy.FACTOR_MULT)
  if selected == 'volt':
    from openpilot.starpilot.lateral.volt_policy import VoltTorquePolicy
    return TorqueExtension(VoltTorquePolicy(parent, cp))
  if selected == 'bolt':
    from openpilot.starpilot.lateral.bolt_policy import BoltTorquePolicy
    return TorqueExtension(BoltTorquePolicy(parent, cp))
  raise ValueError('No StarPilot torque policy for CarParams')


def selected_policy(controller):
  extension = controller.starpilot_extension
  return None if extension is None else extension.policy


def create_turn_assist(cp, enabled):
  from openpilot.starpilot.lateral.controller_selection import turn_assist_supported
  if not enabled or not turn_assist_supported(cp):
    return None
  from openpilot.starpilot.lateral.torque_turn_assist import TorqueTurnAssist
  return TorqueTurnAssist(cp)


def apply_turn_assist(parent, cs, vm, params, curvature, output):
  if parent.turn_assist is None:
    return output
  return parent.turn_assist.apply(cs, vm, params, curvature, output, parent.steer_max)
