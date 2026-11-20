"""Catch device binaries linked to build-environment Cap'n Proto/KJ libraries."""

from pathlib import Path
import ast
import subprocess
import sys
import unittest
from unittest.mock import Mock


ROOT = Path(__file__).resolve().parents[3]


@unittest.skipUnless(sys.platform.startswith('linux'), 'Requires Linux readelf')
class DeviceRuntimeDependenciesTest(unittest.TestCase):
  def test_capnp_and_kj_are_not_external_dependencies(self):
    for binary in ('openpilot/selfdrive/pandad/pandad',
                   'openpilot/system/camerad/camerad',
                   'openpilot/system/loggerd/bootlog',
                   'openpilot/system/loggerd/loggerd',
                   'openpilot/system/loggerd/encoderd',
                   'openpilot/tools/replay/replay'):
      with self.subTest(binary=binary):
        dynamic = subprocess.check_output(['readelf', '--dynamic', '--wide', str(ROOT / binary)], text=True)
        self.assertNotRegex(dynamic, r'\(NEEDED\).*\[.*lib(?:capnp|kj)',
                            f"{binary}: link Cap'n Proto/KJ statically for AGNOS")


class LiveSteeringServiceInventoryTest(unittest.TestCase):
  def test_live_steering_tracking_is_registered_in_shipped_native_consumers(self):
    service = 'starpilotSteeringTracking'
    tree = ast.parse((ROOT / 'openpilot/cereal/services.py').read_text())
    declarations = [node.value for node in tree.body if isinstance(node, ast.AnnAssign)
                    and isinstance(node.target, ast.Name) and node.target.id == '_services']
    self.assertEqual(len(declarations), 1)
    table = declarations[0]
    if not isinstance(table, ast.Dict):
      self.fail('Service registry must be a dictionary')
    declared = [value for key, value in zip(table.keys, table.values, strict=True)
                if isinstance(key, ast.Constant) and key.value == service]
    self.assertEqual(len(declared), 1)
    definition = declared[0]
    if not isinstance(definition, ast.Tuple):
      self.fail('Service declaration must be a tuple')
    self.assertIs(ast.literal_eval(definition.elts[0]), True)
    for binary in ('openpilot/system/loggerd/loggerd', 'openpilot/tools/replay/replay'):
      with self.subTest(binary=binary):
        self.assertIn(service.encode(), (ROOT / binary).read_bytes(),
                      f'{binary}: rebuild after changing the native service registry')


class VisionBufferAllocatorTest(unittest.TestCase):
  def test_device_build_uses_ion_without_a_build_host_device_node(self):
    source = ast.parse((ROOT / 'msgq_repo/SConscript').read_text())
    first = next(i for i, node in enumerate(source.body) if isinstance(node, ast.Assign)
                 and any(isinstance(target, ast.Name) and target.id == 'vipc_files' for target in node.targets))
    allocator = compile(ast.Module(body=source.body[first:first + 2], type_ignores=[]), '<visionipc build>', 'exec')
    for arch, ion_present, expected in (('comma_arm64', False, 'visionbuf_ion.cc'),
                                        ('comma_arm64', True, 'visionbuf_ion.cc'),
                                        ('aarch64', False, 'visionbuf.cc'),
                                        ('x86_64', False, 'visionbuf.cc'),
                                        ('Darwin', False, 'visionbuf.cc'),
                                        ('aarch64', True, 'visionbuf_ion.cc')):
      with self.subTest(arch=arch, ion_present=ion_present):
        node = Mock()
        node.exists.return_value = ion_present
        context = {'arch': arch, 'File': Mock(return_value=node)}
        exec(allocator, context)
        self.assertEqual(context['vipc_files'][-1], expected)
