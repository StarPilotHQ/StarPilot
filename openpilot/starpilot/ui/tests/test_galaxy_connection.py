"""Local Galaxy stays passwordless, including when credential storage is unavailable."""

import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from openpilot.starpilot.galaxy.access import AccessStatus, GalaxyAccessOwner
from openpilot.starpilot.galaxy.remote import RemotePairing
from openpilot.starpilot.galaxy.server import make_server
from openpilot.starpilot.ui.raylib import rl

from openpilot.starpilot.ui import galaxy_access
from openpilot.starpilot.ui.galaxy_access import GalaxyAccessFlow, GalaxyConnectionPage, GalaxyConnectionView, RemoteStatus, connection_url
from openpilot.system.ui.widgets import Widget


class TestGalaxyConnection(unittest.TestCase):
  def test_native_client_uses_local_authenticated_pairing_routes(self):
    with tempfile.TemporaryDirectory() as directory:
      root = Path(directory)
      pairing = RemotePairing(root / "pairing")
      parked = [True]
      server = make_server(port=0, owner=GalaxyAccessOwner(root / "credentials"),
                           remote_pairing=pairing, parked=lambda: parked[0])
      worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
      worker.start()
      try:
        with patch.object(galaxy_access, "LOCAL_PORT", server.server_port), \
             patch.object(galaxy_access, "LOCAL_ORIGIN", f"http://127.0.0.1:{server.server_port}"):
          self.assertFalse(galaxy_access._remote_operation("status").paired)
          paired = galaxy_access._remote_operation("pair", "  password123  ")
          self.assertTrue(paired.paired)
          self.assertTrue(galaxy_access.REMOTE_URL.fullmatch(paired.url))
          record = pairing.read()
          assert record is not None
          self.assertEqual(record["slug"], paired.url.rsplit("/", 1)[1])
          self.assertTrue(galaxy_access._remote_operation("status").paired)
          parked[0] = False
          self.assertIn("Turn off the vehicle", galaxy_access._remote_operation("unpair").error)
          self.assertIsNotNone(pairing.read())
          parked[0] = True
          self.assertFalse(galaxy_access._remote_operation("unpair").paired)
          self.assertIsNone(pairing.read())
      finally:
        server.shutdown()
        worker.join(timeout=2)
        server.server_close()

  def test_only_numeric_lan_ipv4_becomes_a_browser_url(self):
    self.assertEqual(connection_url("192.168.1.111"), "http://192.168.1.111:8082/#/")
    self.assertEqual(connection_url("10.0.0.1"), "http://10.0.0.1:8082/#/")
    for address in ("", "localhost", "127.0.0.1", "0.0.0.0", "169.254.1.1", "224.0.0.1", "::1",
                    "192.168.1.111/?password=secret"):
      with self.subTest(address=address):
        self.assertIsNone(connection_url(address))

  def test_native_wifi_address_updates_without_credentials(self):
    with tempfile.TemporaryDirectory() as directory:
      flow = GalaxyAccessFlow(GalaxyAccessOwner(Path(directory)), lambda: True)
      wifi = SimpleNamespace(ipv4_address="192.168.1.111", stop=Mock())
      with patch("openpilot.starpilot.ui.galaxy_access.WifiManager", return_value=wifi) as provider, \
           patch("openpilot.starpilot.ui.galaxy_access.time.monotonic", side_effect=[1.0, 1.5, 2.1]):
        self.assertEqual(flow._url(), "http://192.168.1.111:8082/#/")
        wifi.ipv4_address = "192.168.1.112"
        self.assertEqual(flow._url(), "http://192.168.1.111:8082/#/")
        self.assertEqual(flow._url(), "http://192.168.1.112:8082/#/")
        provider.assert_called_once_with()

  def test_opening_connection_does_not_remove_configured_access(self):
    with tempfile.TemporaryDirectory() as directory:
      owner = GalaxyAccessOwner(Path(directory))
      self.assertTrue(owner.configure("secure-password", lambda: True))
      flow = GalaxyAccessFlow(owner, lambda: True)
      with patch("openpilot.starpilot.ui.galaxy_access.GalaxyConnectionView", return_value="connection page"), \
           patch("openpilot.starpilot.ui.galaxy_access.gui_app.push_widget") as pushed:
        flow.open_large()
      pushed.assert_called_once_with("connection page")
      self.assertEqual(owner.status().status, AccessStatus.CONFIGURED_LOCAL)

  def test_both_pages_show_local_access_even_when_driving_without_reading_credentials(self):
    owner = Mock()
    owner.status.side_effect = RuntimeError("credential storage unavailable")
    parked = Mock(return_value=True)
    flow = GalaxyAccessFlow(owner, parked)
    for method, page in ((flow.open_large, "GalaxyConnectionView"), (flow.open_compact, "GalaxyConnectionPage")):
      with self.subTest(page=page), patch(f"openpilot.starpilot.ui.galaxy_access.{page}", return_value="connection page"), \
           patch("openpilot.starpilot.ui.galaxy_access.gui_app.push_widget") as pushed:
        parked.return_value = True
        method()
        pushed.assert_called_once_with("connection page")
        pushed.reset_mock()
        parked.return_value = False
        method()
        pushed.assert_called_once_with("connection page")
    owner.assert_not_called()
    self.assertEqual(owner.mock_calls, [])

  def test_compact_button_available_without_credential_storage(self):
    class Button(Widget):
      def __init__(self, text, value, icon):
        super().__init__()
        self.text, self.value = text, value

      def _render(self, rect):
        pass

    owner = Mock()
    owner.status.side_effect = RuntimeError("credential storage unavailable")
    parked = Mock(return_value=True)
    flow = GalaxyAccessFlow(owner, parked)
    with patch("openpilot.selfdrive.ui.mici.widgets.button.BigButton", Button), \
         patch("openpilot.starpilot.ui.galaxy_access.gui_app.texture"):
      button = flow.compact_button()
      button._update_state()
      self.assertTrue(button.enabled)
      self.assertEqual(button.value, "pair your comma")
      parked.return_value = False
      self.assertTrue(button.enabled)
    self.assertEqual(owner.mock_calls, [])

  def test_pairing_requires_parked_and_single_flight(self):
    parked = Mock(return_value=False)
    flow = GalaxyAccessFlow(Mock(), parked)
    self.assertFalse(flow.pair("password123"))
    self.assertFalse(flow.unpair())
    parked.return_value = True
    self.assertFalse(flow.pair("1234567"))
    self.assertIn("at least 8", flow.remote.error)
    with patch.object(flow, "_remote_worker", Mock()) as worker:
      self.assertTrue(flow.pair("password123"))
      self.assertFalse(flow.unpair())
      worker.submit.assert_called_once()
    flow.close()

  def test_status_error_keeps_last_pairing_metadata(self):
    flow = GalaxyAccessFlow(Mock(), lambda: True)
    url = "https://galaxy.firestar.link/Abcdef1234567890"
    flow.remote = RemoteStatus(True, url, True)
    from concurrent.futures import Future
    pending = Future()
    pending.set_result(RemoteStatus(error="Local Galaxy is unavailable"))
    flow._remote_future = pending
    flow._remote_next_check = float("inf")
    self.assertEqual(flow.update_remote(), RemoteStatus(True, url, True, error="Local Galaxy is unavailable"))

  def test_large_page_shows_passwordless_address_and_qr_without_credential_storage(self):
    class Button(Widget):
      def __init__(self, text, callback, **kwargs):
        super().__init__()
        self.text = text

      def _render(self, rect):
        pass

    owner = Mock()
    owner.status.side_effect = RuntimeError("credential storage unavailable")
    flow = GalaxyAccessFlow(owner, lambda: True)
    url = "http://192.168.1.111:8082/#/"
    with patch("openpilot.system.ui.widgets.button.Button", Button), \
         patch.object(flow, "_url", return_value=url), \
         patch.object(flow, "update_remote", return_value=RemoteStatus()), \
         patch("openpilot.starpilot.ui.galaxy_access.make_texture", return_value=None) as qr, \
         patch("openpilot.starpilot.ui.galaxy_access.gui_app.font"), \
         patch.object(rl, "draw_rectangle_rec"), patch.object(rl, "draw_text_ex") as draw:
      view = GalaxyConnectionView(flow)
      view._update_state()
      view._render(rl.Rectangle(0, 0, 2160, 1080))
      qr.assert_called_once_with(url)
      texts = [call.args[1] for call in draw.call_args_list]
      self.assertIn(url, texts)
      self.assertIn("No password is needed on this network.", texts)
      self.assertLess(texts.index("Choose a password, then pair your comma with Galaxy."), texts.index("Local network"))
      self.assertEqual((view._close.text, view._pair.text, view._unpair.text),
                       ("Close", "Set password & pair", "Unpair remote Galaxy"))
    self.assertEqual(owner.mock_calls, [])

  def test_compact_page_has_local_and_remote_views_without_credential_access(self):
    class Scroller:
      def __init__(self):
        self._scroller = Mock()

      def _update_state(self):
        pass

    owner = Mock()
    owner.status.side_effect = RuntimeError("credential storage unavailable")
    flow = GalaxyAccessFlow(owner, lambda: True)
    url = "http://192.168.1.111:8082/#/"
    qr_widgets = [Mock(_texture=None, _url=""), Mock(_texture=None, _url="")]
    with patch("openpilot.system.ui.widgets.scroller.NavScroller", Scroller), \
         patch("openpilot.selfdrive.ui.mici.widgets.qr.QR", side_effect=qr_widgets), \
         patch("openpilot.selfdrive.ui.mici.widgets.button.GreyBigButton") as button, \
         patch("openpilot.selfdrive.ui.mici.widgets.button.BigButton"), \
         patch("openpilot.selfdrive.ui.mici.widgets.dialog.BigInputDialog", return_value="masked prompt") as prompt, \
         patch("openpilot.starpilot.ui.galaxy_access.gui_app.texture"), \
         patch.object(flow, "_url", return_value=url), \
         patch.object(flow, "update_remote", return_value=RemoteStatus()):
      page = GalaxyConnectionPage(flow)
      page._update_state()
      self.assertEqual(qr_widgets[0]._url, url)
      qr_widgets[0]._generate_qr_code.assert_called_once_with()
      qr_widgets[0].set_visible.assert_called_once_with(True)
      qr_widgets[1].set_visible.assert_called_once_with(False)
      self.assertEqual(button.call_args_list[0].args, ("local Galaxy • no password", "connect to Wi-Fi"))
      self.assertEqual([call.args for call in button.call_args_list], [
        ("local Galaxy • no password", "connect to Wi-Fi"),
        ("remote Galaxy", "checking pairing"),
        ("remote connection", "")])
      self.assertEqual(page._scroller.add_widgets.call_args.args[0],
                       [page._pair, page._remote_qr, page._remote_address, page._remote_state, page._unpair, page._qr, page._address])
      with patch("openpilot.starpilot.ui.galaxy_access.gui_app.push_widget") as pushed:
        page._enter_password()
        self.assertIs(prompt.call_args.kwargs["password_mode"], True)
        self.assertEqual(prompt.call_args.kwargs["minimum_length"], 8)
        pushed.assert_called_once_with("masked prompt")
    self.assertEqual(owner.mock_calls, [])
