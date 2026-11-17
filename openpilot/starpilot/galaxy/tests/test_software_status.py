"""Software status from actual temporary Params and authenticated loopback HTTP."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from openpilot.common.params import Params
from openpilot.starpilot.ui.brand import DISPLAY_VERSION
from openpilot.starpilot.galaxy.access import GalaxyAccessOwner
from openpilot.starpilot.galaxy.server import make_server
from openpilot.starpilot.galaxy.software_status import SoftwareStatus, SoftwareUnavailable, MAX_FIELD_BYTES
from openpilot.starpilot.galaxy.software_operations import SoftwareOperationError


class SoftwareReaderTest(unittest.TestCase):
  def setUp(self):
    temporary = tempfile.TemporaryDirectory()
    self.addCleanup(temporary.cleanup)
    self.params = Params(temporary.name)
    self.reader = SoftwareStatus(self.params)

  def raw(self, key: str, value: bytes):
    Path(self.params.get_param_path(key)).write_bytes(value)

  def test_real_params_encodings_and_distinct_update_states(self):
    self.params.put("Version", "0.9.0", block=True)
    self.params.put("GitBranch", "Dom", block=True)
    self.params.put("GitCommit", "a" * 40, block=True)
    self.params.put("UpdaterState", "idle", block=True)
    self.params.put("UpdaterTargetBranch", "Dom", block=True)
    self.params.put("LastUpdateTime", datetime(2026, 9, 20, 12, 30), block=True)
    self.params.put("UpdaterLastCheckTime", datetime(2026, 9, 21, 9, 15), block=True)
    self.params.put("UpdaterLastFetchTime", datetime(2026, 9, 19, 10, 0), block=True)
    self.params.put_bool("UpdaterFetchAvailable", True, block=True)
    self.params.put_bool("UpdateAvailable", False, block=True)
    self.params.put("UpdateFailedCount", 2, block=True)
    result = self.reader.snapshot()
    self.assertEqual(result["installed"], {"version": "0.9.0", "displayVersion": DISPLAY_VERSION, "branch": "Dom", "commit": "a" * 40})
    self.assertEqual(result["updater"]["lastSuccessAt"], "2026-09-20T12:30:00Z")
    self.assertEqual(result["updater"]["lastCheckedAt"], "2026-09-21T09:15:00Z")
    self.assertEqual(result["updater"]["lastFetchAt"], "2026-09-19T10:00:00Z")
    self.assertTrue(result["updater"]["targetChangeFound"])
    self.assertFalse(result["updater"]["finalizedUpdateReady"])
    self.assertEqual(result["updater"]["failedCount"], 2)
    self.params.put_bool("UpdateAvailable", True, block=True)
    self.assertTrue(self.reader.snapshot()["updater"]["finalizedUpdateReady"])

  def test_os_update_requires_valid_branch_and_boolean(self):
    for value, expected in (({"branch": "Dom", "required": True}, {"branch": "Dom", "required": True}),
                            ({"branch": "Dom", "required": "yes"}, {}), ([], {}), (None, {})):
      with self.subTest(value=value):
        self.raw("UpdaterOSUpdate", json.dumps(value).encode())
        self.assertEqual(self.reader.snapshot()["updater"]["osUpdate"], expected)

  def test_absent_and_malformed_never_imply_up_to_date(self):
    result = self.reader.snapshot()
    self.assertIsNone(result["updater"]["targetChangeFound"])
    self.assertIsNone(result["updater"]["finalizedUpdateReady"])
    self.raw("UpdaterFetchAvailable", b"false")
    self.raw("UpdateAvailable", b"\xff")
    self.raw("UpdaterLastFetchTime", b"not-a-date")
    self.raw("UpdateFailedCount", b"-1")
    self.raw("UpdaterState", b"\xff")
    corrupt = self.reader.snapshot()["updater"]
    self.assertIsNone(corrupt["targetChangeFound"])
    self.assertIsNone(corrupt["finalizedUpdateReady"])
    self.assertIsNone(corrupt["lastFetchAt"])
    self.assertIsNone(corrupt["failedCount"])
    self.assertIsNone(corrupt["state"])
    self.raw("UpdateFailedCount", b"00")
    self.assertIsNone(self.reader.snapshot()["updater"]["failedCount"])

  def test_fast_progress_supports_full_unicode_detail_and_remains_bounded(self):
    detail = "\U0001f680" * 512
    self.params.put("UpdaterFastState", {"version": 1, "stage": "fetching", "detail": detail, "branch": "a" * 128}, block=True)
    self.assertEqual(self.reader.snapshot()["updater"]["fast"], {"stage": "fetching", "detail": detail})
    self.raw("UpdaterFastState", b"x" * 8193)
    with self.assertRaises(SoftwareUnavailable):
      self.reader.snapshot()

  def test_oversized_symlink_and_replacement_fail_closed(self):
    path = Path(self.params.get_param_path("Version"))
    path.write_bytes(b"x" * (MAX_FIELD_BYTES + 1))
    with self.assertRaises(SoftwareUnavailable):
      self.reader.snapshot()
    path.unlink()
    outside = path.parent.parent / "outside"
    outside.write_text("secret")
    path.symlink_to(outside)
    with self.assertRaises(SoftwareUnavailable):
      self.reader.snapshot()
    path.unlink()
    path.write_text("before")
    original = SoftwareStatus._read
    def replaced(fd, key):
      value = original(fd, key)
      if key == "Version":
        path.rename(path.parent / "old-version")
        path.write_text("after")
      return value
    with mock.patch.object(SoftwareStatus, "_read", side_effect=replaced):
      with self.assertRaises(SoftwareUnavailable):
        self.reader.snapshot()

  def test_missing_default_store_is_not_created(self):
    with tempfile.TemporaryDirectory() as empty, mock.patch.dict("os.environ", {"PARAMS_ROOT": str(Path(empty) / "absent"),
                                                                  "OPENPILOT_PREFIX": "d"}):
      with self.assertRaises(SoftwareUnavailable):
        SoftwareStatus().snapshot()
      self.assertFalse((Path(empty) / "absent").exists())

  def test_default_reader_respects_selected_params_prefix(self):
    with tempfile.TemporaryDirectory() as temporary:
      with mock.patch.dict("os.environ", {"PARAMS_ROOT": temporary, "OPENPILOT_PREFIX": "d"}):
        standard = Params(temporary)
        standard.put("Version", "standard-build", block=True)
      with mock.patch.dict("os.environ", {"PARAMS_ROOT": temporary, "OPENPILOT_PREFIX": "review"}):
        selected = Params(temporary)
        selected.put("Version", "selected-build", block=True)
        self.assertEqual(SoftwareStatus().snapshot()["installed"]["version"], "selected-build")
      with mock.patch.dict("os.environ", {"PARAMS_ROOT": temporary, "OPENPILOT_PREFIX": "d"}):
        self.assertEqual(SoftwareStatus().snapshot()["installed"]["version"], "standard-build")
      with mock.patch.dict("os.environ", {"PARAMS_ROOT": temporary, "OPENPILOT_PREFIX": "absent"}):
        with self.assertRaises(SoftwareUnavailable):
          SoftwareStatus().snapshot()
        self.assertFalse((Path(temporary) / "absent").exists())
      with mock.patch.dict("os.environ", {"PARAMS_ROOT": temporary, "OPENPILOT_PREFIX": "../d"}):
        with self.assertRaises(SoftwareUnavailable):
          SoftwareStatus()

  def test_active_params_link_cannot_leave_store(self):
    root = Path(self.params.get_param_path("Version")).parent
    with tempfile.TemporaryDirectory() as outside:
      (Path(outside) / "Version").write_text("secret")
      root.unlink()
      root.symlink_to(outside)
      with self.assertRaises(SoftwareUnavailable):
        self.reader.snapshot()

  def test_retained_sibling_namespace_is_read_without_following_escape_or_changed_link(self):
    root = Path(self.params.get_param_path("Version")).parent
    active = Path(root.readlink())
    retained = root.parent / "retained-20260924"
    self.params.put("Version", "retained-build", block=True)
    active.rename(retained)
    root.unlink()
    root.symlink_to(retained)
    self.assertEqual(self.reader.snapshot()["installed"]["version"], "retained-build")

    other = root.parent / "other-namespace"
    other.mkdir()
    (other / "Version").write_text("other-build")
    original_read = SoftwareStatus._read
    def changed(fd, key):
      result = original_read(fd, key)
      if key == "Version":
        root.unlink()
        root.symlink_to(other)
      return result
    with mock.patch.object(SoftwareStatus, "_read", side_effect=changed):
      with self.assertRaises(SoftwareUnavailable):
        self.reader.snapshot()
    root.unlink()
    root.symlink_to(root.parent.parent / "outside")
    with self.assertRaises(SoftwareUnavailable):
      self.reader.snapshot()


class SoftwareHTTPTest(unittest.TestCase):
  def setUp(self):
    temporary = tempfile.TemporaryDirectory()
    self.addCleanup(temporary.cleanup)
    root = Path(temporary.name)
    self.params = Params(str(root / "params"))
    self.params.put("Version", "fixture-build", block=True)
    self.source = SoftwareStatus(self.params)
    self.owner = GalaxyAccessOwner(root / "access")
    self.operations = self.make_operations()
    self.server = make_server(port=0, owner=self.owner, software=self.source, software_operations=self.operations)
    self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    self.thread.start()
    self.addCleanup(self.close_server)

  def make_operations(self):
    return None

  def close_server(self):
    self.server.shutdown()
    self.thread.join(timeout=2)
    self.server.server_close()

  def request(self, path, *, method="GET", body=None, headers=None):
    # Exercise the password-backed forwarded path.
    connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=2)
    try:
      connection.request(method, path, body=body, headers={'Forwarded': 'for=203.0.113.8', **(headers or {})})
      response = connection.getresponse()
      return response.status, response.read(), dict(response.getheaders())
    finally:
      connection.close()

  def login(self):
    payload = json.dumps({"password": "password123"})
    status, _, headers = self.request("/api/auth/login", method="POST", body=payload,
                                    headers={"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{self.server.server_port}"})
    self.assertEqual(status, 200)
    return headers["Set-Cookie"].split(";", 1)[0]

  def test_auth_before_io_and_get_only(self):
    with mock.patch.object(self.source, "snapshot", wraps=self.source.snapshot) as read:
      self.assertEqual(self.request("/api/software/status")[0], 503)
      self.owner.configure("password123", lambda: True)
      self.assertEqual(self.request("/api/software/status")[0], 401)
      read.assert_not_called()
    cookie = self.login()
    status, body, headers = self.request("/api/software/status", headers={"Cookie": cookie})
    self.assertEqual(status, 200)
    self.assertEqual(headers["Cache-Control"], "no-store")
    self.assertEqual(json.loads(body)["installed"]["version"], "fixture-build")
    self.assertEqual(self.request("/api/software/status", headers={"Cookie": cookie, "Host": "other.example"})[0], 403)
    origin = f"http://127.0.0.1:{self.server.server_port}"
    self.assertEqual(self.request("/api/software/status", method="POST", body="{}",
                                  headers={"Cookie": cookie, "Origin": origin, "Content-Type": "application/json"})[0], 405)

  def test_old_galaxy_gets_refresh_guidance_after_upgrade(self):
    self.owner.configure("password123", lambda: True)
    with mock.patch.object(self.source, "snapshot", wraps=self.source.snapshot) as read:
      self.assertEqual(self.request("/api/update/fast/status")[0], 401)
      read.assert_not_called()
    self.params.put('GitBranch', 'StarPilot', block=True)
    self.params.put('GitCommit', 'a' * 40, block=True)
    cookie = self.login()
    status, body, headers = self.request('/api/update/fast/status', headers={'Cookie': cookie})
    self.assertEqual(status, 200)
    self.assertEqual(headers['Cache-Control'], 'no-store')
    result = json.loads(body)
    self.assertEqual((result['branch'], result['localCommit']), ('StarPilot', 'a' * 40))
    self.assertFalse(result['running'])
    self.assertFalse(result['updateAvailable'])
    self.assertEqual(result['stage'], 'updated')
    self.assertIn('Reload this page', result['message'])
    self.assertEqual(self.request('/api/update/fast/status', method='POST', body='{}',
                                  headers={'Cookie': cookie, 'Origin': f'http://127.0.0.1:{self.server.server_port}',
                                           'Content-Type': 'application/json'})[0], 405)

  def test_logout_and_credential_change_during_read_suppress_result(self):
    self.owner.configure("password123", lambda: True)
    cookie = self.login()
    for operation in ("logout", "remove"):
      with self.subTest(operation=operation):
        entered, release = threading.Event(), threading.Event()
        original = self.source.snapshot
        def paused(entered=entered, release=release, original=original):
          entered.set()
          if not release.wait(2):
            raise TimeoutError("fixture release missing")
          return original()
        with mock.patch.object(self.source, "snapshot", side_effect=paused), ThreadPoolExecutor(max_workers=1) as pool:
          pending = pool.submit(self.request, "/api/software/status", headers={"Cookie": cookie})
          self.assertTrue(entered.wait(1))
          if operation == "logout":
            origin = f"http://127.0.0.1:{self.server.server_port}"
            self.assertEqual(self.request("/api/auth/logout", method="POST", body="{}",
                                          headers={"Cookie": cookie, "Origin": origin, "Content-Type": "application/json"})[0], 200)
          else:
            self.assertTrue(self.owner.remove(lambda: True))
          release.set()
          status, body, _ = pending.result(timeout=2)
        self.assertEqual(status, 401 if operation == "logout" else 503)
        self.assertNotIn(b"fixture-build", body)
        if operation == "logout":
          cookie = self.login()


class SoftwareOperationsHTTPTest(SoftwareHTTPTest):
  def make_operations(self):
    operations = mock.Mock()
    operations.snapshot.return_value = {"parked": True, "availableBranches": ["Dom"], "selectedTarget": "Dom", "request": None,
                                       "canCheck": True, "canSelect": True, "canDownload": False, "canInstall": False}
    operations.action.return_value = operations.snapshot.return_value
    return operations

  def post(self, payload, cookie=None, origin=None):
    headers = {"Content-Type": "application/json", "Origin": origin or f"http://127.0.0.1:{self.server.server_port}"}
    if cookie is not None:
      headers["Cookie"] = cookie
    return self.request("/api/software/action", method="POST", body=json.dumps(payload), headers=headers)

  def test_actions_require_session_and_same_origin_before_owner(self):
    self.assertEqual(self.post({"action": "check"})[0], 503)
    self.owner.configure("password123", lambda: True)
    self.assertEqual(self.post({"action": "check"})[0], 401)
    cookie = self.login()
    self.assertEqual(self.post({"action": "check"}, cookie, "http://other.example")[0], 403)
    self.operations.action.assert_not_called()
    self.assertEqual(self.post([], cookie)[0], 400)
    self.operations.action.assert_not_called()

  def test_actions_are_forwarded_once_and_status_stays_read_only(self):
    self.owner.configure("password123", lambda: True)
    cookie = self.login()
    status, body, _ = self.request("/api/software/status", headers={"Cookie": cookie})
    self.assertEqual(status, 200)
    self.assertEqual(json.loads(body)["operations"]["availableBranches"], ["Dom"])
    self.operations.action.assert_not_called()
    for action in ("check", "select", "download", "install"):
      self.operations.action.reset_mock()
      payload = {"action": action} if action == "check" else {"action": action, "branch": "Dom"}
      def accepted(operation, request, *, authorized, expected_action=action, expected_payload=payload):
        self.assertEqual((operation, request), (expected_action, expected_payload))
        self.assertTrue(authorized())
        return self.operations.snapshot.return_value
      self.operations.action.side_effect = accepted
      status, body, headers = self.post(payload, cookie)
      self.assertEqual(status, 200)
      self.assertEqual(headers["Cache-Control"], "no-store")
      self.assertEqual(json.loads(body)["installed"]["version"], "fixture-build")
      self.operations.action.assert_called_once()

  def test_rejection_and_mid_operation_revocation_are_not_success(self):
    self.owner.configure("password123", lambda: True)
    cookie = self.login()
    self.operations.action.side_effect = SoftwareOperationError("Target changed", status=409)
    self.assertEqual(self.post({"action": "install", "branch": "Dom"}, cookie)[0], 409)
    effects = []
    def revoked(operation, payload, *, authorized):
      self.assertTrue(authorized())
      self.assertTrue(self.owner.remove(lambda: True))
      if not authorized():
        raise SoftwareOperationError("Session ended", status=403)
      effects.append(operation)
      return self.operations.snapshot.return_value
    self.operations.action.side_effect = revoked
    self.assertEqual(self.post({"action": "download", "branch": "Dom"}, cookie)[0], 401)
    self.assertEqual(effects, [])
