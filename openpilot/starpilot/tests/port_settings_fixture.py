"""Small shared persisted-setting fixture for ported Tesla and map controls."""
from openpilot.starpilot.ui.feature_settings_state import FeatureSettingsRequest

class Saved:
  def __init__(self, path):
    self.path = path/'params'
    self.path.mkdir()
    self.writes = []
  def get_param_path(self, key=''):
    return str(self.path/key)
  def put(self, key, data, block=False):
    assert block, '7.0 persistence must finish before confirmation'
    self.writes.append((key, data))
    (self.path/key).write_bytes(data)

def request(row, value):
  return FeatureSettingsRequest(row.key, row.source, value, confirmation=True,
    vehicle_fingerprint=row.vehicle_fingerprint, dependencies=row.dependencies, display_unit=row.display_unit)
