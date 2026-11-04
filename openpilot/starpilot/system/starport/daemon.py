"""starportd: local control socket for direct StarPort projection.

Runs only when Android Auto and Bluetooth are enabled. Projection starts when the user presses
Start, or on its own when auto-connect is on and the chosen car is on (see auto_connect.py);
until then nothing but a Bluetooth status check and the car's hands-free gateway runs. Commands are one JSON line per
connection on ``STARPORT_SOCKET_PATH``, mirroring bluetooth_managerd.

  python -m openpilot.starpilot.system.starport.daemon            # service
  python -m openpilot.starpilot.system.starport.daemon --once     # foreground diagnostic run
  python -m openpilot.starpilot.system.starport.daemon --once --synthetic   # test pattern, no UI needed
"""

from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
import os
import signal
import socketserver
import sys
import threading
import time
from typing import Any

from openpilot.starpilot.system.starport.protocol import STARPORT_SOCKET_PATH
from openpilot.starpilot.system.starport.source_verifier import StarPortSourceVerifier
from openpilot.starpilot.system.starport.supervisor import Supervisor

PAIRING_COMMANDS = {"prepare_pairing", "pairing_status", "pairing_response", "cancel_pairing", "pair_device"}
AUTHENTICATED_COMMANDS = {"start", "set_auto_connect", "select_receiver", "set_view", "set_connection", "set_car_type", "set_car_profile",
                          "prepare_pairing", "pairing_status", "pairing_response", "cancel_pairing", "pair_device", "devices", "bluetooth_action",
                          "phone_window", "bluetooth_power"}


def _offroad() -> bool:
  try:
    from openpilot.common.params import Params
    return Params().get_bool("IsOffroad")
  except Exception:
    return False


def _enabled() -> bool:
  try:
    from openpilot.common.params import Params
    from openpilot.starpilot.system.starport.settings import enabled
    return enabled(Params())
  except Exception:
    return False


def handle(supervisor: Supervisor, request: dict[str, Any], verifier: StarPortSourceVerifier | None = None, *, parked=None) -> dict[str, Any]:
  command = str(request.get("command", ""))
  if command not in {"status", "stop", "forget_receiver"}:  # forgetting a car must work with Android Auto off
    from openpilot.common.params import Params
    from openpilot.starpilot.system.starport.settings import enabled
    if not enabled(Params()):
      raise RuntimeError("Enable StarPort in Tools first")
  if command in AUTHENTICATED_COMMANDS:
    source = request.get('source')
    if not isinstance(source, str) or verifier is None or not verifier.valid(source):
      raise RuntimeError('Authenticated Galaxy StarPort source required')
    if command not in ('bluetooth_action', 'phone_window', 'bluetooth_power'):
      supervisor.bind_source_session(('galaxy', source))
  if command in PAIRING_COMMANDS and not (parked or _offroad)():
    raise RuntimeError("Disengage and shift into Park before pairing")
  if command == 'bluetooth_action':
    from openpilot.starpilot.bluetooth.owner import BluetoothRejected, BluetoothUnavailable
    if set(request) != {'command', 'source', 'operation', 'address'}:
      raise ValueError('Invalid companion Bluetooth request')
    if not (parked or _offroad)():
      return {'handled': True, 'error_code': 'park_required'}
    try:
      result = supervisor.companion_bluetooth_action(request['operation'], request['address'], ('galaxy', request['source']))
    except (BluetoothRejected, BluetoothUnavailable) as error:
      return {'handled': True, 'error_code': error.code, 'message': str(error)[:160]}
    except (OSError, RuntimeError):
      return {'handled': True, 'error_code': 'service_unavailable'}
    return {'handled': result is not None, **({'bluetooth': result} if result is not None else {})}
  if command == 'bluetooth_power':
    from openpilot.starpilot.bluetooth.owner import BluetoothRejected, BluetoothUnavailable
    if set(request) != {'command', 'source', 'enabled'} or type(request['enabled']) is not bool:
      raise ValueError('Invalid companion Bluetooth power request')
    if not (parked or _offroad)():
      return {'handled': True, 'error_code': 'park_required'}
    try:
      result = supervisor.companion_power(request['enabled'], ('galaxy', request['source']))
    except (BluetoothRejected, BluetoothUnavailable) as error:
      return {'handled': True, 'error_code': error.code, 'message': str(error)[:160]}
    except (OSError, RuntimeError):
      return {'handled': True, 'error_code': 'service_unavailable'}
    return {'handled': result is not None, **({'bluetooth': result} if result is not None else {})}
  if command == 'phone_window':
    from openpilot.starpilot.bluetooth.owner import BluetoothRejected, BluetoothUnavailable
    operation = request.get('operation')
    fields = {'command', 'source', 'operation'}
    fields |= {'prompt_id', 'accepted', 'value'} if operation == 'pairing_response' else {'address'} if operation == 'pair' else set()
    if set(request) != fields or operation not in ('accept_pair', 'pair', 'scan', 'stop_scan', 'pairing_response', 'cancel_pair', 'status'):
      raise ValueError('Invalid phone pairing request')
    if operation != 'status' and not (parked or _offroad)():
      return {'handled': True, 'error_code': 'park_required'}
    answer = {key: request[key] for key in ('prompt_id', 'accepted', 'value', 'address') if key in request}
    try:
      result = supervisor.phone_window(operation, ('galaxy', request['source']), **answer)
    except (BluetoothRejected, BluetoothUnavailable) as error:
      return {'handled': True, 'error_code': error.code, 'message': str(error)[:160]}
    except (OSError, RuntimeError):
      return {'handled': True, 'error_code': 'service_unavailable'}
    return {'handled': result is not None, **({'bluetooth': result} if result is not None else {})}
  if command == "status":
    return {"status": supervisor.status()}
  if command == "start":
    supervisor.user_start()
  elif command == "stop":
    supervisor.user_stop()
  elif command == "set_auto_connect":
    supervisor.set_auto_connect(bool(request.get("enabled", True)))
  elif command == "set_dedicated_wifi":
    supervisor.set_dedicated_wifi(request.get("enabled") is True)
  elif command == "set_allow_hevc":
    supervisor.set_allow_hevc(request.get("enabled") is True)
  elif command == "select_receiver":
    supervisor.select_receiver(str(request.get("address", "")), str(request.get("name", "")))
  elif command == "forget_receiver":
    return {"cleared": supervisor.forget_receiver(str(request.get("address", "")))}
  elif command == "set_view":
    supervisor.set_view(str(request.get("view", "")))
  elif command == "set_connection":
    supervisor.set_connection(str(request.get("connection", "")))
  elif command == "set_car_type":
    supervisor.set_car_type(str(request.get("car_type", "")))
  elif command == "set_car_profile":
    supervisor.set_car_profile(str(request.get("profile", "")))
  elif command == "prepare_pairing":
    supervisor.prepare_pairing()
  elif command == "pairing_status":
    return {"pairing": supervisor.pairing_status()}
  elif command == "pair_device":
    address = request.get("address")
    if not isinstance(address, str):
      raise ValueError("Invalid Bluetooth address")
    supervisor.pair_device(address)
  elif command == "pairing_response":
    prompt, accepted = request.get('prompt_id'), request.get('accepted')
    if not isinstance(prompt, str) or type(accepted) is not bool:
      raise ValueError('Invalid pairing response')
    supervisor.pairing_response(prompt, accepted, request.get('value', ''))
  elif command == "cancel_pairing":
    supervisor.cancel_pairing()
  elif command == "devices":
    return {"devices": supervisor.devices()}
  else:
    raise RuntimeError(f"Unknown Android Auto command: {command}")
  return {}


class RequestHandler(socketserver.StreamRequestHandler):
  def handle(self) -> None:
    try:
      request = json.loads(self.rfile.readline(64 * 1024))
      response = {"ok": True, **handle(self.server.supervisor, request, self.server.source_verifier, parked=self.server.parked)}
    except Exception as error:
      response = {"ok": False, "error": str(error)}
    self.wfile.write(json.dumps(response, separators=(",", ":"), default=str).encode() + b"\n")


class Server(socketserver.ThreadingUnixStreamServer):
  daemon_threads = True

  def __init__(self, path: str, supervisor: Supervisor, source_verifier: StarPortSourceVerifier, parked=_offroad):
    self.supervisor = supervisor
    self.source_verifier = source_verifier
    self.parked = parked
    super().__init__(path, RequestHandler)


def run_once(duration: float, synthetic: bool) -> int:
  """Foreground diagnostic: start, print state changes, stop after ``duration`` seconds or Ctrl-C."""
  supervisor = Supervisor(synthetic=synthetic)
  stop = threading.Event()
  signal.signal(signal.SIGINT, lambda *_: stop.set())
  signal.signal(signal.SIGTERM, lambda *_: stop.set())
  supervisor.start()
  deadline = time.monotonic() + duration
  last = None
  try:
    while not stop.is_set() and time.monotonic() < deadline:
      status = supervisor.status()
      summary = (status["state"], status["detail"], status["error"])
      if summary != last:
        print(json.dumps({key: status[key] for key in ("state", "label", "detail", "error", "mode", "head_unit")}, default=str), flush=True)
        last = summary
      if status["state"] == "streaming" and int(time.monotonic()) % 5 == 0:
        print(json.dumps({"stats": status["stats"]}), flush=True)
      stop.wait(1.0)
  finally:
    supervisor.close()
  return 0 if supervisor.status()["error"] == "" else 1


def main() -> int:
  parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
  parser.add_argument("--once", action="store_true", help="run one foreground session for diagnostics")
  parser.add_argument("--duration", type=float, default=900.0)
  parser.add_argument("--synthetic", action="store_true", help="with --once: send a moving test pattern instead of the UI")
  args = parser.parse_args()
  if args.once:
    return run_once(args.duration, args.synthetic)

  # Core 6 next to the renderer, before any thread starts so all inherit it
  # (see placement.py); re-pinned by housekeeping after power saving.
  from openpilot.common.hardware import COMMA_HARDWARE
  placement = None
  if COMMA_HARDWARE:
    os.setpriority(os.PRIO_PROCESS, 0, 5)
    from openpilot.starpilot.system.starport.placement import RendererPlacement
    placement = RendererPlacement(idle=False, report=lambda event: print(json.dumps(event), flush=True))
    placement.start()

  try:
    os.unlink(STARPORT_SOCKET_PATH)
  except FileNotFoundError:
    pass
  from openpilot.starpilot.system.starport.bluetooth_bridge import SharedBluetoothOwner
  source_verifier = StarPortSourceVerifier()
  from openpilot.common.params import Params
  from openpilot.starpilot.galaxy.evidence import EvidenceSource
  from openpilot.starpilot.galaxy.settings import LiveContextSource
  with ExitStack() as cleanup:
    evidence = EvidenceSource().start()
    cleanup.callback(evidence.close)
    authority = LiveContextSource(Params(), messages=evidence, evidence_wait_ms=0)
    cleanup.callback(authority.close)
    bluetooth_owner = SharedBluetoothOwner(authority.configuration_allowed,
      session_valid=lambda session: len(session) == 2 and session[0] == 'galaxy' and source_verifier.valid(session[1]))
    cleanup.callback(bluetooth_owner.close)
    bluetooth_owner.recover_phone_role()
    supervisor = Supervisor(shared_bluetooth_owner=bluetooth_owner, projection_enabled=_enabled)
    supervisor.recover_usb()
    cleanup.callback(supervisor.close)
    exit_event = threading.Event()
    cleanup.callback(exit_event.set)

    def housekeeping():
      while not exit_event.wait(2.0):
        try:
          supervisor.maintain()
        except Exception:
          pass
        if placement is not None:
          try:
            placement.maintain(time.monotonic())
          except Exception:
            pass

    threading.Thread(target=housekeeping, daemon=True).start()
    from openpilot.starpilot.system.starport.bugsink_report import Reporter
    cleanup.callback(Reporter(parked=lambda: _offroad() and not supervisor.status()["running"]).start().close)
    server = Server(STARPORT_SOCKET_PATH, supervisor, source_verifier, authority.configuration_allowed)
    cleanup.callback(server.server_close)

    def shutdown(*_):
      exit_event.set()
      threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    try:
      os.chmod(STARPORT_SOCKET_PATH, 0o660)
      server.serve_forever(poll_interval=0.5)
    finally:
      exit_event.set()
      try:
        os.unlink(STARPORT_SOCKET_PATH)
      except FileNotFoundError:
        pass
  return 0


if __name__ == "__main__":
  sys.exit(main())
