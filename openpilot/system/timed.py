#!/usr/bin/env python3
import datetime
import os
import subprocess
import time
from typing import NoReturn

import openpilot.cereal.messaging as messaging
from openpilot.common.time_helpers import min_date, MAX_DATE, system_time_valid
from openpilot.common.swaglog import cloudlog
from openpilot.common.params import Params
from openpilot.common.gps import get_gps_location_service
from openpilot.common.hardware import PC
from openpilot.common.hardware.hw import Paths

try:
  from zoneinfo import ZoneInfo
except Exception:
  ZoneInfo = None

# Hyundai/Kia CAN-FD cluster wall clock (LOCAL_TIME), local time, ~1 Hz
CAR_CLOCK_ADDR = 0x4F0
CAR_CLOCK_BUS = 1
CAR_CLOCK_MARKER = 0x4A
CAR_CLOCK_DEFAULT_TZ = "America/Chicago"
CAR_CLOCK_OFFSET_PATH = os.path.join(Paths.comma_home(), "car_clock_utc_offset") if PC else "/data/car_clock_utc_offset"
CAR_CLOCK_READ_WINDOW = 1.2
CAR_CLOCK_NO_CAN_WINDOW = 0.3
CAR_CLOCK_LEARN_INTERVAL = 600.
CAR_CLOCK_LEARN_RETRY = 60.
CAR_CLOCK_MAX_DRIFT = datetime.timedelta(minutes=2)
MAX_UTC_OFFSET_MINUTES = 14 * 60


def parse_car_clock(dat: bytes):
  if len(dat) < 7 or dat[0] != CAR_CLOCK_MARKER:
    return None
  try:
    return datetime.datetime(2000 + dat[5], (dat[4] >> 2) & 0x0F, dat[6], dat[1], dat[2], dat[3])
  except ValueError:
    return None


def read_car_clock(can_sock, window=CAR_CLOCK_READ_WINDOW):
  messaging.drain_sock_raw(can_sock)
  start = time.monotonic()
  got_can = False
  while (elapsed := time.monotonic() - start) < window:
    if not got_can and elapsed > CAR_CLOCK_NO_CAN_WINDOW:
      return None
    dat = can_sock.receive()
    if dat is None:
      continue
    got_can = True
    for frame in messaging.log_from_bytes(dat).can:
      if frame.address == CAR_CLOCK_ADDR and frame.src == CAR_CLOCK_BUS:
        local = parse_car_clock(bytes(frame.dat))
        if local is not None:
          return local
  return None


def learn_utc_offset(car_local, utc_now):
  diff = car_local - utc_now
  minutes = round(diff.total_seconds() / 900) * 15
  if abs(minutes) > MAX_UTC_OFFSET_MINUTES:
    return None
  offset = datetime.timedelta(minutes=minutes)
  if abs(diff - offset) > CAR_CLOCK_MAX_DRIFT:
    return None
  return offset


def load_utc_offset():
  try:
    with open(CAR_CLOCK_OFFSET_PATH) as f:
      minutes = int(f.read().strip())
  except (OSError, ValueError):
    return None
  if abs(minutes) > MAX_UTC_OFFSET_MINUTES:
    return None
  return datetime.timedelta(minutes=minutes)


def save_utc_offset(offset):
  tmp = CAR_CLOCK_OFFSET_PATH + ".tmp"
  try:
    with open(tmp, "w") as f:
      f.write(str(int(offset.total_seconds() // 60)))
    os.replace(tmp, CAR_CLOCK_OFFSET_PATH)
  except OSError:
    cloudlog.exception("timed.failed_saving_car_clock_offset")


def car_clock_to_utc(car_local, utc_offset):
  if utc_offset is not None:
    return car_local - utc_offset
  if ZoneInfo is None:
    return None
  try:
    tz = ZoneInfo(CAR_CLOCK_DEFAULT_TZ)
  except Exception:
    cloudlog.exception("timed.bad_timezone")
    return None
  # fold=0: on the repeated DST fall-back hour, assume the first pass
  return car_local.replace(tzinfo=tz, fold=0).astimezone(datetime.UTC).replace(tzinfo=None)


def set_time(new_time):
  diff = datetime.datetime.now(datetime.UTC).replace(tzinfo=None) - new_time
  if abs(diff) < datetime.timedelta(seconds=10):
    cloudlog.debug(f"Time diff too small: {diff}")
    return

  cloudlog.debug(f"Setting time to {new_time}")
  try:
    subprocess.run(f"TZ=UTC date -s '{new_time}'", shell=True, check=True)
  except subprocess.CalledProcessError:
    cloudlog.exception("timed.failed_setting_time")


def clock_message():
  msg = messaging.new_message('clocks')
  msg.valid = system_time_valid()
  mono = time.monotonic_ns()
  boot = time.clock_gettime_ns(time.CLOCK_BOOTTIME)
  if time.monotonic_ns() - mono <= 1_000_000:
    # Retain the existing wire fields for replay's clock pair, independently of wall-clock corrections.
    msg.logMonoTime = mono
    msg.clocks.deprecated.monotonicNanos = mono
    msg.clocks.deprecated.bootTimeNanos = boot
  msg.clocks.wallTimeNanos = time.time_ns()
  return msg


def main() -> NoReturn:
  """
    timed has two responsibilities:
    - getting the current time from GPS, or the car's clock when GPS and NTP are unavailable
    - publishing the time in the logs

    AGNOS will also use NTP to update the time.
  """

  params = Params()
  gps_location_service = get_gps_location_service(params)

  pm = messaging.PubMaster(['clocks'])
  sm = messaging.SubMaster([gps_location_service])
  can_sock = messaging.sub_sock('can', timeout=100)

  utc_offset = load_utc_offset()
  set_from_car_clock = False
  next_offset_learn = 0.

  while True:
    sm.update(1000)

    pm.send('clocks', clock_message())

    gps = sm[gps_location_service]
    gps_time = datetime.datetime.fromtimestamp(gps.unixTimestampMillis / 1000., datetime.UTC).replace(tzinfo=None)
    gps_usable = (sm.updated[gps_location_service] and
                  (time.monotonic() - sm.logMonoTime[gps_location_service] / 1e9) <= 2.0 and
                  gps.hasFix and min_date() <= gps_time <= MAX_DATE)

    if gps_usable:
      set_time(gps_time)
      set_from_car_clock = False
    elif not system_time_valid():
      car_local = read_car_clock(can_sock)
      car_utc = car_clock_to_utc(car_local, utc_offset) if car_local is not None else None
      if car_utc is not None and min_date() <= car_utc <= MAX_DATE:
        cloudlog.warning(f"timed: setting time from car clock: {car_utc} (offset {utc_offset})")
        set_time(car_utc)
        set_from_car_clock = True

    # learn only against GPS/NTP time, never against a clock the car set
    if system_time_valid() and not set_from_car_clock and time.monotonic() >= next_offset_learn:
      car_local = read_car_clock(can_sock)
      next_offset_learn = time.monotonic() + (CAR_CLOCK_LEARN_INTERVAL if car_local is not None else CAR_CLOCK_LEARN_RETRY)
      if car_local is not None:
        offset = learn_utc_offset(car_local, datetime.datetime.now(datetime.UTC).replace(tzinfo=None))
        if offset is not None and offset != utc_offset:
          cloudlog.warning(f"timed: car clock UTC offset {utc_offset} -> {offset}")
          save_utc_offset(offset)
          utc_offset = offset

    if gps_usable:
      time.sleep(10)

if __name__ == "__main__":
  main()
