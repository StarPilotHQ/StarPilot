"""Native Galaxy local-access dialogs shared by StarPilot profiles."""

from __future__ import annotations

import ipaddress
import http.client
import json
import re
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, replace

from openpilot.starpilot.ui.raylib import rl

from openpilot.common.qrcode import make_texture
from openpilot.common.swaglog import cloudlog
from openpilot.starpilot.galaxy.access import GalaxyAccessOwner
from openpilot.system.ui.lib.application import FontWeight, gui_app
from openpilot.system.ui.lib.wifi_manager import WifiManager
from openpilot.system.ui.widgets import Widget

GALAXY_ICON = "icons_mici/settings/galaxy.png"
REMOTE_URL = re.compile(r"https://galaxy\.firestar\.link/[A-Za-z0-9]{16}\Z")
LOCAL_PORT = 8082
LOCAL_ORIGIN = f"http://127.0.0.1:{LOCAL_PORT}"


@dataclass(frozen=True)
class RemoteStatus:
  paired: bool = False
  url: str = ""
  available: bool = False
  legacy: bool = False
  error: str = ""


def _local_api(path: str, *, cookie: str = "", payload: dict | None = None, deadline: float) -> tuple[int, dict, str]:
  def remaining():
    timeout = deadline - time.monotonic()
    if timeout <= 0:
      raise TimeoutError("Galaxy request timed out")
    return timeout

  connection = http.client.HTTPConnection("127.0.0.1", LOCAL_PORT, timeout=remaining())
  headers = {"Host": f"127.0.0.1:{LOCAL_PORT}"}
  if cookie:
    headers["Cookie"] = cookie
  if payload is not None:
    headers["Content-Type"] = "application/json"
    headers["Origin"] = LOCAL_ORIGIN
  try:
    connection.request("POST" if payload is not None else "GET", path,
                       body=json.dumps(payload) if payload is not None else None, headers=headers)
    connection.sock.settimeout(remaining())
    response = connection.getresponse()
    response.fp.raw._sock.settimeout(remaining())
    raw = response.read(16_385)
    if len(raw) > 16_384:
      raise ValueError("Galaxy response is too large")
    data = json.loads(raw) if raw else {}
    if type(data) is not dict:
      raise ValueError("Invalid Galaxy response")
    session = response.getheader("Set-Cookie", "").split(";", 1)[0]
    return response.status, data, session
  finally:
    connection.close()


def _remote_operation(action: str, password: str = "") -> RemoteStatus:
  deadline = time.monotonic() + 3
  code, auth, cookie = _local_api("/api/auth/session", deadline=deadline)
  if code != 200 or not auth.get("authenticated") or not auth.get("localAccess") or not cookie.startswith("galaxy_session="):
    return RemoteStatus(error="Local Galaxy is unavailable")
  if action in ("pair", "unpair"):
    path = "/api/galaxy/pair" if action == "pair" else "/api/galaxy/unpair"
    code, result, _ = _local_api(path, cookie=cookie, payload={"password": password} if action == "pair" else {}, deadline=deadline)
    if code != 200:
      error = result.get("error", "Galaxy pairing is unavailable")
      return RemoteStatus(error=error if type(error) is str and len(error) <= 128 else "Galaxy pairing is unavailable")
    code, auth, cookie = _local_api("/api/auth/session", deadline=deadline)
    if code != 200 or not auth.get("authenticated") or not auth.get("localAccess"):
      return RemoteStatus(error="Local Galaxy is unavailable")
  code, result, _ = _local_api("/api/galaxy/status", cookie=cookie, deadline=deadline)
  if code != 200:
    return RemoteStatus(error="Galaxy pairing status is unavailable")
  paired = result.get("paired") is True
  url = result.get("url")
  if paired and (type(url) is not str or REMOTE_URL.fullmatch(url) is None):
    return RemoteStatus(error="Galaxy pairing status is unavailable")
  return RemoteStatus(paired, url if paired and isinstance(url, str) else "", result.get("tunnelClientAvailable") is True,
                      result.get("legacyPairingAvailable") is True)


def connection_url(address: str) -> str | None:
  try:
    ip = ipaddress.IPv4Address(address)
  except ipaddress.AddressValueError:
    return None
  if ip.is_unspecified or ip.is_loopback or ip.is_multicast or ip.is_link_local or ip.is_reserved:
    return None
  return f"http://{ip}:8082/#/"


class GalaxyAccessFlow:
  def __init__(self, owner: GalaxyAccessOwner, parked: Callable[[], bool]):
    self.parked = parked
    self._wifi_manager: WifiManager | None = None
    self._url_cache: str | None = None
    self._url_next_check = 0.0
    self.remote = RemoteStatus()
    self._remote_worker: ThreadPoolExecutor | None = None
    self._remote_future: Future | None = None
    self._remote_action = "status"
    self._remote_next_check = 0.0

  @property
  def busy(self) -> bool:
    return self._remote_future is not None

  def _submit(self, action: str, password: str = "") -> bool:
    if self.busy or action != "status" and not self.parked():
      return False
    if self._remote_worker is None:
      self._remote_worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="galaxy-native")
    self._remote_action = action
    self._remote_future = self._remote_worker.submit(_remote_operation, action, password)
    self._remote_next_check = time.monotonic() + 5
    return True

  def update_remote(self) -> RemoteStatus:
    future = self._remote_future
    if future is not None and future.done():
      self._remote_future = None
      try:
        updated = future.result()
        self.remote = replace(self.remote, error=updated.error) if updated.error and self._remote_action == "status" else updated
      except (OSError, ValueError, RuntimeError, http.client.HTTPException):
        self.remote = replace(self.remote, error="Local Galaxy is unavailable") if self._remote_action == "status" else \
          RemoteStatus(error="Local Galaxy is unavailable")
    if self._remote_future is None and time.monotonic() >= self._remote_next_check:
      self._submit("status")
    return self.remote

  def pair(self, password: str) -> bool:
    password = password.strip()
    minimum = 6 if self.remote.legacy else 8
    if not minimum <= len(password) <= 255:
      self.remote = replace(self.remote, error=f"Galaxy password must be at least {minimum} characters")
      return False
    return self._submit("pair", password)

  def unpair(self) -> bool:
    return self._submit("unpair")

  def _url(self) -> str | None:
    now = time.monotonic()
    if now < self._url_next_check:
      return self._url_cache
    self._url_next_check = now + 1.0
    if self._wifi_manager is None:
      self._wifi_manager = WifiManager()
    self._url_cache = connection_url(self._wifi_manager.ipv4_address)
    return self._url_cache

  def close(self) -> None:
    if self._wifi_manager is not None:
      self._wifi_manager.stop()
      self._wifi_manager = None
    if self._remote_worker is not None:
      self._remote_worker.shutdown(wait=False, cancel_futures=True)
      self._remote_worker = None

  def __del__(self):
    self.close()

  def open_large(self) -> None:
    gui_app.push_widget(GalaxyConnectionView(self))

  def open_compact(self) -> None:
    gui_app.push_widget(GalaxyConnectionPage(self))

  def compact_button(self):
    from openpilot.selfdrive.ui.mici.widgets.button import BigButton
    flow = self

    class GalaxyButton(BigButton):
      def __init__(self):
        super().__init__("galaxy", "pair your comma", gui_app.texture(GALAXY_ICON, 64, 64))
        self.set_click_callback(flow.open_compact)

      def _update_state(self):
        super()._update_state()
        self.set_enabled(True)

    return GalaxyButton()


class GalaxyConnectionView(Widget):
  """Large-screen local address and remote pairing page."""

  def __init__(self, flow: GalaxyAccessFlow):
    from openpilot.system.ui.widgets.button import Button
    super().__init__()
    self.flow = flow
    self._url: str | None = None
    self._qr: rl.Texture | None = None
    self._remote_url = ""
    self._remote_qr: rl.Texture | None = None
    self._remote = RemoteStatus()
    self._close = self._child(Button("Close", gui_app.pop_widget, font_size=48))
    self._pair = self._child(Button("Set password & pair", self._enter_password, font_size=42))
    self._unpair = self._child(Button("Unpair remote Galaxy", self._confirm_unpair, font_size=42))

  def _enter_password(self):
    if not self.flow.parked() or self.flow.busy or self._remote.paired:
      return
    from openpilot.system.ui.widgets import DialogResult
    from openpilot.system.ui.widgets.keyboard import Keyboard
    keyboard = Keyboard(min_text_size=6 if self._remote.legacy else 8, password_mode=True, show_password_toggle=False)
    keyboard.set_title("Galaxy password", "Use your existing Galaxy password, or set a new one")

    def entered(result):
      if result == DialogResult.CONFIRM:
        self.flow.pair(keyboard.text)
      keyboard.clear()

    keyboard.set_callback(entered)
    gui_app.push_widget(keyboard)

  def _unpair_result(self, result) -> None:
    from openpilot.system.ui.widgets import DialogResult
    if result == DialogResult.CONFIRM:
      self.flow.unpair()

  def _confirm_unpair(self):
    if not self.flow.parked() or self.flow.busy or not self._remote.paired:
      return
    from openpilot.system.ui.widgets.confirm_dialog import ConfirmDialog
    gui_app.push_widget(ConfirmDialog("Disconnect remote Galaxy access?", "Unpair",
                                      callback=self._unpair_result))

  def _update_state(self):
    url = self.flow._url()
    if url != self._url:
      if self._qr is not None and self._qr.id != 0:
        rl.unload_texture(self._qr)
      self._url, self._qr = url, None
      if url:
        try:
          self._qr = make_texture(url)
        except Exception:
          cloudlog.exception("Galaxy connection QR generation failed")
    remote = self.flow.update_remote()
    self._remote = remote
    if remote.url != self._remote_url:
      if self._remote_qr is not None and self._remote_qr.id != 0:
        rl.unload_texture(self._remote_qr)
      self._remote_url, self._remote_qr = remote.url, None
      if remote.url:
        try:
          self._remote_qr = make_texture(remote.url)
        except Exception:
          cloudlog.exception("Galaxy remote QR generation failed")
    self._pair.set_enabled(lambda: self.flow.parked() and not self.flow.busy and not self._remote.paired)
    self._unpair.set_enabled(lambda: self.flow.parked() and not self.flow.busy and self._remote.paired)

  def _render(self, rect: rl.Rectangle):
    rl.draw_rectangle_rec(rect, rl.BLACK)
    font = gui_app.font(FontWeight.MEDIUM)
    left = rect.x + 140
    rl.draw_text_ex(font, "Connect to Galaxy", rl.Vector2(left, rect.y + 90), 76, 0, rl.WHITE)
    remote = self._remote
    status = remote.error or ("Paired. Remote client unavailable." if remote.paired and not remote.available else
                              "Scan the code to open your Galaxy." if remote.paired else "Choose a password, then pair your comma with Galaxy.")
    rl.draw_text_ex(font, status, rl.Vector2(left, rect.y + 220), 40, 0, rl.LIGHTGRAY)
    if remote.paired:
      rl.draw_text_ex(font, remote.url, rl.Vector2(left, rect.y + 300), 44, 0, rl.WHITE)
      if self._remote_qr is not None:
        rl.draw_texture_pro(self._remote_qr, rl.Rectangle(0, 0, self._remote_qr.width, self._remote_qr.height),
                            rl.Rectangle(rect.x + rect.width - 440, rect.y + 180, 260, 260), rl.Vector2(0, 0), 0, rl.WHITE)
      self._unpair.render(rl.Rectangle(left, rect.y + 430, 520, 110))
    else:
      self._pair.render(rl.Rectangle(left, rect.y + 345, 520, 110))
    rl.draw_text_ex(font, "Local network", rl.Vector2(left, rect.y + 605), 50, 0, rl.WHITE)
    message = ("Or open this address on the same network:" if self._url else
               "Connect to Wi-Fi to show the local address.")
    rl.draw_text_ex(font, message, rl.Vector2(left, rect.y + 680), 36, 0, rl.LIGHTGRAY)
    if self._url:
      rl.draw_text_ex(font, self._url, rl.Vector2(left, rect.y + 745), 44, 0, rl.WHITE)
      if self._qr is not None:
        rl.draw_texture_pro(self._qr, rl.Rectangle(0, 0, self._qr.width, self._qr.height),
                            rl.Rectangle(rect.x + rect.width - 420, rect.y + 610, 220, 220), rl.Vector2(0, 0), 0, rl.WHITE)
    rl.draw_text_ex(font, "No password is needed on this network.", rl.Vector2(left, rect.y + 810), 36, 0, rl.LIGHTGRAY)
    self._close.render(rl.Rectangle(rect.x + rect.width - 430, rect.y + rect.height - 165, 300, 110))

  def __del__(self):
    if self._qr is not None and self._qr.id != 0:
      rl.unload_texture(self._qr)
    if self._remote_qr is not None and self._remote_qr.id != 0:
      rl.unload_texture(self._remote_qr)


class GalaxyConnectionPage:
  """Compact local and remote connection page using native QR and dialogs."""

  def __new__(cls, flow: GalaxyAccessFlow):
    from openpilot.selfdrive.ui.mici.widgets.button import BigButton, GreyBigButton
    from openpilot.selfdrive.ui.mici.widgets.dialog import BigConfirmationDialog, BigInputDialog
    from openpilot.selfdrive.ui.mici.widgets.qr import QR
    from openpilot.system.ui.widgets.scroller import NavScroller

    class Page(NavScroller):
      def __init__(self):
        super().__init__()
        self._url: str | None = None
        self._qr = QR("", width=300)
        self._remote_url: str | None = None
        self._remote_qr = QR("", width=300)
        self._address = GreyBigButton("local Galaxy • no password", "connect to Wi-Fi")
        self._remote_address = GreyBigButton("remote Galaxy", "checking pairing")
        self._remote_state = GreyBigButton("remote connection", "")
        self._pair = BigButton("pair with Galaxy", "1. set a password", gui_app.texture(GALAXY_ICON, 64, 64))
        self._pair.set_click_callback(self._enter_password)
        self._unpair = BigButton("unpair remote Galaxy", "", gui_app.texture(GALAXY_ICON, 64, 64))
        self._unpair.set_click_callback(self._confirm_unpair)
        self._scroller.add_widgets([self._pair, self._remote_qr, self._remote_address, self._remote_state,
                                    self._unpair, self._qr, self._address])

      def _submit_password(self, password: str) -> None:
        flow.pair(password)

      def _submit_unpair(self) -> None:
        flow.unpair()

      def _enter_password(self):
        if flow.parked() and not flow.busy and not flow.remote.paired:
          gui_app.push_widget(BigInputDialog("Galaxy password", minimum_length=6 if flow.remote.legacy else 8, password_mode=True,
                                             confirm_callback=self._submit_password))

      def _confirm_unpair(self):
        if flow.parked() and not flow.busy and flow.remote.paired:
          gui_app.push_widget(BigConfirmationDialog("slide to unpair", gui_app.texture(GALAXY_ICON, 64, 64), self._submit_unpair, red=True))

      @staticmethod
      def _set_qr(widget, url: str):
        if widget._url != url:
          if widget._texture is not None and widget._texture.id != 0:
            rl.unload_texture(widget._texture)
          widget._url = url
          widget._texture = widget._generate_qr_code() if url else None
        widget.set_visible(bool(url))

      def _update_state(self):
        super()._update_state()
        url = flow._url()
        self._url = url
        self._set_qr(self._qr, url or "")
        self._address.set_value(url or "connect to Wi-Fi to show the local address")
        remote = flow.update_remote()
        self._remote_url = remote.url
        self._set_qr(self._remote_qr, remote.url)
        self._remote_address.set_value(remote.url if remote.paired else "not paired • set a password")
        warning = remote.error or ("Remote client unavailable" if remote.paired and not remote.available else "")
        self._remote_state.set_value(warning)
        self._remote_state.set_visible(bool(warning))
        self._pair.set_visible(not remote.paired)
        self._unpair.set_visible(remote.paired)
        self._pair.set_enabled(lambda: flow.parked() and not flow.busy)
        self._unpair.set_enabled(lambda: flow.parked() and not flow.busy)

      def _update_layout_rects(self):
        super()._update_layout_rects()
        # Match the native compact pairing page: the square cannot exceed the viewport height.
        self._qr.set_rect(rl.Rectangle(self._qr.rect.x, self._qr.rect.y, self._rect.height, self._rect.height))
        self._remote_qr.set_rect(rl.Rectangle(self._remote_qr.rect.x, self._remote_qr.rect.y, self._rect.height, self._rect.height))

    return Page()
