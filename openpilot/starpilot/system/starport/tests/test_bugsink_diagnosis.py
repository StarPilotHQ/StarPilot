

def test_head_unit_software_versions_are_not_redacted_as_addresses():
  from openpilot.starpilot.system.starport.bugsink_diagnosis import sanitize
  clean = sanitize({"head_unit_software_version": "1.2.3.4", "head_unit_software_build": "W28E-V155.3.8", "detail": "peer 1.2.3.4"})
  assert clean == {"head_unit_software_version": "1.2.3.4", "head_unit_software_build": "W28E-V155.3.8", "detail": "peer <ip>"}
