"""One Pair phone button beside the Bluetooth controls: the StarPilot app and phone music pair in one window."""
import base64
import datetime
import json
import threading
import time

from openpilot.starpilot.galaxy_bluetooth.bootstrap import retry_setup, setup_status
from openpilot.starpilot.galaxy_bluetooth.bridge import claim
from openpilot.starpilot.galaxy_bluetooth.bridge.pairing import app_only, remove_by_address, remove_phone

ACTION = '/api/bluetooth/action'
STATUS = '/api/bluetooth/status'
READY = 'Ready to pair'

_status_read_at = -1e9
_status_text = ''


def cached_setup_status(ttl=2.):
  # Widgets poll every frame; the status file changes at most every few seconds.
  global _status_read_at, _status_text
  now = time.monotonic()
  if now - _status_read_at >= ttl:
    _status_read_at, _status_text = now, setup_status()
  return _status_text


def permitted(allowed):
  try:
    return bool(allowed())
  except Exception:
    return False


def forget_phone_keys(allowed, address):
  """Forgetting a Bluetooth device in the list also forgets the app key paired with it."""
  if permitted(allowed):
    try:
      remove_by_address(address)
    except (OSError, ValueError):
      pass


def app_pairing_title(phone, phones):
  """The phone's name; the added date only tells apart phones that share a name."""
  label = phone['label']
  if sum(1 for other in phones if other['label'] == label) > 1:
    try:
      added = datetime.datetime.fromisoformat(phone['added'])
      return f'{label} ({added:%b} {added.day})'
    except (KeyError, ValueError):
      pass
  return label


def app_pairing_subtitle(phone):
  try:
    used = datetime.date.fromisoformat(phone['last_used'])
  except (KeyError, ValueError):
    return 'not used yet'
  return f'last used {used:%b} {used.day}'


def forget_app_phone(allowed, phone_id):
  if not permitted(allowed):
    return
  try:
    remove_phone(phone_id)
  except (OSError, ValueError):
    pass


def is_app_link(device):
  """A phone that is connected to comma's Bluetooth bridge for the StarPilot app but never bonded for music or audio."""
  return bool(device.get('connected')) and not device.get('paired') and not device.get('trusted')


def forget_app_only(allowed):
  if not permitted(allowed):
    return
  try:
    for phone in app_only():
      remove_phone(phone['id'])
  except (OSError, ValueError):
    pass


def app_only_count():
  try:
    return len(app_only())
  except (OSError, ValueError):
    return 0


def galaxy_call():
  """A request function that talks to the local Galaxy server the way the phone bridge does."""
  from openpilot.starpilot.galaxy_bluetooth.bridge.proxy import GalaxyProxy
  proxy = GalaxyProxy()

  def call(method, path, payload=None):
    request = {'id': 'comma-ui', 'path': path, 'method': method, 'headers': {}}
    if payload is not None:
      request['body'] = base64.b64encode(json.dumps(payload).encode()).decode()
    response = proxy.handle(request)
    try:
      data = json.loads(base64.b64decode(response.get('body') or ''))
    except ValueError:
      data = {}
    return response.get('status', 0), data if isinstance(data, dict) else {}
  return call


class PhoneWindow:
  """Opens the pairing window through Galaxy and follows it. No drawing; run from a worker thread.

  Galaxy is used (not the Bluetooth owner directly) so Android Auto and the Spotify music profile are handled
  the same as for a window opened from the Galaxy web page.
  """
  def __init__(self, call, claim_status=None, clock=time.monotonic):
    self.call = call
    self.claim_status = claim_status or (lambda: claim.status(claim.CLAIM_PATH))
    self.clock = clock
    self.state = 'opening'
    self.adapter = ''
    self.app = ''
    self.music = ''
    self.code = ''
    self.deadline = 0.

  def open(self):
    try:
      status, data = self.call('POST', ACTION, {'operation': 'accept_pair'})
    except Exception:
      status, data = 0, {}
    if status == 200:
      self.state = 'waiting'
      self.adapter = str(data.get('adapterName') or '')[:80]
      self.deadline = self.clock() + claim.WINDOW_SECONDS
    else:
      self.state = 'busy' if status == 409 else 'unavailable'
    return self.state

  def poll(self):
    if self.state != 'waiting':
      return self.state
    try:
      status, data = self.call('GET', STATUS)
    except Exception:
      status, data = 0, {}
    if status == 200:
      self.adapter = str(data.get('adapterName') or self.adapter)[:80]
      pairing = data.get('pairing') if isinstance(data.get('pairing'), dict) else {}
      prompt = pairing.get('prompt') if isinstance(pairing.get('prompt'), dict) else {}
      self.code = str(prompt.get('value') or '') if prompt.get('kind') == 'confirmation' and prompt.get('displayOnly') else ''
      if pairing.get('incoming') and pairing.get('state') == 'paired':
        self.music = str(pairing.get('name') or 'Phone')[:40]
    self.app = str(self.claim_status().get('claimed') or '')
    if self.app and self.music:
      self.state = 'done'
    elif self.clock() >= self.deadline:
      self.state = 'closed'
    return self.state

  def cancel(self):
    if self.state == 'waiting':
      try:
        self.call('POST', ACTION, {'operation': 'cancel_pair'})
      except Exception:
        pass
      self.state = 'closed'

  def view(self):
    return {'state': self.state, 'adapter': self.adapter, 'app': self.app, 'music': self.music, 'code': self.code,
            'left': max(0, int(self.deadline - self.clock())) if self.state == 'waiting' else 0}


def message_lines(view, bridge=READY):
  """The text the pairing screen shows for one state of the window."""
  state, adapter = view['state'], view['adapter'] or 'this comma'
  if state == 'opening':
    return ['Opening pairing…']
  if state == 'busy':
    return ['Bluetooth is busy.', 'Park, stop any Android Auto pairing, then try again.']
  if state == 'unavailable':
    return ['Pairing is not available.', 'Check that Bluetooth is on, then try again.']
  waiting = state == 'waiting'
  if waiting:
    lines = ['Open the StarPilot app and tap Pair.', f'No app? Tap {adapter} in your phone’s Bluetooth settings.']
  elif state == 'done':
    lines = [f'All set. {view["app"]} is paired.']
  else:
    lines = ['Pairing closed.']
  lines += [f'App    {view["app"] or ("waiting" if waiting else "not paired")}',
            f'Music  {view["music"] or ("waiting" if waiting else "not paired")}']
  if view['code']:
    lines.append(f'If your phone shows {view["code"]}, tap Pair.')
  if waiting:
    lines.append(f'{view["left"] // 60}:{view["left"] % 60:02d} left')
  if bridge != READY and state in ('waiting', 'closed'):
    lines.append(f'Phone app link: {bridge}')
  return lines


def PairPhone(allowed):
  from openpilot.starpilot.ui.raylib import rl
  from openpilot.common.hardware import HARDWARE
  from openpilot.selfdrive.ui.ui_state import device
  from openpilot.system.ui.lib.text_measure import measure_text_cached
  from openpilot.system.ui.lib.application import gui_app, FontWeight
  from openpilot.system.ui.widgets import Widget
  from openpilot.system.ui.widgets.button import Button

  class Screen(Widget):
    def __init__(self):
      super().__init__()
      self.window = PhoneWindow(galaxy_call())
      self.lock = threading.Lock()
      self.view = self.window.view()
      self.stop = threading.Event()
      self.c4 = HARDWARE.get_device_type() == 'mici'
      self.close_button = self._child(Button('Close', self._close, font_size=24 if self.c4 else 42))
      threading.Thread(target=self._run, daemon=True).start()

    def _close(self):
      self.stop.set()
      gui_app.pop_widget()

    def _run(self):
      try:
        if self.window.open() == 'waiting':
          while not self.stop.wait(1.):
            if not permitted(allowed) or self.window.poll() != 'waiting':
              break
            with self.lock:
              self.view = self.window.view()
      finally:
        self.window.cancel()
        with self.lock:
          self.view = self.window.view()

    def hide_event(self):
      self.stop.set()
      super().hide_event()

    def _render(self, rect):
      rl.draw_rectangle_rec(rect, rl.BLACK)
      font = gui_app.font(FontWeight.MEDIUM)
      compact = rect.height < 600
      with self.lock:
        view = dict(self.view)
      if view['state'] in ('opening', 'waiting'):
        device._reset_interactive_timeout()  # keep the screen on while the window is open
      if self.c4:
        margin = 16
        width = rect.width - margin * 2

        def text(line, x, y, size=22, color=rl.LIGHTGRAY, max_width=width):
          label = line
          while label and measure_text_cached(font, label, size).x > max_width:
            line = line[:-1]
            label = line + '…'
          rl.draw_text_ex(font, label, rl.Vector2(x, y), size, 0, color)

        x = rect.x + margin
        text('Pair your phone', x, rect.y + 10, 26, rl.WHITE)
        state = view['state']
        if state == 'waiting':
          lines = ['App: tap Pair in StarPilot.', 'Music: open phone Bluetooth settings.', view['adapter'] or 'Select this comma.']
          if view['code']:
            lines[2] = f'Confirm {view["code"]} on your phone.'
          bridge = cached_setup_status()
          if bridge != READY:
            lines[2] = f'App link: {bridge}'
        else:
          lines = message_lines(view)[:2]
        for i, line in enumerate(lines):
          text(line, x, rect.y + 49 + i * 29)
        half = width / 2
        for i, (label, value) in enumerate((('App', view['app']), ('Music', view['music']))):
          status = 'paired' if value else ('waiting' if state == 'waiting' else 'not paired')
          text(f'{label}: {status}', x + i * half, rect.y + 141, 22,
               rl.GREEN if value else rl.LIGHTGRAY, half - 8)
        if state == 'waiting':
          text(f'{view["left"] // 60}:{view["left"] % 60:02d} left', x, rect.y + rect.height - 46)
        self.close_button.render(rl.Rectangle(rect.x + rect.width - margin - 160,
                                             rect.y + rect.height - 62, 160, 48))
        return
      rl.draw_text_ex(font, 'Pair your phone', rl.Vector2(rect.x + 28, rect.y + 16), 26 if compact else 48, 0, rl.WHITE)
      size, step = (22, 30) if compact else (34, 48)
      y = rect.y + (60 if compact else 100)
      for line in message_lines(view, cached_setup_status()):
        rl.draw_text_ex(font, line, rl.Vector2(rect.x + 28, y), size, 0, rl.LIGHTGRAY)
        y += step
      self.close_button.render(rl.Rectangle(rect.x + rect.width - (150 if compact else 300),
                                           rect.y + rect.height - (66 if compact else 120),
                                           130 if compact else 260, 54 if compact else 90))

  return Screen()


def open_pairing(allowed):
  if not permitted(allowed):
    return
  from openpilot.system.ui.lib.application import gui_app
  if cached_setup_status() not in (READY, 'Bridge startup pending'):
    try:
      retry_setup(restart=False)
    except OSError:
      pass
  gui_app.push_widget(PairPhone(allowed))


def large_phone_rows(allowed):
  from openpilot.system.ui.lib.application import gui_app
  from openpilot.system.ui.widgets import DialogResult
  from openpilot.system.ui.widgets.confirm_dialog import ConfirmDialog
  from openpilot.system.ui.widgets.list_view import button_item

  rows = [button_item('Pair phone', 'Pair', description='Pairs the StarPilot app and phone music in one step',
                      callback=lambda: open_pairing(allowed), enabled=lambda: permitted(allowed))]
  if app_only_count():
    rows.append(button_item('Forget app pairings', 'Forget',
                            description=f'{app_only_count()} saved for the StarPilot app only (no Bluetooth music pairing)',
                            callback=lambda: gui_app.push_widget(ConfirmDialog(
                              'Forget phones saved for the StarPilot app only? They must pair again.', 'Forget',
                              callback=lambda result: forget_app_only(allowed) if result == DialogResult.CONFIRM else None)),
                            enabled=lambda: permitted(allowed)))
  return rows
