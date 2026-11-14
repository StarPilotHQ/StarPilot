"""Burn In Protection: dims pixels that stop changing. It watches the finished picture, not the widgets.

A few times a second the detector looks at the frame that is about to be shown and
tracks, for every pixel, how long it has stayed the same. Pixels that have sat still
fade to a lower brightness; a pixel that changes is back at full brightness on the
next look. This is the logo dimming TVs do, and it needs no knowledge of what is drawn.

Three rules keep it from touching the live camera picture:

* A still pixel only dims when it is part of something drawn: it sits at a sharp edge
  that has stayed put (text, icons, borders) and stands out from what is around it.
  A flat patch of sky that happens not to change has no such edge and is left alone.
* Stillness only counts while the rest of the picture is moving. At a standstill the
  whole scene is still, so nothing new is learned and the scene is not dimmed in patches.
* When nothing on screen is changing for a while (parked, a menu left open) the whole
  picture dims evenly instead, like a TV dimming a paused picture.

This module is plain numpy so it can be tested with recorded frames. It is written for
the comma's small cores: whole-array byte and 16-bit operations, and bit-packed rows
for anything that spreads pixel by pixel. static_dim_screen.py reads the screen and
draws the result.
"""

from __future__ import annotations

import numpy as np

SAMPLE_HZ = 4.0            # looks per second
FULL_EVERY = 4             # every Nth look checks the whole picture (once a second); the looks between only check the
                           # pixels that are dimmed, which is all it takes to brighten a changed one at once
AVERAGE_EVERY = 2          # the averaged picture is updated on every Nth whole-picture look (every 2 s)
SEARCH_MIN_S = 4.0         # the search for drawn pixels runs this often after something dimmed has changed,
SEARCH_MAX_S = 16.0        # stretching to this while it keeps finding the same pixels
MOTION_SPACING = 4         # the motion measure samples every Nth pixel each way
TOLERANCE = 28             # a pixel has changed when any color channel moves more than this (0-255); wide enough for
                           # slightly see-through HUD text, whose color follows the camera picture behind it a little
DIM_START_S = 15.0         # still for this long before any dimming
DIM_FULL_S = 45.0          # fully dimmed after this long
RELEASE_S = 1.5            # a still pixel that no longer qualifies brightens over this long
REJOIN_S = 2.0             # a pixel that qualifies again (or joins dimmed neighbors) fades over this long
BRIGHT_MIN = 40            # darker pixels are left alone: there is nothing to protect
EDGE_CONTRAST = 40         # difference between pixels two apart that counts as a sharp edge (drawn edges are smoothed over a pixel)
STAND_OUT_MIN = 30         # a drawn pixel differs at least this much from what surrounds it (flat sky next to text does not),
                           # and stands out at least 0.8 times as much as anything within two pixels (the smoothed rim of a letter does not)
FILL_REACH_PX = 12         # from an edge, dimming spreads this far through still pixels of the same color that also stand out
                           # (the inside of a thick stroke, a border where something else crosses it)
FILL_SIMILAR = 20          # "same color": neighboring pixels within this of each other
SURROUND_BLOCK_PX = 4      # surroundings are averaged in blocks of this size,
SURROUND_BLOCKS = 4        # over this many blocks each way (about 36 px)
SLOW_SHIFT = 3             # the edges are searched in the picture averaged over about 2**SLOW_SHIFT updates (16 s)
MOTION_STEP = 6            # the picture's motion is the share of pixels that moved more than this since the last look,
MOTION_SMOOTH = 0.25       # smoothed over about a second. (Night driving: 2 % or more. A daytime stop: under 1.5 %.)
MOVING_ABOVE = 0.02        # the picture counts as moving above this share,
FROZEN_BELOW = 0.004       # and as frozen below this share,
FROZEN_ABOVE = 0.008       # until it rises above this one
JOIN_S = 1.5               # a new still pixel touching fully dimmed ones of its color counts as proven after this long, so a
JOIN_SPREAD_PX = 16        # changed digit lights up briefly and then matches the rest of its number; spreads this far a search

_Q = 6                     # averaged picture is kept in 1/64ths of a level


def _box_sum(values: np.ndarray, radius: int, axis: int) -> np.ndarray:
  """Sum over a window of 2*radius+1 along one axis (edges use what is there)."""
  total = np.cumsum(values, axis=axis, dtype=np.int64)
  size = values.shape[axis]
  lower = np.arange(size) - radius - 1
  high = np.take(total, np.minimum(np.arange(size) + radius, size - 1), axis=axis)
  low = np.take(total, np.maximum(lower, 0), axis=axis)
  shape = [1] * values.ndim
  shape[axis] = size
  return high - low * (lower >= 0).reshape(shape)


class _Rows:
  """Yes/no pictures packed 64 pixels to a word, so spreading by one pixel is a handful of tiny operations."""

  def __init__(self, width: int, height: int):
    self.width, self.height = width, height
    self.bytes = -(-width // 8)
    self.words = -(-self.bytes // 8)
    self._padded = np.zeros((height, self.words * 8), np.uint8)

  def pack(self, mask: np.ndarray) -> np.ndarray:
    self._padded[:, :self.bytes] = np.packbits(mask, axis=1, bitorder="little")
    return self._padded.view("<u8").copy()

  def unpack(self, rows: np.ndarray) -> np.ndarray:
    return np.unpackbits(rows.view(np.uint8), axis=1, count=self.width, bitorder="little").view(bool)

  @staticmethod
  def right(rows: np.ndarray) -> np.ndarray:
    """Each pixel takes the value of its left neighbor."""
    out = rows << np.uint64(1)
    out[:, 1:] |= rows[:, :-1] >> np.uint64(63)
    return out

  @staticmethod
  def left(rows: np.ndarray) -> np.ndarray:
    """Each pixel takes the value of its right neighbor."""
    out = rows >> np.uint64(1)
    out[:, :-1] |= rows[:, 1:] << np.uint64(63)
    return out

  def spread(self, start: np.ndarray, steps: int, allowed: np.ndarray, across: np.ndarray, down: np.ndarray) -> np.ndarray:
    """Grow `start` a pixel at a time through `allowed`, only between neighbors marked alike.
    across[y, x] links pixel x to x+1; down[y, x] links row y to y+1."""
    reached = start & allowed
    for _ in range(steps):
      grown = reached | self.right(reached & across) | (self.left(reached) & across)
      grown[1:] |= reached[:-1] & down[:-1]
      grown[:-1] |= reached[1:] & down[:-1]
      grown &= allowed
      if np.array_equal(grown, reached):
        break
      reached = grown
    return reached


class StaticPixelDetector:
  """Tracks how long each pixel has been still and how far it should be dimmed (level 0-255)."""

  def __init__(self, width: int, height: int, sample_hz: float = SAMPLE_HZ):
    self.width, self.height = int(width), int(height)
    self.sample_hz = float(sample_hz)
    full_hz = sample_hz / FULL_EVERY               # whole-picture looks per second: what stillness is counted in
    self.start_samples = max(1, round(DIM_START_S * full_hz))
    self.ramp_samples = max(1, round((DIM_FULL_S - DIM_START_S) * full_hz))
    self.full_samples = self.start_samples + self.ramp_samples
    self.join_samples = max(1, round(JOIN_S * full_hz))
    self.rejoin_step = max(1, round(255 / max(1.0, REJOIN_S * full_hz)))
    self.release_step = max(1, round(255 / max(1.0, RELEASE_S * full_hz)))
    self.search_min = max(1, round(SEARCH_MIN_S * full_hz))
    self.search_max = max(self.search_min, round(SEARCH_MAX_S * full_hz))
    self.search_gap = self.search_min
    self.search_at = self.search_min
    self.veil_start = max(1, round(DIM_START_S * sample_hz))      # the frozen-picture veil is timed in looks of either kind
    self.veil_ramp = max(1, round((DIM_FULL_S - DIM_START_S) * sample_hz))
    self.veil_release = 1.0 / max(1.0, RELEASE_S * sample_hz)
    self._ramp = (np.arange(self.ramp_samples + 1) * 255 // self.ramp_samples).astype(np.int16)
    h, w = self.height, self.width
    self.ref = np.zeros((h, w, 4), np.uint8)       # each pixel's color when it last changed
    self.mark = np.zeros((h, w), np.int32)         # how many moving-picture looks there had been when it last changed
    self.level = np.zeros((h, w), np.uint8)        # how far each pixel is dimmed
    self.eligible = np.zeros((h, w), bool)         # pixels that look drawn
    self.slow = np.zeros((3, h, w), np.int16)      # the averaged picture, one plane per color, in 1/64ths
    self.calls = 0            # looks of either kind
    self.look = 0             # whole-picture looks
    self.moving_looks = 0     # whole-picture looks during which the picture was moving
    self.motion = 1.0         # smoothed share of the screen that moved between looks
    self.moving = True
    self.frozen = False
    self._dimmed = np.zeros(0, np.intp)   # where the dimmed pixels are, for the quick looks
    self.frozen_samples = 0
    self.veil = 0.0           # even dimming of the whole picture while it is frozen, 0 to 1
    self._hi = np.zeros((h, w, 4), np.uint8)
    self._lo = np.zeros((h, w, 4), np.uint8)
    small = self.ref[::MOTION_SPACING, ::MOTION_SPACING].shape
    self._last_small = np.zeros(small, np.uint8)   # a sparse copy of the previous look, for the motion measure
    self._small_hi = np.zeros(small, np.uint8)
    self._small_lo = np.zeros(small, np.uint8)
    self._rows = _Rows(w, h)
    block = SURROUND_BLOCK_PX
    self._block_rows, self._block_cols = -(-h // block), -(-w // block)
    self._padded = np.zeros((self._block_rows * block, self._block_cols * block), np.int16)
    ones = np.ones((self._block_rows, self._block_cols), np.int64)
    self._block_count = _box_sum(_box_sum(ones, SURROUND_BLOCKS, 1), SURROUND_BLOCKS, 0) * (block * block)

  def reset(self) -> None:
    self.look = 0
    self.search_gap = self.search_at = self.search_min
    self.eligible[...] = False
    self.wake()

  def wake(self) -> None:
    """Everything back to full brightness at once (a touch on the screen)."""
    self.mark[...] = self.moving_looks
    self.level[...] = 0
    self._dimmed = np.zeros(0, np.intp)
    self.motion = 1.0
    self.moving = True
    self.frozen = False
    self.frozen_samples = 0
    self.veil = 0.0

  def _pixels(self, values: np.ndarray) -> np.ndarray:
    return values.view(np.uint32)[..., 0]

  def sample(self, frame: np.ndarray) -> bool:
    """Take one look at the picture: `frame` is height x width x 4 bytes (red, green, blue, alpha 255).
    Returns True when the dimming may have changed and the mask should be rebuilt.
    Every FULL_EVERY-th look compares the whole picture; the looks between are quick ones that only check the
    pixels that are dimmed, so a dimmed pixel that changes is still back at full brightness within a look."""
    if frame.shape != self.ref.shape or frame.dtype != np.uint8 or not frame.flags.c_contiguous:
      raise ValueError("frame must be a contiguous height x width x 4 byte array")
    if self.look == 0:
      self.ref[...] = frame
      self._last_small[...] = frame[::MOTION_SPACING, ::MOTION_SPACING]
      for channel in range(3):
        self.slow[channel] = frame[..., channel].astype(np.int16) << _Q
      self.look = 1
      self.calls = 0
      self.wake()
      return True
    self.calls += 1
    dirty = self._watch_motion(frame)
    if self.calls % FULL_EVERY:
      return self._quick_look(frame) or dirty
    return self._full_look(frame) or dirty

  def _watch_motion(self, frame: np.ndarray) -> bool:
    """How much of the picture moved since the last look (a sparse grid of pixels is enough), and the veil that
    follows from a frozen picture. Returns True when the veil changed."""
    small = frame[::MOTION_SPACING, ::MOTION_SPACING]
    np.maximum(small, self._last_small, out=self._small_hi)
    np.minimum(small, self._last_small, out=self._small_lo)
    np.subtract(self._small_hi, self._small_lo, out=self._small_hi)
    moved = int(np.count_nonzero(self._pixels(self._small_hi > MOTION_STEP)))
    self._last_small[...] = small
    self.motion += (moved / (self._last_small.shape[0] * self._last_small.shape[1]) - self.motion) * MOTION_SMOOTH
    self.moving = self.motion >= MOVING_ABOVE

    veil = self.veil
    if self.frozen:
      self.frozen = self.motion < FROZEN_ABOVE
    else:
      self.frozen = self.motion < FROZEN_BELOW
    self.frozen_samples = self.frozen_samples + 1 if self.frozen else 0
    if self.frozen:
      self.veil = max(self.veil, min(1.0, max(0.0, (self.frozen_samples - self.veil_start) / self.veil_ramp)))
    else:
      self.veil = max(0.0, self.veil - self.veil_release)
    return round(veil * 255) != round(self.veil * 255)

  def _quick_look(self, frame: np.ndarray) -> bool:
    """Only the dimmed pixels: any that changed go back to full brightness now."""
    where = self._dimmed
    if where.size == 0:
      return False
    now = self._pixels(frame).reshape(-1)[where]
    was = self._pixels(self.ref).reshape(-1)[where]
    one, two = now.view(np.uint8), was.view(np.uint8)
    changed = ((np.maximum(one, two) - np.minimum(one, two)) > TOLERANCE).view(np.uint32) != 0
    if not changed.any():
      return False
    hit = where[changed]
    self._pixels(self.ref).reshape(-1)[hit] = now[changed]
    self.mark.reshape(-1)[hit] = self.moving_looks
    self.level.reshape(-1)[hit] = 0
    self._dimmed = where[~changed]
    self._search_soon()
    return True

  def _search_soon(self) -> None:
    """Something that was dimmed has changed: look for drawn pixels at the short rhythm again."""
    self.search_gap = self.search_min
    self.search_at = min(self.search_at, self.look + self.search_min)

  def _full_look(self, frame: np.ndarray) -> bool:
    self.look += 1
    if self.moving:
      self.moving_looks += 1
    dirty = False
    np.maximum(frame, self.ref, out=self._hi)
    np.minimum(frame, self.ref, out=self._lo)
    np.subtract(self._hi, self._lo, out=self._hi)
    changed = self._pixels(self._hi > TOLERANCE) != 0
    if np.count_nonzero(changed):
      np.putmask(self._pixels(self.ref), changed, self._pixels(frame))
      np.putmask(self.mark, changed, self.moving_looks)
      if self._dimmed.size:
        np.putmask(self.level, changed, 0)
        dimmed = np.flatnonzero(self.level)
        if dimmed.size != self._dimmed.size:
          dirty = True
          self._search_soon()
        self._dimmed = dimmed

    if self.look % AVERAGE_EVERY == 0:
      self._average(frame)
    if self.look >= self.search_at:
      before = self.eligible
      self._search(frame)
      # While the search keeps finding (nearly) the same pixels it runs less and less often.
      differing = int(np.count_nonzero(before != self.eligible))
      same = differing <= max(8, int(np.count_nonzero(self.eligible)) // 50)   # camera noise moves a few pixels each time
      self.search_gap = min(self.search_max, self.search_gap * 2) if same else self.search_min
      self.search_at = self.look + self.search_gap
    return self._update_level(frame) or dirty

  def _surroundings(self, plane: np.ndarray) -> np.ndarray:
    """One color plane of the averaged picture blurred over about 36 px: what the area around each pixel looks like."""
    block, h, w = SURROUND_BLOCK_PX, self.height, self.width
    padded = self._padded
    padded[:h, :w] = plane
    padded[h:, :w] = plane[-1:]
    padded[:, w:] = padded[:, w - 1:w]
    tall = padded[0::block].astype(np.int32)
    for offset in range(1, block):
      tall += padded[offset::block]
    small = tall[:, 0::block].copy()
    for offset in range(1, block):
      small += tall[:, offset::block]
    blurred = (_box_sum(_box_sum(small, SURROUND_BLOCKS, 1), SURROUND_BLOCKS, 0) // self._block_count).astype(np.int16)
    return np.repeat(np.repeat(blurred, block, axis=0), block, axis=1)[:h, :w]

  def _difference(self, first, second) -> np.ndarray:
    """Largest difference over the three colors between two shifted views of the averaged picture."""
    out = np.abs(self.slow[0][first] - self.slow[0][second])
    for channel in (1, 2):
      np.maximum(out, np.abs(self.slow[channel][first] - self.slow[channel][second]), out=out)
    return out

  def _average(self, frame: np.ndarray) -> None:
    """Fold this look into the averaged picture. Moving scenery averages out; what is drawn stays sharp."""
    for channel in range(3):
      plane = self.slow[channel]
      plane += ((frame[..., channel].astype(np.int16) << _Q) - plane) >> SLOW_SHIFT

  def _search(self, frame: np.ndarray) -> None:
    """Find the pixels that look drawn on top of the picture: still pixels at a lasting sharp edge that stand out
    from their surroundings, plus the still pixels of the same color behind them (the inside of a thick stroke)."""
    h, w = self.height, self.width
    stands_out = np.zeros((h, w), np.int16)
    for channel in range(3):
      plane = self.slow[channel]
      np.maximum(stands_out, np.abs(plane - self._surroundings(plane)), out=stands_out)

    edge = np.zeros((h, w), bool)
    strong = self._difference((slice(None), slice(2, None)), (slice(None), slice(None, -2))) > (EDGE_CONTRAST << _Q)
    edge[:, 2:] |= strong
    edge[:, :-2] |= strong
    strong = self._difference((slice(2, None), slice(None)), (slice(None, -2), slice(None))) > (EDGE_CONTRAST << _Q)
    edge[2:] |= strong
    edge[:-2] |= strong
    same_across = np.zeros((h, w), bool)
    same_across[:, :-1] = self._difference((slice(None), slice(1, None)), (slice(None), slice(None, -1))) <= (FILL_SIMILAR << _Q)
    same_down = np.zeros((h, w), bool)
    same_down[:-1] = self._difference((slice(1, None), slice(None)), (slice(None, -1), slice(None))) <= (FILL_SIMILAR << _Q)

    rows = stands_out.copy()
    for shift in (1, 2):
      np.maximum(rows[:, shift:], stands_out[:, :-shift], out=rows[:, shift:])
      np.maximum(rows[:, :-shift], stands_out[:, shift:], out=rows[:, :-shift])
    most = rows.copy()
    for shift in (1, 2):
      np.maximum(most[shift:], rows[:-shift], out=most[shift:])
      np.maximum(most[:-shift], rows[shift:], out=most[:-shift])
    most -= most >> 2
    most += most >> 4   # about 0.8 of the largest nearby

    calm = (self.moving_looks - self.mark) >= self.join_samples
    fillable = calm & (stands_out >= (STAND_OUT_MIN << _Q))
    seeds = edge & fillable & (stands_out >= most)
    pack = self._rows.pack
    allowed = pack(fillable)
    region_rows = self._rows.spread(pack(seeds), FILL_REACH_PX, allowed, pack(same_across), pack(same_down))
    region = self._rows.unpack(region_rows)
    self.eligible = region

    if self.moving:
      # A new still pixel that touches fully dimmed ones of its own color is part of the same thing: when a digit
      # changes, its new strokes light up for a few seconds and then match the rest of the number.
      veteran = (self.moving_looks - self.mark) >= self.full_samples
      if np.any(region & ~veteran):
        alike_across = np.zeros((h, w), bool)
        one, two = frame[:, 1:], frame[:, :-1]
        alike_across[:, :-1] = self._pixels((np.maximum(one, two) - np.minimum(one, two)) > FILL_SIMILAR) == 0
        alike_down = np.zeros((h, w), bool)
        one, two = frame[1:], frame[:-1]
        alike_down[:-1] = self._pixels((np.maximum(one, two) - np.minimum(one, two)) > FILL_SIMILAR) == 0
        reached = self._rows.spread(pack(veteran & region), JOIN_SPREAD_PX, region_rows, pack(alike_across), pack(alike_down))
        np.putmask(self.mark, self._rows.unpack(reached) & ~veteran, self.moving_looks - self.full_samples)

  def _update_level(self, frame: np.ndarray) -> bool:
    """Move each pixel's dimming toward where it should be. Only pixels that are dimmed or may dim are touched."""
    where = np.flatnonzero(self.eligible | (self.level != 0))
    if where.size == 0:
      return False
    color = self._pixels(frame).reshape(-1)[where]
    bright = ((color.view(np.uint8) >= BRIGHT_MIN).view(np.uint32) & np.uint32(0x00FFFFFF)) != 0
    proven = (self.moving_looks - self.start_samples) - self.mark.reshape(-1)[where]
    np.clip(proven, 0, self.ramp_samples, out=proven)
    target = self._ramp[proven]
    target *= bright & self.eligible.reshape(-1)[where]
    before = self.level.reshape(-1)[where]
    level = before.astype(np.int16)
    moved = np.where(target > level, np.minimum(target, level + np.int16(self.rejoin_step)),
                     np.maximum(target, level - np.int16(self.release_step))).astype(np.uint8)
    self._dimmed = where[moved != 0]
    if np.array_equal(moved, before):
      return False
    self.level.reshape(-1)[where] = moved
    return True

  def mask(self, strength: float) -> np.ndarray:
    """Opacity of the black veil for each pixel, 0-255: 255 * strength where fully dimmed.
    Dimming is widened by a pixel and then blurred, so thin strokes keep it in full and its edge is soft."""
    h, w = self.height, self.width
    wide = np.zeros((h + 2, w + 2), np.uint8)
    wide[1:-1, 1:-1] = self.level
    rows = np.maximum(np.maximum(wide[:-2], wide[1:-1]), wide[2:])
    spread = np.maximum(np.maximum(rows[:, :-2], rows[:, 1:-1]), rows[:, 2:])
    tall = np.zeros((h + 2, w + 2), np.uint16)
    tall[1:-1, 1:-1] = spread
    tall[0, 1:-1], tall[-1, 1:-1] = spread[0], spread[-1]
    tall[:, 0], tall[:, -1] = tall[:, 1], tall[:, -2]
    sums = tall[:-2] + tall[1:-1] + tall[2:]
    soft = sums[:, :-2] + sums[:, 1:-1] + sums[:, 2:]
    scale = int(round(max(0.0, min(1.0, strength)) * 256))
    out = ((soft * np.uint32(scale * 7282)) >> np.uint32(24)).astype(np.uint8)   # 7282 / 65536 = 1 / 9
    floor = int(round(self.veil * 255)) * scale >> 8
    if floor:
      np.maximum(out, np.uint8(floor), out=out)
    return out

  def coverage(self) -> float:
    """Share of the screen that is dimmed at all."""
    if self.veil > 0.02:
      return 1.0
    return float(np.count_nonzero(self.level > 5)) / (self.width * self.height)
