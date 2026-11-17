import gc
import uuid

import msgq


def test_native_binding_keeps_all_64_reader_slots():
  name = f'nb_readers_{uuid.uuid4().hex}'
  publisher = msgq.pub_sock(name)
  readers = [msgq.sub_sock(name, timeout=1000) for _ in range(64)]
  publisher.wait_for_readers()
  publisher.send(b'all registered readers receive the same message')
  assert [reader.receive() for reader in readers] == [b'all registered readers receive the same message'] * 64


def test_poller_retains_registered_socket_and_returns_its_identity():
  name = f'nb_poller_{uuid.uuid4().hex}'
  publisher = msgq.pub_sock(name)
  reader = msgq.sub_sock(name, timeout=1000)
  poller = msgq.Poller()
  poller.registerSocket(reader)
  identity = id(reader)
  del reader
  gc.collect()
  assert poller.poll(0) == []
  publisher.send(b'live')
  ready = poller.poll(1000)
  assert len(ready) == 1 and id(ready[0]) == identity
  assert ready[0].receive() == b'live'
  assert poller.poll(0) == []
