"""Native Raylib API shared by StarPilot and upstream widgets."""
import raylib_nb as rl
from raylib_nb._backend import native
from openpilot.starpilot.ui.native import _raylib_extra

_raylib_extra.install(rl, native.__file__)
