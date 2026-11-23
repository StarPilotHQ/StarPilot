import pytest

from openpilot.common.version import build_metadata_from_dict


@pytest.mark.parametrize("origin, channel, expected", [
  ("https://github.com/StarPilotHQ/StarPilot.git", "stable", True),
  ("https://github.com/StarPilotHQ/StarPilot.git", "testing", True),
  ("https://github.com/firestar5683/StarPilot.git", "stable", True),
  ("git@github.com:firestar5683/StarPilot.git", "testing", True),
  ("https://github.com/StarPilotHQ/StarPilot.git", "StarPilot", True),
  ("https://github.com/StarPilotHQ/StarPilot.git", "Dom", True),
  ("https://github.com/firestar5683/StarPilot.git", "StarPilot", True),
  ("https://github.com/firestar5683/StarPilot.git", "Dom", True),
  ("https://github.com/StarPilotHQ/StarPilot.git", "feature", False),
  ("https://github.com/StarPilotHQ/StarPilot.git", "HEAD", False),
  ("https://github.com/other/StarPilot.git", "stable", False),
  ("https://github.com/other/StarPilot.git", "testing", False),
  ("https://github.com/commaai/openpilot.git", "release-tizi", True),
  ("https://github.com/commaai/openpilot.git", "master", False),
  ("https://github.com/StarPilotHQ/StarPilot.git", "replay", False),
])
def test_standard_startup(origin, channel, expected):
  metadata = build_metadata_from_dict({"channel": channel, "openpilot": {"git_origin": origin}})
  assert metadata.standard_startup is expected
