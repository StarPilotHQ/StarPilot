"""
Copyright (c) 2026-, Zeph Leggett.

This file is part of jetlink and is licensed under the MIT License.
See the LICENSE file in the root directory for more details.

What jetlink needs from openpilot, as one interface the fork implements.

jetlink never imports openpilot. The fork has one adapter module that holds
every openpilot import jetlink needs; it implements `Openpilot` below and
hands the object in, and jetlink.openpilot reaches openpilot through it and
nothing else. So jetlink is tested against a fake, and an openpilot sync that
moves something jetlink relies on fails the adapter's tests in the fork, where
the fix belongs.

The interface is split by process. The resident gadget owner gets data only
(OwnerConfig), no callbacks, so it stays at the standard library and about
10 MB. The heavy processes get the adapter object and each uses its side:
StatusSide in manager, the UI, hardwared and the model manager, WorkerSide in
the provisioning run, ModelSide in modeld.

The standard library only: the owner imports this module.
"""
from __future__ import annotations

import importlib
import inspect
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

# the Jetlink setting: off; a Jetson, a Linux PC or a Mac on USB; an
# iPhone on the cable. The INT param holds the index
MODES = ('off', 'usb', 'ios')
# what a joining model reports as big_model_state, by the names of the fork's
# modelDataV2SP.acceleratorState enum
STATES = ('none', 'joining', 'retrying', 'ready', 'running', 'unavailable')


@dataclass(frozen=True)
class Keys:
  """The params the fork declares for jetlink, by name: jetlink itself knows no param name."""
  link: str                      # INT, an index into MODES: the Jetlink setting
  offroad: str                   # BOOL: is the car parked (manager's IsOffroad)
  progress: str                  # JSON {stage, frac, msg, drops}: provisioning and join progress
  spec: str                      # JSON: the built model's spec and whether its engine is built
  # sunnypilot's model manager: the big-model pick, JSON {ref, displayName},
  # and the big-model catalog, JSON {bundles}. Without one both are None, and
  # jetlink runs its own default big model, or the one picked on the server.
  # A fork with a picker of its own and no catalog (StarPilot) declares only
  # big_model, and may name its pick by the ONNX's hash, {sha256,
  # displayName}; a newer pick made on the server is written into it
  big_model: str | None = None
  catalog: str | None = None
  # BOOL: an iPhone on a direct cable is asked to charge from the comma
  # (comma.port). Off when unset, and without the key
  charge_phone: str | None = None
  # STRING: where the model manager takes a pick by its ref and stores it in
  # big_model (sunnypilot's ModelManager_DownloadRef). A newer pick made on
  # the server is written there, so the comma's own list shows it; without
  # the key it drives all the same
  select: str | None = None
  # API 3's kept pointers, unread since the model index: until the next minor release
  pointers: str | None = None


@dataclass(frozen=True)
class OwnerConfig:
  """What the resident gadget owner needs, built without importing anything heavy."""
  params_dir: Path                           # the params store's directory, by params.cc's rule
  keys: Keys                                 # the settings it reads and the pick it watches
  chestnut_ids: frozenset[tuple[int, int]]   # (vid, pid) of comma's chestnut, running or in its ROM: never held as a host
  adapter: str                               # the adapter module, which the provisioning run is started with
  cwd: Path                                  # where the run starts: the checkout
  env: Mapping[str, str]                     # over the owner's environment, for the run
  log_file: Path                             # the owner's rotating log


@dataclass(frozen=True)
class ModelFace:
  """What openpilot's modeld reads off a ModelState, supplied for comma's large model."""
  parser: Callable[[], Any]                  # a new Parser: .parse_outputs(dict[str, ndarray]) -> dict
  frame_size: Callable[[int, int], int]      # a camera frame buffer's size for (w, h): get_nv12_info(w, h)[3]
  desire_len: int                            # ModelConstants.DESIRE_LEN
  constants: Any                             # modeld_v2's ModelConstants, which modeld_tinygrad reads off the model
  lat_smooth_seconds: float                  # modeld's LAT_SMOOTH_SECONDS
  long_smooth_seconds: float                 # modeld's LONG_SMOOTH_SECONDS
  get_action_from_model: Callable[..., Any]  # modeld's action function


# The sides are typing Protocols so a type checker can hold the adapter to
# them; conformance() is the check both repos run. A parameter jetlink passes
# by keyword is keyword-only here, and only those names must match. The
# members in OPTIONAL are ones jetlink reads with getattr and a fallback.

class StatusSide(Protocol):
  """Every reader: manager, the UI, hardwared, the model manager."""
  keys: Keys
  log: Any                  # logging.Logger-like (cloudlog): debug, info, warning, error, exception

  def params_dir(self) -> Path:
    """The params store's directory, by params.cc's rule, worked out on every
    call so it follows OPENPILOT_PREFIX as Params does. jetlink reads the link
    setting and IsOffroad there as files, for every setting a heavy process
    reads (manager's should_run among them), so it is cheap and never raises."""

  def get(self, key: str) -> Any:
    """A param's decoded value. None when unset or unknown to this build; never raises."""

  def chestnut_present(self) -> bool:
    """Is comma's chestnut fitted? A USB walk; jetlink caches the answer."""

  def camera(self) -> tuple[int, int, int, int]:
    """This device's (cam_w, cam_h, model_w, model_h): the warp modeld loads."""

  def warp_path(self, cam_w: int, cam_h: int, model_w: int, model_h: int) -> Path:
    """Where modeld's warp for this geometry is: openpilot's driving warp
    pickle, which warp.Warps.load reads."""


class WorkerSide(StatusSide, Protocol):
  """The provisioning run: writes, files, the network."""
  basedir: Path             # the checkout, whose .lfsconfig names the nearest LFS server

  def put(self, key: str, value: Any, *, block: bool = False) -> None:
    """Write a param. May raise, as Params does for a key this build does not declare."""

  def remove(self, key: str) -> None:
    """Clear a param."""

  def model_root(self) -> Path:
    """The model manager's model root (openpilot's Paths.model_root()). jetlink
    keeps its downloads in a directory of its own under it, `jetlink/`: the
    model manager's cache clear deletes every file in the root it does not
    recognise, a downloaded 1.75 GB ONNX included, and leaves directories alone."""


class ModelSide(WorkerSide, Protocol):
  """modeld: comma's model face, whether anything is in control, and the
  structured log line the link's telemetry goes to. What modeld writes onto
  the model every frame is Jetlink.attach's."""

  def model_face(self) -> ModelFace:
    """What a ModelState for comma's large model has to carry."""

  def in_control(self) -> bool:
    """Is openpilot or MADS in control? The joining model asks on modeld's
    frame thread before every frame and swaps the large model in only while
    not. True when it cannot tell; never raises."""

  def event(self, name: str, **fields: Any) -> None:
    """A structured log line (cloudlog.event)."""

  def small_reset(self, small: Any) -> Callable[[], None] | None:
    """Optional (OPTIONAL): the reset of the fork's small model for a hand-back, or None
    for jetlink's own (warp.prepare_reset, which knows stock modeld's,
    modeld_v2's and a ModelState with GPU queues and numpy inputs). Asked once
    on modeld's main thread before the frame loop. The reset runs on the
    frame thread at a hand-back, so it zeroes the history in place and
    allocates and compiles nothing; warp.reset_queues makes one."""

  def model_warp(self, small: Any, cam_w: int, cam_h: int, model_w: int, model_h: int) -> FrameWarp | None:
    """Optional (OPTIONAL): the warp the link sends frames through, warmed,
    or None for openpilot's driving warp pickle at warp_path (warp.Warp). For
    a fork whose runtime cannot load that pickle, typically with its small
    model's own warp. Asked once on modeld's main thread before the frame
    loop, after the small model's warmup; raising keeps the drive on the
    small model, as a pickle that does not load does. The offroad checks
    still look for the pickle (Warps.built): only modeld has a small model
    to ask, so warp_path must name a file either way."""


class Openpilot(ModelSide, Protocol):
  """The whole adapter: one object implements every side."""


# What modeld hands Jetlink.attach and gets back: not the adapter's, pinned so
# a fork knows what its ModelState needs and what it may read off the result.

class SmallModel(Protocol):
  """The fork's own ModelState, whatever its shape. jetlink calls run()
  positionally and writes attributes onto it (lat_delay, PLANPLUS_CONTROL);
  the rest is optional, read with getattr: the frame's desire key and numpy
  inputs (modeld_v2), the lateral delay a joining large model starts on, and
  sunnypilot's planplus gain."""
  desire_key: str
  numpy_inputs: Any
  lat_delay: float
  PLANPLUS_CONTROL: float

  def run(self, bufs: Any, transforms: Any, inputs: Any, after_enqueue: Callable[[], None] | None = None) -> Any:
    """One frame's outputs, as modeld's ModelState.run."""


class JoinedModel(Protocol):
  """What attach returns and modeld reads off and writes onto every frame.
  frame_drop_ratio is written, lat_delay read and written. lat_delay and the
  face (get_action_from_model through LONG_SMOOTH_SECONDS, modeld_v2's reads)
  follow the model that drives, like any name it does not define, so over a
  stock modeld's small model they are there only while the large one drives."""
  handovers: int                    # moves on every swap and hand-back; modeld forgives that frame's drops
  big_model_state: str              # one of STATES
  chestnut: bool                    # modelV2.big
  get_action_from_model: Callable[..., Any]
  constants: Any
  LAT_SMOOTH_SECONDS: float
  LONG_SMOOTH_SECONDS: float
  frame_drop_ratio: float           # modeld's share of dropped camera frames, before every run()
  lat_delay: float

  def run(self, bufs: Any, transforms: Any, inputs: Any, after_enqueue: Callable[[], None] | None = None) -> Any:
    """One frame's outputs, from whichever model drives."""

  def close(self) -> None:
    """Let go of the link at modeld's exit."""


class FrameWarp(Protocol):
  """What an adapter's model_warp returns, warp.Warp's shape, run on modeld's
  frame thread. Only one of the small and the large model runs a frame, so a
  warp that shares the small model's JIT and buffers is never in use by both.
  On a comma, a warp that reads its GPU output back moves it first with
  warp.make_coherent, before the warp's JIT first runs."""
  output: Any   # the warped frame, (2, 6, model_h/2, model_w/2) uint8 as bytes: read after wait(), until the next start()

  def start(self, frame: int, big_frame: int, tfm: Any, big_tfm: Any) -> None:
    """Warp the camera buffers at these addresses under their 3x3 transforms."""

  def wait(self) -> None:
    """Return once `output` holds what start() warped."""


# The members of each protocol an implementation may lack: jetlink reads them
# with getattr and a fallback. conformance() passes an object without one and
# holds one with it to its signature, and adding one keeps API. Kept out of
# the protocols, where a name would be a member itself; a type checker still
# asks for these, as typing has no optional member
OPTIONAL: dict[type, tuple[str, ...]] = {
  ModelSide: ('small_reset', 'model_warp'),
  SmallModel: ('desire_key', 'numpy_inputs', 'lat_delay', 'PLANPLUS_CONTROL'),
  JoinedModel: ('get_action_from_model', 'constants', 'LAT_SMOOTH_SECONDS', 'LONG_SMOOTH_SECONDS', 'lat_delay'),
}


def optional(protocol: type) -> frozenset[str]:
  """The members of `protocol` an implementation may lack, inherited ones included."""
  return frozenset(name for cls in protocol.__mro__ for name in OPTIONAL.get(cls, ()))


def load_adapter(module: str) -> Openpilot:
  """The adapter an adapter module makes. An adapter module is the fork's
  module that holds every openpilot import jetlink needs: its top level
  imports only the standard library, and it has adapter() -> Openpilot and
  owner_config() -> OwnerConfig. The entry points that run as their own
  process (the provisioning run) are told its name."""
  return importlib.import_module(module).adapter()


def members(protocol: type) -> dict[str, inspect.Signature | None]:
  """Every member of `protocol`, inherited ones included: a method's signature
  without self, or None for a data member."""
  found: dict[str, inspect.Signature | None] = {}
  for cls in reversed(protocol.__mro__):
    if cls is object or cls.__module__ == 'typing':
      continue
    for name in inspect.get_annotations(cls):
      if not name.startswith('_'):
        found[name] = None
    for name, value in vars(cls).items():
      if not name.startswith('_') and inspect.isfunction(value):
        sig = inspect.signature(value)
        found[name] = sig.replace(parameters=list(sig.parameters.values())[1:])
  return found


def _shape(sig: inspect.Signature) -> list[tuple[str | None, Any, bool]]:
  """What a caller depends on: each parameter's kind and whether it has a
  default, and the name of one jetlink passes by keyword (keyword-only). The
  other names are the adapter's to choose."""
  return [(p.name if p.kind is p.KEYWORD_ONLY else None, p.kind, p.default is not p.empty)
          for p in sig.parameters.values()]


def _plain(sig: inspect.Signature) -> str:
  """The parameters alone, as a caller writes them: '(key, value, block=False)'."""
  return str(sig.replace(parameters=[p.replace(annotation=p.empty) for p in sig.parameters.values()],
                         return_annotation=sig.empty))


def conformance(obj: object, protocol: type) -> list[str]:
  """Each member of `protocol` that `obj` lacks, or has with parameters a
  caller would trip on (another count, kind or default, or another name for
  one passed by keyword); [] when it conforms. One checker for both repos:
  jetlink pins the interface with it, and the fork's tests run it against the
  real adapter."""
  problems = []
  may_lack = optional(protocol)
  for name, expected in members(protocol).items():
    if not hasattr(obj, name):
      if name not in may_lack:
        problems.append(f"{name}: missing")
      continue
    if expected is None:
      continue
    value = getattr(obj, name)
    if not callable(value):
      problems.append(f"{name}: not callable")
      continue
    try:
      actual = inspect.signature(value)
    except (TypeError, ValueError):
      problems.append(f"{name}: no signature to check")
      continue
    if _shape(actual) != _shape(expected):
      problems.append(f"{name}{_plain(actual)}: expected {name}{_plain(expected)}")
  return problems
