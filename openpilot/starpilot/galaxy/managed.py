"""Manager-supervised local Galaxy service across ignition changes."""

import os
import signal
import threading
import time

from openpilot.starpilot.galaxy.mdns import run as run_mdns
from openpilot.starpilot.galaxy.server import make_server
from openpilot.starpilot.galaxy.server import make_remote_server
from openpilot.starpilot.galaxy.remote import default_remote_pairing, make_gateway_auth_server
from openpilot.starpilot.galaxy.remote_service import supervise_tunnel


def serve_managed(server, *, extra_servers=(), tunnel=None) -> None:
  # The manual server retains its normal threading policy. A managed child has
  # a five-second stop grace, so a slow request cannot keep the process alive.
  server.daemon_threads = True
  server.block_on_close = False
  stop = threading.Event()
  errors: list[BaseException] = []

  def request_stop(signum, frame):
    stop.set()

  def serve():
    try:
      server.serve_forever(poll_interval=0.1)
    except BaseException as exc:
      errors.append(exc)
      stop.set()

  def serve_extra(extra):
    try:
      extra.serve_forever(poll_interval=0.1)
      if not stop.is_set():
        errors.append(RuntimeError('Galaxy auxiliary listener stopped'))
        stop.set()
    except BaseException as exc:
      errors.append(exc)
      stop.set()

  def serve_tunnel():
    try:
      assert tunnel is not None
      tunnel(stop)
      if not stop.is_set():
        errors.append(RuntimeError('Galaxy tunnel supervisor stopped'))
        stop.set()
    except BaseException as exc:
      errors.append(exc)
      stop.set()

  old_handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
  for sig in old_handlers:
    signal.signal(sig, request_stop)

  worker = threading.Thread(target=serve, name='galaxy-http', daemon=True)
  extras = [threading.Thread(target=serve_extra, args=(extra,), daemon=True)
            for extra in extra_servers]
  tunnel_worker = threading.Thread(target=serve_tunnel, daemon=True) if tunnel is not None else None
  started = False
  try:
    for extra, thread in zip(extra_servers, extras, strict=True):
      extra.daemon_threads = True
      thread.start()
    worker.start()
    started = True
    if tunnel_worker is not None:
      tunnel_worker.start()
    while worker.is_alive() and not stop.wait(0.1):
      pass
  finally:
    stop.set()
    drained = False
    try:
      if tunnel_worker is not None and tunnel_worker.is_alive():
        tunnel_worker.join(timeout=6)
      for extra, thread in zip(extra_servers, extras, strict=True):
        if thread.is_alive():
          extra.shutdown()
          thread.join(timeout=0.5)
      if started:
        server.shutdown()
        worker.join(timeout=0.5)
      # Both web listeners use the same owners. One deadline bounds their
      # accepted requests before any shared reader can be closed.
      deadline = time.monotonic() + 4.0
      drained = not started or server.drain_requests(max(0.0, deadline - time.monotonic()))
      for extra in extra_servers:
        drain = getattr(extra, 'drain_requests', None)
        if callable(drain):
          extra_drained = drain(max(0.0, deadline - time.monotonic()))
          drained = drained and extra_drained
    finally:
      try:
        for extra in extra_servers:
          extra.server_close()
      finally:
        try:
          server.server_close(close_sources=drained)
        finally:
          for sig, handler in old_handlers.items():
            signal.signal(sig, handler)

  if errors:
    raise RuntimeError('Galaxy HTTP service stopped unexpectedly') from errors[0]


def main() -> None:
  if os.getenv('STARPILOT_GALAXY_DISABLE') == '1':
    raise RuntimeError('Managed Galaxy disabled by STARPILOT_GALAXY_DISABLE=1')
  server = make_server(port=8082, host='0.0.0.0')
  try:
    server.start_drive_history()
    pairing = default_remote_pairing()
    remote_server = make_remote_server(server)
    try:
      from openpilot.common.params import Params
      params = Params()
      from openpilot.starpilot.connect.provider import galaxy_device_id
      auth_server = make_gateway_auth_server(pairing, lambda: galaxy_device_id(params))
    except BaseException:
      remote_server.server_close()
      raise
  except BaseException:
    server.server_close()
    raise
  print(f'Galaxy local/LAN service listening on port {server.server_port}', flush=True)
  mdns_stop = threading.Event()
  mdns = threading.Thread(target=run_mdns, args=(mdns_stop, server.server_port), name='galaxy-mdns', daemon=True)
  mdns.start()
  try:
    serve_managed(server, extra_servers=(remote_server, auth_server), tunnel=lambda stop: supervise_tunnel(stop, pairing))
  finally:
    mdns_stop.set()
    mdns.join(timeout=3)


if __name__ == '__main__':
  main()
