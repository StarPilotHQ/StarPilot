import sys
import time
from pathlib import Path

from openpilot.common.file_chunker import materialize_file_chunked
from openpilot.common.hardware import AGNOS
from openpilot.common.hardware.usb import CHESTNUT_USB_PRODUCT, USB_DEVICES_PATH, is_chestnut_usb_id

MODELS_DIR = Path(__file__).resolve().parent / 'models'


def modeld_pkl_path(chestnut: bool):
  prefix = 'big_' if chestnut else ''
  path = MODELS_DIR / f'{prefix}driving_tinygrad.pkl'
  return materialize_file_chunked(path) if path.is_file() or Path(f'{path}.chunkmanifest').exists() else path

def load_oob(path, chestnut=False):
  from openpilot.starpilot.models.runtime_bridge.runtime import selected_legacy, load_oob as protected_load
  if AGNOS:
    if not selected_legacy():
      raise RuntimeError('Prepare the model runtime before loading a device artifact')
    return protected_load(materialize_file_chunked(path), chestnut)
  from tinygrad import Context
  device = 'USB+AMD:LLVM' if chestnut else 'QCOM' if AGNOS else 'METAL' if sys.platform == 'darwin' else 'CPU:LLVM'
  with Context(DEV=device):
    from tinygrad_repo.examples.openpilot.helpers import load_pickle
    return load_pickle(materialize_file_chunked(path), out_of_band=True)

def chestnut_present() -> bool:
  for d in USB_DEVICES_PATH.glob("*"):
    try:
      usb_id = (int((d / "idVendor").read_text(), 16), int((d / "idProduct").read_text(), 16))
      product = (d / "product").read_text().strip()
      if is_chestnut_usb_id(*usb_id) and product == CHESTNUT_USB_PRODUCT:
        return True
    except Exception:
      pass
  return False

def chestnut_compiled() -> bool:
  path = modeld_pkl_path(chestnut=True)
  # the big model is only built for releases, in a dev checkout it's a git LFS pointer
  return path.is_file() and path.stat().st_size > 1024 and all(
    (MODELS_DIR / f'big_driving_warp_{size}_tinygrad.pkl').is_file() for size in ('1344x760', '1928x1208'))

def wait_for_chestnut(timeout: float = 10.) -> None:
  start = time.monotonic()
  while not chestnut_present():
    if time.monotonic() - start > timeout:
      raise TimeoutError("chestnut did not enumerate")
    time.sleep(0.1)
