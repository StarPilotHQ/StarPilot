"""Versioned conditional-mode proposals carried by the sole planner publisher."""

from dataclasses import dataclass
import hashlib
import re
import secrets

from openpilot.cereal import messaging
from openpilot.starpilot.conditional_mode.host import HostProposal
from openpilot.starpilot.conditional_mode.policy import ModeChoice
from openpilot.starpilot.conditional_mode.runtime_settings import (
  DocumentState,
  SafeModeState,
  SettingsSnapshot,
)


# Proposals reach selfdrived 30-70 ms after observation; 100 ms expired most before their successor arrived.
LIFETIME_NS = 150_000_000
MODEL_MAX_AGE_NS = 150_000_000
CHOICES = {ModeChoice.STOCK: 'stock', ModeChoice.CEM: 'conditionalExperimental', ModeChoice.CCM: 'conditionalChill'}
_HEX = re.compile(r'[0-9a-f]{64}\Z')


def settings_fingerprint(snapshot: SettingsSnapshot | None, *, owner=None) -> str | None:
  if owner is not None:
    from openpilot.starpilot.conditional_mode.card_settings import CardSettingsView

    if type(owner) is CardSettingsView:
      return owner.fingerprint(snapshot)
  return _settings_fingerprint(snapshot)


def _settings_fingerprint(snapshot: SettingsSnapshot | None) -> str | None:
  """Bind two independent readers to the exact saved document and SafeMode."""
  if (
    snapshot is None
    or snapshot.document_state not in (DocumentState.ABSENT, DocumentState.VALID)
    or snapshot.safe_mode_state not in (SafeModeState.ABSENT_FALSE, SafeModeState.FALSE)
  ):
    return None
  digest = hashlib.sha256(b'StarPilot conditional preferences v1\0')
  for raw in (snapshot.document_raw, snapshot.safe_mode_raw):
    # Absence differs from a present empty file and from an explicit default.
    digest.update(b'absent\0' if raw is None else len(raw).to_bytes(4, 'big') + raw)
  return digest.hexdigest()


@dataclass(frozen=True)
class ModeObservation:
  session: str
  sequence: int
  observed_ns: int
  expires_ns: int
  drive_id: int
  model_ns: int
  car_state_ns: int
  settings_revision: int
  fingerprint: str
  choice: ModeChoice
  override: bool | None
  status: str
  reason: str
  status_code: int


class StatusPublisher:
  def __init__(self):
    self.session = secrets.token_hex(16)
    self.sequence = 0

  def attach(self, event, proposal: HostProposal, snapshot: SettingsSnapshot | None, *, now_ns: int, drive_id: int, model_ns: int, car_state_ns: int):
    """Attach a proposal without granting or changing native control authority."""
    if event is None:
      event = messaging.new_message('slcState')
      event.valid = True
      event.logMonoTime = now_ns
    self.sequence += 1
    fingerprint = settings_fingerprint(snapshot)
    decision = proposal.decision
    authority = proposal.projected.authority if proposal.projected is not None else None
    permitted = (
      fingerprint is not None
      and proposal.status == 'proposed'
      and decision is not None
      and snapshot is not None
      and proposal.settings_revision == snapshot.revision
      and decision.qualified
      and authority is not None
      and authority.fresh
      and authority.system_long_capable
      and not authority.safe_mode
      and authority.driving_enabled
      and authority.long_active
      and type(proposal.override_experimental) is bool
      and proposal.override_experimental == decision.requested_experimental
      and (proposal.choice in (ModeChoice.CEM, ModeChoice.CCM) or
           proposal.choice is ModeChoice.STOCK and proposal.projected.scene.committed_stop is True and
           decision.reason.value == 'cem_stop' and proposal.override_experimental is True)
    )
    event.slcState.conditionalMode = {
      'version': 1,
      'sessionId': self.session,
      'sequence': self.sequence,
      'observedMonoTime': now_ns,
      'validUntilMonoTime': now_ns + LIFETIME_NS,
      'driveStartMonoTime': drive_id,
      'modelMonoTime': model_ns,
      'carStateMonoTime': car_state_ns,
      'settingsRevision': snapshot.revision if snapshot is not None else 0,
      'settingsFingerprint': fingerprint or '',
      'choice': CHOICES[proposal.choice],
      'hasOverride': permitted,
      'experimental': proposal.override_experimental if permitted else False,
      'status': proposal.status,
      'reason': decision.reason.value if decision is not None else '',
      'statusCode': decision.status_code if decision is not None else 0,
      'perceivedStop': proposal.projected is not None and proposal.projected.perceived_stop is True,
    }
    return event


def observation(state, now_ns: int) -> ModeObservation | None:
  """Nested freshness is independent of the outer SLC availability flag."""
  try:
    value = state.conditionalMode
    stamp, expiry = int(value.observedMonoTime), int(value.validUntilMonoTime)
    model, car_state, drive = int(value.modelMonoTime), int(value.carStateMonoTime), int(value.driveStartMonoTime)
    session, fingerprint = str(value.sessionId), str(value.settingsFingerprint)
    choice = {wire: choice for choice, wire in CHOICES.items()}[str(value.choice)]
    status, reason = str(value.status), str(value.reason)
    if (
      type(now_ns) is not int
      or value.version != 1
      or not re.fullmatch(r'[0-9a-f]{32}', session)
      or value.sequence <= 0
      or not 0 < stamp <= now_ns <= expiry
      or not 0 < expiry - stamp <= LIFETIME_NS
      or not 0 < drive <= model <= stamp
      or not drive <= car_state <= stamp
      or stamp - model > MODEL_MAX_AGE_NS
      or stamp - car_state > LIFETIME_NS
      or value.settingsRevision <= 0
      or not _HEX.fullmatch(fingerprint)
      or len(status) > 64
      or len(reason) > 64
      or value.statusCode > 255
      or value.hasOverride
      and (status != 'proposed' or choice is ModeChoice.STOCK and
           (reason != 'cem_stop' or value.statusCode != 8 or not value.experimental))
    ):
      return None
    return ModeObservation(
      session,
      int(value.sequence),
      stamp,
      expiry,
      drive,
      model,
      car_state,
      int(value.settingsRevision),
      fingerprint,
      choice,
      bool(value.experimental) if value.hasOverride else None,
      status,
      reason,
      int(value.statusCode),
    )
  except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
    return None
