import ctypes, platform, unittest
from types import SimpleNamespace
from unittest.mock import patch
from tinygrad import Device
from tinygrad.device import TinyELF
from tinygrad.dtype import dtypes
from tinygrad.helpers import Target
from tinygrad.renderer.cstyle import ClangRenderer

class TestQCOM(unittest.TestCase):
  def test_program_build_preserves_signature(self):
    from tinygrad.runtime import ops_qcom

    target = Target(device='QCOM')
    common = (b'compiled-binary', 'same_kernel', target)
    first = TinyELF(*common, ((None, 0, dtypes.float, (4,)),))
    second = TinyELF(*common, ((None, 0, dtypes.half, (8,)),))

    class Program:
      src = (None, None, None, SimpleNamespace(arg=common[0]))
      def __init__(self, elf): self.elf = elf
      def to_elf(self): return self.elf

    with patch.object(ops_qcom, 'QCOMProgramData', side_effect=lambda _dev, elf: SimpleNamespace(image=b'code', signature=elf.signature)) as parsed:
      with patch.object(ops_qcom, 'make_program', side_effect=lambda _prg, _size, _dev: object()), \
           patch.object(ops_qcom, 'patch', side_effect=lambda _buf, _offsets, _image: object()):
        a = ops_qcom.qcom_build_program(object(), Program(first), ('QCOM',))
        b = ops_qcom.qcom_build_program(object(), Program(second), ('QCOM',))
        again = ops_qcom.qcom_build_program(object(), Program(first), ('QCOM',))
    self.assertIsNot(a, b)
    self.assertIsNot(a, again)
    self.assertEqual(a[0].signature, first.signature)
    self.assertEqual(b[0].signature, second.signature)
    self.assertEqual(again[0].signature, first.signature)
    self.assertEqual(parsed.call_count, 3)

  # although part of the QCOM runtime, this tests flushing the CPU's dcache
  @unittest.skipUnless(isinstance(Device["CPU"].renderer, ClangRenderer) and platform.machine().lower() in {"arm64", "aarch64"},
                       "dcache_flush's inline asm needs ClangRenderer, and runs on arm64")
  def test_dcache_flush(self):
    from tinygrad.runtime.ops_qcom import dcache_flush
    buf = (ctypes.c_uint8 * 64)()
    dcache_flush()(ctypes.addressof(buf), 0)

if __name__ == '__main__':
  unittest.main()
