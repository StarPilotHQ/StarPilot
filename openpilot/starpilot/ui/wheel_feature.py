import hashlib

from openpilot.starpilot.aol.vehicle import policy_for
from openpilot.starpilot.conditional_mode.manual import ioniq6_media_eligible
from openpilot.starpilot.feature_capabilities import lateral_supported
from openpilot.starpilot.controllers.wheel_actions import ACTIONS, DEFAULTS, KEYS, PADDLE_KEY, button_reported, eligible, paddle_bookmark_available
from openpilot.starpilot.saved_document import commit_exact
from openpilot.starpilot.saved_source import read_saved
from openpilot.starpilot.ui.feature_settings_state import FeatureRow


PREFIX = "wheel:"
LABELS = ("LKAS press", "Main cruise press", "Distance short press", "Distance long press", "Distance very long press",
          "Cancel short press", "Cancel long press", "Cancel very long press", "MODE press", "MODE long press",
          "MODE very long press", "Star button", "Star long press", "Star very long press", "Left paddle press")


class WheelFeature:
  def __init__(self, owner):
    self.owner = owner

  def capability(self):
    cp = self.owner.vehicle_params()
    if not eligible(cp):
      return None
    reader = cp.as_reader() if hasattr(cp, "as_reader") else cp
    base = (cp.carFingerprint, hashlib.sha256(reader.as_builder().to_bytes()).hexdigest())
    if self.owner.configuration_vehicle():
      return base + (self.owner.longitudinal_available(), "configuration")
    return base + (True,) if self.owner.configuration_longitudinal() else base

  def _policy(self, cp):
    policy = policy_for(cp)
    if self.owner.configuration_vehicle():
      from openpilot.starpilot.car.hyundai.settings import configuration_wheel_policy
      policy = configuration_wheel_policy(cp) or policy
    return policy

  def fixed_role(self, cp, key, policy):
    if key not in KEYS[:2] or not policy.settings_supported:
      return None
    if key == KEYS[0] and (not button_reported(cp, key) or
                          not (policy.lkas_button_supported or policy.auxiliary_lkas_supported)):
      return None
    if policy.fixed_cruise_buttons and not (key == KEYS[0] and policy.auxiliary_lkas_supported):
      return "Toggle AOL" if cp.brand == 'hyundai' else "Factory cruise / AOL"
    if (key == KEYS[1] and policy.runtime_supported and cp.brand in ('chrysler', 'mazda', 'nissan', 'volkswagen') and
        not button_reported(cp, key)):
      return "Factory cruise / AOL"
    if cp.brand == 'ford' and policy.explicit_latch and policy.runtime_supported and key == KEYS[1]:
      sources = [read_saved(self.owner.params, name, 8) for name in KEYS[:2]]
      return "Factory cruise / AOL" if all(ok and value != b'9' for value, ok in sources) else "Factory cruise"
    return None

  def choices(self, key):
    cp = self.owner.vehicle_params()
    if self.capability() is None:
      return ()
    if key == PADDLE_KEY:
      return ("Off", "Bookmark") if paddle_bookmark_available(self.owner.params, cp) else ()
    policy = self._policy(cp)
    if self.fixed_role(cp, key, policy) is not None:
      return ()
    media = ioniq6_media_eligible(cp)
    if self.owner.configuration_vehicle():
      from openpilot.starpilot.car.hyundai.settings import configuration_wheel_policy, configuration_media_supported
      policy = configuration_wheel_policy(cp) or policy
      media = configuration_media_supported(cp) or media
    if not button_reported(cp, key):
      return ()
    if key == "LKASButtonControl" and not (policy.lkas_button_supported or policy.auxiliary_lkas_supported):
      return ()
    if key in KEYS[:2] and policy.fixed_cruise_buttons and not (
        key == "LKASButtonControl" and policy.auxiliary_lkas_supported):
      return ()
    if key in KEYS[5:8] and cp.brand == 'gm':
      from opendbc.car.gm.cancel_remap import supported
      if supported(cp) and read_saved(self.owner.params, 'RemapCancelToDistance', 8) != (b'1', True):
        return ()
    if key in KEYS[8:] and not media:
      return ()
    codes = {0, 8, 11, 12, 13}
    if self.owner.longitudinal_available():
      codes.update((1, 2, 5, 10, 14))
      codes.add(6)
    if lateral_supported(cp):
      codes.add(7)
    if not policy.fixed_cruise_buttons and policy.settings_supported:
      if key in KEYS[:5] or not policy.explicit_latch:
        codes.update((3, 4, 9))
    elif policy.distance_pause_only and key in KEYS[2:5]:
      codes.update((3, 4))
    if key == "MainCruiseButtonControl":
      codes &= {0, 9}
      if self.owner.longitudinal_available():
        codes.add(10)
    return tuple(label for label, code in ACTIONS.items() if code in codes)

  def rows(self):
    capability = self.capability()
    if capability is None:
      return []
    rows = []
    cp = self.owner.vehicle_params()
    policy = self._policy(cp)
    for key, label in zip(KEYS, LABELS, strict=True):
      fixed = self.fixed_role(cp, key, policy)
      if fixed is not None:
        raw, _ = read_saved(self.owner.params, key, 8)
        rows.append(FeatureRow(PREFIX + key, label, fixed, raw, (), available=False,
                               reason="Vehicle-controlled AOL role; this button cannot be reassigned. Saved preferences are unchanged.",
                               capability=capability))
        continue
      choices = self.choices(key)
      if not choices:
        continue
      raw, readable = read_saved(self.owner.params, key, 8)
      number = DEFAULTS.get(key, 0) if raw is None else int(raw) if raw in tuple(str(i).encode() for i in range(15)) else -1
      selected = next((name for name, code in ACTIONS.items() if code == number), None)
      implicit_toggle = False
      if cp.brand == 'hyundai' and policy.explicit_latch and key in KEYS[:2]:
        from openpilot.starpilot.car.hyundai.aol import independent_cruise_buttons_supported
        sources = [read_saved(self.owner.params, name, 8) for name in KEYS[:2]]
        ordinary_actions = (None, *(str(code).encode() for code in ACTIONS.values() if code != 9))
        implicit_toggle = independent_cruise_buttons_supported(cp) and all(ok and value in ordinary_actions
                                                                          for value, ok in sources)
      ford_role = cp.brand == 'ford' and policy.explicit_latch and policy.runtime_supported and key == KEYS[0]
      if (implicit_toggle or ford_role) and selected == "Off":
        selected = "Toggle AOL"
      rows.append(FeatureRow(PREFIX + key, label, selected or "Invalid saved action", raw, choices,
                             available=readable and self.owner.authority("preferences"),
                             reason=("LKAS also toggles AOL independently of its saved extra action." if ford_role else
                                     "Neither LKAS nor MAIN is assigned Toggle AOL, so both retain the default AOL toggle." if implicit_toggle else
                                     "Bookmark on release. Available while Left Paddle Longitudinal Pause is Off." if key == PADDLE_KEY else
                                     "Off adds no action to the vehicle's existing button behavior. " +
                                     "Axis pause and AOL assignments apply after restarting. Other actions can apply this drive. " +
                                     "Long actions fire while held."),
                             capability=capability, repair_value="Off" if selected is None else ""))
    return rows

  def apply(self, request):
    key = request.key.removeprefix(PREFIX)
    if key not in KEYS or request.value not in self.choices(key) or request.capability != self.capability():
      return False
    capability = request.capability
    def authorized():
      return (self.owner.authority("preferences") and capability == self.capability() and
              request.value in self.choices(key))
    return commit_exact(self.owner.params, key=key, max_bytes=8, raw=str(ACTIONS[request.value]).encode(),
                        expected=request.expected, authorized=authorized, temp_prefix=".tmp_wheel_").committed
