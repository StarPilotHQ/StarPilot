DEFAULT_SLOWDOWN_PERCENT = 50


def valid_slowdown_percent(value: object) -> bool:
  return type(value) is int and 0 <= value <= 100
