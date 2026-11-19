def event_rules(cp) -> dict:
  if cp.brand == "gm":
    from openpilot.starpilot.car.gm.events import event_rules as gm_rules
    return gm_rules(cp)
  if cp.brand == "honda":
    from openpilot.starpilot.car.honda.events import event_rules as honda_rules
    return honda_rules(cp)
  if cp.brand == "leapmotor":
    from openpilot.starpilot.car.leapmotor.events import event_rules as leapmotor_rules
    return leapmotor_rules(cp)
  return {}


def unsupported_gear(cp, gear) -> bool:
  if cp.brand == "gm":
    from openpilot.starpilot.car.gm.events import unsupported_gear as gm_unsupported
    return gm_unsupported(cp, gear)
  return False
