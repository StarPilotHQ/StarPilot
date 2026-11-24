"""The "3D drive render" setting: which screen draws the rendered road view while driving.

One saved choice with three values, so the car screen (StarPort) and the comma's own screen can never both be
chosen. It is the ``driveRender`` field of the OnroadCustomizations document; an absent field is the default.
The comma's own screen only offers it on the large profile (comma 3 / 3X); elsewhere a saved ``device`` acts as off.
"""

FIELD = "driveRender"
OFF, STARPORT, DEVICE = "off", "starport", "device"
VALUES = (OFF, STARPORT, DEVICE)
DEFAULT = STARPORT

KEY = "DriveRender"
LABEL = "3D drive render"
LABELS = {OFF: "Off", STARPORT: "StarPort", DEVICE: "Comma display"}


def normalize(value) -> str:
  """A saved value, or the default when it is absent or not one of the three choices."""
  return value if type(value) is str and value in VALUES else DEFAULT


def effective(value, large_profile: bool) -> str:
  """The choice this device acts on: ``device`` needs the large profile."""
  value = normalize(value)
  return OFF if value == DEVICE and not large_profile else value


def choices(large_profile: bool) -> tuple[str, ...]:
  return VALUES if large_profile else (OFF, STARPORT)


def description(large_profile: bool) -> str:
  device = " Comma display shows it on the comma 3 or 3X screen and the camera in the car." if large_profile else ""
  return ("Draws a rendered 3D view of the road, lanes and traffic in place of the front camera. " +
          "StarPort shows it on the car screen." + device + " Off shows the front camera on both; " +
          "StarPort's layout can still place a birds-eye panel over it. " +
          "StarPort uses the choice from its next connection.")


def stream_content(value) -> str:
  """What StarPort's car screen draws for a saved choice: the render only when StarPort is chosen, else the camera."""
  return "birds_eye" if normalize(value) == STARPORT else "full"


def read(params) -> str:
  """The saved choice; needs only the read interface (ProjectionParams works)."""
  from openpilot.starpilot.ui.onroad_customization import read_customization
  return normalize(read_customization(params).get(FIELD))
