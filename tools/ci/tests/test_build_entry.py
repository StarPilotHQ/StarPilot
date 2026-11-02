"""The build wrapper's CLI contract, without a container or device."""

import os
import hashlib
import json
from pathlib import Path
import pickle
import shutil
import struct
import subprocess
import sys
import tempfile
import types
import unittest


SOURCE = Path(__file__).resolve().parents[3] / "build"
HELPER = SOURCE.parent / "scripts/laptop_device_build.sh"
VALIDATOR = SOURCE.parent / "tools/laptop_device_build/validate_artifacts.py"
MAPD_HELPER = SOURCE.parent / "scripts/build_mapd_provider.sh"
NATIVE_HELPER = SOURCE.parent / "scripts/native_device_build.sh"


def captured_pickle(backend: str, *, host_backend: str | None = None) -> bytes:
  """A small valid pickle graph with the current TinyJit class references."""
  names = ("tinygrad", "tinygrad.engine", "tinygrad.engine.jit")
  saved = {name: sys.modules.get(name) for name in names}
  try:
    root, engine, jit = (types.ModuleType(name) for name in names)
    root.engine, engine.jit = engine, jit
    sys.modules.update(zip(names, (root, engine, jit), strict=True))
    tiny = type("_TinyJit", (), {"__module__": "tinygrad.engine.jit"})
    captured = type("CapturedJit", (), {"__module__": "tinygrad.engine.jit"})
    jit._TinyJit, jit.CapturedJit = tiny, captured
    run = tiny()
    run.state = captured()
    if host_backend is not None:
      run.state.host_backend = host_backend
    return pickle.dumps({"metadata": {"output_slices": {}, "input_shapes": {}}, "run": run,
                         "input_specs": {}, "output_specs": {}, "device": backend})
  finally:
    for name, module in saved.items():
      if module is None:
        sys.modules.pop(name, None)
      else:
        sys.modules[name] = module


class BuildEntryTest(unittest.TestCase):
  def setUp(self):
    self.tmp = tempfile.TemporaryDirectory()
    self.addCleanup(self.tmp.cleanup)
    self.root = Path(self.tmp.name)
    shutil.copy2(SOURCE, self.root / "build")
    helper = self.root / "scripts/laptop_device_build.sh"
    helper.parent.mkdir()
    helper.write_text("""#!/usr/bin/env bash
printf '%s\\n' "$@" > "$ARGS_FILE"
mkdir -p panda/board/obj
printf changed > panda/board/obj/gitversion.h
printf changed > panda/board/obj/version
exit "${HELPER_EXIT:-0}"
""")
    helper.chmod(0o755)
    native = self.root / "scripts/native_device_build.sh"
    native.write_text('#!/usr/bin/env bash\nprintf "native\\n" > "$ARGS_FILE"\nprintf "%s\\n" "$@" >> "$ARGS_FILE"\n')
    native.chmod(0o755)
    mapd = self.root / "scripts/build_mapd_provider.sh"
    mapd.write_text('#!/usr/bin/env bash\nprintf "mapd\\n" > "$ARGS_FILE"\n')
    mapd.chmod(0o755)
    verifier = self.root / "tools/release/stage_mapd_provider.py"
    verifier.parent.mkdir(parents=True)
    verifier.write_text('import os, sys\nfrom pathlib import Path\n' +
                        'Path("mapd-check").write_text("\\n".join(sys.argv[1:]))\n' +
                        'sys.exit(int(os.getenv("MAPD_CHECK_EXIT", "0")))\n')
    self.env = os.environ.copy()
    self.env["ARGS_FILE"] = str(self.root / "args")

  def run_build(self, *args, helper_exit=0):
    env = dict(self.env, HELPER_EXIT=str(helper_exit))
    return subprocess.run([str(self.root / "build"), *args], cwd=self.root, env=env, check=False)

  def args(self):
    return (self.root / "args").read_text().splitlines()

  def test_shortcuts_and_numeric_jobs(self):
    self.assertEqual(self.run_build("--params", "6").returncode, 0)
    self.assertEqual(self.args(), ["scons", "--no-scrub", "openpilot/common/libparams_c.so", "-j6"])
    self.assertEqual(self.run_build("--cereal").returncode, 0)
    self.assertEqual(self.args(), ["scons", "--no-scrub", "openpilot/cereal/libcereal.a",
                                   "openpilot/cereal/libsocketmaster.a", "openpilot/cereal/messaging/bridge",
                                   "openpilot/cereal/services.h"])
    self.assertEqual(self.run_build("--panda", "8").returncode, 0)
    self.assertEqual(self.args(), ["scons", "--no-scrub", "panda/board/obj/panda.bin.signed", "panda/board/obj/panda_h7.bin.signed",
                                   "panda/board/obj/body_h7.bin.signed", "-j8"])

  def test_default_and_passthrough(self):
    self.assertEqual(self.run_build().returncode, 0)
    self.assertEqual(self.args(), ["build"])
    self.assertEqual(self.run_build("--verbose", "4").returncode, 0)
    self.assertEqual(self.args(), ["build", "4", "--verbose"])
    self.assertEqual(self.run_build("4", "--verbose").returncode, 0)
    self.assertEqual(self.args(), ["build", "4", "--verbose"])
    self.assertEqual(self.run_build("--verbose", "4", "FEATURE=value", "8").returncode, 0)
    self.assertEqual(self.args(), ["build", "8", "--verbose", "FEATURE=value"])
    self.assertEqual(self.run_build("8").returncode, 0)
    self.assertEqual(self.args(), ["build", "8"])

  def test_explicit_scons_jobs_options_keep_their_value(self):
    for option in ("-j", "--jobs"):
      with self.subTest(option=option):
        self.assertEqual(self.run_build("--verbose", option, "4").returncode, 0)
        self.assertEqual(self.args(), ["build", "--verbose", option, "4"])
        self.assertEqual(self.run_build("--panda", option, "4").returncode, 0)
        self.assertEqual(self.args(), ["scons", "--no-scrub", "panda/board/obj/panda.bin.signed",
                                       "panda/board/obj/panda_h7.bin.signed", "panda/board/obj/body_h7.bin.signed", option, "4"])

  def test_zero_jobs_fail_before_package_or_helper_mutation(self):
    (self.root / "prebuilt").write_text("preserve")
    for args in (("0",), ("--verbose", "00"), ("--panda", "0"), ("--params", "0")):
      with self.subTest(args=args):
        self.assertEqual(self.run_build(*args).returncode, 2)
        self.assertFalse((self.root / "args").exists())
        self.assertFalse((self.root / "mapd-check").exists())
        self.assertFalse((self.root / "panda").exists())
        self.assertEqual((self.root / "prebuilt").read_text(), "preserve")

  def test_missing_map_package_stops_full_build_but_allows_repair(self):
    self.env["MAPD_CHECK_EXIT"] = "9"
    self.assertEqual(self.run_build().returncode, 9)
    self.assertFalse((self.root / "args").exists())
    self.assertEqual((self.root / "mapd-check").read_text().splitlines(),
                     ["--source", str(self.root), "--require-tracked"])
    (self.root / "mapd-check").unlink()
    self.assertEqual(self.run_build("--mapd").returncode, 0)
    self.assertEqual(self.args(), ["mapd"])
    self.assertFalse((self.root / "mapd-check").exists())

  def test_firmware_assignments_require_environment_before_helper_dispatch(self):
    for prefix in (("--panda", "4"), ("4",)):
      for assignment in ("RELEASE=1", "CERT=debug", "RELEASE="):
        for release_env in ({}, {"RELEASE": "1", "CERT": "environment key"}):
          with self.subTest(prefix=prefix, assignment=assignment, environment=release_env):
            result = subprocess.run([str(self.root / "build"), *prefix, assignment], cwd=self.root,
                                    env=dict(self.env, **release_env), check=False, capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn("environment variables", result.stderr)
            self.assertIn("RELEASE=1 CERT=/path/to/certificate ./build --panda 4", result.stderr)
            self.assertFalse((self.root / "args").exists())
            self.assertFalse((self.root / "panda").exists())

  def test_environment_and_unrelated_scons_assignments_remain_supported(self):
    helper = self.root / "scripts/laptop_device_build.sh"
    helper.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$RELEASE" "$CERT" "$@" > "$ARGS_FILE"\n')
    result = subprocess.run([str(self.root / "build"), "--panda", "4", "FEATURE=value"], cwd=self.root,
                            env=dict(self.env, RELEASE="1", CERT="certificate with spaces"), check=False)
    self.assertEqual(result.returncode, 0)
    self.assertEqual(self.args(), ["1", "certificate with spaces", "scons", "--no-scrub",
                                  "panda/board/obj/panda.bin.signed", "panda/board/obj/panda_h7.bin.signed", "panda/board/obj/body_h7.bin.signed",
                                  "-j4", "FEATURE=value"])

  def test_mapd_is_explicit_and_cannot_be_mixed_with_scons(self):
    self.assertEqual(self.run_build("--mapd").returncode, 0)
    self.assertEqual(self.args(), ["mapd"])
    (self.root / "args").unlink()
    self.assertEqual(self.run_build("--mapd", "--panda").returncode, 2)
    self.assertFalse((self.root / "args").exists())
    self.assertEqual(self.run_build().returncode, 0)
    self.assertEqual(self.args(), ["build"])

  def test_help_does_not_invoke_builder(self):
    result = subprocess.run([str(self.root / "build"), "--help"], cwd=self.root, env=self.env,
                            check=False, capture_output=True, text=True)
    self.assertEqual(result.returncode, 0)
    self.assertIn("--panda", result.stdout)
    self.assertIn("--mapd", result.stdout)
    self.assertIn("RELEASE=1 CERT=/path/to/certificate ./build --panda 4", result.stdout)
    self.assertFalse((self.root / "args").exists())

  def test_actual_agnos_arm64_dispatch_uses_native_helper(self):
    # Only the temporary copy changes the immutable /AGNOS probe; production
    # has no environment override that can redirect a build to native mode.
    build = self.root / "build"
    build.write_text(build.read_text().replace("-f /AGNOS", f"-f {self.root}/AGNOS"))
    (self.root / "AGNOS").touch()
    fake_uname = self.root / "fake-bin/uname"
    fake_uname.parent.mkdir()
    fake_uname.write_text('#!/usr/bin/env bash\n[[ "$1" == -s ]] && echo Linux || echo aarch64\n')
    fake_uname.chmod(0o755)
    self.env["PATH"] = f"{fake_uname.parent}:{self.env['PATH']}"
    self.assertEqual(self.run_build("--panda", "2").returncode, 0)
    self.assertEqual(self.args(), ["native", "scons", "--no-scrub", "panda/board/obj/panda.bin.signed", "panda/board/obj/panda_h7.bin.signed",
                                   "panda/board/obj/body_h7.bin.signed", "-j2"])
    self.assertEqual(self.run_build().returncode, 0)
    self.assertEqual(self.args(), ["native", "build"])

  def test_non_panda_shortcut_restores_metadata_even_on_failure(self):
    obj = self.root / "panda/board/obj"
    obj.mkdir(parents=True)
    (obj / "gitversion.h").write_text("original")
    self.assertEqual(self.run_build("--params", helper_exit=7).returncode, 7)
    self.assertEqual((obj / "gitversion.h").read_text(), "original")
    self.assertFalse((obj / "version").exists())

  def test_panda_shortcut_retains_generated_metadata(self):
    self.assertEqual(self.run_build("--panda").returncode, 0)
    self.assertEqual((self.root / "panda/board/obj/version").read_text(), "changed")


class NativeDeviceBuildTest(unittest.TestCase):
  def setUp(self):
    self.tmp = tempfile.TemporaryDirectory()
    self.addCleanup(self.tmp.cleanup)
    self.root = Path(self.tmp.name).resolve()
    helper = self.root / "scripts/native_device_build.sh"
    helper.parent.mkdir()
    shutil.copy2(NATIVE_HELPER, helper)
    helper.write_text(helper.read_text().replace("-f /AGNOS", f"-f {self.root}/AGNOS")
                      .replace("/usr/local/venv/bin/scons", f"{self.root}/missing/scons"))
    (self.root / "AGNOS").touch()
    fake_uname = self.root / "fake-bin/uname"
    fake_uname.parent.mkdir()
    fake_uname.write_text('#!/usr/bin/env bash\n[[ "$1" == -s ]] && echo Linux || echo aarch64\n')
    fake_uname.chmod(0o755)
    scons = self.root / ".venv/bin/scons"
    scons.parent.mkdir(parents=True)
    scons.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$@" > "$SCONS_ARGS_FILE"\n' +
                     'printf "%s" "$PYTHONPATH" > "$SCONS_PYTHONPATH_FILE"\nexit "${SCONS_EXIT:-0}"\n')
    scons.chmod(0o755)
    validator = self.root / "tools/laptop_device_build/validate_artifacts.py"
    validator.parent.mkdir(parents=True)
    validator.write_text('import os, sys\nsys.exit(int(os.getenv("VALIDATOR_EXIT", "0")))\n')
    (validator.parent / 'package_model_chunks.py').write_text('')
    (validator.parent / 'validate_params_registry.py').write_text('')
    manifest = self.root / 'openpilot/common/prebuilt_manifest.py'
    manifest.parent.mkdir(parents=True)
    manifest.write_text('')
    self.env = dict(os.environ, PATH=f"{fake_uname.parent}:{os.environ['PATH']}",
                    SCONS_ARGS_FILE=str(self.root / "scons-args"),
                    SCONS_PYTHONPATH_FILE=str(self.root / "scons-pythonpath"))

  def run_helper(self, *args, **extra_env):
    return subprocess.run([str(self.root / "scripts/native_device_build.sh"), *args], cwd=self.root,
                          env=dict(self.env, **extra_env), check=False, capture_output=True, text=True)

  def test_complete_build_validates_before_prebuilt_and_binds_checkout(self):
    self.assertEqual(self.run_helper("build", "2", VALIDATOR_EXIT="1").returncode, 1)
    self.assertFalse((self.root / "prebuilt").exists())
    self.assertEqual((self.root / "scons-args").read_text().splitlines(), ["-j2"])
    self.assertEqual((self.root / "scons-pythonpath").read_text().split(":"),
                     [str(self.root / name) for name in ("", "msgq_repo", "opendbc_repo", "rednose_repo",
                                                          "teleoprtc_repo", "tinygrad_repo")])
    result = self.run_helper("build", "2")
    self.assertEqual(result.returncode, 0, result.stderr)
    self.assertTrue((self.root / "prebuilt").exists())
    self.assertEqual(self.run_helper("build", "2", "--dry-run").returncode, 0)
    self.assertFalse((self.root / "prebuilt").exists())

  def test_zero_jobs_fail_before_scons_or_prebuilt_change(self):
    (self.root / "prebuilt").write_text("preserve")
    self.assertEqual(self.run_helper("build", "0").returncode, 2)
    self.assertFalse((self.root / "scons-args").exists())
    self.assertEqual((self.root / "prebuilt").read_text(), "preserve")

  def test_firmware_assignments_fail_before_scons_or_prebuilt_change(self):
    (self.root / "prebuilt").write_text("preserve")
    for mode in ("build", "scons"):
      for assignment in ("RELEASE=1", "CERT=debug", "RELEASE="):
        with self.subTest(mode=mode, assignment=assignment):
          result = self.run_helper(mode, "4", assignment, RELEASE="1", CERT="environment key")
          self.assertEqual(result.returncode, 2)
          self.assertIn("environment variables", result.stderr)
          self.assertFalse((self.root / "scons-args").exists())
          self.assertEqual((self.root / "prebuilt").read_text(), "preserve")

  def test_unrelated_scons_assignments_are_forwarded(self):
    self.assertEqual(self.run_helper("scons", "FEATURE=value").returncode, 0)
    self.assertEqual((self.root / "scons-args").read_text().splitlines(), ["FEATURE=value"])

  def test_shortcut_never_marks_full_build(self):
    self.assertEqual(self.run_helper("scons", "--no-scrub", "openpilot/common/libparams_c.so").returncode, 0)
    self.assertEqual((self.root / "scons-args").read_text().splitlines(),
                     ["openpilot/common/libparams_c.so"])
    self.assertFalse((self.root / "prebuilt").exists())


class LaptopReleaseForwardingTest(unittest.TestCase):
  def setUp(self):
    self.tmp = tempfile.TemporaryDirectory(prefix="laptop build ")
    self.addCleanup(self.tmp.cleanup)
    self.root = Path(self.tmp.name).resolve()
    helper = self.root / "scripts/laptop_device_build.sh"
    helper.parent.mkdir()
    helper.write_text(HELPER.read_text().replace('\nmain "$@"\n', '\n'))
    self.engine = self.root / "fake engine"
    self.engine.write_text('#!/usr/bin/env bash\nprintf "%s\\0" "$@" > "$CAPTURE_FILE"\n')
    self.engine.chmod(0o755)
    self.capture = self.root / "container args"

  def run_scons(self, **environment):
    env = os.environ.copy()
    env.pop("RELEASE", None)
    env.pop("CERT", None)
    env.update(environment, CAPTURE_FILE=str(self.capture), FAKE_ENGINE=str(self.engine))
    command = ''.join(('source "$1"; detect_engine() { printf "%s" "$FAKE_ENGINE"; }; ',
                      'ensure_image_exists() { :; }; ensure_sysroot_layout() { :; }; ',
                      'scrub_mixed_arch_artifacts() { :; }; ',
                      'run_larch64_scons 4 --no-scrub panda/board/obj/panda_h7.bin.signed ',
                      'panda/board/obj/body_h7.bin.signed FEATURE=value --verbose "--tag=two words"'))
    return subprocess.run(["bash", "-c", command, "bash", str(self.root / "scripts/laptop_device_build.sh")],
                          cwd=self.root, env=env, check=False, capture_output=True, text=True)

  def args(self):
    return [value.decode() for value in self.capture.read_bytes().split(b'\0')[:-1]]

  def test_direct_modes_reject_firmware_assignments_before_build(self):
    (self.root / "prebuilt").write_text("preserve")
    command = ''.join(('source "$1"; shift; ',
                      'run_larch64_build() { touch "$CAPTURE_FILE"; }; ',
                      'run_larch64_scons() { touch "$CAPTURE_FILE"; }; main "$@"'))
    env = dict(os.environ, CAPTURE_FILE=str(self.capture), RELEASE="1", CERT="environment key")
    for mode in ("build", "scons"):
      for assignment in ("RELEASE=1", "CERT=debug", "RELEASE="):
        with self.subTest(mode=mode, assignment=assignment):
          result = subprocess.run(["bash", "-c", command, "bash",
                                   str(self.root / "scripts/laptop_device_build.sh"), mode, "4", assignment],
                                  cwd=self.root, env=env, check=False, capture_output=True, text=True)
          self.assertEqual(result.returncode, 2)
          self.assertIn("environment variables", result.stderr)
          self.assertFalse(self.capture.exists())
          self.assertEqual((self.root / "prebuilt").read_text(), "preserve")

  def test_direct_build_zero_jobs_fail_before_builder_mutation(self):
    (self.root / "prebuilt").write_text("preserve")
    command = 'source "$1"; run_larch64_build() { touch "$CAPTURE_FILE"; }; main build 0'
    result = subprocess.run(["bash", "-c", command, "bash", str(self.root / "scripts/laptop_device_build.sh")],
                            cwd=self.root, env=dict(os.environ, CAPTURE_FILE=str(self.capture)),
                            check=False, capture_output=True, text=True)
    self.assertEqual(result.returncode, 2)
    self.assertIn("jobs must be positive", result.stderr)
    self.assertFalse(self.capture.exists())
    self.assertEqual((self.root / "prebuilt").read_text(), "preserve")

  def test_release_certificate_with_spaces_is_mounted_and_forwarded(self):
    cert = self.root / "certs with spaces/release key"
    cert.parent.mkdir()
    cert.write_text("test certificate")
    result = self.run_scons(RELEASE="1", CERT=str(cert.relative_to(self.root)))
    self.assertEqual(result.returncode, 0, result.stderr)
    args = self.args()
    self.assertIn(f"{cert}:/tmp/starpilot-signing-cert:ro", args)
    self.assertEqual(args[args.index("-e") + 1], "RELEASE=1")
    self.assertEqual(args[args.index("-e") + 3], "CERT=/tmp/starpilot-signing-cert")
    self.assertIn("--tag=two\\ words", args[-1])
    self.assertIn("FEATURE=value", args[-1])

  def test_debug_does_not_inherit_release_and_invalid_release_fails_early(self):
    result = self.run_scons()
    self.assertEqual(result.returncode, 0, result.stderr)
    self.assertNotIn("-e", self.args())
    self.capture.unlink()
    result = self.run_scons(RELEASE="")
    self.assertNotEqual(result.returncode, 0)
    self.assertIn("RELEASE must be nonempty", result.stderr)
    self.assertFalse(self.capture.exists())
    result = self.run_scons(RELEASE="1")
    self.assertNotEqual(result.returncode, 0)
    self.assertIn("RELEASE requires CERT", result.stderr)
    self.assertFalse(self.capture.exists())
    result = self.run_scons(RELEASE="1", CERT="missing key")
    self.assertNotEqual(result.returncode, 0)
    self.assertIn("CERT", result.stderr)
    self.assertFalse(self.capture.exists())
    result = self.run_scons(RELEASE="1", CERT=str(self.root))
    self.assertNotEqual(result.returncode, 0)
    self.assertIn("readable regular file", result.stderr)
    self.assertFalse(self.capture.exists())
    with tempfile.TemporaryDirectory(prefix="external key ") as external:
      outside = Path(external) / "release key"
      outside.write_text("test certificate")
      result = self.run_scons(RELEASE="1", CERT=str(outside))
      self.assertEqual(result.returncode, 0, result.stderr)
      self.assertIn(f"{outside.resolve()}:/tmp/starpilot-signing-cert:ro", self.args())
      self.capture.unlink()
      (self.root / "linked key").symlink_to(outside)
      result = self.run_scons(RELEASE="1", CERT="linked key")
      self.assertEqual(result.returncode, 0, result.stderr)
      self.assertIn(f"{outside.resolve()}:/tmp/starpilot-signing-cert:ro", self.args())


class MapdHelperSafetyTest(unittest.TestCase):
  def setUp(self):
    self.tmp = tempfile.TemporaryDirectory()
    self.addCleanup(self.tmp.cleanup)
    self.root = Path(self.tmp.name).resolve()
    helper = self.root / 'scripts/build_mapd_provider.sh'
    helper.parent.mkdir()
    shutil.copy2(MAPD_HELPER, helper)
    self.helper = helper

  def test_missing_offline_cache_fails_without_creating_package(self):
    result = subprocess.run([str(self.helper)], cwd=self.root, capture_output=True, text=True,
                            env=dict(os.environ, COMMA_MAPD_MODULE_CACHE=str(self.root / 'missing')))
    self.assertEqual(result.returncode, 1)
    self.assertIn('offline Go module cache missing', result.stderr)
    self.assertFalse((self.root / 'openpilot/starpilot/maps/provider').exists())

  def test_external_build_cache_rejected_before_mutation(self):
    modules = self.root / 'modules'
    modules.mkdir()
    with tempfile.TemporaryDirectory() as external:
      outside = Path(external) / 'unsafe-cache'
      result = subprocess.run([str(self.helper)], cwd=self.root, capture_output=True, text=True,
                              env=dict(os.environ, COMMA_MAPD_MODULE_CACHE=str(modules),
                                       COMMA_MAPD_BUILD_CACHE=str(outside)))
      self.assertEqual(result.returncode, 1)
      self.assertIn('must resolve beneath this checkout', result.stderr)
      self.assertFalse(outside.exists())


class ArtifactCheckTest(unittest.TestCase):
  def setUp(self):
    self.tmp = tempfile.TemporaryDirectory()
    self.addCleanup(self.tmp.cleanup)
    self.root = Path(self.tmp.name)
    helper = self.root / "scripts/laptop_device_build.sh"
    helper.parent.mkdir()
    shutil.copy2(HELPER, helper)
    validator = self.root / "tools/laptop_device_build/validate_artifacts.py"
    validator.parent.mkdir(parents=True)
    shutil.copy2(VALIDATOR, validator)
    shutil.copy2(VALIDATOR.with_name("validate_flm_native.py"), validator.with_name("validate_flm_native.py"))
    chunks = self.root / "openpilot/common/file_chunker.py"
    chunks.parent.mkdir(parents=True)
    (self.root / "openpilot/__init__.py").touch()
    shutil.copy2(SOURCE.parent / "openpilot/common/file_chunker.py", chunks)
    shipped = b"verified RDF artifact fixture"
    self.write("openpilot/selfdrive/modeld/models/rdf43_driving_tinygrad.pkl", shipped)
    self.write("openpilot/starpilot/models/catalog.py",
               (f"DEFAULT_SMALL='rdf43'\nDEFAULT_SMALL_SHA256='{hashlib.sha256(shipped).hexdigest()}'\n"
                + f"DEFAULT_SMALL_SIZE={len(shipped)}\n").encode())

    elf = bytearray(128)
    elf[:6] = b"\x7fELF\x02\x01"
    struct.pack_into("<H", elf, 16, 3)
    struct.pack_into("<H", elf, 18, 183)
    struct.pack_into("<I", elf, 20, 1)
    struct.pack_into("<Q", elf, 32, 64)
    struct.pack_into("<HHH", elf, 52, 64, 56, 1)
    struct.pack_into("<II", elf, 64, 1, 5)
    struct.pack_into("<QQ", elf, 72, 120, 0)
    struct.pack_into("<QQ", elf, 96, 8, 8)
    elf[120:] = b"device!!"
    self.elf = bytes(elf)
    obj = bytearray(200)
    obj[:6] = b"\x7fELF\x02\x01"
    struct.pack_into("<H", obj, 16, 1)
    struct.pack_into("<H", obj, 18, 183)
    struct.pack_into("<I", obj, 20, 1)
    struct.pack_into("<Q", obj, 40, 64)
    struct.pack_into("<HHH", obj, 52, 64, 0, 0)
    struct.pack_into("<HH", obj, 58, 64, 2)
    struct.pack_into("<I", obj, 128 + 4, 1)
    struct.pack_into("<Q", obj, 128 + 24, 192)
    struct.pack_into("<Q", obj, 128 + 32, 8)
    obj[192:] = b"objdata!"
    archive_header = (b"file.o/".ljust(16) + b"0".ljust(12) + b"0".ljust(6) + b"0".ljust(6) +
                      b"644".ljust(8) + b"200".ljust(10) + b"`\n")
    archive = b"!<arch>\n" + archive_header + bytes(obj)

    for name in ("openpilot/starpilot/ui/native/_raylib_extra.so", "openpilot/starpilot/flm/_primitive_walk.so",
                 "openpilot/common/libparams_c.so", "openpilot/selfdrive/pandad/pandad",
                 "openpilot/system/loggerd/loggerd", "openpilot/system/loggerd/encoderd",
                 "msgq_repo/msgq/_ipc.so", "msgq_repo/msgq/visionipc/_visionipc.so",
                 "rednose_repo/rednose/helpers/ekf_sym_pyx.so", "openpilot/tools/replay/replay",
                 "openpilot/tools/replay/libmedia_mux.so", "opendbc_repo/opendbc/can/_raw_value.so"):
      self.write(name, self.elf)
    flm_sources = {"_primitive_walk.pyx": "openpilot/starpilot/flm/_primitive_walk.pyx",
                   "native_primitives.py": "openpilot/starpilot/flm/native_primitives.py",
                   "SConscript": "openpilot/starpilot/flm/SConscript", "SConstruct": "SConstruct"}
    for name, path in flm_sources.items():
      self.write(path, f"# synthetic artifact-validation source: {name}\n".encode())
    self.write("openpilot/starpilot/flm/_primitive_walk.binding.json", json.dumps({
      "schema": 1, "revision": "flm-exact-primitives-v1",
      "sources": {name: hashlib.sha256((self.root / path).read_bytes()).hexdigest() for name, path in flm_sources.items()},
      "artifact_sha256": hashlib.sha256(self.elf).hexdigest(),
      "abi": {"implementation": "cpython", "cache_tag": "cpython-312", "python": [3, 12],
              "SOABI": "cpython-312-aarch64-linux-gnu", "machine": "aarch64", "pointer_bits": 64, "platform": "linux"},
    }).encode())
    for name in ("openpilot/cereal/libcereal.a", "openpilot/cereal/libsocketmaster.a"):
      self.write(name, archive)
    for name in ("dmonitoring_model_tinygrad.pkl",):
      code = captured_pickle("QCOM", host_backend="CPU")
      self.write(f"openpilot/selfdrive/modeld/models/{name}", struct.pack("<q", len(code)) + code + b"buffer")
    for kind in ("driving", "dm"):
      for size in ("1344x760", "1928x1208"):
        self.write(f"openpilot/selfdrive/modeld/models/{kind}_warp_{size}_tinygrad.pkl",
                   captured_pickle("QCOM", host_backend="CPU"))

  def write(self, name, data):
    path = self.root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)

  def verify(self, env=None):
    return subprocess.run([str(self.root / "scripts/laptop_device_build.sh"), "verify-artifacts"],
                          cwd=self.root, env=env, check=False, capture_output=True, text=True)

  def test_complete_models_required_before_prebuilt(self):
    result = self.verify()
    self.assertEqual(result.returncode, 0, result.stderr)
    (self.root / "openpilot/selfdrive/modeld/models/dmonitoring_model_tinygrad.pkl").unlink()
    self.assertNotEqual(self.verify().returncode, 0)

  def test_replay_runtime_required_before_prebuilt(self):
    for name in ("openpilot/tools/replay/replay", "openpilot/tools/replay/libmedia_mux.so"):
      with self.subTest(name=name):
        (self.root / name).unlink()
        result = self.verify()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(name, result.stderr)
        self.write(name, self.elf)

  def test_can_decoder_required_and_valid(self):
    name = "opendbc_repo/opendbc/can/_raw_value.so"
    wrong = bytearray(self.elf)
    struct.pack_into("<H", wrong, 18, 62)
    for defect, data in (("missing", None), ("foreign", wrong), ("header_only", self.elf[:20])):
      with self.subTest(defect=defect):
        self.assertEqual(self.verify().returncode, 0)
        if data is None:
          (self.root / name).unlink()
        else:
          self.write(name, data)
        result = self.verify()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(name, result.stderr)
        self.write(name, self.elf)

  def test_raylib_native_binding_required_and_valid(self):
    name = "openpilot/starpilot/ui/native/_raylib_extra.so"
    wrong = bytearray(self.elf)
    struct.pack_into("<H", wrong, 18, 62)
    for defect, data in (("missing", None), ("foreign", wrong), ("header_only", self.elf[:20])):
      with self.subTest(defect=defect):
        self.assertEqual(self.verify().returncode, 0)
        if data is None:
          (self.root / name).unlink()
        else:
          self.write(name, data)
        result = self.verify()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(name, result.stderr)
        self.write(name, self.elf)

  def test_flm_native_binding_required_and_valid(self):
    names = ("_primitive_walk.binding.json", "_primitive_walk.so", "_primitive_walk.pyx", "native_primitives.py", "SConscript")
    for name in names:
      path = self.root / "openpilot/starpilot/flm" / name
      original = path.read_bytes()
      for defect in ("missing", "changed"):
        with self.subTest(name=name, defect=defect):
          self.assertEqual(self.verify().returncode, 0)
          if defect == "missing":
            path.unlink()
          elif name.endswith(".json"):
            receipt = json.loads(original)
            receipt["abi"]["machine"] = "x86_64"
            path.write_text(json.dumps(receipt))
          else:
            path.write_bytes(original + b"changed")
          result = self.verify()
          self.assertNotEqual(result.returncode, 0)
          self.assertIn("Device artifact validation failed", result.stderr)
          path.write_bytes(original)

  def test_shipped_driving_model_required_and_pinned(self):
    path = self.root / "openpilot/selfdrive/modeld/models/rdf43_driving_tinygrad.pkl"
    path.write_bytes(b"x" * path.stat().st_size)
    self.assertNotEqual(self.verify().returncode, 0)
    path.unlink()
    self.assertNotEqual(self.verify().returncode, 0)

  def test_foreign_elf_rejected(self):
    wrong = bytearray(self.elf)
    struct.pack_into("<H", wrong, 18, 62)
    self.write("openpilot/selfdrive/pandad/pandad", wrong)
    self.assertNotEqual(self.verify().returncode, 0)

  def test_header_only_elf_rejected(self):
    self.write("openpilot/selfdrive/pandad/pandad", self.elf[:20])
    self.assertNotEqual(self.verify().returncode, 0)

  def test_archive_object_section_cannot_extend_past_member(self):
    path = self.root / "openpilot/cereal/libcereal.a"
    archive = bytearray(path.read_bytes())
    # Global ar header + member header + second ELF section's file offset.
    struct.pack_into("<Q", archive, 8 + 60 + 128 + 24, 10_000)
    path.write_bytes(archive)
    self.assertNotEqual(self.verify().returncode, 0)

  def test_metal_backend_rejected(self):
    self.write("openpilot/selfdrive/modeld/models/driving_warp_1344x760_tinygrad.pkl", captured_pickle("METAL"))
    self.assertNotEqual(self.verify().returncode, 0)

  def test_mixed_foreign_gpu_backend_rejected(self):
    path = "openpilot/selfdrive/modeld/models/driving_warp_1344x760_tinygrad.pkl"
    for foreign in ("METAL", "METAL:0", "AMD", "USB+AMD:LLVM"):
      with self.subTest(foreign=foreign):
        self.write(path, captured_pickle("QCOM", host_backend=foreign))
        self.assertNotEqual(self.verify().returncode, 0)

  def test_cpu_only_capture_rejected(self):
    self.write("openpilot/selfdrive/modeld/models/driving_warp_1344x760_tinygrad.pkl", captured_pickle("CPU"))
    self.assertNotEqual(self.verify().returncode, 0)

  def test_extra_unknown_warp_rejected(self):
    self.write("openpilot/selfdrive/modeld/models/driving_warp_999x999_tinygrad.pkl", captured_pickle("QCOM"))
    self.assertNotEqual(self.verify().returncode, 0)

  def test_arbitrary_qcom_string_rejected(self):
    self.write("openpilot/selfdrive/modeld/models/driving_warp_1344x760_tinygrad.pkl", pickle.dumps("QCOM"))
    self.assertNotEqual(self.verify().returncode, 0)

  def test_mismatched_checkout_mount_rejected(self):
    with tempfile.TemporaryDirectory() as other:
      result = self.verify(dict(os.environ, COMMA_HOST_ROOT_DIR=other))
    self.assertNotEqual(result.returncode, 0)
    self.assertIn("COMMA_HOST_ROOT_DIR", result.stderr)

  def test_symlinked_external_sysroot_rejected(self):
    with tempfile.TemporaryDirectory() as other:
      (self.root / ".comma_sysroot").symlink_to(other, target_is_directory=True)
      result = self.verify()
    self.assertNotEqual(result.returncode, 0)
    self.assertIn("resolves outside", result.stderr)

  def test_relative_mount_overrides_are_normalized(self):
    script = (self.root / "scripts/laptop_device_build.sh").read_text().replace('\nmain "$@"\n', '\n')
    source_only = self.root / "scripts/source_only.sh"
    source_only.write_text(script)
    result = subprocess.run(["bash", "-c", 'source "$1"; printf "%s\\n%s\\n" "$SYSROOT_DIR" "$HOST_CACHE_DIR"',
                             "bash", str(source_only)], cwd=self.root,
                            env=dict(os.environ, COMMA_SYSROOT_DIR=".comma_sysroot", COMMA_HOST_CACHE_DIR=".cache"),
                            check=False, capture_output=True, text=True)
    self.assertEqual(result.returncode, 0, result.stderr)
    self.assertEqual(result.stdout.splitlines(), [str(self.root.resolve() / ".comma_sysroot"),
                                                  str(self.root.resolve() / ".cache")])

  def test_only_plain_default_build_sets_prebuilt(self):
    script = (self.root / "scripts/laptop_device_build.sh").read_text().replace('\nmain "$@"\n', '\n')
    source_only = self.root / "scripts/source_only.sh"
    source_only.write_text(script)
    command = " ".join([
      'source "$1";',
      'run_larch64_scons() { :; }; verify_device_artifacts() { :; }; python3() { :; }; detect_engine() { echo true; };',
      'run_larch64_build 4 -n; [[ ! -e "$ROOT_DIR/prebuilt" ]] || exit 21;',
      'run_larch64_build 4 openpilot/common/libparams_c.so; [[ ! -e "$ROOT_DIR/prebuilt" ]] || exit 22;',
      'run_larch64_build 4; [[ -e "$ROOT_DIR/prebuilt" ]] || exit 23',
    ])
    result = subprocess.run(["bash", "-c", command, "bash", str(source_only)], cwd=self.root,
                            check=False, capture_output=True, text=True)
    self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
  unittest.main()

