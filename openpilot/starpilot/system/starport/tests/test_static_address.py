from openpilot.starpilot.system.starport.bootstrap import WifiCredentials
from openpilot.starpilot.system.starport.network import connection_settings, static_address


def test_static_address_sits_beside_the_dongle():
  assert static_address("10.42.1.1") == "10.42.1.77"
  assert static_address("192.168.180.77") == "192.168.180.78"
  assert static_address("8.8.8.8") == "" and static_address("nonsense") == ""


def test_static_ip_switches_the_join_to_a_manual_address():
  credentials = WifiCredentials("dongle", "12345678", "", 2, 0)
  assert connection_settings(credentials, "aa0")["ipv4"]["method"] == ("s", "auto")
  ipv4 = connection_settings(credentials, "aa0", "10.42.1.77")["ipv4"]
  assert ipv4["method"] == ("s", "manual")
  assert ipv4["address-data"] == ("aa{sv}", [{"address": ("s", "10.42.1.77"), "prefix": ("u", 24)}])
