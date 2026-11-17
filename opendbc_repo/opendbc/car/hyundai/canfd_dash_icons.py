from opendbc.car import DT_CTRL


class CanFDDashIcons:
  def __init__(self):
    self.disengage_frame = 0
    self.disengaging = False
    self.previous_lateral = False

  def update(self, frame, enabled, lateral_active):
    if lateral_active:
      self.disengaging = False
    elif self.previous_lateral:
      self.disengaging = True
    if not self.disengaging:
      self.disengage_frame = frame
    blinking = self.disengaging and (frame - self.disengage_frame) * DT_CTRL < 1.0
    self.previous_lateral = lateral_active
    return 2 if lateral_active else 3 if blinking else 0
