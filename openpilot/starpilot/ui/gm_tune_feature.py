from opendbc.car.gm.long_tune import CONFIGURABLE_ACC_TUNE_CARS, VOLT_TUNE_CARS, tune_options
from openpilot.starpilot.car.gm.tune_preferences import KEY, CHOICES, read_choice
from openpilot.starpilot.saved_document import commit_exact
from openpilot.starpilot.ui.feature_settings_state import FeatureRow


class GmTuneFeature:
  def __init__(self, owner):
    self.owner = owner

  def capability(self):
    owner = self.owner
    cp = owner.vehicle_params()
    try:
      if cp is None or cp.brand != "gm" or cp.notCar:
        return None
      configured = bool(owner.configuration_vehicle())
      if cp.carFingerprint in VOLT_TUNE_CARS:
        return None
      if configured and cp.carFingerprint in CONFIGURABLE_ACC_TUNE_CARS:
        options = (0, 1)
      else:
        options = tune_options(cp)
      if len(options) < 2:
        return None
      return (str(cp.carFingerprint), configured, bool(cp.openpilotLongitudinalControl), bool(cp.pcmCruise),
              bool(cp.passive), bool(cp.dashcamOnly), int(cp.flags), int(cp.alternativeExperience),
              tuple((str(c.safetyModel), int(c.safetyParam)) for c in cp.safetyConfigs), options)
    except (AttributeError, TypeError, ValueError, OverflowError):
      return None

  def rows(self, parked):
    capability = self.capability()
    if capability is None:
      return ()
    value, raw, readable, valid = read_choice(self.owner.params)
    options = capability[-1]
    applicable = valid and value in options
    reason = "Vehicle Default preserves the vehicle's normal tune. ACC Tune softens low-speed acceleration and adjusts the braking handoff."
    if valid and not applicable:
      reason = "The saved tune belongs to a different vehicle. Vehicle Default is used until you choose an applicable tune."
    if not valid:
      reason = "Restore Vehicle Default to repair this saved choice." if readable else "Saved choice cannot be read."
    timing = " Updates within half a second."
    return (FeatureRow(KEY, "Speed Control Tune", CHOICES[value] if valid else "Invalid saved choice", raw,
                       choices=tuple(CHOICES[v] for v in options), available=parked and self.owner.authority("parked_preferences") and readable,
                       capability=capability, repair_value="" if applicable else CHOICES[0], default_value=CHOICES[0],
                       reason=reason + timing),)

  def apply(self, request):
    value = next((code for code, label in CHOICES.items() if label == request.value), None)
    if (request.key != KEY or value is None or request.dependencies or request.related_source is not None or
        request.display_unit or request.direction):
      return False
    owner = self.owner

    def authorized():
      capability = self.capability()
      _, raw, readable, valid = read_choice(owner.params)
      return (owner.authority("parked_preferences") and bool(request.vehicle_fingerprint) and
              owner.vehicle_fingerprint() == request.vehicle_fingerprint and capability is not None and
              capability == request.capability and value in capability[-1] and readable and raw == request.expected and
              (valid or value == 0))

    result = commit_exact(owner.params, key=KEY, max_bytes=8, raw=str(value).encode(), expected=request.expected,
                          authorized=authorized, temp_prefix=".gm-tune-")
    return result.committed and result.verified


class GmTruckFeature:
  def __init__(self, owner):
    self.owner = owner

  def capability(self):
    from opendbc.car.gm.truck_longitudinal import truck_tuning_supported
    cp = self.owner.vehicle_params()
    if not truck_tuning_supported(cp):
      return None
    return (str(cp.carFingerprint), int(cp.flags), int(cp.alternativeExperience),
            str(cp.transmissionType), bool(cp.openpilotLongitudinalControl), bool(cp.pcmCruise),
            tuple((str(c.safetyModel), int(c.safetyParam)) for c in cp.safetyConfigs))

  def rows(self, parked):
    from openpilot.starpilot.car.gm.tune_preferences import TRUCK_KEY, read_truck_choice
    capability = self.capability()
    if capability is None:
      return ()
    enabled, raw, readable, valid = read_truck_choice(self.owner.params)
    return (FeatureRow(TRUCK_KEY, "Truck Speed Control", ("On" if enabled else "Off") if valid else "Invalid saved choice", raw,
                       choices=("Off", "On"), available=parked and self.owner.authority("parked_preferences") and readable,
                       capability=capability, repair_value="" if valid else "Off", default_value="Off",
                       reason="Softens small acceleration requests, reduces grade corrections, and smooths light braking. Updates within half a second."),)

  def apply(self, request):
    from openpilot.starpilot.car.gm.tune_preferences import TRUCK_KEY, read_truck_choice
    if (request.key != TRUCK_KEY or request.value not in ("Off", "On") or request.dependencies or
        request.related_source is not None or request.display_unit or request.direction):
      return False
    owner = self.owner

    def authorized():
      capability = self.capability()
      _, raw, readable, valid = read_truck_choice(owner.params)
      return (owner.authority("parked_preferences") and bool(request.vehicle_fingerprint) and
              owner.vehicle_fingerprint() == request.vehicle_fingerprint and capability is not None and
              capability == request.capability and readable and raw == request.expected and
              (valid or request.value == "Off"))

    result = commit_exact(owner.params, key=TRUCK_KEY, max_bytes=8, raw=b"1" if request.value == "On" else b"0",
                          expected=request.expected, authorized=authorized, temp_prefix=".gm-truck-")
    return result.committed and result.verified


class GmEvPresetFeature:
  KEY = 'EVTuning'

  def __init__(self, owner):
    self.owner = owner

  def capability(self):
    from opendbc.car.gm.feature_capabilities import longitudinal_supported
    from opendbc.car.structs import CarParams
    cp = self.owner.vehicle_params()
    if cp is None or not longitudinal_supported(cp):
      return None
    return (str(cp.carFingerprint), bool(cp.openpilotLongitudinalControl), bool(cp.pcmCruise),
            str(cp.transmissionType), int(cp.flags), int(cp.alternativeExperience),
            tuple((str(c.safetyModel), int(c.safetyParam)) for c in cp.safetyConfigs),
            cp.transmissionType == CarParams.TransmissionType.direct)

  def read_choice(self, capability):
    from openpilot.starpilot.saved_source import read_saved
    raw, readable = read_saved(self.owner.params, self.KEY, 8)
    default = capability[-1]
    valid = readable and raw in (None, b'0', b'1')
    return (default if raw is None else raw == b'1'), raw, readable, valid, default

  def rows(self, parked):
    capability = self.capability()
    if capability is None:
      return ()
    value, raw, readable, valid, default = self.read_choice(capability)
    return (FeatureRow(self.KEY, 'EV Acceleration Presets', ('On' if value else 'Off') if valid else 'Invalid saved choice', raw,
                       choices=('Off', 'On'), available=parked and self.owner.authority('parked_preferences') and readable,
                       capability=capability, repair_value='' if valid else ('On' if default else 'Off'),
                       default_value='On' if default else 'Off',
                       reason=('Select EV curves for inherited acceleration presets. ' +
                               'Individual personality and custom curves remain as selected. ' +
                               'Truck presets take priority. Updates within one second.')),)

  def apply(self, request):
    if (request.key != self.KEY or request.value not in ('Off', 'On') or request.dependencies or
        request.related_source is not None or request.display_unit or request.direction):
      return False
    def authorized():
      capability = self.capability()
      if capability is None:
        return False
      _, raw, readable, valid, default = self.read_choice(capability)
      return (self.owner.authority('parked_preferences') and bool(request.vehicle_fingerprint) and
              self.owner.vehicle_fingerprint() == request.vehicle_fingerprint and capability == request.capability and
              readable and raw == request.expected and (valid or request.value == ('On' if default else 'Off')))
    result = commit_exact(self.owner.params, key=self.KEY, max_bytes=8,
                          raw=b'1' if request.value == 'On' else b'0', expected=request.expected,
                          authorized=authorized, temp_prefix='.gm-ev-preset-')
    return result.committed and result.verified
