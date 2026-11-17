import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

import msgq._ipc


@pytest.fixture(scope="module")
def receive_gil_probe(tmp_path_factory):
  if not sys.platform.startswith("linux"):
    pytest.skip("LD_PRELOAD probe requires Linux")
  directory = tmp_path_factory.mktemp("receive-gil")
  source = Path(__file__).with_name("receive_gil_probe.cc")
  library = directory / "probe.so"
  compiler = shutil.which("c++")
  assert compiler is not None, "Native messaging tests require a C++ compiler"
  subprocess.run([compiler, "-std=c++17", "-shared", "-fPIC", str(source),
                  "-I", str(source.parents[2]), "-ldl", "-o", str(library)], check=True, timeout=30)
  return library


@pytest.mark.parametrize(("non_blocking", "fake", "expected"), [(True, False, "1"), (False, False, "0"), (True, True, "0")])
def test_receive_only_releases_gil_when_it_can_wait(receive_gil_probe, tmp_path, non_blocking, fake, expected):
  result = tmp_path / "gil"
  preload = ":".join(filter(None, (os.environ.get("LD_PRELOAD"), str(receive_gil_probe))))
  environment = dict(os.environ, LD_PRELOAD=preload, MSGQ_GIL_LIBRARY=msgq._ipc.__file__,
                     MSGQ_GIL_RESULT=str(result))
  environment.pop("CEREAL_FAKE", None)
  if fake:
    environment["CEREAL_FAKE"] = "1"
  code = f"""
import msgq
from openpilot.common.prefix import OpenpilotPrefix
with OpenpilotPrefix():
  sock = msgq.sub_sock('test_receive_gil', timeout=1)
  assert sock.receive(non_blocking={non_blocking!r}) is None
"""
  subprocess.run([sys.executable, "-c", code], env=environment, check=True, timeout=10)
  observations = result.read_text()
  assert observations and set(observations) == {expected}
