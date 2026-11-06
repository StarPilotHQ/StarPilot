"""Pulling away from a stop must not disarm Mazda AOL.

Route 00000037--106ca451eb (2023 Mazda3 GEN2 TI2, 2026-11-05) shows the
sequence at every stop:
  - stopped: panda withholds lateral (its vehicle_moving gate) but keeps its AOL
    token, and the host ignores the denial because temporary_restriction holds;
  - first frame with standstill=False (v=0.05 m/s): the host stopped ignoring the
    denial, but the safety-wire sample it judged was taken while still stopped.
    The host cleared allowedLatch in that same frame, so the driver had to cycle MAIN.

These tests replay that timing. The host side is real: native_latch_rejected
computes the rejection exactly as card.py does, and MazdaStockCardIntent consumes
it. Panda is modelled as granting lateral PANDA_CATCHUP_NS after the car moves.
"""
from types import SimpleNamespace

import pytest

from opendbc.car import structs
from opendbc.car.mazda.values import CAR, MazdaSafetyFlags
from openpilot.starpilot.aol.intent import AolSettings
from openpilot.starpilot.aol.runtime import SAFETY_MAX_AGE_NS
from openpilot.starpilot.aol.vehicle import native_latch_rejected
from openpilot.starpilot.car.mazda.stock_intent import RESTRICTION_RELEASE_GRACE_NS, MazdaStockCardIntent

GEN2 = int(MazdaSafetyFlags.GEN2)
MAZDA_MODEL = int(structs.CarParams.SafetyModel.mazda)
FRAME_NS = 10_000_000
WIRE_LAG_NS = 30_000_000       # safety-wire sample age when the host judges it
PANDA_CATCHUP_NS = 40_000_000  # panda sees motion and re-grants lateral


def marked_gen2_cp():
  cp = structs.CarParams(carFingerprint=CAR.MAZDA_3_2019, brand="mazda", flags=GEN2, pcmCruise=True,
                         openpilotLongitudinalControl=False, passive=False, notCar=False, dashcamOnly=False,
                         minSteerSpeed=0.0, transmissionType=structs.CarParams.TransmissionType.automatic,
                         fingerprintSource=structs.CarParams.FingerprintSource.can)
  cp.alternativeExperience = 32
  cp.init("safetyConfigs", 1)
  cp.safetyConfigs[0].safetyModel = MAZDA_MODEL
  cp.safetyConfigs[0].safetyParam = GEN2
  return cp


class Drive:
  def __init__(self, wire_lag_ns=WIRE_LAG_NS):
    self.wire_lag_ns = wire_lag_ns
    self.cp = marked_gen2_cp()
    self.intent = MazdaStockCardIntent(self.cp, AolSettings(True, 0., 0, 0, (0, 0, 0), (0, 0, 0)))
    self.now = 1_000_000_000
    self.moving_since = None   # when panda could first see motion; None while stopped
    self.panda_revoked = False

  def state(self, *, available=True, standstill=False):
    cs = structs.CarState(canValid=True, gearShifter='drive', standstill=standstill, vEgo=0.0 if standstill else 0.05)
    cs.cruiseState.available = available
    return cs

  def panda_allows(self, observed_ns):
    return (not self.panda_revoked and self.moving_since is not None and
            observed_ns >= self.moving_since + PANDA_CATCHUP_NS)

  def tick(self, *, standstill=False, available=True):
    self.now += FRAME_NS
    if standstill:
      self.moving_since = None
    elif self.moving_since is None:
      self.moving_since = self.now
    cs = self.state(available=available, standstill=standstill)
    observed = self.now - self.wire_lag_ns
    native = SimpleNamespace(safetyModel=MAZDA_MODEL, safetyParam=GEN2, requestedLateral=self.intent.allowed_latch,
                             lateralAllowed=self.panda_allows(observed), observedMonoTime=observed)
    # As card.py: a rejection is stamped with the sample's observation time.
    rejection_ns = observed if native_latch_rejected(self.cp, native, state=cs.as_reader()) else 0
    self.intent.update(cs.as_reader(), fault_active=False, now_ns=self.now, native_rejection_ns=rejection_ns)
    return self.intent.allowed_latch

  def arm_and_cruise(self):
    # Already driving with MAIN off; MAIN on is the driver's arming gesture; then
    # cruise with panda granting.
    for _ in range(20):
      self.tick(available=False)
    assert self.tick(), "MAIN-on did not arm"
    for _ in range(100):
      assert self.tick(), "lost arming while cruising with lateral granted"


def test_grace_outlasts_the_oldest_judged_safety_sample():
  assert RESTRICTION_RELEASE_GRACE_NS > SAFETY_MAX_AGE_NS


# Up to the oldest safety sample the host still accepts (SAFETY_MAX_AGE_NS).
@pytest.mark.parametrize('wire_lag_ns', (WIRE_LAG_NS, SAFETY_MAX_AGE_NS - PANDA_CATCHUP_NS))
def test_stop_and_pull_away_keeps_aol_armed(wire_lag_ns):
  drive = Drive(wire_lag_ns)
  drive.arm_and_cruise()
  for frame in range(1300):  # a 13 s stop, as at 2581.8-2594.5 s in the route
    assert drive.tick(standstill=True), f"stopped frame {frame}"
  for frame in range(300):   # pull away: panda's stale denial, then its grant
    assert drive.tick(), f"pull-away frame {frame}"


def test_short_stop_keeps_aol_armed():
  drive = Drive()
  drive.arm_and_cruise()
  for _ in range(260):       # a 2.6 s stop, as at 319.2-321.9 s in the route
    drive.tick(standstill=True)
  assert all(drive.tick() for _ in range(300))


def test_real_revocation_after_a_stop_still_disarms():
  drive = Drive()
  drive.arm_and_cruise()
  for _ in range(200):
    drive.tick(standstill=True)
  drive.panda_revoked = True
  last_stopped = drive.now
  drive.tick(standstill=True)
  last_stopped = drive.now
  frames = 0
  while drive.tick():
    frames += 1
    assert frames < 100, "a persistent denial never disarmed"
  # Disarms on the first frame past the grace, not before.
  assert drive.now - last_stopped > RESTRICTION_RELEASE_GRACE_NS
  assert drive.now - last_stopped <= RESTRICTION_RELEASE_GRACE_NS + FRAME_NS


def test_denial_without_a_preceding_restriction_disarms_at_once():
  drive = Drive()
  drive.arm_and_cruise()
  drive.panda_revoked = True
  assert not drive.tick()
