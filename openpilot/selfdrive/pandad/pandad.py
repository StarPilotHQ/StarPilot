#!/usr/bin/env python3
# simple pandad wrapper that updates the panda first
import os
import usb1
import time
import signal
import subprocess
from pathlib import Path

from panda import Panda, PandaDFU, PandaProtocolMismatch, McuType, FW_PATH
from openpilot.common.basedir import BASEDIR
from openpilot.common.params import Params
from openpilot.common.hardware import HARDWARE
from openpilot.common.swaglog import cloudlog
from openpilot.starpilot.panda_wake import FirmwareChoice, firmware_path, startup_choice


DEFAULT_FIRMWARE_CHOICE = FirmwareChoice()


def get_expected_signature(mcu_type: McuType, choice: FirmwareChoice = DEFAULT_FIRMWARE_CHOICE) -> bytes:
  fn = firmware_path(FW_PATH, mcu_type.config.app_fn, choice)
  return Panda.get_signature_from_firmware(fn)

def flash_panda(panda_serial: str, choice: FirmwareChoice = DEFAULT_FIRMWARE_CHOICE):
  panda = Panda(panda_serial)
  hardware = panda.get_type()
  # The comma 4 (CUATRO) may run the Tesla wake image; other alternates still need a DOS, TRES or red panda.
  tesla_cuatro = hardware == Panda.HW_TYPE_CUATRO and choice == FirmwareChoice(tesla=True)
  if choice.alternate and hardware not in (Panda.HW_TYPE_DOS, Panda.HW_TYPE_TRES, Panda.HW_TYPE_RED_PANDA) and not tesla_cuatro:
    choice = FirmwareChoice()
  fw_path = firmware_path(FW_PATH, panda.get_mcu_type().config.app_fn, choice)
  fw_signature = get_expected_signature(panda.get_mcu_type(), choice)
  internal_panda = panda.is_internal()

  panda_version = "bootstub" if panda.bootstub else panda.get_version()
  panda_signature = b"" if panda.bootstub else panda.get_signature()
  cloudlog.warning(f"Panda {panda_serial} connected, version: {panda_version}, signature {panda_signature.hex()[:16]}, expected {fw_signature.hex()[:16]}")

  if panda.bootstub or panda_signature != fw_signature:
    cloudlog.info("Panda firmware out of date, update required")
    panda.flash(fn=fw_path)
    cloudlog.info("Done flashing")

  if panda.bootstub:
    bootstub_version = panda.get_version()
    cloudlog.info(f"Flashed firmware not booting, flashing development bootloader. {bootstub_version=}, {internal_panda=}")
    if internal_panda:
      HARDWARE.recover_internal_panda()
    panda.recover(reset=(not internal_panda))
    cloudlog.info("Done flashing bootstub")

  if panda.bootstub:
    cloudlog.info("Panda still not booting, exiting")
    raise AssertionError

  panda_signature = panda.get_signature()
  if panda_signature != fw_signature:
    cloudlog.info("Version mismatch after flashing, exiting")
    raise AssertionError

  if panda.get_packets_versions() != (Panda.HEALTH_PACKET_VERSION, Panda.CAN_PACKET_VERSION):
    panda.close()
    raise PandaProtocolMismatch("Panda CAN or health protocol hash mismatch after firmware validation")

  panda.close()
  return os.path.basename(fw_path)


def firmware_environment(selected_firmware: dict[str, str]) -> dict[str, str]:
  # Bind each flashed board's exact image to its hardware serial, including stock images.
  environment = {key: value for key, value in os.environ.items() if not key.startswith('STARPILOT_PANDA_FIRMWARE')}
  if not selected_firmware:
    raise ValueError('Missing Panda firmware selection')
  for serial, filename in selected_firmware.items():
    if len(serial) != 24 or any(char not in '0123456789abcdefABCDEF' for char in serial):
      raise ValueError('Invalid Panda hardware serial')
    environment['STARPILOT_PANDA_FIRMWARE_' + serial] = filename
  environment['STARPILOT_PANDA_FIRMWARE_MAP'] = '1'
  return environment


def wait_for_internal_panda(timeout: int = 15) -> bool:
  if HARDWARE.get_device_type() != "tici":
    return True
  deadline = time.monotonic() + timeout
  usb = Path("/sys/bus/usb/devices/1-1.2")
  while True:
    try:
      vendor = int((usb / "idVendor").read_text().strip(), 16)
      product = int((usb / "idProduct").read_text().strip(), 16)
      supported = (vendor in Panda.USB_VIDS and product in Panda.USB_PIDS) or (vendor, product) == (0x0483, 0xdf11)
      bus = int((usb / "busnum").read_text())
      device = int((usb / "devnum").read_text())
      if supported and bus > 0 and device > 0 and os.access(f"/dev/bus/usb/{bus:03d}/{device:03d}", os.R_OK | os.W_OK):
        return True
    except (OSError, ValueError):
      pass
    if time.monotonic() >= deadline:
      return False
    time.sleep(0.01)


def main() -> None:
  # signal pandad to close the relay and exit
  def signal_handler(signum, frame):
    cloudlog.info(f"Caught signal {signum}, exiting")
    nonlocal do_exit
    do_exit = True
    if process is not None:
      process.send_signal(signal.SIGINT)

  process = None
  do_exit = False
  signal.signal(signal.SIGINT, signal_handler)

  # check health for lost heartbeat
  try:
    if wait_for_internal_panda(timeout=0):
      for s in Panda.list():
        with Panda(s) as p:
          health = p.health()
          if p.is_internal() and health["heartbeat_lost"]:
            Params().put_bool("PandaHeartbeatLost", True, block=True)
            cloudlog.event("heartbeat lost", deviceState=health)
  except Exception:
    cloudlog.exception("pandad.uncaught_exception")

  count = 0
  while not do_exit:
    try:
      cloudlog.event("pandad.flash_and_connect", count=count)
      if (count % 2) == 0:
        HARDWARE.reset_internal_panda()
      else:
        HARDWARE.recover_internal_panda()
      count += 1
      wait_for_internal_panda()

      # Flash all Pandas in DFU mode
      for serial in PandaDFU.list():
        cloudlog.info(f"Panda in DFU mode found, flashing recovery {serial}")
        PandaDFU(serial).recover()
        time.sleep(1)
        wait_for_internal_panda()

      panda_serials = Panda.list()
      if len(panda_serials):
        cloudlog.info(f"{len(panda_serials)} panda found, connecting - {panda_serials}")
        choice = startup_choice(Params())
        if len(set(panda_serials)) != len(panda_serials):
          raise ValueError('Duplicate Panda hardware serial')
        selected_firmware = {serial: flash_panda(serial, choice) for serial in panda_serials}
        ordering = []
        for serial in panda_serials:
          with Panda(serial) as panda:
            ordering.append((not panda.is_internal(), panda.get_type(), panda.get_usb_serial()))
        panda_serials = [serial for _, _, serial in sorted(ordering)]
        if set(panda_serials) != set(selected_firmware):
          raise ValueError('Panda hardware identity changed after flashing')

        # run real pandad
        os.environ['MANAGER_DAEMON'] = 'pandad'
        process = subprocess.Popen(["./pandad", *panda_serials], cwd=os.path.join(BASEDIR, "openpilot/selfdrive/pandad"),
                                   env=firmware_environment(selected_firmware))
        process.wait()
    # TODO: wrap all panda exceptions in a base panda exception
    except (usb1.USBErrorNoDevice, usb1.USBErrorPipe):
      # a panda was disconnected while setting everything up. let's try again
      cloudlog.exception("Panda USB exception while setting up")
    except PandaProtocolMismatch:
      cloudlog.exception("pandad.protocol_mismatch")
    except Exception:
      cloudlog.exception("pandad.uncaught_exception")


if __name__ == "__main__":
  main()
