"""The network watcher closes only internet connections that were open on a link the comma has left."""

from openpilot.starpilot.system import netwatch

HOME = ("192.168.35.20", "192.168.35.1", "aa:aa")
PHONE = ("172.20.10.4", "172.20.10.1", "bb:bb")
CELL = ("14", "10.230.35.11")
SPOTIFY = ("172.20.10.4", 50000, "35.186.224.24", 443)
CAR = ("172.20.10.4", 50001, "172.20.10.9", 30515)
OTHER = ("10.230.35.11", 50002, "35.186.224.24", 443)


def watcher(marks, open_connections):
  closed = []
  made = netwatch.Watcher(read=lambda name: marks[name], connections=lambda: set(open_connections),
                          close=lambda connection: closed.append(connection) or True)
  made.start()
  return made, closed


def test_leaving_wifi_closes_its_internet_connections_after_it_settles():
  marks = {"wlan0": PHONE, "ppp0": CELL}
  open_connections = {SPOTIFY, CAR, OTHER}
  made, closed = watcher(marks, open_connections)
  marks["wlan0"] = None
  assert made.tick(0.0) == [] and made.tick(3.0) == []
  assert made.tick(4.5) == [("wlan0", 1)] and closed == [SPOTIFY]


def test_a_short_drop_back_onto_the_same_network_closes_nothing():
  marks = {"wlan0": HOME, "ppp0": CELL}
  made, closed = watcher(marks, {("192.168.35.20", 1, "35.186.224.24", 443)})
  marks["wlan0"] = None
  made.tick(0.0)
  marks["wlan0"] = HOME
  assert made.tick(2.0) == [] and made.tick(9.0) == [] and closed == []


def test_connections_opened_after_the_change_or_already_closed_are_left_alone():
  marks = {"wlan0": None, "ppp0": CELL}
  open_connections = {OTHER, ("10.230.35.11", 50003, "1.1.1.1", 443)}
  made, closed = watcher(marks, open_connections)
  marks["ppp0"] = ("15", "10.230.35.11")  # cellular came back on a new interface with the same address
  made.tick(0.0)
  open_connections.discard(("10.230.35.11", 50003, "1.1.1.1", 443))
  open_connections.add(("10.230.35.11", 50009, "8.8.8.8", 443))
  made.tick(1.0)
  assert made.tick(3.5) == [("ppp0", 1)] and closed == [OTHER]


def test_a_wifi_change_waits_for_the_new_gateway_before_it_is_judged():
  marks = {"wlan0": ("192.168.1.5", "192.168.1.1", "aa:aa"), "ppp0": CELL}
  made, closed = watcher(marks, {("192.168.1.5", 7, "35.186.224.24", 443)})
  marks["wlan0"] = None
  made.tick(0.0)
  marks["wlan0"] = ("192.168.1.5", "192.168.1.1", "")
  assert made.tick(1.0) == [] and closed == []
  marks["wlan0"] = ("192.168.1.5", "192.168.1.1", "cc:cc")  # same addresses, a different network
  made.tick(2.0)
  assert made.tick(5.0) == [("wlan0", 1)] and len(closed) == 1


def test_parsers_read_routes_neighbours_and_sockets():
  routes = "\n".join(["Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask",
                      "ppp0\t00000000\t4040400A\t0003\t0\t0\t1000\t00000000",
                      "wlan0\t00000000\t02630A0A\t0003\t0\t0\t600\t00000000",
                      "wlan0\t00630A0A\t00000000\t0001\t0\t0\t600\t00FFFFFF"])
  assert netwatch.default_routes(routes) == [(600, "wlan0", "10.10.99.2"), (1000, "ppp0", "10.64.64.64")]
  neighbors = "IP address HW type Flags HW address Mask Device\n10.10.99.2 0x1 0x2 ac:bc:32:ab:eb:8f * wlan0\n"
  assert netwatch.gateway_hardware("wlan0", "10.10.99.2", neighbors) == "ac:bc:32:ab:eb:8f"
  sockets = "0 0 10.10.99.177:50176 35.186.224.31:443\n0 0 127.0.0.1:45953 127.0.0.1:60136\n"
  assert netwatch.parse_connections(sockets) == {("10.10.99.177", 50176, "35.186.224.31", 443), ("127.0.0.1", 45953, "127.0.0.1", 60136)}
  assert not netwatch.internet(CAR, "172.20.10.4") and netwatch.internet(SPOTIFY, "172.20.10.4")
