"""Differential admission checks against the original IntFlag arithmetic."""
import ast
from itertools import product
from pathlib import Path
from types import ModuleType

import pytest

from opendbc.car.hyundai import canfd_owner, canfd_stock_aol, captured_adrv_startup
from opendbc.car.hyundai.values import CAR, HyundaiFlags
from openpilot.starpilot.aol.tests.test_canfd_stock_profiles import params


class EnumArithmetic(ast.NodeTransformer):
  def visit_Attribute(self, node):
    node = self.generic_visit(node)
    if node.attr == 'value' and isinstance(node.value, ast.Attribute) and isinstance(node.value.value, ast.Name) and node.value.value.id == 'HyundaiFlags':
      return node.value
    return node

  def visit_Call(self, node):
    node = self.generic_visit(node)
    if isinstance(node.func, ast.Name) and node.func.id == 'int' and len(node.args) == 1:
      arg = node.args[0]
      if isinstance(arg, ast.Attribute) and arg.attr == 'flags':
        return arg
    return node


def enum_reference(module):
  reference = ModuleType(module.__name__ + '_enum_reference')
  tree = EnumArithmetic().visit(ast.parse(Path(module.__file__).read_text()))
  exec(compile(ast.fix_missing_locations(tree), module.__file__, 'exec'), reference.__dict__)
  return reference


@pytest.fixture(scope='module')
def references():
  owner = enum_reference(canfd_owner)
  stock = enum_reference(canfd_stock_aol)
  startup = enum_reference(captured_adrv_startup)
  stock.config_index = owner.config_index
  startup.topology_index = owner.topology_index
  return owner, stock, startup


def compare(cp, references):
  owner, stock, startup = references
  assert canfd_owner.topology_index(cp) == owner.topology_index(cp)
  assert canfd_owner.config_index(cp) == owner.config_index(cp)
  assert captured_adrv_startup.first_generation(cp) == startup.first_generation(cp)
  assert canfd_stock_aol._base_word(cp) == stock._base_word(cp)
  for marked in (False, True):
    assert canfd_stock_aol.qualified(cp, marked_only=marked) == stock.qualified(cp, marked_only=marked)
    assert canfd_stock_aol.qualified_long(cp, marked_only=marked) == stock.qualified_long(cp, marked_only=marked)


@pytest.mark.parametrize('identity', (CAR.KIA_EV6, CAR.GENESIS_GV70_ELECTRIFIED_1ST_GEN, CAR.HYUNDAI_KONA_EV_2ND_GEN))
def test_all_mask_combinations_and_disallowed_bits(identity, references):
  cp = params(identity)
  bits = sorted({value for name, value in vars(canfd_stock_aol).items() if name.startswith('_FLAG_')})
  # Mutate the SAME builder on every call; no prior eligibility may survive.
  for selected in product((0, 1), repeat=len(bits)):
    cp.flags = sum(bit for bit, enabled in zip(bits, selected, strict=True) if enabled)
    compare(cp, references)
  for flag in HyundaiFlags:
    for unknown in (0, 1 << 31):
      cp.flags = int(flag) | unknown
      compare(cp, references)
  cp.flags = 1 << 31
  compare(cp, references)


@pytest.mark.parametrize('identity', tuple(CAR))
def test_live_identity_topology_and_policy_gates(identity, references):
  cp = params(identity)
  original_flags = int(cp.flags)
  for count in (0, 1, 2, 3):
    cp.safetyConfigs = ([{'safetyModel': 'noOutput', 'safetyParam': 0}] * (count - 1) +
                        [{'safetyModel': 'hyundaiCanfd', 'safetyParam': 0x811}]) if count else []
    for flag in (0, original_flags, original_flags | (1 << 31)):
      cp.flags = flag
      compare(cp, references)
    cp.flags = original_flags
    for field in ('passive', 'dashcamOnly', 'notCar', 'openpilotLongitudinalControl', 'pcmCruise'):
      original = getattr(cp, field)
      setattr(cp, field, not original)
      compare(cp, references)
      setattr(cp, field, original)
    for experience in (0, 32, 1, 64):
      cp.alternativeExperience = experience
      compare(cp, references)
    for control in ('torque', 'angle'):
      cp.steerControlType = control
      compare(cp, references)


def test_unknown_high_bits_on_non_capnp_wrapper(references):
  cp = params(CAR.KIA_EV6)

  class Proxy:
    def __init__(self, flags):
      self.flags = flags

    def __getattr__(self, name):
      return getattr(cp, name)
  for flags in (1 << 32, 1 << 63, int(cp.flags) | (1 << 32), int(cp.flags) | (1 << 63)):
    wrapper = Proxy(flags)
    compare(wrapper, references)
    assert canfd_stock_aol._base_word(wrapper) is None
