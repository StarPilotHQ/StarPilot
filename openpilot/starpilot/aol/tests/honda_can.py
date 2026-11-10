"""Configuration codec for real selected Honda CarState parser subscriptions.
Every subscribed source is packed by its own current production DBC. No parser
health, state, native grant, or clock is injected by this codec.
"""

from opendbc.can.packer import CANPacker
from opendbc.car import Bus
from opendbc.car.honda.values import HondaFlags


class HondaCan:
  def __init__(self, ci):
    self.ci = ci
    ci.update([])
    self.packers = {bus: CANPacker(parser.dbc.name) for bus, parser in ci.can_parsers.items()}
    self.gear_msg = ci.CS.gearbox_msg
    self.main_msg = ci.CS.car_state_scm_msg
    self.required = {
      'SCM_BUTTONS': {'CRUISE_BUTTONS', 'CRUISE_SETTING'},
      'ENGINE_DATA': {'XMISSION_SPEED'},
      'WHEEL_SPEEDS': {'WHEEL_SPEED_FL', 'WHEEL_SPEED_FR', 'WHEEL_SPEED_RL', 'WHEEL_SPEED_RR'},
      'POWERTRAIN_DATA': {'ACC_STATUS', 'BRAKE_PRESSED', 'BRAKE_SWITCH', 'PEDAL_GAS'},
      'STEER_STATUS': {'STEER_STATUS', 'STEER_TORQUE_SENSOR'},
      self.gear_msg: {'GEAR_SHIFTER'},
      'SEATBELT_STATUS': {'SEATBELT_DRIVER_LATCHED', 'SEATBELT_DRIVER_LAMP'},
    }
    self.required.setdefault(self.main_msg, set()).add('MAIN_ON')
    pt = ci.can_parsers[Bus.pt]
    for name, fields in self.required.items():
      assert name in pt.dbc.name_to_msg and fields <= pt.dbc.name_to_msg[name].sigs.keys(), (name, fields)
      assert pt.dbc.name_to_msg[name].address in pt.addresses, name
    assert self.gear_msg in ('GEARBOX_AUTO', 'GEARBOX_CVT')
    drive = [value for value, name in ci.CS.shifter_values.items() if name == 'D']
    assert len(drive) == 1, ci.CS.shifter_values
    self.drive = drive[0]
    self.alt_brake = bool(ci.CP.flags & HondaFlags.BOSCH_ALT_BRAKE)
    if self.alt_brake:
      brake_msg = pt.dbc.name_to_msg['BRAKE_MODULE']
      assert (brake_msg.address, brake_msg.size) == (0x1BE, 3)
      assert 'BRAKE_PRESSED' in brake_msg.sigs
      assert brake_msg.address in pt.addresses

  def frames(self, *, cruise_button=0, lkas=0, cruise=0, brake=False, main=True, eps=0, gear=None, gas=0,
             omit=(), speed_kph=45., steering_torque=0):
    intended: dict[str, dict[str, float]] = {
      'SCM_BUTTONS': {'CRUISE_BUTTONS': cruise_button, 'CRUISE_SETTING': lkas},
      'ENGINE_DATA': {'XMISSION_SPEED': speed_kph},
      'WHEEL_SPEEDS': {f'WHEEL_SPEED_{wheel}': speed_kph for wheel in ('FL', 'FR', 'RL', 'RR')},
      'POWERTRAIN_DATA': {
        'ACC_STATUS': cruise,
        'BRAKE_PRESSED': 0 if self.alt_brake else int(brake),
        'BRAKE_SWITCH': 0 if self.alt_brake else int(brake),
        'PEDAL_GAS': gas,
      },
      'STEER_STATUS': {'STEER_STATUS': eps, 'STEER_TORQUE_SENSOR': steering_torque},
      self.gear_msg: {'GEAR_SHIFTER': self.drive if gear is None else gear},
      'SEATBELT_STATUS': {'SEATBELT_DRIVER_LATCHED': 1, 'SEATBELT_DRIVER_LAMP': 0},
      'BRAKE_MODULE': {'BRAKE_PRESSED': int(brake)},
      'ACC_HUD': {'CRUISE_SPEED': 45.0},
      'CRUISE': {'CRUISE_SPEED_PCM': 45.0},
    }
    intended.setdefault(self.main_msg, {})['MAIN_ON'] = int(main)
    frames = []
    for bus, parser in self.ci.can_parsers.items():
      for address in sorted(parser.addresses):
        name = parser.dbc.addr_to_msg[address].name
        if (bus, name) in omit:
          continue
        values = intended.get(name, {}) if bus == Bus.pt else {}
        assert values.keys() <= parser.dbc.name_to_msg[name].sigs.keys(), (name, values)
        frames.append(self.packers[bus].make_can_msg(name, parser.bus, values))
    return frames
