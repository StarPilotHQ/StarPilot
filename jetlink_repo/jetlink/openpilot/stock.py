"""
Copyright (c) 2026-, Zeph Leggett.

This file is part of jetlink and is licensed under the MIT License.
See the LICENSE file in the root directory for more details.

The Openpilot interface over stock openpilot's paths: the base a fork's
adapter subclasses, overriding only what its fork does differently.

The one jetlink module that imports openpilot, and only inside a method, where
it is used: the top level is the standard library and interface.py, because
the resident owner imports this module (through the fork's owner_config()).
Which method runs where matters. owner_config() imports only
openpilot.common.basedir (os alone); anything under common.hardware loads
capnp, swaglog loads zmq, and model_face() imports modeld itself, so it is for
modeld alone, where that module is already loaded.

Data are class attributes, behaviour small methods, each a fork may override;
one override point per value: a fork sets KEYS, and the interface's keys
reads it. It is jetlink's contract:
renaming or removing one, or changing what one means, is a breaking change
and bumps API. scripts/upstream (the upstream-paths CI job) checks the paths
and literals here against commaai/openpilot master weekly.

A fork's adapter module defines its subclass lazily, as its top level must
import without a jetlink checkout: docs/porting.md, "The adapter module".
Defined there, the class's module names the adapter module the provisioning
run is started with, so it needs no ADAPTER_MODULE.
"""
from __future__ import annotations

import os
import time
from functools import cached_property
from pathlib import Path

# root.AGNOS is openpilot's own test for a comma (common.hardware.AGNOS), which
# that package answers only after loading capnp; root is on the owner's path
from jetlink.comma import root
from jetlink.openpilot.interface import Keys, ModelFace, OwnerConfig

# how long a Params or SubMaster that failed to load is left before the next
# try: in_control() asks on every realtime frame
RETRY_S = 5.0
# what the base engaged() reads, by service: only those in IN_CONTROL_SERVICES
_ENGAGED = {'carControl': ('enabled', 'latActive', 'longActive'), 'selfdriveState': ('enabled', 'active')}
# what marks stock modeld's ModelState, which small_reset() zeroes (and its prev_desire)
_MODEL_STATE = ('input_queues', 'state_pairs', 'packed_input')


class Stock:
  """jetlink.openpilot.interface.Openpilot over stock openpilot (the 0.11 layout)."""

  # the fork's adapter module, which the provisioning run imports; None for
  # the module the subclass is defined in
  ADAPTER_MODULE: str | None = None
  # the canonical names, which a fork declares in params_keys.h. No model
  # manager upstream: no big-model slot or catalog, so jetlink runs its default
  KEYS = Keys(link='JetlinkLink', offroad='IsOffroad', progress='JetlinkProgress', spec='JetlinkSpec',
              charge_phone='JetlinkChargePhone')
  # comma's chestnut, running and in its ROM (common.hardware.usb's
  # CHESTNUT_USB_IDS + CHESTNUT_ROM_USB_IDS, which CI holds this to): written
  # out, as the owner cannot import that package
  CHESTNUT_IDS = frozenset({(0xADD1, 0x0001), (0x3801, 0x0001), (0x174C, 0x2464), (0x174C, 0x2463)})
  # what in_control() reads; carState too, so a dead card counts as in control.
  # It goes with engaged(): the base reads only the services listed here, so a
  # fork that adds one (zoompilot's carControlSP) overrides engaged() to read it
  IN_CONTROL_SERVICES = ('carState', 'carControl', 'selfdriveState')
  # modeld's driving warp, which its SConscript compiles per camera
  WARP_DIR = 'openpilot/selfdrive/modeld/models'
  WARP_NAME = 'driving_warp_{cam_w}x{cam_h}_tinygrad.pkl'
  OWNER_LOG = Path('/data/log/jetlink-owner.log')

  def __init__(self):
    # nothing imported here: owner_config() builds one in the owner
    self._stores: dict[Path, object] = {}
    self._params_after = 0.0
    self._sm = None
    self._sm_after = 0.0
    self._in_control_error: str | None = None

  # -- the readers --------------------------------------------------------------

  # the interface's members, over the class attributes a fork sets
  @property
  def keys(self) -> Keys:
    return self.KEYS

  @cached_property
  def log(self):
    from openpilot.common.swaglog import cloudlog
    return cloudlog

  @cached_property
  def basedir(self) -> Path:
    # the checkout's root, which holds .lfsconfig and the openpilot package
    from openpilot.common.basedir import BASEDIR
    return Path(BASEDIR)

  def params_dir(self) -> Path:
    # params.cc and hw.h's rule: PARAMS_ROOT, else /data/params on a comma and
    # $HOME/.comma<OPENPILOT_PREFIX>/params elsewhere, then /<OPENPILOT_PREFIX,
    # or d>. HOME unset gives /.comma, as the C++ concatenation does. The
    # environment only, per call, so it follows a prefix as Params does
    prefix = os.environ.get('OPENPILOT_PREFIX', '')
    store = os.environ.get('PARAMS_ROOT')
    if store is None:
      store = '/data/params' if root.AGNOS else os.environ.get('HOME', '') + '/.comma' + prefix + '/params'
    return Path(store) / os.environ.get('OPENPILOT_PREFIX', 'd')

  def _params(self):
    # one Params per store: making one costs more than the read on a comma, and
    # the UI reads several five times a second. By store, for a test's prefix
    where = self.params_dir()
    store = self._stores.get(where)
    if store is None:
      # one that failed to load is not tried again for RETRY_S: get() is on the UI's frame
      if time.monotonic() < self._params_after:
        raise RuntimeError("openpilot's Params failed to load, retrying shortly")
      try:
        from openpilot.common.params import Params
        store = self._stores[where] = Params()
      except Exception:
        self._params_after = time.monotonic() + RETRY_S
        raise
    return store

  def get(self, key: str):
    # hardwared's and the UI's threads read here, which UnknownKeyName (a
    # params library older than the key) must not take down
    try:
      return self._params().get(key)
    except Exception:
      return None

  def chestnut_present(self) -> bool:
    # any chestnut id, its ROM's included, the owner's walk: never the port
    # beside one, even one modeld ignores until its firmware matches
    from jetlink.comma.port import chestnut_attached
    return chestnut_attached(self.CHESTNUT_IDS)

  def camera(self) -> tuple[int, int, int, int]:
    # modeld takes the size from camerad's buffer; a comma four's os04c10 is
    # the 1344x760 one, every other device's the 1928x1208
    from openpilot.common.hardware import HARDWARE
    from openpilot.common.transformations.camera import _ar_ox_fisheye, _os_fisheye
    from openpilot.common.transformations.model import MEDMODEL_INPUT_SIZE
    camera = _os_fisheye if HARDWARE.get_device_type() == 'mici' else _ar_ox_fisheye
    return camera.width, camera.height, *MEDMODEL_INPUT_SIZE

  def warp_path(self, cam_w: int, cam_h: int, model_w: int, model_h: int) -> Path:
    # scons builds it for the one model input size, which jetlink checks the warp makes
    return self.basedir / self.WARP_DIR / self.WARP_NAME.format(cam_w=cam_w, cam_h=cam_h, model_w=model_w, model_h=model_h)

  # -- the provisioning run --------------------------------------------------------

  def put(self, key: str, value, *, block: bool = False) -> None:
    self._params().put(key, value, block=block)

  def remove(self, key: str) -> None:
    self._params().remove(key)

  def model_root(self) -> Path:
    # sunnypilot's Paths.model_root(), where there is one; upstream has none,
    # and the same place it would be
    from openpilot.common.hardware.hw import Paths
    if hasattr(Paths, 'model_root'):
      return Path(Paths.model_root())
    return Path('/data/media/0/models') if root.AGNOS else Path(Paths.comma_home()) / 'media' / '0' / 'models'

  # -- modeld ---------------------------------------------------------------------

  def parser(self):
    """A new Parser for comma's large model's outputs."""
    from openpilot.selfdrive.modeld.parse_model_outputs import Parser
    return Parser()

  def model_constants(self):
    """The ModelConstants modeld reads off the model."""
    from openpilot.selfdrive.modeld.constants import ModelConstants
    return ModelConstants

  def action_function(self):
    """modeld's get_action_from_model."""
    from openpilot.selfdrive.modeld.modeld import get_action_from_model
    return get_action_from_model

  def model_face(self) -> ModelFace:
    from openpilot.selfdrive.modeld.constants import ModelConstants
    from openpilot.selfdrive.modeld.modeld import LAT_SMOOTH_SECONDS, LONG_SMOOTH_SECONDS
    from openpilot.system.camerad.cameras.nv12_info import get_nv12_info
    # desire_len is comma's large model's, which is stock modeld's
    return ModelFace(parser=self.parser, frame_size=lambda w, h: get_nv12_info(w, h)[3],
                     desire_len=ModelConstants.DESIRE_LEN, constants=self.model_constants(),
                     lat_smooth_seconds=LAT_SMOOTH_SECONDS, long_smooth_seconds=LONG_SMOOTH_SECONDS,
                     get_action_from_model=self.action_function())

  def engaged(self, sm) -> bool:
    """Is openpilot in control, off fresh and valid IN_CONTROL_SERVICES?
    carControl's enabled or either axis, or selfdriveState's enabled or active,
    of those services the fork lists."""
    return any(getattr(sm[service], field) for service, fields in _ENGAGED.items()
               if service in self.IN_CONTROL_SERVICES for field in fields)

  def in_control(self) -> bool:
    # on modeld's frame thread before every frame: a poll that never waits, and
    # a service late or invalid, or any failure, holds the swap off, never modeld
    try:
      if self._sm is None:
        # one try per RETRY_S, not one per frame
        if time.monotonic() < self._sm_after:
          return True
        self._sm_after = time.monotonic() + RETRY_S
        from openpilot.cereal import messaging
        self._sm = messaging.SubMaster(list(self.IN_CONTROL_SERVICES))
      services = self.IN_CONTROL_SERVICES
      self._sm.update(0)
      result = not (self._sm.all_alive(services) and self._sm.all_valid(services)) or self.engaged(self._sm)
    except Exception as e:
      # once per distinct error: this runs 20 times a second
      if self._in_control_error != (text := f"{type(e).__name__}: {e}"):
        self._in_control_error = text
        try:
          self.log.error("jetlink: in_control() failed: %s", text)
        except Exception:
          pass
      return True
    self._in_control_error = None
    return bool(result)

  def event(self, name: str, **fields) -> None:
    self.log.event(name, **fields)

  def small_reset(self, small):
    """Stock modeld's ModelState, as its warmup leaves it: the recurrent state
    on the GPU, the packed host inputs and the desire edge, zeroed in place.
    None for another shape, so jetlink's own reset runs."""
    if not all(hasattr(small, name) for name in _MODEL_STATE):
      return None
    from jetlink.openpilot.warp import reset_for_stock
    return reset_for_stock(small, getattr(small, 'prev_desire', None))

  # -- the adapter module's, for jetlinkd --------------------------------------------

  @classmethod
  def owner_config(cls) -> OwnerConfig:
    """What jetlinkd needs, data only: openpilot.common.basedir is the one import."""
    from openpilot.common.basedir import BASEDIR
    op = cls()
    # the provisioning run starts in the checkout with it on the path, where
    # the launch script links jetlink in
    return OwnerConfig(params_dir=op.params_dir(), keys=op.keys, chestnut_ids=cls.CHESTNUT_IDS,
                       adapter=cls.ADAPTER_MODULE or cls.__module__, cwd=Path(BASEDIR), env={'PYTHONPATH': BASEDIR},
                       log_file=cls.OWNER_LOG)

  @classmethod
  def main(cls) -> None:
    """jetlinkd: hold the USB gadget until manager stops this process."""
    from jetlink.openpilot.owner import main
    main(cls.owner_config())
