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
