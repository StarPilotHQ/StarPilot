"""The per-session device slice: bounded line lists from a collector, saved beside the session log."""

import json

from openpilot.starpilot.system.starport import system_slice


def fake_collector(since, deadline, *, command_set):
  names = list(command_set)
  files = {"network": b"line one aa:bb:cc:dd:ee:ff\nline two\n", "proc.txt": b"==== /proc/loadavg\n0.5\n"}
  manifest = {name: {"command": "x", "exit": 0} for name in names}
  manifest["kernel"]["error"] = "timed out after 6 s"
  files["manifest.json"] = json.dumps({"commands": manifest}).encode()
  return files


def test_collect_turns_command_output_into_line_lists():
  result = system_slice.collect(1_000_000.0, 1_000_060.0, collector=fake_collector)
  sources = result["sources"]
  assert sources["network"]["lines"] == ["line one xx:xx:xx:xx:xx:ff", "line two"]
  assert sources["kernel"]["error"].startswith("timed out") and "lines" not in sources["kernel"]
  assert "error" in sources["bluetooth"] and sources["proc"]["lines"][0].startswith("====")


def test_commands_are_niced_and_cover_the_session_window():
  command = system_slice.commands(1_000_000.0, 1_000_060.0)["network"]
  assert command[:3] == ["nice", "-n", "19"] and "--utc" in command
  assert command[command.index("--since") + 1] == "1970-01-12 13:46:10 UTC" and "NetworkManager" in command


def test_scrub_line_masks_names_ssids_and_addresses_and_caps_length():
  line = system_slice.scrub_line("assoc ssid=Home bssid aa:bb:cc:dd:ee:ff 'My Car' Danny " + "z" * 500, names=["Danny"])
  assert "Home" not in line and "aa:bb" not in line and "My Car" not in line and "Danny" not in line and len(line) <= 300


def test_sidecar_round_trip_and_pruning(tmp_path):
  log = tmp_path / "session-000007-20261016-120000.jsonl"
  log.write_text("")
  system_slice.write_sidecar(log, 1_000_000.0, 1_000_060.0, collector=fake_collector)
  assert system_slice.sidecar_path(log).name == "session-000007.system.json"
  assert system_slice.read_sidecar(log)["sources"]["network"]["lines"][1] == "line two"
  stale = tmp_path / "session-000002.system.json"
  stale.write_text("{}")
  system_slice.prune_sidecars(tmp_path)
  assert not stale.exists() and system_slice.sidecar_path(log).exists()


def test_stall_capture_sits_beside_the_session_capture_and_is_pruned_with_it(tmp_path):
  log = tmp_path / "session-000007-20261016-120000.jsonl"
  log.write_text("")
  system_slice.write_sidecar(log, 1_000_000.0, 1_000_060.0, collector=fake_collector, tag=".stall")
  stall = tmp_path / "session-000007.stall.system.json"
  assert stall.exists() and not system_slice.sidecar_path(log).exists()
  system_slice.prune_sidecars(tmp_path)
  assert stall.exists()
  log.unlink()
  system_slice.prune_sidecars(tmp_path)
  assert not stall.exists()


def test_a_collector_that_raises_leaves_no_sidecar(tmp_path):
  log = tmp_path / "session-000001-20261016-120000.jsonl"
  log.write_text("")

  def broken(*_args, **_kwargs):
    raise RuntimeError("boom")

  system_slice.write_sidecar(log, 0.0, 1.0, collector=broken)
  assert system_slice.read_sidecar(log) == {}
