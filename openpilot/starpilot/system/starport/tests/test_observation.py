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
