import asyncio
from unittest.mock import Mock


from openpilot.cereal import messaging
from openpilot.system.webrtc.webrtcd import StreamSession
from teleoprtc.builder import WebRTCAnswerBuilder, WebRTCOfferBuilder


def test_raw_session_only_publishes_requested_services():
  session = object.__new__(StreamSession)
  session.raw = True
  session.incoming_bridge_services = ['testJoystick']
  session.shared_pub_master = Mock()
  session.logger = Mock()
  accepted = messaging.new_message('testJoystick').to_bytes()
  rejected = messaging.new_message('carState').to_bytes()
  session.message_handler(rejected)
  session.shared_pub_master.send.assert_not_called()
  session.message_handler(accepted)
  session.shared_pub_master.send.assert_called_once_with('testJoystick', accepted)
  session.message_handler(b'invalid capnp')
  session.shared_pub_master.send.assert_called_once_with('testJoystick', accepted)
  session.logger.exception.assert_called_once()


def test_raw_loopback_preserves_large_capnp_message(monkeypatch):
  async def check():
    import teleoprtc.stream as rtc

    # The production constructor supplies ICE servers; keep this test entirely on loopback.
    class LocalPeerConnection:
      GatheringState = rtc.PeerConnection.GatheringState
      State = rtc.PeerConnection.State

      def __new__(cls, config):
        config.ice_servers = []
        config.bind_address = '127.0.0.1'
        return original_peer(config)

    original_peer = rtc.PeerConnection
    monkeypatch.setattr(rtc, 'PeerConnection', LocalPeerConnection)
    answer = None
    received = asyncio.Queue()

    async def connect(offer):
      nonlocal answer
      answer = WebRTCAnswerBuilder(offer.sdp).stream()
      answer.set_message_handler(received.put_nowait)
      return await answer.start()

    builder = WebRTCOfferBuilder(connect)
    builder.add_messaging()
    offer = builder.stream()
    try:
      await asyncio.wait_for(offer.start(), 15)
      await asyncio.wait_for(offer.wait_for_connection(), 15)
      assert answer is not None
      await asyncio.wait_for(answer.wait_for_connection(), 15)
      payload = messaging.new_message(None, aolSafetyWire=bytes(128 * 1024)).to_bytes()
      channel = offer.get_messaging_channel()
      assert channel.max_message_size() >= len(payload)
      channel.send(payload)
      assert await asyncio.wait_for(received.get(), 10) == payload
    finally:
      await offer.stop()
      if answer is not None:
        await answer.stop()

  asyncio.run(check())
