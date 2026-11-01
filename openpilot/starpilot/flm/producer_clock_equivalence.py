"""Pinned mixed-producer closure and fresh bounded clock equivalence.

Source/ABI provenance is not inspection of a vehicle's running kernel. This
optional source packet grants no native, plant, model or live authority.
"""
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import time

# Each advertised clock resolution contributes to the conservative interval.
# Two supported 1ms clocks already contribute 2ms before sampling overhead.
MAX_CLOCK_RESOLUTION_NS = 1_000_000
MAX_CLOCK_UNCERTAINTY_NS = 3_000_000
PRODUCER_SOURCE_HASHES = (
  ('openpilot', 'system/camerad/cameras/spectra.cc', '4fa26c17f0fb792a4ab6815657beccddee1d641faa2fd02e5aa7393f717a2f3a'),
  ('openpilot', 'system/camerad/cameras/camera_qcom2.cc', 'b34c24c9984a0ac086f7b961cb7ff09d0501231da50aaad0e676bb3dc3f2edd9'),
  ('openpilot', 'system/camerad/cameras/camera_common.cc', 'bd9a416a6ee21047b4deec6df04276d8717cd92fe14de43f3e9daff86b62bb8f'),
  ('openpilot', 'selfdrive/modeld/modeld.py', '56b7544da156bc73e2852d0d806d3081c0f2f28286d222d2825c18f3c761dd9d'),
  ('openpilot', 'selfdrive/modeld/fill_model_msg.py', 'b3916c34d1073d0c26a7a8adc51e7b148f63a1ac68b388226170fd0f8b8b56b4'),
  ('openpilot', 'system/sensord/sensord.py', '7c79ab07d775622a43001e5a9b9ccc9a7bafb065940185849b33eefc2fd26420'),
  ('openpilot', 'system/sensord/sensors/lsm6ds3_gyro.py', '3e561f3d8572e1477c9b4b810c7570837d51e81c5672fe21ec67a68cae00dd92'),
  ('openpilot', 'system/sensord/sensors/lsm6ds3_accel.py', 'a7db3f51a2645f9b712dd34c4df4be6fd03a22565b9e72078dba13d2d4c5f073'),
  ('openpilot', 'selfdrive/locationd/locationd.py', '632ce44e7137d2f5fbadc420a7bafecf0bfa9558ea5d2eb5321273e322c524c5'),
  ('openpilot', 'selfdrive/locationd/models/pose_kf.py', '05925942c847e00ff01f4eade113bf2ef938bc638e5c119942059901219a8901'),
  ('msgq', 'visionipc/visionipc_server.cc', 'a4325a5171eacb931eb271d06a992d2f27216040df113d92099f62cdfa67d81a'),
  ('msgq', 'visionipc/visionipc_client.cc', 'd142cd60eedabfc581e9770fcd6f0ca81b762ba9dbc608b54dd20a27a317a52d'),
  ('msgq', 'visionipc/visionipc.h', 'e21425d41aea4e01a19ed17969f0e9ff6f76caeb8f6818860c76791e042b92b9'),
  ('msgq', 'visionipc/visionipc_pyx.pyx', '346e7a53985132855954e62f4274984c2df39abe88ae2a3d050906327d9d0b83'),
  ('rednose', 'helpers/kalmanfilter.py', '103e29e336d87ad23134495d5db4853a3f72c9677a8422d0697d70ec1ae1cd4e'),
  ('rednose', 'helpers/ekf_sym.py', 'e56d50c5c6cd11f1ebc2b682e1b30e60fee89856a8225a30bdf4b2ce0a7b7dc0'),
  ('rednose', 'helpers/ekf_sym.cc', 'cb9003888d56fd9da8b45402030c6821e83333c3146867e1759e44b2fda20771'),
  ('rednose', 'helpers/ekf_sym_pyx.pyx', '039236aeb965d87a5ac7a880e40e8cb15ddd09685ada12b17bcc540681ed0b17'),
  ('rednose', 'helpers/ekf_sym.h', 'f08854f4336900737ff2e9ff7a5ec930f245795d394e02468f0a4fd73a4a9068'),
)
KERNEL_ABI_SOURCE_PIN = (
  'agnos-sdm845:4ee6b71b8ab9a461248985470726f5917943bc91',
  'V4L_EVENT_CAM_REQ_MGR_SOF_BOOT_TS:get_monotonic_boottime64',
  '56c3d16e405a435d49ca6a814a95a698582b8127ad7c71d25846f89b90bca114',
  '86e5c4212d6ca6fcae7eb1d2c763331ec100c930ac3ed5789a0ae4f3b73f2b70',
  'GPIO:ktime_get_real_ns->sensord-wall-minus-monotonic',
)
CLOSURE_BYTES = json.dumps((PRODUCER_SOURCE_HASHES, KERNEL_ABI_SOURCE_PIN), separators=(',', ':')).encode('ascii')
CLOSURE_SHA256 = hashlib.sha256(CLOSURE_BYTES).hexdigest()
CONVENTION = 'mono_boot_equivalent_interval_v1'


def clock_uncertainty_limit(source_domain):
  return MAX_CLOCK_UNCERTAINTY_NS if source_domain == CONVENTION else MAX_CLOCK_RESOLUTION_NS


def pinned_default_contract():
  """Startup-only bounded installed producer closure reads, never per control/pose work."""
  from openpilot.starpilot.flm.original_source_capture import OriginalSourceClockContract
  import importlib.util
  roots = {}
  if len(PRODUCER_SOURCE_HASHES) > 32:
    raise ValueError('original producer closure bound')
  for package, relative, expected in PRODUCER_SOURCE_HASHES:
    if package not in roots:
      spec = importlib.util.find_spec(package)
      if spec is None or not spec.submodule_search_locations:
        raise ValueError('installed original producer package source unavailable')
      roots[package] = Path(next(iter(spec.submodule_search_locations))).resolve()
    data = (roots[package] / relative).read_bytes()
    if not 0 < len(data) <= 1024 * 1024 or hashlib.sha256(data).hexdigest() != expected:
      raise ValueError(f'unsupported original mixed-producer source closure: {package}/{relative}')
  return OriginalSourceClockContract(CONVENTION, CLOSURE_SHA256, CLOSURE_SHA256, 'pinned-source-abi')


@dataclass(frozen=True)
class ClockEquivalence:
  samples: tuple
  mono_resolution_ns: int
  boot_resolution_ns: int
  offset_lower_ns: int
  offset_upper_ns: int
  uncertainty_ns: int

  def primitive(self):
    return (self.samples, self.mono_resolution_ns, self.boot_resolution_ns,
      self.offset_lower_ns, self.offset_upper_ns, self.uncertainty_ns)


def equivalence_from_samples(samples, mono_resolution_ns, boot_resolution_ns):
  if (type(samples) is not tuple or len(samples) != 3 or
      any(type(value) is not int or not 0 < value <= MAX_CLOCK_RESOLUTION_NS
        for value in (mono_resolution_ns, boot_resolution_ns))):
    raise ValueError('three original samples and explicit clock resolutions required')
  intervals = []
  previous = 0
  quantization = mono_resolution_ns + boot_resolution_ns
  for sample in samples:
    if type(sample) is not tuple or len(sample) != 3 or any(type(value) is not int or value <= 0 for value in sample):
      raise ValueError('original clock sample shape/types')
    mono_before, boot, mono_after = sample
    if not previous <= mono_before <= mono_after:
      raise ValueError('original clock sample reversed/reordered')
    previous = mono_after
    intervals.append((boot - mono_after - quantization, boot - mono_before + quantization))
  lower = max(a for a, _ in intervals)
  upper = min(b for _, b in intervals)
  uncertainty = max(abs(lower), abs(upper))
  if not lower <= 0 <= upper or uncertainty > MAX_CLOCK_UNCERTAINTY_NS:
    raise ValueError('mixed producer clocks not bounded-equivalent at original receipt')
  return ClockEquivalence(samples, mono_resolution_ns, boot_resolution_ns, lower, upper, uncertainty)


def sample_equivalence():
  # Include clock_getres quantization; zero or omitted resolution is invalid.
  mono_resolution = time.clock_getres(time.CLOCK_MONOTONIC)
  boot_resolution = time.clock_getres(time.CLOCK_BOOTTIME)
  if any(type(value) not in (int, float) or not math.isfinite(value) or value <= 0 for value in (mono_resolution, boot_resolution)):
    raise ValueError('original clock resolution unavailable')
  resolutions = tuple(max(1, math.ceil(value * 1e9)) for value in (mono_resolution, boot_resolution))
  samples = tuple((time.monotonic_ns(), time.clock_gettime_ns(time.CLOCK_BOOTTIME), time.monotonic_ns()) for _ in range(3))
  return equivalence_from_samples(samples, *resolutions)


def source_interval(raw_timestamp_ns, received_ns, now_ns, witness):
  if not isinstance(witness, ClockEquivalence):
    raise ValueError('typed original clock witness required')
  checked = equivalence_from_samples(witness.samples, witness.mono_resolution_ns, witness.boot_resolution_ns)
  if checked != witness or any(type(value) is not int for value in (raw_timestamp_ns, received_ns, now_ns)):
    raise ValueError('changed original clock witness')
  lower, upper = raw_timestamp_ns - witness.uncertainty_ns, raw_timestamp_ns + witness.uncertainty_ns
  if not 0 < lower <= upper <= received_ns <= now_ns or now_ns - lower > 100_000_000:
    raise ValueError('original source interval freshness/future')
  return lower, upper
