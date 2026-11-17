"""Native Raylib API shared by StarPilot and upstream widgets."""
import raylib_nb as rl
from raylib_nb._backend import native
from openpilot.starpilot.ui.native import _raylib_extra

native_library_path = native.__file__
if native_library_path is None:
  raise RuntimeError("The active Raylib backend has no native library path")

_raylib_extra.install(rl, native_library_path)
