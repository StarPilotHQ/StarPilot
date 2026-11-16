from unittest.mock import Mock

import pytest
import requests

from openpilot.starpilot.analytics.report import MAX_PAYLOAD, ReportClient, WRITE_URL, build_payload, fetch_branch_commit


def test_compatible_measurements_and_precise_field_types():
  payload = build_payload({"branch": "SecretGoodStarPilot", "car_make": "gm", "car_model": "BOLT",
                           "driving_model": "actual-rdf", "device": "mici", "dongle_id": "unit",
                           "metrics": {"has_pedal": True, "lagd_valid_blocks": 4, "lagd_applied_delay_seconds": .25,
                                       "up_to_date": False, "model_variant": "small", "commit": "c" * 40},
                           "counters": {"drives": 3, "meters": 1609.344, "seconds": 3600, "current_months_meters": 2500},
                           "branch_commit": "a" * 40}, {"VoltSNG": False, "DrivingProfile": "comfort", "valid_gm_VoltSNG": True,
                                                    "default_known_gm_VoltSNG": True, "default_gm_VoltSNG": False}, 123).decode()
  first, second = payload.splitlines()
  assert first.startswith("user_stats,branch=SecretGoodStarPilot,car_make=GM,car_model=BOLT,")
  assert "device_generation=C4" in first and "driving_model=actual-rdf" in first
  assert "car_make=Hyundai" in build_payload({"car_make": "hyundai"}, {}, 1).decode()
  assert 'commit="' + "c" * 40 + '"' in first
  assert "has_pedal=true" in first and "lagd_valid_blocks=4i" in first
  assert "lagd_applied_delay_seconds=0.25" in first and "up_to_date=false" in first
  assert "starpilot_drives=3i" in first and "starpilot_hours=1.0" in first
  assert "current_months_kilometers=2i" in first and "starpilot_miles=" in first
  assert 'setting_DrivingProfile="comfort"' in first and "setting_VoltSNG=false" in first
  assert "valid_gm_VoltSNG=true" in first and "default_gm_VoltSNG=false" in first
  assert "setting_valid_" not in first and "setting_default_" not in first
  assert second == f'branch_commits,branch=SecretGoodStarPilot commit="{"a" * 40}" 123'


def test_unknown_data_is_not_zero_and_coordinates_require_coarse_owner():
  payload = build_payload({"latitude": 41.1234567, "longitude": -87.1234567,
                           "metrics": {"latitude": 41.1234567, "has_pedal": "true", "lagd_valid_blocks": False}}, {}, 1).decode()
  assert "driving_model=unknown" in payload
  assert "starpilot_miles=" not in payload and "starpilot_hours=" not in payload
  assert "latitude=" not in payload and "longitude=" not in payload
  assert "has_pedal=" not in payload and "lagd_valid_blocks=" not in payload
  coarse = build_payload({"coarse_location": {"latitude": 41.8, "longitude": -87.6}}, {}, 1).decode()
  assert "latitude=41.8" in coarse and "longitude=-87.6" in coarse


def test_line_protocol_escaping_and_secret_exclusion():
  payload = build_payload({"branch": 'a,b=c\\d\nnew line'},
                          {"SafeText": 'a"b\\c\nnext', "Token": "credential", "ApiKey": "credential", "SecOCKey": "credential", "GalaxyPairing": "credential",
                           "CarVin": "private-vin", "LastGPSPosition": "private-location", "RawJSON": {},
                           "BadFloat": float("nan"), "Huge": 1 << 64, 2: "invalid-key", "Empty": ""}, 5).decode()
  assert len(payload.splitlines()) == 1
  assert r"branch=a\,b\=c\\d\ new\ line" in payload
  assert r'setting_SafeText="a\"b\\c next"' in payload
  assert 'setting_Empty=""' in payload
  assert "credential" not in payload and "private-vin" not in payload and "private-location" not in payload
  assert "RawJSON" not in payload and "BadFloat" not in payload and "Huge" not in payload


def test_payload_and_timestamp_bounds():
  for value in (0, -1, True, 1 << 63):
    with pytest.raises(ValueError, match="timestamp"):
      build_payload({}, {}, value)
  with pytest.raises(ValueError, match="limit"):
    build_payload({}, {f"Feature{i}": "x" * 256 for i in range(2051)}, 1)


def test_transport_is_fixed_bounded_and_does_not_follow_redirects(capsys):
  session, response = Mock(), Mock(status_code=204)
  session.post.return_value = response
  client = ReportClient("private-credential", session=session)
  assert client.send(b"user_stats event=1i 1\n", allowed=lambda: True)
  args, kwargs = session.post.call_args
  assert args == (WRITE_URL,)
  assert kwargs["params"] == {"org": "StarPilot", "bucket": "StarPilot", "precision": "ns"}
  assert kwargs["timeout"] == (3, 10) and kwargs["allow_redirects"] is False and kwargs["stream"] is True
  assert kwargs["headers"]["Authorization"] == "Token private-credential"
  response.close.assert_called_once()
  assert "private-credential" not in repr(client)
  session.post.side_effect = requests.Timeout("private-credential")
  assert not client.send(b"user_stats event=1i 1\n")
  assert not capsys.readouterr().out and not capsys.readouterr().err


def test_transport_gate_missing_credentials_and_accidental_payload_credentials():
  session = Mock()
  assert not ReportClient(None, session=session).send(b"x")
  client = ReportClient(None, session=session)
  assert not client.send(b"x", "fresh-credential", allowed=lambda: False)
  assert not client.send(b"fresh-credential", "fresh-credential")
  assert not client.send(b"x" * (MAX_PAYLOAD + 1), "fresh-credential")
  assert not client.send(b"x", "bad\ncredential")
  session.post.assert_not_called()


def test_branch_commit_lookup_stays_on_official_repository_and_bounds_body():
  session, response = Mock(), Mock(status_code=200)
  response.iter_content.return_value = [b'{"sha":"' + b"b" * 40 + b'"}']
  session.get.return_value = response
  assert fetch_branch_commit("feature/branch", session=session) == "b" * 40
  assert session.get.call_args.args[0] == "https://api.github.com/repos/firestar5683/StarPilot/commits/feature%2Fbranch"
  assert session.get.call_args.kwargs["allow_redirects"] is False
  response.iter_content.return_value = [b"x" * 65_537]
  assert fetch_branch_commit("branch", session=session) is None
  response.close.assert_called()


def test_actual_scalar_collector_fields_survive_without_double_prefix():
  from openpilot.starpilot.analytics import settings
  from openpilot.starpilot.ui.feature_settings_state import FeatureRow

  rows = (("gm", FeatureRow("VoltSNG", "Volt SNG", "On", choices=("Off", "On"), default_value="Off")),
          ("lane", FeatureRow("Gain", "Gain", ".75", step=.05, minimum=.5, maximum=1.5, default_value="1")))
  collected = settings._fields(rows)
  payload = build_payload({}, collected, 1).decode()
  assert "setting_gm_VoltSNG=true" in payload
  assert "default_gm_VoltSNG=false" in payload
  assert "valid_gm_VoltSNG=true" in payload
  assert "default_known_gm_VoltSNG=true" in payload
  assert "setting_lane_Gain=0.75" in payload
  assert "default_lane_Gain=1.0" in payload
  assert "settings_count=2.0" in payload and "settings_truncated=false" in payload
  assert "setting_setting_" not in payload and "setting_default_" not in payload


def test_owned_http_sessions_are_closed(monkeypatch):
  session = Mock()
  monkeypatch.setattr(requests, "Session", lambda: session)
  client = ReportClient(None)
  client.close()
  session.close.assert_called_once()
  session.reset_mock()
  session.get.side_effect = requests.Timeout("no output")
  assert fetch_branch_commit("branch") is None
  session.close.assert_called_once()
  borrowed = ReportClient(None, session=session)
  session.reset_mock()
  borrowed.close()
  session.close.assert_not_called()


def test_branch_lookup_gate_prevents_request():
  session = Mock()
  assert fetch_branch_commit("branch", session=session, allowed=lambda: False) is None
  session.get.assert_not_called()


def test_builtin_authentication_allows_normal_branch_branding():
  from openpilot.starpilot.analytics.credentials import CLIENT_TOKEN
  session = Mock()
  session.post.return_value = Mock(status_code=204)
  payload = build_payload({'branch': 'SecretGood' + CLIENT_TOKEN}, {}, 1)
  assert CLIENT_TOKEN.encode() in payload
  assert ReportClient(CLIENT_TOKEN, session=session).send(payload)
  assert session.post.call_args.kwargs['data'] == payload



@pytest.mark.parametrize('major', [7, 8, 6, 0, -1, 100, True, '7', 7.0, None])
def test_software_major_is_a_validated_integer_not_a_branch_guess(major):
  payload = build_payload({'branch': 'StarPilot', 'metrics': {'software_major': major}}, {}, 1).decode()
  if type(major) is int and 0 < major < 100:
    assert f'software_major={major}i' in payload
  else:
    assert 'software_major=' not in payload
