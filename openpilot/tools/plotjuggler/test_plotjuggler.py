import os
import glob
import shutil
import signal
import selectors
import subprocess
import time

import unittest

from openpilot.common.test import OpenpilotTestCase
from openpilot.common.basedir import BASEDIR
from openpilot.common.timeout import Timeout, TimeoutException
from openpilot.tools.plotjuggler.juggle import DEMO_ROUTE, install

PJ_DIR = os.path.join(BASEDIR, "openpilot/tools/plotjuggler")

class TestPlotJuggler(OpenpilotTestCase):
  SHARED_DOWNLOAD_CACHE = True  # reuse the demo route across runs; this tests PlotJuggler, not downloading

  @unittest.skipIf(not shutil.which('qmake'), "Qt not installed")
  def test_demo(self):
    install()

    pj = os.path.join(PJ_DIR, "juggle.py")
    with subprocess.Popen(f'QT_QPA_PLATFORM=offscreen {pj} "{DEMO_ROUTE}/:2"',
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, shell=True, start_new_session=True) as p:
      output = ""
      try:
        assert p.stdout is not None
        with selectors.DefaultSelector() as selector:
          selector.register(p.stdout, selectors.EVENT_READ)
          try:
            with Timeout(180):
              while "Done reading Rlog data" not in output.splitlines():
                for key, _ in selector.select(0.1):
                  chunk = os.read(key.fileobj.fileno(), 65536)
                  if chunk:
                    output += chunk.decode("utf-8", errors="replace")
                  else:
                    selector.unregister(key.fileobj)
                if p.poll() is not None:
                  self.fail(f"PlotJuggler exited before startup (status {p.returncode}):\n{output}")
          except TimeoutException:
            self.fail(f"PlotJuggler did not report startup within 180 seconds:\n{output}")
          time.sleep(2)
          for key, _ in selector.select(0):
            chunk = os.read(key.fileobj.fileno(), 65536)
            output += chunk.decode("utf-8", errors="replace")
          self.assertIsNone(p.poll(), f"PlotJuggler exited after startup (status {p.returncode}):\n{output}")
          self.assertNotIn("Raw file read failed", output)
      finally:
        try:
          os.killpg(p.pid, signal.SIGTERM)
        except ProcessLookupError:
          pass
        try:
          p.wait(timeout=5)
        except subprocess.TimeoutExpired:
          pass
        finally:
          try:
            os.killpg(p.pid, signal.SIGKILL)
          except ProcessLookupError:
            pass
          p.wait(timeout=5)

  # TODO: also test that layouts successfully load
  def test_layouts(self, subtests):
    bad_strings = (
      # if a previously loaded file is defined,
      # PJ will throw a warning when loading the layout
      "fileInfo",
      "previouslyLoaded_Datafiles",
    )
    for fn in glob.glob(os.path.join(PJ_DIR, "layouts/*")):
      name = os.path.basename(fn)
      with subtests.test(layout=name):
        with open(fn) as f:
          layout = f.read()
          violations = [s for s in bad_strings if s in layout]
          assert len(violations) == 0, f"These should be stripped out of the layout: {str(violations)}"
