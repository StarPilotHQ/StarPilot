"""Quick Select geometry shared by native input, painting and layout metadata."""

RADIUS = 28
ROW_BODY = (30, 842, 656, 208)
COLUMN_BODY = (30, 378, 208, 672)
ROW_BUTTONS = tuple((126 + index * 184, 858, 176, 176) for index in range(3))
COLUMN_BUTTONS = tuple((46, 394 + index * 184, 176, 176) for index in range(3))


def shelf_geometry(vertical=False):
  return ((COLUMN_BODY, COLUMN_BUTTONS, (30, 946, 208, 104)) if vertical else
          (ROW_BODY, ROW_BUTTONS, (30, 842, 96, 208)))


def intersects(first, second):
  x, y, width, height = first
  left, top, w, h = second
  return x < left + w and left < x + width and y < top + h and top < y + height


def contains(rect, x, y):
  left, top, width, height = rect
  return left <= x < left + width and top <= y < top + height


def rounded_contains(rect, x, y):
  if not contains(rect, x, y):
    return False
  left, top, width, height = rect
  cx = max(left + RADIUS, min(x, left + width - RADIUS))
  cy = max(top + RADIUS, min(y, top + height - RADIUS))
  return (x - cx) ** 2 + (y - cy) ** 2 <= RADIUS ** 2


def shelf_metadata():
  return [{"id": name, "label": label, **dict(zip(("x", "y", "width", "height"), body, strict=True))}
          for name, label, body in (("horizontal", "Quick Select shelf", ROW_BODY),
                                    ("vertical", "Quick Select fallback", COLUMN_BODY))]
