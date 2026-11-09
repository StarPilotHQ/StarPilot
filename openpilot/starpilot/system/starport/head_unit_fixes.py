"""Car-specific fixes: rules matched on one exact head-unit build, applied through small reusable actions.

A rule names the build it was seen on, the failure it targets and an ordered ladder of actions. A trial runs one more action
per level (see quirk_state), so the smallest change that helps is the one that stays. Rules are either
``experimental`` (they run only when the owner switches them on) or ``validated`` (a tested sequence, applied automatically
to the matching build). The global "Car-specific fixes" switch turns every rule off, whatever has been learned.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from openpilot.starpilot.system.starport.quirk_state import QuirkState

EXPERIMENTAL, VALIDATED = "experimental", "validated"

# Actions a rule's ladder may use. Each opens or sends one thing, and nothing else.
OPEN_BLUETOOTH = "open_bluetooth"          # open the car's Bluetooth channel, without a pairing request
OPEN_PHONE_STATUS = "open_phone_status"    # open the car's phone-status channel
OPEN_SENSORS = "open_sensors"              # open the car-data channel (driving status and night only)
OPEN_VENDOR = "open_vendor"                # open the car's vendor channel; nothing is sent on it
REQUEST_BT_PAIRING = "request_bt_pairing"  # send the Bluetooth pairing request with this comma's own address
ACTIONS = (OPEN_BLUETOOTH, OPEN_PHONE_STATUS, OPEN_SENSORS, OPEN_VENDOR, REQUEST_BT_PAIRING)

# ServiceDiscoveryResponse field numbers (JSON turns them into strings).
SDR_LABELS = {"2": "car_make", "3": "car_model", "4": "car_year", "7": "head_unit_make", "8": "head_unit_model",
              "9": "head_unit_software_build", "10": "head_unit_software_version"}
MATCH_FIELDS = ("car_make", "car_model", "car_year", "head_unit_make", "head_unit_model", "head_unit_software_version")

# Automatic experiments (an experimental rule that starts without being switched on) are not enabled in this release.
AUTO_EXPERIMENTS = False
WIRELESS, WIRED = "wireless", "wired"


@dataclass(frozen=True)
class Evidence:
  failure: str                    # what the car does
  sessions: tuple[str, ...]       # where it was seen
  early_shutdown_s: float | None  # the known-bad baseline: seconds of video before the car ends the session


@dataclass(frozen=True)
class Rule:
  id: str
  label: str                      # shown next to the toggle
  match: dict[str, str]           # every MATCH_FIELDS entry, compared as a whole string
  transports: tuple[str, ...]
  status: str
  evidence: Evidence
  ladder: tuple[str, ...]         # one action per level, cumulative
  validated_level: int = 0        # for a validated rule: how many ladder steps the tested sequence uses
  summary: str = ""               # one sentence for the setup suggestion
  setting_path: str = "Settings → Developer → StarPort → Specific car experiments"


RULES: tuple[Rule, ...] = (
  Rule(id="gm_mychevrolet_w28e", label="2021 Chevrolet myChevrolet (W28E build)",
       match={"car_make": "Chevrolet", "car_model": "myChevrolet", "car_year": "2021", "head_unit_make": "Chevrolet",
              "head_unit_model": "myChevrolet", "head_unit_software_version": "W28E-V155.3.8-MIHS21A-107"},
       transports=(WIRELESS, WIRED), status=EXPERIMENTAL,
       evidence=Evidence("The car ends every session 10-25 s after video starts (shutdown reason 1), wired and wireless.",
                         ("2026-10-12 sessions 5-8", "2026-11-08 sessions 1, 2, 4"), 25.0),
       ladder=(OPEN_BLUETOOTH, OPEN_PHONE_STATUS, OPEN_SENSORS, OPEN_VENDOR, REQUEST_BT_PAIRING),
       summary="This car's screen keeps ending after about 25 seconds."),
)


@dataclass(frozen=True)
class BootstrapFix:
  """A Wi-Fi setup tweak for whichever receiver is chosen, run only when its experimental toggle is on."""
  id: str
  label: str
  experiment: str                 # the bootstrap experiment it switches on
  evidence: Evidence
  status: str = EXPERIMENTAL


BOOTSTRAP_FIXES: tuple[BootstrapFix, ...] = (
  BootstrapFix(id="phone_ping", label="Dongle Wi-Fi setup: ping like a phone", experiment="phone_ping",
               evidence=Evidence("A Mavlou dongle never sends its Wi-Fi start request to the comma. A phone pings it once a second after its version reply.",
                                 ("2026-11-03 phone capture", "2026-11-07 sessions 5, 19, 24"), None)),
)


def rule_ids() -> tuple[str, ...]:
  return tuple(rule.id for rule in RULES)


def experiment_ids() -> tuple[str, ...]:
  return rule_ids() + tuple(fix.id for fix in BOOTSTRAP_FIXES)


def experimental_entries() -> list[dict]:
  """Every experimental fix the settings page lists: the car rules, then the device tweaks."""
  return ([{"id": rule.id, "label": rule.label, "evidence": rule.evidence.failure} for rule in RULES if rule.status == EXPERIMENTAL] +
          [{"id": fix.id, "label": fix.label, "evidence": fix.evidence.failure} for fix in BOOTSTRAP_FIXES if fix.status == EXPERIMENTAL])


def bootstrap_experiment(config: dict) -> str:
  """The Wi-Fi setup experiment to add, or "". Needs the master switch, the experiments switch and its own toggle."""
  if not (config.get("head_unit_fixes", True) and config.get("head_unit_experiments")):
    return ""
  chosen = config.get("head_unit_experiment_rules") or []
  return next((fix.experiment for fix in BOOTSTRAP_FIXES if fix.id in chosen), "")


def validate_rules(rules: tuple[Rule, ...] = RULES) -> list[str]:
  """Problems that would let a rule match more than the one build it was written for."""
  problems = []
  seen: set[str] = set()
  for rule in rules:
    if rule.id in seen:
      problems.append(f"{rule.id}: duplicate id")
    seen.add(rule.id)
    if set(rule.match) != set(MATCH_FIELDS):
      problems.append(f"{rule.id}: match must name exactly {MATCH_FIELDS}")
    for key, value in rule.match.items():
      if not value.strip() or any(char in value for char in "*?[]|^$\\"):
        problems.append(f"{rule.id}: {key} must be a plain, non-empty string")
    if not rule.evidence.failure or not rule.evidence.sessions:
      problems.append(f"{rule.id}: evidence is required")
    if not rule.transports or not set(rule.transports) <= {WIRELESS, WIRED}:
      problems.append(f"{rule.id}: transports")
    if not rule.ladder or not set(rule.ladder) <= set(ACTIONS) or len(set(rule.ladder)) != len(rule.ladder):
      problems.append(f"{rule.id}: ladder must use known actions once each")
    if rule.status == VALIDATED and not 1 <= rule.validated_level <= len(rule.ladder):
      problems.append(f"{rule.id}: validated rules need a validated_level")
    if rule.status not in (EXPERIMENTAL, VALIDATED):
      problems.append(f"{rule.id}: status")
  return problems


def head_unit_labels(described: dict[str, Any]) -> dict[str, str]:
  """The identifying text of a discovery response, by label, from its numbered fields (keys are numbers or their JSON strings)."""
  labels = {}
  for number, label in SDR_LABELS.items():
    values = described.get(number, described.get(int(number)))
    if isinstance(values, list) and values and isinstance(values[0], str) and values[0] and values[0] != "redacted":
      labels[label] = values[0][:80]
  return labels


def matches(rule: Rule, labels: dict[str, str]) -> bool:
  return all(labels.get(key, "").casefold() == value.casefold() for key, value in rule.match.items())


def near_miss(rule: Rule, labels: dict[str, str]) -> bool:
  """The same car and head-unit model on a build the rule wasn't written for: nothing applies, but it is worth a log line."""
  return (not matches(rule, labels) and all(labels.get(key, "").casefold() == rule.match[key].casefold()
                                            for key in ("car_make", "car_model", "head_unit_make", "head_unit_model")))


@dataclass(frozen=True)
class Selection:
  rule: Rule
  level: int
  actions: tuple[str, ...]
  generation: int


def _enabled(config: dict, rule: Rule) -> bool:
  if not config.get("head_unit_fixes", True):
    return False
  if rule.status == VALIDATED:
    return True
  if AUTO_EXPERIMENTS:
    return True
  return bool(config.get("head_unit_experiments")) and rule.id in (config.get("head_unit_experiment_rules") or [])


def eligible(config: dict, car_type: str, profile: str) -> bool:
  """A rule never applies behind an adapter, or on top of a car profile the owner or the fingerprint picked."""
  return config.get("head_unit_fixes", True) and car_type != "adapter" and profile == "default"


def select(config: dict, labels: dict[str, str], transport: str, quirks: QuirkState, key: str, *, car_type: str,
           profile: str) -> Selection | None:
  """The fix to run for this session, or None. Starts a trial the first time a rule applies to a car."""
  if not eligible(config, car_type, profile):
    return None
  for rule in RULES:
    if not matches(rule, labels) or transport not in rule.transports or not _enabled(config, rule):
      continue
    if not key:
      return None
    trial = quirks.trial(key, rule.id) or quirks.begin(key, labels, rule.id)
    if trial is None or trial["state"] == "off":
      return None
    level = min(trial["level"], rule.validated_level) if rule.status == VALIDATED else trial["level"]
    if not 1 <= level <= len(rule.ladder):
      return None
    return Selection(rule, level, rule.ladder[:level], trial["generation"])
  return None


def suggestion(config: dict, labels: dict[str, str], transport: str, quirks: QuirkState, key: str, *, car_type: str,
               profile: str) -> dict | None:
  """Advice for the setup screen: a fix made for this exact build exists and this car has shown the failure. Advice only."""
  if not eligible(config, car_type, profile) or not key:
    return None
  for rule in RULES:
    if rule.status != EXPERIMENTAL or not matches(rule, labels) or transport not in rule.transports:
      continue
    if _enabled(config, rule) or quirks.dismissed(key, rule.id):
      continue
    trial = quirks.trial(key, rule.id)
    if trial is not None and trial["state"] == "off":
      continue
    if quirks.early_shutdowns(key) < 2:
      continue
    return {"rule": rule.id, "title": rule.summary, "label": rule.label, "path": rule.setting_path,
            "detail": f"{rule.summary} A setting made for this exact car might help: {rule.setting_path} → {rule.label}. It's experimental."}
  return None
