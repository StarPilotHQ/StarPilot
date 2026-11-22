from pathlib import Path

import pytest
from openpilot.common.params import Params
from openpilot.selfdrive.car.card import alpha_long_requested


@pytest.mark.parametrize('release', [False, True])
@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('raw', [None, b'0', b'1'])
def test_retained_choice_is_separate_from_release_runtime_authority(tmp_path, release, enabled, raw):
  params = Params(str(tmp_path))
  path = Path(params.get_param_path('AlphaLongitudinalEnabled'))
  if raw is not None:
    path.write_bytes(raw)
  assert alpha_long_requested(params, is_release=release, openpilot_enabled=enabled) == (raw == b'1' and not release and enabled)
  assert (path.read_bytes() if path.exists() else None) == raw
  # Returning to a supported development branch restores intent without a rewrite.
  assert alpha_long_requested(params, is_release=False) == (raw == b'1')


def test_disabled_master_starts_from_stock_radar_hda1_parameters(tmp_path):
  from opendbc.car import gen_empty_fingerprint
  from opendbc.car.hyundai.interface import CarInterface
  from opendbc.car.hyundai.values import CAR

  params = Params(str(tmp_path))
  params.put_bool('AlphaLongitudinalEnabled', True, block=True)
  fingerprints = gen_empty_fingerprint()
  fingerprints[0].update({0x1CF: 8, 0x130: 32, 0x35: 32, 0x175: 24, 0xA0: 24, 0xEA: 24, 0x1A0: 32})
  identity = CAR.KIA_SORENTO_4TH_GEN
  requested = alpha_long_requested(params, is_release=False, openpilot_enabled=False)
  cp = CarInterface.get_params(identity, fingerprints, [], requested, False, False)
  assert cp.pcmCruise and not cp.openpilotLongitudinalControl
  assert not CarInterface.startup_required(cp)

  requested = alpha_long_requested(params, is_release=False, openpilot_enabled=True)
  cp = CarInterface.get_params(identity, fingerprints, [], requested, False, False)
  assert cp.openpilotLongitudinalControl and CarInterface.startup_required(cp)
  assert params.get_bool('AlphaLongitudinalEnabled')
