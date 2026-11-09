import errno

from openpilot.starpilot.system.starport import bootstrap as bs
from openpilot.starpilot.system.starport.supervisor import _tcp_failure_kind


def test_wifi_status_labels_name_the_known_codes():
  assert bs.describe_status(0) == "success"
  assert bs.describe_status(-1) == "status -1 (no compatible version)"
  assert bs.describe_status(-11) == "status -11 (network unavailable)"
  assert bs.describe_status(-3) == "status -3 (incorrect credentials)"
  assert bs.describe_status(-99) == "status -99"
  assert bs.describe_status(2**64 - 1) == "status -1 (no compatible version)"


def test_tcp_failure_kinds():
  assert _tcp_failure_kind(TimeoutError()) == "timeout"
  assert _tcp_failure_kind(ConnectionRefusedError()) == "refused"
  assert _tcp_failure_kind(OSError(errno.EHOSTUNREACH, "no route")) == "unreachable"
  assert _tcp_failure_kind(OSError(errno.EPERM, "denied")) == "other"


def test_phone_ping_experiment_gets_a_dongle_that_waits_for_the_phone_to_ping():
  from openpilot.starpilot.system.starport.tests.test_starport import run_bootstrap
  result, joined, seen, _ = run_bootstrap(version_first=True, experiment="phone_ping", require_phone_ping=True)
  assert seen["phone_ping_first"] and joined and result.endpoint.port == 5288


def test_default_bootstrap_sends_no_pings_of_its_own():
  from openpilot.starpilot.system.starport.tests.test_starport import run_bootstrap
  _, _, seen, _ = run_bootstrap(version_first=True)
  assert bs.WIFI_PING_REQUEST not in seen["messages"]


def test_unofficial_dongle_profile_and_last_rung_do_not_ping():
  assert "phone_ping" not in bs.EXPERIMENTS["all"][1]


def test_phone_ping_needs_every_switch():
  from openpilot.starpilot.system.starport import head_unit_fixes as hf
  on = {"head_unit_fixes": True, "head_unit_experiments": True, "head_unit_experiment_rules": ["phone_ping"]}
  assert hf.bootstrap_experiment(on) == "phone_ping"
  assert hf.bootstrap_experiment({**on, "head_unit_fixes": False}) == ""
  assert hf.bootstrap_experiment({**on, "head_unit_experiments": False}) == ""
  assert hf.bootstrap_experiment({**on, "head_unit_experiment_rules": []}) == ""
  assert hf.bootstrap_experiment({"head_unit_fixes": True}) == ""
