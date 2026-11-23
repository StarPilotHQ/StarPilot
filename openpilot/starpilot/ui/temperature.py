from openpilot.starpilot.saved_source import read_saved

TEMPERATURE_UNIT_KEY = "TemperatureGaugeUnit"
FOLLOW_METRIC = "Follow Use Metric"
TEMPERATURE_UNITS = {FOLLOW_METRIC: b"auto", "Fahrenheit": b"f", "Celsius": b"c"}


def read_temperature_metric(params, metric: bool) -> bool:
  raw, readable = read_saved(params, TEMPERATURE_UNIT_KEY, 8)
  return raw == b"c" if readable and raw in (b"c", b"f") else metric


def format_temperature(celsius: float | None, metric: bool) -> str:
  if celsius is None:
    return "--"
  return f"{celsius:.0f}\u00b0C" if metric else f"{celsius * 1.8 + 32:.0f}\u00b0F"
