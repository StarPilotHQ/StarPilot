import socket
import ssl
import struct
import threading
import time

import pytest

from openpilot.starpilot.system.starport.session import Session, AuthenticationRejected
from openpilot.starpilot.system.starport.tests.fake_head_unit import FakeHeadUnit, make_identity
from openpilot.starpilot.system.starport.wire import field


@pytest.fixture(scope='module')
def identity(tmp_path_factory):
  return make_identity(tmp_path_factory.mktemp('motorola-identity'))


class EarlyPingHeadUnit(FakeHeadUnit):
  def _session(self):
    self._send(0, 0x0b, field(1, 123456789), encrypted=False)
    header = self._read_exact(4)
    payload = self._read_exact(struct.unpack('>BBH', header)[2])
    self.raw_pong = header + payload
    assert self.raw_pong == struct.pack('>BBH', 0, 3, 2 + len(field(1, 123456789))) + b'\x00\x0c' + field(1, 123456789)
    super()._session()


def connect(hu, identity, **kwargs):
  sock = socket.create_connection(('127.0.0.1', hu.port), timeout=2)
  return Session(sock, str(identity['phone_cert']), str(identity['phone_key']), ca=str(identity['root']), **kwargs)


@pytest.mark.parametrize('early_ping', [False, True])
def test_real_tls_auth_still_required_and_succeeds(identity, early_ping):
  hu = (EarlyPingHeadUnit if early_ping else FakeHeadUnit)(identity)
  session = connect(hu, identity)
  try:
    session.authenticate()
    assert session.authenticated and 'Honda' in session.head_unit_subject
    assert hu.version_reply == (1, 5, 0)
    if early_ping:
      assert hu.raw_pong[4:6] == b'\x00\x0c'
  finally:
    session.peer.close()
    hu.close()


def test_early_ping_does_not_bypass_rejected_auth(identity):
  hu = EarlyPingHeadUnit(identity, reject_auth=True)
  session = connect(hu, identity)
  try:
    with pytest.raises(AuthenticationRejected):
      session.authenticate()
    assert not session.authenticated
  finally:
    session.peer.close()
    hu.close()


def test_early_ping_does_not_bypass_untrusted_head_unit(identity, tmp_path):
  foreign = make_identity(tmp_path)
  hu = EarlyPingHeadUnit(foreign, require_client_cert=False)
  session = connect(hu, identity)
  try:
    with pytest.raises(ssl.SSLError):
      session.authenticate()
    assert not session.authenticated
  finally:
    session.peer.close()
    hu.close()


def frame(channel, kind, body=b''):
  payload = struct.pack('>H', kind) + body
  return struct.pack('>BBH', channel, 3, len(payload)) + payload


def raw_session(identity, **kwargs):
  phone, hu = socket.socketpair()
  session = Session(phone, str(identity['phone_cert']), str(identity['phone_key']), ca=str(identity['root']), **kwargs)
  return session, hu


@pytest.mark.parametrize('payload', [b'\x08\x80', field(1, b'123'), field(1, 1) + field(1, 2), b'\x00'])
def test_malformed_early_ping_rejected(identity, payload):
  session, hu = raw_session(identity)
  try:
    hu.sendall(frame(0, 0x0b, payload))
    with pytest.raises(ValueError):
      session.authenticate()
    assert not session.authenticated
  finally:
    session.peer.close()
    hu.close()


@pytest.mark.parametrize('channel,kind,body', [(1, 0x0b, field(1, 1)), (0, 3, b'x'), (0, 1, b'\x00\x01')])
def test_other_pre_version_messages_still_rejected(identity, channel, kind, body):
  session, hu = raw_session(identity)
  try:
    hu.sendall(frame(channel, kind, body))
    with pytest.raises(ValueError, match='Expected version request'):
      session.authenticate()
  finally:
    session.peer.close()
    hu.close()


def test_ping_without_version_never_authenticates(identity):
  session, hu = raw_session(identity, handshake_timeout=.08)
  try:
    hu.sendall(frame(0, 0x0b, field(1, 9)))
    start = time.monotonic()
    with pytest.raises(TimeoutError):
      session.authenticate()
    assert time.monotonic() - start < .4
    assert not session.authenticated
  finally:
    session.peer.close()
    hu.close()


def test_ping_flood_is_count_bounded(identity):
  session, hu = raw_session(identity)
  try:
    hu.sendall(b''.join(frame(0, 0x0b, field(1, i)) for i in range(9)))
    with pytest.raises(ValueError, match='Excessive pre-version'):
      session.authenticate()
    assert not session.authenticated
  finally:
    session.peer.close()
    hu.close()


def test_repeated_pings_do_not_reset_version_deadline(identity):
  session, hu = raw_session(identity, handshake_timeout=.08)
  stop = threading.Event()
  def keepalive():
    for i in range(8):
      if stop.wait(.02):
        return
      try:
        hu.sendall(frame(0, 0x0b, field(1, i)))
      except OSError:
        return
  worker = threading.Thread(target=keepalive, daemon=True)
  worker.start()
  try:
    start = time.monotonic()
    with pytest.raises(TimeoutError):
      session.authenticate()
    assert time.monotonic() - start < .14
    assert not session.authenticated
  finally:
    stop.set()
    worker.join(1)
    session.peer.close()
    hu.close()


def test_relaying_adapter_may_ping_past_the_handshake_timeout(identity):
  events = []
  session, hu = raw_session(identity, handshake_timeout=.5, version_wait=5.0, log=lambda name, **v: events.append(name))
  def relay():
    for i in range(12):
      hu.sendall(frame(0, 0x0b, field(1, i)))
      time.sleep(.2)
    time.sleep(.2)
    hu.sendall(frame(0, 0x01, b'\x00\x01\x00\x07'))
  worker = threading.Thread(target=relay, daemon=True)
  worker.start()
  try:
    with pytest.raises((TimeoutError, OSError)):
      session.authenticate()
    assert events.count('pre_version_ping') == 12 and 'version' in events
    assert not session.authenticated
  finally:
    worker.join(2)
    session.peer.close()
    hu.close()
