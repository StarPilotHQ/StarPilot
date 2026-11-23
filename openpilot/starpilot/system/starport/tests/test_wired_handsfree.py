from unittest.mock import Mock

from openpilot.starpilot.system.starport import supervisor as supervisor_module
from openpilot.starpilot.system.starport.supervisor import Supervisor

CAR = "F8:36:9B:0A:7D:C8"


def fixture(monkeypatch, *, paired=True, links=True):
  monkeypatch.setattr(supervisor_module, "WIRED_HFP_WAIT", 0.2)
  monkeypatch.setattr(supervisor_module, "WIRED_HFP_SETTLE", 0.0)
  unit = Supervisor.__new__(Supervisor)
  unit.config = {"receiver_address": "", "companion_address": "", "connection": "wired"}
  unit._wired_car = ""
  unit._pairing_until = 0.0
  unit._shared_bluetooth_owner = None
  unit._hfp_link = supervisor_module.threading.Event()
  unit.log = Mock()
  phone = Mock()
  phone.snapshot.return_value = True, {"paired": paired, "connected": False}
  phone.devices.return_value = [{"address": "AA:BB:CC:DD:EE:01", "paired": True}]
  phone.connect_handsfree.side_effect = lambda address: unit._hfp_link.set() if links else None
  unit._phone = Mock(return_value=phone)
  unit._adapter_address = Mock(return_value="39:90:21:64:07:00")
  unit._release_phone = Mock()
  return unit, phone


def test_linked_car_returns_this_comma_address(monkeypatch):
  unit, phone = fixture(monkeypatch)
  assert unit._wired_handsfree(CAR.lower()) == "39:90:21:64:07:00"
  phone.acquire.assert_called_once()
  phone.connect_handsfree.assert_called_once_with(CAR)
  assert unit._selected_receiver() == CAR and unit._hfp_accepts(CAR)
  unit._release_phone.assert_not_called()


def test_unpaired_car_is_left_alone(monkeypatch):
  unit, phone = fixture(monkeypatch, paired=False)
  assert unit._wired_handsfree(CAR) == ""
  phone.acquire.assert_not_called()
  assert unit._wired_car == ""
  assert unit.log.call_args.kwargs == {"outcome": "not_paired", "car_tail": "C8", "paired_tails": ["01"]}


def test_no_link_sends_nothing_and_gives_back_bluetooth(monkeypatch):
  unit, phone = fixture(monkeypatch, links=False)
  assert unit._wired_handsfree(CAR) == ""
  unit._release_phone.assert_called_once()
  assert unit._selected_receiver() == ""


def test_lease_failure_gives_back_bluetooth(monkeypatch):
  unit, phone = fixture(monkeypatch)
  phone.acquire.side_effect = RuntimeError("Bluetooth pairing is busy")
  assert unit._wired_handsfree(CAR) == ""
  unit._release_phone.assert_called_once()
  assert unit._wired_car == ""


def test_usb_cleanup_error_still_clears_the_wired_car(monkeypatch):
  from openpilot.starpilot.system.starport import usb_accessory
  unit, _ = fixture(monkeypatch)
  unit.config["usb_mode"] = "auto"
  unit._wired_car = CAR
  gadget = Mock()
  gadget.prepare.side_effect = RuntimeError("session ended")
  monkeypatch.setattr(supervisor_module, "wired_blocker", lambda: "")
  monkeypatch.setattr(supervisor_module.identity_store, "load_identity", lambda: None)
  monkeypatch.setattr(usb_accessory, "AccessoryGadget", lambda log: gadget)
  monkeypatch.setattr(usb_accessory, "UeventListener", Mock)
  unit._release_usb = Mock(side_effect=OSError("gadget busy"))
  try:
    unit._attempt_usb()
  except OSError:
    pass
  assert unit._wired_car == "" and unit._selected_receiver() == ""
  unit._release_phone.assert_called_once()
