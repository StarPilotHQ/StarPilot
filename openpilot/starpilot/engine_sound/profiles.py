"""Engine sound profiles. A profile with an installed loop bank plays it; otherwise it falls back to the synth voice."""
from __future__ import annotations

from dataclasses import dataclass

DEFAULT_PROFILE = 'lfa'


@dataclass(frozen=True)
class Profile:
  name: str
  label: str
  loops: str = ''                     # loop bank directory name under loops.BANK_ROOT

  idle_rpm: float = 900.0
  redline_rpm: float = 7000.0
  # Engine rpm per m/s of road speed for each virtual gear; empty means one continuous range.
  gears: tuple[float, ...] = ()
  top_speed: float = 45.0             # m/s reaching redline, continuous profiles only
  upshift_rpm: tuple[float, float] = (2500.0, 6500.0)    # at zero and full load
  downshift_rpm: tuple[float, float] = (1300.0, 3800.0)
  launch_slip_rpm: float = 2200.0     # rpm above idle a full pedal reaches before the wheels catch up
  rev_seconds: float = 0.9            # idle to redline with the pedal down and no load
  rise_seconds: float = 0.12
  fall_seconds: float = 0.45
  limiter: bool = False

  # Pitch in Hz per rpm of the lowest partial. 1/120 is one four-stroke cycle.
  pitch: float = 1.0 / 120.0
  # (partial number, level at no load, level at full load)
  partials: tuple[tuple[int, float, float], ...] = ()
  detune: float = 0.0                 # second voice pitch offset, as a ratio
  whine_ratio: float = 0.0            # sine at this multiple of the lowest partial, rising with load
  whine_level: float = 0.0
  noise: tuple[float, float] = (0.0, 0.0)        # broadband level at no load and full load
  noise_pulse: float = 0.0            # how strongly noise is gated by combustion pulses (diesel clatter)
  pulse_partial: int = 8              # partial the pulses follow
  tremolo: tuple[float, float] = (0.0, 0.0)      # rate in Hz, depth
  gain: tuple[float, float] = (0.35, 1.0)        # loudness at no load and full load
  pops: float = 0.0                   # overrun pops and crackle, 0 none to 1 heavy
  burble: bool = False                # recorded burble bursts instead of single pops at low revs and on downshifts
  pop_pitch: float = 1.0              # playback speed of every pop; below 1 is deeper
  turbo: float = 0.0                  # turbo whistle and blow-off valve, 0 none to 1 loud


def _engine_partials(firing: int, count: int, rough: float, bright: float) -> tuple[tuple[int, float, float], ...]:
  """Half-order series with the firing order and its multiples emphasised."""
  out = []
  for k in range(1, count + 1):
    on_order = k % firing == 0
    rolloff = 1.0 / (1.0 + (k / firing) ** 1.4)
    idle = rolloff * (1.0 if on_order else rough)
    load = rolloff ** (1.0 - bright) * (1.0 if on_order else rough * 1.6)
    out.append((k, idle, load))
  return tuple(out)


RACE_GEARS = (520.0, 340.0, 250.0, 198.0, 165.0, 142.0)
RACE_REDLINE = 8600.0


def _recorded(name: str, label: str, *, cylinders: int, idle: float, redline: float, loops: str = '', pops: float = 0.0,
              burble: bool = False, pop_pitch: float = 1.0, turbo: float = 0.0, upshift: float = 0.38,
              downshift: float = 0.2, gearing: float = 1.0) -> Profile:
  """A bundled loop bank (of the same name unless given), geared like the race car but scaled to its own rev range.

  upshift and downshift are the light-throttle shift points as shares of redline; full throttle always shifts at 96%.
  gearing above 1 makes every gear shorter, so road speed turns into more rpm.
  """
  scale = redline / RACE_REDLINE * gearing
  return Profile(
    name=name, label=label, loops=loops or name, pops=pops, burble=burble, pop_pitch=pop_pitch, turbo=turbo,
    idle_rpm=idle, redline_rpm=redline,
    gears=tuple(ratio * scale for ratio in RACE_GEARS),
    upshift_rpm=(upshift * redline, 0.96 * redline), downshift_rpm=(downshift * redline, max(0.6, downshift + 0.25) * redline),
    launch_slip_rpm=0.4 * redline, rev_seconds=0.8, rise_seconds=0.08, fall_seconds=0.35, limiter=True,
    partials=_engine_partials(firing=cylinders, count=40, rough=0.25, bright=0.5),
    noise=(0.03, 0.18), gain=(0.7, 1.0),
  )


PROFILES: dict[str, Profile] = {profile.name: profile for profile in (
  _recorded('bmw_m3', 'BMW M3', cylinders=6, idle=700.0, redline=7000.0, pops=1.0, burble=True),
  _recorded('bmw_m3_deep', 'BMW M3 Deep', cylinders=6, idle=700.0, redline=7000.0, pops=1.0, burble=True, pop_pitch=0.8),
  _recorded('bmw_m52', 'BMW M52', cylinders=6, idle=700.0, redline=7000.0),
  _recorded('f1_car', 'F1 Car', cylinders=12, idle=2000.0, redline=17000.0),
  _recorded('ferrari_f136', 'Ferrari F136', cylinders=8, idle=1000.0, redline=9000.0),
  # Banks from real recordings that end at 3000 and 5250 rpm; above about 1.5 times that the loops sound sped up.
  _recorded('hellcat', 'Hellcat', cylinders=8, idle=700.0, redline=4500.0),
  _recorded('jaguar_ftype', 'Jaguar F-Type', cylinders=8, idle=721.0, redline=6500.0, pops=1.0, burble=True),
  _recorded('lfa', 'LFA', cylinders=10, idle=1000.0, redline=9000.0),
  # Recorded only at idle and from 3250 rpm up, so it is geared short and shifts like a race car to stay in that range.
  _recorded('nissan_super_gt', 'Nissan Super GT1', cylinders=4, idle=1556.0, redline=7500.0, upshift=0.62, downshift=0.45,
            gearing=1.2),
  _recorded('nissan_super_gt2', 'Nissan Super GT2', cylinders=4, idle=1556.0, redline=7500.0, upshift=0.62, downshift=0.45,
            gearing=1.2),
  _recorded('supra_turbo', 'Supra Turbo', cylinders=6, idle=800.0, redline=6000.0, turbo=1.0),
)}


def get(name: str) -> Profile:
  return PROFILES.get(name, PROFILES[DEFAULT_PROFILE])
