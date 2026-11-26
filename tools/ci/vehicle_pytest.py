import ast
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def split_targets(root, targets):
  unittest_targets, pytest_targets = [], []
  for target in dict.fromkeys(targets):
    path = root / target.split("::", 1)[0]
    tree = ast.parse(path.read_text(), filename=str(path))
    requires_pytest = any(
      isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_") or
      isinstance(node, ast.ClassDef) and node.name.startswith("Test") and (not node.bases or node.decorator_list)
      for node in tree.body)
    (pytest_targets if requires_pytest else unittest_targets).append(target)
  return unittest_targets, pytest_targets


def test_id(nodeid):
  path, *nodes = nodeid.split("::")
  return ".".join((Path(path).with_suffix("").as_posix().replace("/", "."), *nodes))


class Recorder:
  def __init__(self):
    self.nodeids = []
    self.errors = []
    self.records = {}

  def pytest_configure(self, config):
    if hasattr(config, "workerinput") or getattr(config.option, "numprocesses", None) not in (None, 0) or getattr(config.option, "dist", "no") != "no":
      import pytest
      raise pytest.UsageError("vehicle tests require serial execution in the pinned native process")

  def pytest_collection_modifyitems(self, session, config, items):
    selected, imported = [], []
    for item in items:
      owner = item.cls if item.cls is not None else item.obj
      (selected if getattr(owner, "__module__", None) == item.module.__name__ else imported).append(item)
    if imported:
      config.hook.pytest_deselected(items=imported)
    items[:] = selected

  def pytest_collection_finish(self, session):
    self.nodeids = [item.nodeid for item in session.items]

  def pytest_collectreport(self, report):
    if report.failed:
      self.errors.append(str(report.longrepr))

  def pytest_runtest_logreport(self, report):
    identity = test_id(report.nodeid)
    record = self.records.setdefault(identity, {"id": identity, "status": "passed", "detail": "",
                                                "time": 0.0, "stdout": "", "stderr": ""})
    record["time"] += report.duration
    xfail = getattr(report, "wasxfail", None)
    if report.failed:
      status = "xpassed" if report.when == "call" and str(report.longrepr).startswith("[XPASS(strict)]") else "failed" if report.when == "call" else "error"
    elif report.skipped:
      status = "xfailed" if xfail else "skipped"
    elif report.when == "call" and xfail:
      status = "xpassed"
    else:
      status = "passed"
    priorities = {"passed": 0, "skipped": 1, "xfailed": 1, "xpassed": 2, "failed": 3, "error": 4}
    if priorities[status] >= priorities[record["status"]]:
      record["status"] = status
      if status != "passed":
        record["detail"] = str(report.longrepr) if report.longrepr else str(xfail or "")
    for name, content in report.sections:
      key = "stderr" if "stderr" in name else "stdout" if "stdout" in name else None
      if key and content not in record[key]:
        record[key] += content


@contextmanager
def serial_environment():
  values = {"PYTEST_ADDOPTS": "", "PYTEST_PLUGINS": "", "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}
  saved = {key: os.environ.get(key) for key in values}
  os.environ.update(values)
  try:
    yield
  finally:
    for key, value in saved.items():
      if value is None:
        os.environ.pop(key, None)
      else:
        os.environ[key] = value


def invoke(arguments, recorder):
  import pytest
  with serial_environment():
    return pytest.main(["-q", "--rootdir=.", "-o", "addopts=", "-p", "no:xdist", "-p", "no:xdist.plugin", *arguments], plugins=[recorder])


def collect(targets):
  if not targets:
    return [], []
  recorder = Recorder()
  code = invoke(["--collect-only", *targets], recorder)
  errors = list(recorder.errors)
  if code != 0:
    errors.append(f"pytest collection exited {code}")
  ids = [test_id(nodeid) for nodeid in recorder.nodeids]
  if len(set(ids)) != len(ids):
    errors.append("pytest collected duplicate test IDs")
  return recorder.nodeids, errors


def run(nodeids):
  if not nodeids:
    return [], []
  recorder = Recorder()
  code = invoke(nodeids, recorder)
  errors = list(recorder.errors)
  if recorder.nodeids != nodeids:
    errors.append("pytest execution collection changed from the selected IDs")
  if code not in (0, 1):
    errors.append(f"pytest execution exited {code}")
  return list(recorder.records.values()), errors


def run_parallel(nodeids, workers):
  """Whole modules split across separate serial pytest processes."""
  modules = {}
  for nodeid in nodeids:
    modules.setdefault(nodeid.split("::", 1)[0], []).append(nodeid)
  chunks = [[] for _ in range(max(1, workers))]
  for module_nodes in sorted(modules.values(), key=len, reverse=True):
    min(chunks, key=len).extend(module_nodes)
  chunks = [chunk for chunk in chunks if chunk]
  if len(chunks) < 2:
    return run(nodeids)
  records, errors = [], []
  with tempfile.TemporaryDirectory(prefix="vehicle-pytest-") as directory:
    processes = []
    for index, chunk in enumerate(chunks):
      request, response = Path(directory) / f"{index}.json", Path(directory) / f"{index}.out.json"
      request.write_text(json.dumps(chunk))
      processes.append((subprocess.Popen([sys.executable, "-m", "tools.ci.vehicle_pytest", str(request), str(response)]), response))
    for process, response in processes:
      code = process.wait()
      if code != 0 or not response.is_file():
        errors.append(f"pytest worker process exited {code}")
        continue
      result = json.loads(response.read_text())
      records.extend(result["records"])
      errors.extend(result["errors"])
  return records, errors


if __name__ == "__main__":
  selected = json.loads(Path(sys.argv[1]).read_text())
  outcome, failures = run(selected)
  Path(sys.argv[2]).write_text(json.dumps({"records": outcome, "errors": failures}))
