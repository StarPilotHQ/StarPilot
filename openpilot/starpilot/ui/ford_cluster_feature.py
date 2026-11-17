"""Ford hands-free cluster display preference shared by Galaxy and the native feature presentation."""
from openpilot.starpilot.saved_document import commit_exact
from openpilot.starpilot.ui.feature_settings_state import FeatureRow


class FordClusterFeature:
  """Shared native/Galaxy saved display preference for Ford CAN-FD profiles."""
  def __init__(self, owner):
    self.owner = owner

  def capability(self):
    from openpilot.starpilot.car.ford.cluster_preferences import qualified
    cp = self.owner.vehicle_params()
    try:
      if cp is None or not qualified(cp):
        return None
      return (str(cp.carFingerprint), int(cp.flags), int(cp.alternativeExperience), bool(cp.openpilotLongitudinalControl),
              tuple((str(c.safetyModel), int(c.safetyParam)) for c in cp.safetyConfigs))
    except (AttributeError, TypeError, ValueError, OverflowError):
      return None

  def rows(self, parked):
    from openpilot.starpilot.car.ford.cluster_preferences import KEY, CHOICES, read_choice
    capability = self.capability()
    if capability is None:
      return ()
    value, raw, readable, valid = read_choice(self.owner.params)
    return (FeatureRow(KEY, "Ford hands-free cluster display", CHOICES[value] if valid else "Invalid saved choice", raw,
                       choices=tuple(CHOICES.values()), available=parked and self.owner.authority("parked_preferences") and readable,
                       capability=capability, repair_value="" if valid else CHOICES[0], default_value=CHOICES[0],
                       reason="Changes the active cluster icon only; driver attention requirements remain unchanged."),)

  def apply(self, request):
    from openpilot.starpilot.car.ford.cluster_preferences import KEY, CHOICES, read_choice
    value = next((code for code, label in CHOICES.items() if label == request.value), None)
    if (request.key != KEY or value is None or request.related_source is not None or request.display_unit or request.direction):
      return False

    def authorized():
      capability = self.capability()
      _, raw, readable, valid = read_choice(self.owner.params)
      return (self.owner.authority("parked_preferences") and bool(request.vehicle_fingerprint) and
              self.owner.vehicle_fingerprint() == request.vehicle_fingerprint and capability is not None and
              capability == request.capability and readable and raw == request.expected and (valid or value == 0))

    result = commit_exact(self.owner.params, key=KEY, max_bytes=8, raw=str(value).encode(), expected=request.expected,
                          authorized=authorized, temp_prefix=".ford-cluster-")
    return result.committed and result.verified
