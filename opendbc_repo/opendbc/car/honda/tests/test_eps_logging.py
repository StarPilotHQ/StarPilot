"""Diagnostic collection responses cannot determine the EPS firmware profile."""

import pytest

from opendbc.car import gen_empty_fingerprint, structs
from opendbc.car.honda.interface import CarInterface
from opendbc.car.honda.values import CAR, HondaFlags, MODIFIED_EPS_FW

SERIAL = b"\x11L-130520-03461268" + b" " * 13
ANGLE = b"39990-TVA,A16C\x00\x00"


def eps(version, *, logging=False, address=0x18DA30F1, subaddress=0):
  return structs.CarParams.CarFw(ecu="eps", address=address, subAddress=subaddress, fwVersion=version,
                                 brand="honda", logging=logging)


def params(identity, firmware):
  fingerprint = gen_empty_fingerprint()
  fingerprint[1][0x1A3] = 8
  return CarInterface.get_params(identity, fingerprint, firmware, False, False, False)


@pytest.mark.parametrize("identity,version", [(identity, version) for identity, versions in MODIFIED_EPS_FW.items() for version in versions])
def test_supported_modified_eps_ignores_logged_diagnostic_response(identity, version):
  expected = params(identity, [eps(version)])
  observed = params(identity, [eps(version), eps(SERIAL, logging=True)])
  assert expected.flags & HondaFlags.EPS_MODIFIED
  assert not expected.dashcamOnly
  assert observed.to_dict() == expected.to_dict()


@pytest.mark.parametrize("other", [eps(SERIAL), eps(ANGLE), eps(b"39990-TVA-A150\x00\x00"),
                                  eps(b"39990-TVA,A150\x00\x00", address=0x18DA31F1),
                                  eps(b"39990-TVA,A150\x00\x00", subaddress=1)])
def test_mixed_nonlogging_eps_still_refuses_modified_profile(other):
  cp = params(CAR.HONDA_ACCORD, [eps(b"39990-TVA,A150\x00\x00"), other, eps(SERIAL, logging=True)])
  assert cp.dashcamOnly
  assert not cp.flags & HondaFlags.EPS_MODIFIED


def test_recorded_custom_angle_eps_remains_unsupported_in_mainline():
  cp = params(CAR.HONDA_ACCORD, [eps(ANGLE), eps(SERIAL, logging=True)])
  assert cp.dashcamOnly
  assert not cp.flags & HondaFlags.EPS_MODIFIED


def test_logged_comma_payload_cannot_create_modified_profile():
  cp = params(CAR.HONDA_ACCORD, [eps(b"39990-TVA-A150\x00\x00"), eps(ANGLE, logging=True)])
  expected = params(CAR.HONDA_ACCORD, [eps(b"39990-TVA-A150\x00\x00")])
  assert cp.to_dict() == expected.to_dict()
  assert not cp.flags & HondaFlags.EPS_MODIFIED
