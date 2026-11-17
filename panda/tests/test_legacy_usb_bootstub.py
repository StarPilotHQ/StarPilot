"""Actual Panda USB construction preserves supported pre-7.0 bootstub identities."""
import struct
from unittest.mock import Mock

import pytest
from panda import Panda, McuType

LEGACY_RESPONSE = b"\xff\x00\xc1\x3e\xde\xad\xd0\x0d" + bytes(56)
SERIAL = "010002000300040005000600"


@pytest.fixture(params=["linux", "darwin"])
def usb_device(monkeypatch, request):
  monkeypatch.setattr("panda.sys.platform", request.param)
  device = Mock()
  device.getVendorID.return_value = 0x3801
  device.getProductID.return_value = 0xddee
  device.getSerialNumber.return_value = SERIAL
  device.getbcdDevice.return_value = 0x0700
  handle = device.open.return_value
  response = {"hardware": LEGACY_RESPONSE}

  def control_read(_kind, request, _value, _index, _length, _timeout):
    if request == 0xc1:
      return response["hardware"]
    if request == 0xdd:
      return struct.pack("<II", Panda.HEALTH_PACKET_VERSION, Panda.CAN_PACKET_VERSION)
    return b""

  handle.controlRead.side_effect = control_read
  context = Mock()
  context.getDeviceList.return_value = [device]
  monkeypatch.setattr("panda.usb1.USBContext", Mock(return_value=context))
  return device, response


@pytest.mark.parametrize("hardware,mcu,internal", [(6, McuType.F4, True), (7, McuType.H7, False),
                                                 (9, McuType.H7, True), (10, McuType.H7, True)])
def test_actual_usb_bootstub_construction_selects_exact_supported_mcu(usb_device, hardware, mcu, internal):
  device, _ = usb_device
  device.getbcdDevice.return_value = hardware << 8
  with Panda(SERIAL, disable_checks=False) as panda:
    assert panda.bootstub
    assert panda.is_connected_usb()
    assert panda.get_type() == bytes([hardware])
    assert panda.get_mcu_type() == mcu
    assert panda.is_internal() is internal
    assert panda.get_mcu_type().config.app_fn == ("panda.bin.signed" if hardware == 6 else "panda_h7.bin.signed")


@pytest.mark.parametrize("bcd", [None, True, -1, 0x10000, 0x2300, 0, 0xff00, 0x0800, 0x0300])
def test_unrecognized_descriptors_never_guess_firmware(usb_device, bcd):
  device, _ = usb_device
  device.getbcdDevice.return_value = bcd
  with Panda(SERIAL, disable_checks=False) as panda:
    assert panda.get_type() == LEGACY_RESPONSE
    with pytest.raises(ValueError, match="Unknown HW"):
      panda.get_mcu_type()


@pytest.mark.parametrize("reported", [b"\x07", b"\x06", b"\x00", b"", None, b"malformed"])
def test_modern_response_cannot_be_overridden_by_descriptor(usb_device, reported):
  _, response = usb_device
  response["hardware"] = reported
  with Panda(SERIAL, disable_checks=False) as panda:
    assert panda.get_type() == reported


def test_application_mode_does_not_use_bootstub_descriptor(usb_device):
  device, _ = usb_device
  device.getProductID.return_value = 0xddcc
  with Panda(SERIAL, disable_checks=False) as panda:
    assert not panda.bootstub
    assert panda.get_type() == LEGACY_RESPONSE
    with pytest.raises(ValueError, match="Unknown HW"):
      panda.get_mcu_type()
  device.getbcdDevice.assert_not_called()


def test_reconnect_discards_bootstub_identity(usb_device):
  device, response = usb_device
  with Panda(SERIAL, disable_checks=False) as panda:
    assert panda.get_type() == b"\x07"
    device.getProductID.return_value = 0xddcc
    response["hardware"] = b"\x06"
    panda.connect()
    assert not panda.bootstub
    assert panda.get_type() == b"\x06"
    assert panda.get_mcu_type() is McuType.F4
    assert panda._handle.bootstub_hw_type is None
