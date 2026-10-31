#!/usr/bin/env python3
"""Run isolated, source-pinned safety qualification gates without nested uv setup."""

import argparse
import codecs
from contextlib import ExitStack
import io
import hashlib
import json
import math
import os
from pathlib import Path
import re
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[2]
SAFETY_TESTS = ROOT / "opendbc_repo/opendbc/safety/tests"
SAFETY_SOURCE_ROOTS = (SAFETY_TESTS.parent, ROOT / "opendbc_repo/opendbc/bluepilot_lateral/safety")
MAX_MUTATION_ARTIFACT_BYTES = 256 * 1024 * 1024
SOURCE_PREFIXES = (
  "opendbc_repo/", "panda/", "openpilot/", "msgq_repo/", "rednose_repo/",
  "teleoprtc_repo/", "tinygrad_repo/", "tinygrad_model_runtime/", "jetlink_repo/", "tools/", "scripts/",
)
SOURCE_EXACT = {".github/workflows/safety.yaml", "pyproject.toml", "uv.lock", "SConstruct",
                "panda/SConscript", "panda/SConstruct", "opendbc_repo/SConscript", "opendbc_repo/pyproject.toml",
                "openpilot/cereal/SConscript", "openpilot/common/SConscript", "panda/tests/libpanda/SConscript",
                "panda/tests/misra/coverage_table",
                "opendbc_repo/opendbc/safety/tests/misra/coverage_table"}
SOURCE_SUFFIXES = {".c", ".h", ".cc", ".cpp", ".py", ".sh", ".capnp", ".dbc", ".toml", ".yaml", ".txt", ".json",
                   ".s", ".S", ".ld", ".mk", ".pyx", ".pxd", ".hpp"}
SOURCE_NAMES = {"SConscript", "SConstruct", "Makefile", "coverage_table"}
# These four tracked outputs are declared by the longitudinal MPC SConscript.
# They may regenerate only during the catalog import prerequisite build; their
# generators, templates, schemas, build definitions, and all safety inputs stay pinned.
MPC_PREREQUISITE_OUTPUTS = {
  "openpilot/selfdrive/controls/lib/longitudinal_mpc_lib/acados_ocp_long.json": "long_mpc.py (SConscript generated_long)",
  "openpilot/selfdrive/controls/lib/longitudinal_mpc_lib/c_generated_code/Makefile": "long_mpc.py (SConscript generated_long)",
  "openpilot/selfdrive/controls/lib/longitudinal_mpc_lib/c_generated_code/acados_solver_long.c": "long_mpc.py (SConscript generated_long)",
  "openpilot/selfdrive/controls/lib/longitudinal_mpc_lib/c_generated_code/acados_ocp_solver_pyx.c": "Cython (SConscript libacados_ocp_solver_c)",
}


def prerequisite_transition(before, after, name):
  """Pin real inputs before build and admit only the four declared output rewrites."""
  allowed = MPC_PREREQUISITE_OUTPUTS if name in ("coverage", "mutation-full") else {}
  if before.keys() != after.keys():
    return False
  return all(before[path] == after[path] or path in allowed for path in before)


UNITTEST_ACCOUNTING = """
def test_ids(group):
  for item in group:
    if isinstance(item, unittest.TestSuite):
      yield from test_ids(item)
    else:
      yield item.id()
targets = list(test_ids(suite))
print(f'Safety qualification collected {len(targets)} tests', flush=True)
result = unittest.TextTestRunner(verbosity=2).run(suite)
suppressed = set()
for test, _reason in result.skipped:
  skipped_id = test.id()
  match = re.fullmatch(r'setUp(?:Class|Module) \\(([^)]+)\\)', skipped_id)
  if match:
    matched = {index for index, target in enumerate(targets) if target.startswith(match.group(1) + '.')}
    if not matched:
      raise RuntimeError(f'unmatched skip holder: {skipped_id}')
    suppressed.update(matched)
  elif skipped_id not in targets:
    raise RuntimeError(f'unexpected skip holder: {skipped_id}')
print(f'Safety qualification suppressed {len(suppressed)} methods', flush=True)
sys.exit(0 if result.wasSuccessful() else 1)
"""


def source_identity():
  listed = subprocess.check_output(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT)
  names = (Path(name.decode()) for name in listed.split(b"\0") if name)
  files = sorted(name for name in names if (name.as_posix() in SOURCE_EXACT or
                 (name.as_posix().startswith(SOURCE_PREFIXES) and (name.suffix in SOURCE_SUFFIXES or name.name in SOURCE_NAMES))) and
                 "obj" not in name.parts and "gen" not in name.parts and (ROOT / name).is_file())
  return {path.as_posix(): hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in files}


def prerequisite_artifact_paths(prerequisites, name):
  paths = list(prerequisites)
  if name in ("coverage", "mutation-full"):
    if "msgq_repo/msgq/visionipc/visionipc_pyx.so" not in paths:
      paths.append("msgq_repo/msgq/visionipc/visionipc_pyx.so")
  if name in ("coverage", "mutation-full"):
    generated = "openpilot/selfdrive/controls/lib/longitudinal_mpc_lib/c_generated_code/"
    extension = ".dylib" if sys.platform == "darwin" else ".so"
    paths.append(generated + "libacados_ocp_solver_long" + extension)
    if sys.platform != "darwin":
      paths.extend(generated + lib for lib in ("libacados.so", "libblasfeo.so", "libhpipm.so", "libqpOASES_e.so.3.1"))
  return paths


def prerequisite_artifact_identity(paths):
  return {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in paths}


def generated_identity():
  files = sorted(path for pattern in ("openpilot/cereal/gen/**/*", "opendbc_repo/opendbc/can/*.so",
                                           "panda/tests/libpanda/*.so", "panda/board/obj/**/*.elf")
                 for path in ROOT.glob(pattern) if path.is_file())
  return {path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in files}


def source_head():
  return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def parse_unittest_summary(output):
  matches = list(re.finditer(r"Ran (\d+) tests? in[^\n]*\n\s*\n(OK|FAILED)(?: \([^\n]*\))?", output))
  match = matches[-1] if matches else None
  status = match.group() if match else ""
  skipped = re.search(r"skipped=(\d+)", status)
  return {"ran": int(match.group(1)) if match else None,
          "skipped": int(skipped.group(1)) if skipped else 0,
          "status": match.group(2) if match else "missing",
          "collected": int(found.group(1)) if (found := re.search(r"^Safety qualification collected (\d+) tests$", output, re.MULTILINE)) else None,
          "suppressed": int(found.group(1)) if (found := re.search(r"^Safety qualification suppressed (\d+) methods$", output, re.MULTILINE)) else None}


def complete_unittest(summary):
  return (summary["status"] == "OK" and summary["collected"] is not None and
          summary["suppressed"] is not None and summary["ran"] + summary["suppressed"] == summary["collected"] and
          summary["ran"] > summary["skipped"])


def parse_mutation_summary(output):
  plain = re.sub(r"\x1b\[[0-9;]*m", "", output)
  summary = {"candidates": int(match.group(1)) if (match := re.search(r"Found (\d+) unique candidates", plain)) else None}
  for label in ("total", "killed", "survived", "infra_error", "pruned_build_incompatible"):
    match = re.search(rf"^  {label}: (\d+)$", plain, re.MULTILINE)
    summary[label] = int(match.group(1)) if match else None
  match = re.search(r"^  proven_equivalent: (\d+)$", plain, re.MULTILINE)
  summary["proven_equivalent"] = int(match.group(1)) if match else 0
  return summary


def complete_mutation_artifact(path, summary, *, digest_out=None):
  """A recorded candidate set must agree with the subprocess summary."""
  try:
    flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
      metadata = os.fstat(fd)
      if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_MUTATION_ARTIFACT_BYTES:
        return False
      with os.fdopen(fd, "rb", closefd=False) as stream:
        raw = stream.read(MAX_MUTATION_ARTIFACT_BYTES + 1)
      if len(raw) > MAX_MUTATION_ARTIFACT_BYTES:
        return False
    finally:
      os.close(fd)
    artifact = json.loads(raw)
    results = artifact["results"]
    pruned = artifact["pruned_build_incompatible"]
    equivalent = artifact.get("proven_equivalent", [])
    version = artifact["schema_version"]
    target_sets = artifact["target_sets"]
    sources = artifact["source_sha256"]
    def digest(value):
      return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None
    if (type(version) is not int or version not in (1, 2) or
        (version == 1 and bool(equivalent)) or
        (version == 2 and "proven_equivalent" not in artifact) or
        type(equivalent) is not list or len(equivalent) != summary.get("proven_equivalent", 0) or
        type(artifact["discovered"]) is not int or artifact["discovered"] != summary["candidates"] or
        type(results) is not list or type(pruned) is not list or type(target_sets) is not list or
        len(pruned) != summary["pruned_build_incompatible"] or
        len(results) != summary["total"] or
        not digest(artifact["preprocessed_source_sha256"]) or
        artifact["safety_input_sha256"] != hashlib.sha256((SAFETY_TESTS / "libsafety/safety.c").read_bytes()).hexdigest() or
        artifact["mutation_runner_sha256"] != hashlib.sha256((SAFETY_TESTS / "mutation.py").read_bytes()).hexdigest() or
        type(artifact["baseline_sec"]) not in (int, float) or not math.isfinite(artifact["baseline_sec"]) or artifact["baseline_sec"] < 0 or
        type(sources) is not dict or not sources or
        any(type(source) is not str or not digest(source_hash) for source, source_hash in sources.items()) or
        any(type(item) is not list or not item or any(type(target) is not str or not target for target in item)
            for item in target_sets)):
      return False
    safety_roots = tuple(source_root.resolve() for source_root in SAFETY_SOURCE_ROOTS)
    for source, claimed_hash in sources.items():
      source_path = (ROOT / "opendbc_repo" / source).resolve()
      if not any(source_path.is_relative_to(source_root) for source_root in safety_roots) or not source_path.is_file() or \
         hashlib.sha256(source_path.read_bytes()).hexdigest() != claimed_hash:
        return False
    ids = [item["site_id"] for item in results]
    pruned_ids = [item["site_id"] for item in pruned]
    equivalent_ids = [item["site_id"] for item in equivalent]
    if (any(type(site_id) is not int for site_id in ids + pruned_ids + equivalent_ids) or
        len(ids) != len(set(ids)) or len(pruned_ids) != len(set(pruned_ids)) or
        len(equivalent_ids) != len(set(equivalent_ids)) or set(equivalent_ids) & set(ids + pruned_ids) or
        set(ids) & set(pruned_ids) or len(ids) + len(pruned_ids) + len(equivalent_ids) != artifact["discovered"] or
        set(ids + pruned_ids + equivalent_ids) != set(range(artifact["discovered"]))):
      return False
    for label in ("killed", "survived", "infra_error"):
      if sum(item["outcome"] == label for item in results) != summary[label]:
        return False
    valid = (all(type(item["selected_test_set"]) is int and
                0 <= item["selected_test_set"] < len(target_sets) and
                type(item["selected_test_count"]) is int and
                item["selected_test_count"] == len(target_sets[item["selected_test_set"]]) and
                item["source"] in sources and type(item["line"]) is int and item["line"] > 0 and
                item["outcome"] in ("killed", "survived", "infra_error") and
                (item["outcome"] == "infra_error" or (item["failure_kind"] is None and item["exit_code"] is None)) and
                all(type(item[key]) is str and item[key] for key in ("mutator", "original_op")) and
                type(item["mutated_op"]) is str and
                (bool(item["mutated_op"]) or (item["mutator"] == "remove_negation" and item["original_op"] == "!")) and
                all(type(item[key]) is str for key in ("details", "stderr_tail", "stdout_tail", "unittest_output_tail")) and
                (item["failure_kind"] is None or type(item["failure_kind"]) is str) and
                (item["exit_code"] is None or type(item["exit_code"]) is int)
                for item in results) and
            all(item["source"] in sources and type(item["line"]) is int and item["line"] > 0
                for item in pruned))
    if valid and version == 2:
      # Independent rediscovery binds ID/offset/context/source/compiler/value,
      # including an empty claimed partition. Never reinterpret v1 survivors.
      proof = subprocess.run([sys.executable, str(SAFETY_TESTS / "mutation.py"),
                              "--verify-equivalence-artifact", str(path)],
                             cwd=ROOT / "opendbc_repo", capture_output=True, text=True, timeout=60)
      valid = proof.returncode == 0 and proof.stdout.strip() == hashlib.sha256(raw).hexdigest()
    if valid and digest_out is not None:
      digest_out.append(hashlib.sha256(raw).hexdigest())
    return valid
  except (OSError, ValueError, TypeError, KeyError, IndexError, RecursionError, OverflowError, subprocess.SubprocessError):
    return False


def check_import_origins(python, env, output, *, full_catalog=False):
  sidecar = output.with_suffix(".origins.json")
  sidecar.unlink(missing_ok=True)
  script = """
import json
from pathlib import Path
import importlib.util
import opendbc, openpilot, msgq
import opendbc.can.parser, opendbc.can.packer, opendbc.can.dbc
from openpilot.cereal import log as cereal_log
import openpilot.cereal.messaging
import msgq.ipc_pyx
from opendbc.safety.tests.libsafety import libsafety_py
print(json.dumps({name: str(Path(module.__file__).resolve()) for name, module in
                  [('opendbc', opendbc), ('openpilot', openpilot),
                   ('msgq', msgq), ('libsafety_py', libsafety_py),
                   ('can_parser', opendbc.can.parser), ('can_packer', opendbc.can.packer),
                   ('can_dbc', opendbc.can.dbc), ('cereal_log', cereal_log),
                   ('cereal_messaging', openpilot.cereal.messaging), ('msgq_ipc', msgq.ipc_pyx)]} |
                  {'panda': importlib.util.find_spec('panda').origin}))
"""
  if full_catalog:
    script += """
import msgq.visionipc.visionipc_pyx
print(json.dumps({'msgq_visionipc': str(Path(msgq.visionipc.visionipc_pyx.__file__).resolve())}))
"""
  result = subprocess.run([python, "-c", script], cwd=output.parent, env=env, capture_output=True, text=True)
  output.write_text(result.stdout + result.stderr)
  if result.returncode:
    return False
  try:
    origins = {}
    for line in result.stdout.splitlines():
      origins.update(json.loads(line))
  except json.JSONDecodeError:
    return False
  expected = {"opendbc": ROOT / "opendbc_repo", "panda": ROOT / "panda", "msgq": ROOT / "msgq_repo",
              "openpilot": ROOT / "openpilot", "libsafety_py": ROOT / "opendbc_repo",
              "can_parser": ROOT / "opendbc_repo", "can_packer": ROOT / "opendbc_repo",
              "can_dbc": ROOT / "opendbc_repo", "cereal_log": ROOT / "openpilot/cereal",
              "cereal_messaging": ROOT / "openpilot/cereal", "msgq_ipc": ROOT / "msgq_repo"}
  if full_catalog:
    expected["msgq_visionipc"] = ROOT / "msgq_repo"
  valid = all(isinstance(origins.get(name), str) and Path(origins[name]).is_relative_to(path)
              for name, path in expected.items())
  if valid:
    sidecar.write_text(json.dumps(origins, sort_keys=True) + "\n")
  return valid


def imported_module_hashes(output):
  origins = json.loads(output.with_suffix(".origins.json").read_text())
  return {name: {"path": path, "sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest()}
          for name, path in origins.items()}


class Gate:
  def __init__(self, name, output, python):
    self.name = name
    self.output = output
    self.python = str(python)
    self.env = dict(os.environ)
    self.env.update({"PYTHONPATH": os.pathsep.join(str(ROOT / part) for part in
                                           ("", "opendbc_repo", "msgq_repo", "rednose_repo", "teleoprtc_repo", "tinygrad_repo", "jetlink_repo")),
                     "PARAMS_ROOT": str(output / "params"), "OPENPILOT_PREFIX": f"safety_qual_{os.getpid()}",
                     "PYTHONDONTWRITEBYTECODE": "1", "FUZZ_SEED": "0", "SCALE": "1", "PWD": str(ROOT)})
    for inherited_key in ("HITL", "RELEASE", "CERT", "SKIP_BUILD", "SKIP_TABLES_DIFF",
                          "LIBSAFETY_PREBUILT", "GCOV_PREFIX", "GCOV_PREFIX_STRIP", "CC", "CXX",
                          "CFLAGS", "CXXFLAGS", "LDFLAGS", "LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH"):
      self.env.pop(inherited_key, None)
    self.env.pop("SAFETY_COVERAGE", None)
    if name == "coverage":
      self.env["SAFETY_COVERAGE"] = "1"
    self.env["PATH"] = str(Path(self.python).parent) + os.pathsep + self.env.get("PATH", "")
    (output / "params").mkdir()
    self.commands = []
    self.artifacts = {}

  @staticmethod
  def stream_output(proc, log, timeout):
    assert proc.stdout is not None
    decoder = io.IncrementalNewlineDecoder(
      codecs.getincrementaldecoder(proc.stdout.encoding)(errors=proc.stdout.errors), translate=True)
    chunks = []
    deadline = time.monotonic() + timeout
    timed_out = False

    def emit(text):
      chunks.append(text)
      log.write(text)
      log.flush()
      sys.stdout.write(text)
      sys.stdout.flush()

    try:
      with selectors.DefaultSelector() as selector:
        selector.register(proc.stdout, selectors.EVENT_READ)
        while selector.get_map():
          remaining = deadline - time.monotonic()
          if not timed_out and remaining <= 0:
            os.killpg(proc.pid, signal.SIGKILL)
            timed_out = True
          for key, _ in selector.select(None if timed_out else max(0, remaining)):
            data = os.read(key.fd, 65536)
            emit(decoder.decode(data, final=not data))
            if not data:
              selector.unregister(key.fileobj)
      try:
        proc.wait(timeout=None if timed_out else max(0, deadline - time.monotonic()))
      except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()
        timed_out = True
    except BaseException:
      try:
        os.killpg(proc.pid, signal.SIGKILL)
      except ProcessLookupError:
        pass
      proc.wait()
      raise
    finally:
      proc.stdout.close()
    if timed_out:
      emit("\nTimed out; process group killed\n")
    return (124 if timed_out else proc.returncode), "".join(chunks)

  def run(self, label, argv, *, cwd=ROOT, timeout=3600):
    start = time.monotonic()
    log = self.output / f"{label}.log"
    try:
      with ExitStack() as stack:
        stream_log = (stack.enter_context(log.open("w"))
                      if label == "mutation" and self.name in ("mutation-list", "mutation-full") else None)
        proc = subprocess.Popen(argv, cwd=cwd, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, start_new_session=True)
        if stream_log is not None:
          code, content = self.stream_output(proc, stream_log, timeout)
        else:
          try:
            content, _ = proc.communicate(timeout=timeout)
            code = proc.returncode
          except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            content, _ = proc.communicate()
            code = 124
            content += "\nTimed out; process group killed\n"
    except OSError as exc:
      code, content = 127, f"{type(exc).__name__}: {exc}\n"
    log.write_text(content)
    self.commands.append({"label": label, "argv": [str(arg) for arg in argv], "cwd": str(cwd),
                          "exit_code": code, "seconds": round(time.monotonic() - start, 3), "log": log.name})
    return code, content

  def hash_artifact(self, path):
    path = Path(path)
    if path.is_file():
      self.artifacts[path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else path.name] = hashlib.sha256(path.read_bytes()).hexdigest()

  def cppcheck_dir(self):
    code, output = self.run("cppcheck-location", [self.python, "-c", "import cppcheck; print(cppcheck.DIR)"])
    if code:
      return None
    location = Path(output.strip())
    version, text = self.run("cppcheck-version", [str(location / "cppcheck"), "--version"])
    if version or re.fullmatch(r"Cppcheck 2\.21(?:\.0)?", text.strip()) is None:
      return None
    return location

  def misra(self, panda):
    directory = self.cppcheck_dir()
    if directory is None:
      return False
    misra = directory / "addons/misra.py"
    check_dir = ROOT / ("panda/tests/misra" if panda else "opendbc_repo/opendbc/safety/tests/misra")
    table, text = self.run("misra-table", [self.python, str(misra), "-generate-table"])
    if table or text != (check_dir / "coverage_table").read_text():
      return False
    include = ROOT / ("panda" if panda else "opendbc_repo")
    compiler = "arm-none-eabi-gcc" if panda else "cc"
    status, compiler_include = self.run("compiler-include", [compiler, "-print-file-name=include"])
    if status:
      return False
    common = [str(directory / "cppcheck"), "--inline-suppr", "-I", str(include),
            "-I", compiler_include.strip(), "--suppressions-list=" + str(check_dir / "suppressions.txt"),
            "--error-exitcode=2", "--check-level=exhaustive", "--safety", "--platform=arm32-wchar_t4",
            "--std=c11", "--enable=all", "--addon=misra"]
    if panda:
      common += ["--disable=unusedFunction", "--suppress=*:*inc/*", "--suppress=*:*include/*",
               "-D__GNUC__=9", "-UCMSIS_NVIC_VIRTUAL", "-UCMSIS_VECTAB_VIRTUAL", "-UBOOTSTUB",
               "-DSTM32H7", "-DSTM32H725xx", "-I", str(ROOT / "opendbc_repo"),
               "-I", str(ROOT / "panda/board/stm32h7/inc"),
               str(ROOT / "panda/board/main.c")]
    else:
      common += ["--enable=unusedFunction", "--suppress=missingIncludeSystem", "--suppress=*:*include/*",
               "-D__GNUC__=9", "-D__has_include_next(x)=0",
               str(ROOT / "opendbc_repo/opendbc/safety/tests/misra/main.c")]
    outcomes = {}
    for variant in ("debug", "release"):
      argv = [*common[:-1], "--checkers-report=" + str(self.output / f"checkers-{variant}.txt")]
      if variant == "debug":
        argv.append("-DALLOW_DEBUG")
      argv.append(common[-1])
      code, text = self.run(f"misra-{variant}", argv, timeout=5400)
      outcomes[variant] = code == 0 and re.search(r"misra violation|error|style: ", text) is None
    self.summary = {"variants": outcomes}
    return all(outcomes.values())

  def execute(self):
    if self.name == "coverage":
      version, _ = self.run("gcovr-version", ["gcovr", "--version"])
      if version:
        return False
      build_code = "from opendbc.safety.tests.libsafety import libsafety_py; print(libsafety_py._build_libsafety())"
      built, text = self.run("build-libsafety", [self.python, "-c", build_code])
      if built:
        return False
      native = self.output / "libsafety-debug.so"
      shutil.copy2(Path(text.strip().splitlines()[-1]), native)
      self.hash_artifact(native)
      # The caller supplies a disposable checkout. Only generated coverage files are cleared.
      for path in (SAFETY_TESTS / "libsafety").glob("*.gcda"):
        path.unlink()
      suite_script = """import re, sys, unittest
from opendbc.safety.tests.libsafety import libsafety_py
libsafety_py.load(sys.argv[1])
suite = unittest.TestLoader().discover('.', pattern='test_*.py')
suite.addTests(unittest.TestLoader().discover('../../bluepilot_lateral/safety/tests', pattern='test_*.py', top_level_dir='../../..'))
""" + UNITTEST_ACCOUNTING
      code, text = self.run("unittest", [self.python, "-c", suite_script, str(native)], cwd=SAFETY_TESTS)
      summary = parse_unittest_summary(text)
      self.summary = summary
      pytest_results = self.output / "pytest-coverage-results.json"
      pytest_code, _ = self.run("pytest-coverage", [self.python, str(ROOT / "tools/ci/safety_pytest_coverage.py"),
                                  "--native", str(native), "--results-json", str(pytest_results)], cwd=ROOT)
      try:
        from tools.ci.safety_pytest_coverage import report_complete, targets_for_coverage
        pytest_report = json.loads(pytest_results.read_text())
        pytest_ok = report_complete(pytest_report, targets_for_coverage(), hashlib.sha256(native.read_bytes()).hexdigest())
      except (OSError, ValueError, TypeError, KeyError):
        pytest_report, pytest_ok = {}, False
      self.summary["pytest_coverage"] = {"complete": pytest_ok, "collected": len(pytest_report.get("nodeids", [])),
                                        "ran": len(pytest_report.get("records", []))}
      if pytest_results.is_file():
        self.hash_artifact(pytest_results)
      if sys.platform == "darwin":
        found, llvm_cov = self.run("llvm-cov-path", ["xcrun", "--find", "llvm-cov"])
        if found:
          return False
        gcov_tool = llvm_cov.strip() + " gcov"
      else:
        gcov_tool = "gcov"
      coverage_argv = ["gcovr", "-r", str(SAFETY_TESTS.parent), "--gcov-executable", gcov_tool,
                       "-d", "--fail-under-line=100", "-e", "^libsafety"]
      for source_root in SAFETY_SOURCE_ROOTS:
        coverage_argv += ["--filter", re.escape(source_root.resolve().as_posix() + "/")]
      gcovr, _ = self.run("coverage", coverage_argv, cwd=SAFETY_TESTS)
      return code == 0 and complete_unittest(summary) and pytest_code == 0 and pytest_ok and gcovr == 0
    if self.name in ("opendbc-misra", "panda-misra"):
      if self.name == "panda-misra":
        built, _ = self.run("build-h7", ["scons", "-j4", "panda/board/obj/panda_h7/main.elf",
                                          "panda/board/obj/panda_h7/bootstub.elf"], timeout=3600)
        if built:
          return False
        self.hash_artifact(ROOT / "panda/board/obj/panda_h7/main.elf")
        self.hash_artifact(ROOT / "panda/board/obj/panda_h7/bootstub.elf")
      return self.misra(self.name == "panda-misra")
    if self.name == "panda-host":
      built, _ = self.run("build", ["scons", "-j4", "panda/tests/libpanda/libpanda.so",
                                      "panda/board/obj/panda_h7/main.elf", "panda/board/obj/panda_h7/bootstub.elf"], timeout=3600)
      if built:
        return False
      self.hash_artifact(ROOT / "panda/tests/libpanda/libpanda.so")
      self.hash_artifact(ROOT / "panda/board/obj/panda_h7/main.elf")
      self.hash_artifact(ROOT / "panda/board/obj/panda_h7/bootstub.elf")
      suite_script = """import re, sys, unittest
suite = unittest.TestLoader().discover('panda/tests', pattern='test_*.py')
""" + UNITTEST_ACCOUNTING
      code, text = self.run("unittest", [self.python, "-W", "error", "-c", suite_script], timeout=5400)
      self.summary = parse_unittest_summary(text)
      return code == 0 and complete_unittest(self.summary)
    if self.name in ("mutation-list", "mutation-full"):
      argv = [self.python, str(SAFETY_TESTS / "mutation.py")]
      artifact = self.output / "mutation-results.json"
      if self.name == "mutation-full" and os.path.lexists(artifact):
        self.summary = {"artifact_valid": False, "error": "mutation result path already exists"}
        return False
      argv += ["--list-only"] if self.name == "mutation-list" else ["-j", "4", "--results-json", str(artifact)]
      code, text = self.run("mutation", argv, cwd=ROOT / "opendbc_repo", timeout=21600)
      self.summary = parse_mutation_summary(text)
      if self.name == "mutation-full":
        artifact_digest = []
        artifact_valid = complete_mutation_artifact(artifact, self.summary, digest_out=artifact_digest)
        self.summary["artifact_valid"] = artifact_valid
        if artifact_valid:
          self.artifacts[artifact.name] = artifact_digest[0]
        counts = self.summary
        reconciled = (all(counts[key] is not None for key in ("candidates", "pruned_build_incompatible", "total", "killed", "survived", "infra_error")) and
                      counts["candidates"] - counts["pruned_build_incompatible"] - counts["proven_equivalent"] == counts["total"] ==
                      counts["killed"] + counts["survived"] + counts["infra_error"])
        return code == 0 and reconciled and artifact_valid and counts["survived"] == 0 and counts["infra_error"] == 0
      return code == 0 and self.summary["candidates"] is not None and self.summary["candidates"] > 0
    raise ValueError(self.name)


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--gate", required=True, choices=("coverage", "opendbc-misra", "panda-misra", "panda-host", "mutation-list", "mutation-full"))
  parser.add_argument("--output", required=True, type=Path)
  args = parser.parse_args()
  output = args.output.resolve()
  if output.exists():
    parser.error("Output directory must be new")
  output.mkdir(parents=True)
  python = ROOT / ".venv/bin/python"
  gate = None
  before = None
  after = None
  bootstrap = None
  bootstrap_ok = False
  prerequisite_outputs = {}
  prerequisite_artifacts = {}
  prerequisite_artifacts_after = {}
  head_before = None
  head_after = None
  origins_ok = False
  imported_before = {}
  imported_after = {}
  generated_before = {}
  passed = False
  error = None
  try:
    if not python.is_file():
      raise RuntimeError("root locked environment is missing")
    gate = Gate(args.gate, output, python)
    head_before = source_head()
    before = source_identity()
    (output / "source-before.json").write_text(json.dumps(before, indent=2) + "\n")
    generated_before = generated_identity()
    (output / "generated-before.json").write_text(json.dumps(generated_before, indent=2) + "\n")
    extension = ".dylib" if sys.platform == "darwin" else ".so"
    prerequisite = f"openpilot/common/libparams_c{extension}"
    prerequisites = [prerequisite, "msgq_repo/msgq/ipc_pyx.so"]
    if args.gate in ("coverage", "mutation-full"):
      # The full catalog includes actual Card and SelfdriveD consumers.
      prerequisites.append("msgq_repo/msgq/visionipc/visionipc_pyx.so")
      prerequisites.append("openpilot/selfdrive/controls/lib/longitudinal_mpc_lib/c_generated_code/acados_ocp_solver_pyx.so")
    built, _ = gate.run("build-imports", ["scons", "--minimal", "-j4", *prerequisites], timeout=3600)
    if built:
      raise RuntimeError("native host import prerequisites failed")
    bootstrap = source_identity()
    (output / "source-after-prerequisites.json").write_text(json.dumps(bootstrap, indent=2) + "\n")
    bootstrap_ok = head_before == source_head() and prerequisite_transition(before, bootstrap, args.gate)
    if args.gate in ("coverage", "mutation-full"):
      for path, producer in MPC_PREREQUISITE_OUTPUTS.items():
        if path not in before or path not in bootstrap:
          raise RuntimeError(f"declared MPC prerequisite output missing: {path}")
        prerequisite_outputs[path] = {"producer": producer, "before_sha256": before[path], "built_sha256": bootstrap[path]}
        gate.hash_artifact(ROOT / path)
    (output / "prerequisite-outputs.json").write_text(json.dumps(prerequisite_outputs, indent=2) + "\n")
    if not bootstrap_ok:
      raise RuntimeError("source inputs or HEAD changed during native import prerequisite build")
    bound_artifacts = prerequisite_artifact_paths(prerequisites, args.gate)
    for artifact in bound_artifacts:
      gate.hash_artifact(ROOT / artifact)
    prerequisite_artifacts = prerequisite_artifact_identity(bound_artifacts)
    origins_ok = check_import_origins(gate.python, gate.env, output / "imports.log", full_catalog=args.gate in ("coverage", "mutation-full"))
    if origins_ok:
      imported_before = imported_module_hashes(output / "imports.log")
    version_codes = [gate.run("python-version", [gate.python, "--version"])[0],
                     gate.run("compiler-version", ["cc", "--version"])[0],
                     gate.run("scons-version", ["scons", "--version"])[0]]
    if args.gate in ("panda-misra", "panda-host"):
      version_codes.append(gate.run("arm-compiler-version", ["arm-none-eabi-gcc", "--version"])[0])
    passed = origins_ok and all(code == 0 for code in version_codes) and gate.execute()
  except Exception as exc:
    error = f"{type(exc).__name__}: {exc}"
  try:
    after = source_identity()
    head_after = source_head()
    (output / "source-after.json").write_text(json.dumps(after, indent=2) + "\n")
  except Exception as exc:
    error = f"source finalization failed: {type(exc).__name__}: {exc}"
  try:
    if prerequisite_artifacts:
      prerequisite_artifacts_after = prerequisite_artifact_identity(prerequisite_artifacts)
  except Exception as exc:
    error = f"prerequisite artifact finalization failed: {type(exc).__name__}: {exc}"
  generated = {}
  try:
    generated = generated_identity()
    (output / "generated-inputs.json").write_text(json.dumps(generated, indent=2) + "\n")
  except Exception as exc:
    error = f"generated input finalization failed: {type(exc).__name__}: {exc}"
  try:
    imported_after = {name: {"path": item["path"], "sha256": hashlib.sha256(Path(item["path"]).read_bytes()).hexdigest()}
                      for name, item in imported_before.items()}
  except Exception as exc:
    error = f"imported module finalization failed: {type(exc).__name__}: {exc}"
  report = {"gate": args.gate, "head_before": head_before, "head_after": head_after,
            "source_unchanged": bootstrap_ok and bootstrap is not None and bootstrap == after,
            "prerequisite_inputs_unchanged": bootstrap_ok, "prerequisite_outputs": prerequisite_outputs,
            "prerequisite_artifacts_before": prerequisite_artifacts, "prerequisite_artifacts_after": prerequisite_artifacts_after,
            "prerequisite_artifacts_unchanged": bool(prerequisite_artifacts) and prerequisite_artifacts == prerequisite_artifacts_after,
            "import_origins_ok": origins_ok,
            "commands": gate.commands if gate else [], "artifacts": gate.artifacts if gate else {},
            "generated_before": len(generated_before), "generated_after": len(generated),
            "imported_modules": imported_before,
            "imported_modules_unchanged": imported_before == imported_after,
            "summary": getattr(gate, "summary", {}), "error": error,
            "passed": (passed and error is None and bool(prerequisite_artifacts) and prerequisite_artifacts == prerequisite_artifacts_after and
                       bootstrap_ok and bootstrap is not None and bootstrap == after and head_before == head_after and imported_before == imported_after)}
  (output / "results.json").write_text(json.dumps(report, indent=2) + "\n")
  print(json.dumps({key: report[key] for key in ("gate", "passed", "summary", "source_unchanged", "import_origins_ok")}))
  return int(not report["passed"])


if __name__ == "__main__":
  raise SystemExit(main())
