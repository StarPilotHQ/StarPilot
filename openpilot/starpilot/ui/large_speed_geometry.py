"""Speed group geometry shared by rendering, input and layout metadata."""

import math

WIDTH = 488
MAX_ROW_HEIGHT = 132
# The compact display's physical size at 402.5 PPI, enlarged 1.3x: the windshield-mounted screen is
# read at arm's length, and this puts the numbers near 27 arcmin at 70 cm (ISO 15008 recommends 20).
PHYSICAL_SCALE = math.hypot(2160, 1080) / 6 / 300
LARGE_SIGN_SCALE = PHYSICAL_SCALE * 1.3
SIGN_WIDTH, SIGN_HEIGHT = 128, 144
LIMIT_ROW_HEIGHT = math.ceil(SIGN_HEIGHT * LARGE_SIGN_SCALE)
CARD_HEIGHT = LIMIT_ROW_HEIGHT
ACTION_WIDTH = 308
ACTION_HEIGHT = 168
ACTION_X = 112
# Attached just below the card.
ACTION_Y = 75 + CARD_HEIGHT - 2
GAP = 8
SOURCE_ROW_HEIGHT = 76
PADDING = 24

LIMIT_RECTS = {'large': (318, 75, math.ceil(SIGN_WIDTH * LARGE_SIGN_SCALE), LIMIT_ROW_HEIGHT),
               'compact': (320, 16, SIGN_WIDTH, SIGN_HEIGHT)}
ACTION_BOUNDS = {
  'large': ((112, ACTION_Y, 420, ACTION_Y + 80), (112, ACTION_Y + 88, 420, ACTION_Y + ACTION_HEIGHT)),
  'compact': ((146, 44, 330, 102), (146, 106, 330, 164)),
}
ACTION_TEXT_SIZE = {'large': 64, 'compact': 48}

CONFIRMATION_SCALE = {'large': LARGE_SIGN_SCALE, 'compact': 1.0}
# The open prompt's Accept/Reject buttons are sized for fingers, which do not change with viewing distance.
PROMPT_SCALE = {'large': PHYSICAL_SCALE, 'compact': 1.0}
CONFIRMATION_HEIGHT = {'large': 168, 'compact': 152}
CONFIRMATION_HEADER = 40
CONFIRMATION_BUTTON = {'large': 180, 'compact': 150}
CONFIRMATION_GAP = 12
CONFIRMATION_PADDING = 8


def confirmation_bounds(profile, x, y, *, opened=False):
  # Opening keeps the notice's top; the buttons' width stays at finger size.
  scale = CONFIRMATION_SCALE[profile]
  width = 2 * CONFIRMATION_PADDING + 2 * CONFIRMATION_BUTTON[profile] + CONFIRMATION_GAP if opened else 240
  # Large starts where the sign's tap area does, clear of a three-digit SET.
  left = x + 218 if profile == 'large' else x - 172 if opened else x - 100
  top = y - (12 if profile == 'large' else 8)
  # The large notice keeps its original spacing below a number on SET's row; its box is transparent.
  height = CONFIRMATION_HEIGHT[profile] if opened else (184 if profile == 'large' else 159)
  return left, top, left + width * (PROMPT_SCALE if opened else CONFIRMATION_SCALE)[profile], top + height * scale


def confirmation_buttons(profile, bounds):
  left, top, _, bottom = bounds
  scale = PROMPT_SCALE[profile]
  return tuple((left + (CONFIRMATION_PADDING + x) * scale, top + CONFIRMATION_HEADER * CONFIRMATION_SCALE[profile],
                left + (CONFIRMATION_PADDING + x + CONFIRMATION_BUTTON[profile]) * scale, bottom)
               for x in (0, CONFIRMATION_BUTTON[profile] + CONFIRMATION_GAP))


def confirmation_footprint(profile):
  left, top, right, bottom = confirmation_bounds(profile, 0, 0, opened=True)
  # The notice is taller than the open buttons on Compact.
  bottom = max(bottom, confirmation_bounds(profile, 0, 0)[3])
  return {'x': left, 'y': top, 'width': right - left, 'height': bottom - top}


SOURCE_TOP = math.ceil(confirmation_bounds('large', 0, 0, opened=True)[3]) + GAP


def card_height(mode):
  return CARD_HEIGHT if mode == 'split' else LIMIT_ROW_HEIGHT if mode == 'limit_only' else MAX_ROW_HEIGHT


def sources_extent(document):
  """Extra height the cruise widget takes for speed-limit sources: a row in the card, or a small card of its own below."""
  if document.get('speedSources') is not True:
    return 0
  return SOURCE_TOP - CARD_HEIGHT + SOURCE_ROW_HEIGHT + (GAP if document.get('speedSourcesCard') is True else 0)
