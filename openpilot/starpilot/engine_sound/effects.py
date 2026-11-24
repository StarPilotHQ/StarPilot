"""Sound layers mixed over a profile's engine voice."""
from __future__ import annotations

from openpilot.starpilot.engine_sound.pops import Pops
from openpilot.starpilot.engine_sound.profiles import Profile
from openpilot.starpilot.engine_sound.turbo import Turbo


def layers(profile: Profile, sample_rate: int) -> list:
  out: list = []
  if profile.pops:
    out.append(Pops(profile.pops, profile.idle_rpm, profile.redline_rpm, sample_rate, burble=profile.burble,
                    pitch=profile.pop_pitch))
  if profile.turbo:
    out.append(Turbo(profile.turbo, profile.idle_rpm, profile.redline_rpm, sample_rate))
  return out
