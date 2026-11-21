import pytest

from opendbc.can import CANPacker
from opendbc.car import Bus, structs
from opendbc.car.hyundai import hyundaican
from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai.tests.test_fca11_legacy import params
from opendbc.car.hyundai.values import DBC


def packet(commands, address):
  return next(frame for frame in commands if frame[0] == address)


def comfort_bounds(data):
  bits = int.from_bytes(data, 'little')
  return ((bits >> 6) & 63) * .0986 - 4.14, (bits & 63) * .0986 - 4.14


def commands(cp, accel, index=0, *, legacy=True, enabled=True, override=False, stopping=False):
  return hyundaican.create_acc_commands(CANPacker(DBC[cp.carFingerprint][Bus.pt]), enabled, accel, 3., index,
                                      structs.CarControl.HUDControl(), 50., stopping, override, True, cp,
                                      legacy_fca11=True, legacy_scc14=legacy)


@pytest.mark.parametrize('accel', [-3.5, -2.51, -1.5, -.5, 0., .5, 2.])
def test_actual_controller_legacy_comfort_bounds_contain_requested_acceleration(accel):
  ci = CarInterface(params())
  ci.update([])
  cc = structs.CarControl(enabled=True, longActive=True)
  cc.actuators.longControlState = structs.CarControl.Actuators.LongControlState.pid
  cc.actuators.accel = accel
  _, messages = ci.CC.update(cc.as_reader(), ci.CS, 0)
  _, data, bus = packet(messages, 0x389)
  lower, upper = comfort_bounds(data)
  assert bus == 0
  assert lower <= accel <= upper
  assert upper - lower <= .0986 + 1e-9
  assert (int.from_bytes(data, 'little') >> 12) & 127 == 3


@pytest.mark.parametrize('accel,expected', [(0., '6a3a900101000000'), (-1.5, '9b36900101000000'),
                                            (-2.4, '5234900101000000')])
def test_legacy_scc14_golden_packets(accel, expected):
  assert packet(commands(params(), accel), 0x389)[1].hex() == expected


@pytest.mark.parametrize('raw,lower,upper', [('aafa010001000000', .0012, .0012),
                                          ('22370b0001000005', -1.3792, -.7876),
                                          ('db85270001000005', -1.8722, -1.4778),
                                          ('770d140001000000', 1.0858, 1.2830)])
def test_factory_legacy_absolute_acceleration_vectors(raw, lower, upper):
  actual = comfort_bounds(bytes.fromhex(raw))
  assert actual == pytest.approx((lower, upper))


def test_unknown_firmware_keeps_current_scc14_encoding():
  cp = params()
  cp.carFw = []
  ci = CarInterface(cp)
  ci.update([])
  cc = structs.CarControl(enabled=True, longActive=True)
  cc.actuators.longControlState = structs.CarControl.Actuators.LongControlState.pid
  cc.actuators.accel = -1.5
  _, messages = ci.CC.update(cc.as_reader(), ci.CS, 0)
  data = packet(messages, 0x389)[1]
  assert int.from_bytes(data, 'little') & 4095 == 0
  assert (int.from_bytes(data, 'little') >> 12) & 127 == 30


def test_legacy_scc14_does_not_change_acceleration_aeb_or_other_packets():
  cp = params()
  mutable = (1 << 19) - 1
  for accel in (-3.5, -2.4, -1., -.5, 0., .5, 2.):
    for enabled, override, stopping in ((True, False, False), (True, True, False),
                                        (True, False, True), (False, False, False)):
      for index in (0, 1, 14, 15):
        before = commands(cp, accel, index, legacy=False, enabled=enabled, override=override, stopping=stopping)
        after = commands(cp, accel, index, enabled=enabled, override=override, stopping=stopping)
        for old, new in zip(before, after, strict=True):
          if old[0] == 0x389:
            assert old[0] == new[0] and old[2] == new[2]
            assert int.from_bytes(old[1], 'little') & ~mutable == int.from_bytes(new[1], 'little') & ~mutable
          else:
            assert old == new


def test_legacy_scc14_keeps_native_acceleration_and_aeb_checks():
  from opendbc.safety.tests.libsafety import libsafety_py
  from opendbc.safety.tests.libsafety.libsafety_py import make_CANPacket

  cp = params()
  config = cp.safetyConfigs[-1]
  native = libsafety_py.libsafety
  for allowed in (False, True):
    for accel in (-4., -3.5, -1., 0., 2., 2.5, 3.5, 4.):
      native.set_safety_hooks(config.safetyModel.raw, config.safetyParam)
      native.init_tests()
      native.set_controls_allowed(allowed)
      before, after = commands(cp, accel, legacy=False), commands(cp, accel)
      for old, new in zip(before, after, strict=True):
        verdict = native.safety_tx_hook(make_CANPacket(old[0], old[2], old[1]))
        assert native.safety_tx_hook(make_CANPacket(new[0], new[2], new[1])) == verdict
      if accel < -3.5 or accel > 3.5:
        address, data, bus = packet(after, 0x421)
        assert not native.safety_tx_hook(make_CANPacket(address, bus, data))


def test_legacy_bounds_leave_the_existing_acceleration_interval_unchanged():
  cp = params()
  for hundredths in range(-350, 201):
    frames = commands(cp, hundredths / 100.)
    bits = int.from_bytes(packet(frames, 0x421)[1], 'little')
    maximum = ((bits >> 24) & 2047) * .01 - 10.23
    minimum = ((bits >> 37) & 2047) * .01 - 10.23
    lower, upper = comfort_bounds(packet(frames, 0x389)[1])
    assert maximum == minimum
    assert max(lower, minimum) == minimum
    assert min(upper, maximum) == maximum
    assert -3.5 - 1e-9 <= minimum <= maximum <= 2. + 1e-9
