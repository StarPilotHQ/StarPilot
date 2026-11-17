import hashlib
import io
import json
from pathlib import Path
import stat
import tempfile
import threading
import time
import unittest
import wave
import zipfile

from openpilot.starpilot.audio.downloads import ORIGIN, SoundDownloadError, SoundDownloads
from openpilot.starpilot.audio.sound_pack import SoundPackLoader, read_wav


def wav(value=1000):
  output = io.BytesIO()
  with wave.open(output, "wb") as sound:
    sound.setnchannels(1)
    sound.setsampwidth(2)
    sound.setframerate(48000)
    sound.writeframes(value.to_bytes(2, "little", signed=True) * 100)
  return output.getvalue()


def archive(files, modes=None):
  output = io.BytesIO()
  with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as target:
    for name, content in files.items():
      info = zipfile.ZipInfo(name)
      info.compress_type = zipfile.ZIP_DEFLATED
      if modes and name in modes:
        info.external_attr = modes[name] << 16
      target.writestr(info, content)
  return output.getvalue()


class Params:
  def __init__(self, path):
    self.path = path

  def get_param_path(self, name):
    return str(self.path)


class SoundDownloadTests(unittest.TestCase):
  def setUp(self):
    self.tmp = tempfile.TemporaryDirectory()
    self.addCleanup(self.tmp.cleanup)
    self.base = Path(self.tmp.name)
    self.root = self.base / "packs"
    self.catalog = self.base / "catalog.json"
    self.parked = [True]
    self.managers = []

  def manager(self, payload, opener=None, slug="duck", digest=None, size=None):
    self.catalog.write_text(json.dumps({"version": 1, "packs": [{
      "id": slug, "name": "Duck", "path": f"theme/Themes/{slug}/sounds.zip",
      "size": len(payload) if size is None else size,
      "sha256": hashlib.sha256(payload).hexdigest() if digest is None else digest,
    }]}))
    manager = SoundDownloads(lambda: self.parked[0], self.root, self.catalog,
                             opener=opener or (lambda url, timeout: io.BytesIO(payload)))
    self.managers.append(manager)
    self.addCleanup(manager.close)
    return manager

  def finish(self, manager):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
      state = manager.snapshot()["job"]["state"]
      if state in ("complete", "cancelled", "failed"):
        return manager.snapshot()
      time.sleep(.005)
    self.fail("download did not finish")

  def test_install_and_loader(self):
    payload = archive({"sounds/": b"", "sounds/engage.wav": wav(2000),
                       "sounds/warning_soft.wav": wav(3000),
                       "__MACOSX/sounds/._engage.wav": b"metadata"})
    def open_url(url, timeout):
      self.assertEqual(url, ORIGIN + "theme/Themes/duck/sounds.zip")
      self.assertGreater(timeout, 0)
      return io.BytesIO(payload)
    manager = self.manager(payload, open_url)
    self.assertEqual(manager.action("download", {"pack": "duck"})["job"]["pack"], "duck")
    snapshot = self.finish(manager)
    self.assertEqual(snapshot["job"]["state"], "complete")
    self.assertTrue(snapshot["packs"][0]["installed"])
    self.assertEqual(len(read_wav(self.root / "duck" / "sounds" / "engage.wav")), 100)
    self.assertFalse((self.root / "duck" / "sounds" / "__MACOSX").exists())
    stock = self.base / "stock"
    stock.mkdir()
    (stock / "engage.wav").write_bytes(wav(1000))
    (stock / "disengage.wav").write_bytes(wav(1000))
    selected = self.base / "SoundPack"
    selected.write_bytes(b"duck")
    sounds = SoundPackLoader(Params(selected), stock, self.root).refresh(("engage.wav", "disengage.wav"), 1)
    assert sounds is not None
    self.assertAlmostEqual(sounds["engage.wav"][0], 2000 / 32768)
    self.assertAlmostEqual(sounds["disengage.wav"][0], 1000 / 32768)

  def test_named_eras_download_select_and_cache_independently(self):
    stock = self.base / "stock"
    stock.mkdir()
    (stock / "engage.wav").write_bytes(wav(500))
    for slug, sample in (("old_engage_sounds", 1000), ("classic_openpilot", 2000)):
      manager = self.manager(archive({"engage.wav": wav(sample)}), slug=slug)
      manager.action("download", {"pack": slug})
      self.assertEqual(self.finish(manager)["job"]["state"], "complete")
    selected = self.base / "SoundPack"
    loader = SoundPackLoader(Params(selected), stock, self.root)
    for now, (slug, sample) in enumerate((("old_engage_sounds", 1000), ("classic_openpilot", 2000), ("old_engage_sounds", 1000))):
      selected.write_text(slug)
      loaded = loader.refresh(("engage.wav",), now)
      assert loaded is not None
      self.assertAlmostEqual(loaded["engage.wav"][0], sample / 32768)
      self.assertIsNone(loader.refresh(("engage.wav",), now + .5))

  def test_truncation_and_checksum_fail_without_publish(self):
    payload = archive({"engage.wav": wav()})
    for case, stream, digest in (("short", payload[:-1], None), ("checksum", payload, "0" * 64)):
      with self.subTest(case=case):
        manager = self.manager(payload, opener=lambda url, timeout, data=stream: io.BytesIO(data), digest=digest)
        manager.action("download", {"pack": "duck"})
        self.assertEqual(self.finish(manager)["job"]["state"], "failed")
        self.assertFalse((self.root / "duck").exists())

  def test_reject_unsafe_and_invalid_zip_members(self):
    cases = (
      archive({"../engage.wav": wav()}),
      archive({"engage.wav": wav()}, {"engage.wav": stat.S_IFLNK | 0o777}),
      archive({"engage.wav": b"not a wav"}),
      archive({"sounds/": b"", "sounds/engage.wav": wav(), "other/engage.wav": wav()}),
    )
    for payload in cases:
      with self.subTest(payload=hashlib.sha256(payload).hexdigest()[:8]):
        manager = self.manager(payload)
        manager.action("download", {"pack": "duck"})
        self.assertEqual(self.finish(manager)["job"]["state"], "failed")
        self.assertFalse((self.root / "duck").exists())

  def test_cancel_and_departure(self):
    payload = archive({"engage.wav": wav()})
    started = threading.Event()
    release = threading.Event()

    class Slow(io.BytesIO):
      def read(self, size=-1):
        started.set()
        release.wait(2)
        return super().read(size)

    manager = self.manager(payload, opener=lambda url, timeout: Slow(payload))
    job = manager.action("download", {"pack": "duck"})["job"]["id"]
    self.assertTrue(started.wait(1))
    manager.action("cancel", {"job": job})
    release.set()
    self.assertEqual(self.finish(manager)["job"]["state"], "cancelled")
    self.assertFalse((self.root / "duck").exists())
    manager = self.manager(payload, opener=lambda url, timeout: Slow(payload))
    release.clear()
    started.clear()
    manager.action("download", {"pack": "duck"})
    self.assertTrue(started.wait(1))
    self.parked[0] = False
    release.set()
    self.assertEqual(self.finish(manager)["job"]["state"], "cancelled")

  def test_conflicts_and_existing_pack_untouched(self):
    payload = archive({"engage.wav": wav()})
    blocker = threading.Event()
    started = threading.Event()

    class Slow(io.BytesIO):
      def read(self, size=-1):
        started.set()
        blocker.wait(2)
        return super().read(size)

    manager = self.manager(payload, opener=lambda url, timeout: Slow(payload))
    manager.action("download", {"pack": "duck"})
    self.assertTrue(started.wait(1))
    with self.assertRaises(SoundDownloadError) as caught:
      manager.action("download", {"pack": "duck"})
    self.assertEqual(caught.exception.status, 409)
    second = self.manager(payload)
    with self.assertRaises(SoundDownloadError) as caught:
      second.action("download", {"pack": "duck"})
    self.assertEqual(caught.exception.status, 409)
    blocker.set()
    self.assertEqual(self.finish(manager)["job"]["state"], "complete")
    original = (self.root / "duck" / "sounds" / "engage.wav").read_bytes()
    with self.assertRaises(SoundDownloadError):
      second.action("download", {"pack": "duck"})
    self.assertEqual((self.root / "duck" / "sounds" / "engage.wav").read_bytes(), original)

  def test_cancel_after_verification_prevents_publish(self):
    payload = archive({"engage.wav": wav()})
    manager = self.manager(payload)
    verified = threading.Event()
    release = threading.Event()
    extract = manager._extract

    def pause_after_extract(source, sounds):
      extract(source, sounds)
      verified.set()
      release.wait(2)

    manager._extract = pause_after_extract
    job = manager.action("download", {"pack": "duck"})["job"]["id"]
    self.assertTrue(verified.wait(1))
    started = time.monotonic()
    manager.action("cancel", {"job": job})
    self.assertLess(time.monotonic() - started, .2)
    release.set()
    self.assertEqual(self.finish(manager)["job"]["state"], "cancelled")
    self.assertFalse((self.root / "duck").exists())

  def test_malformed_catalog_is_unavailable(self):
    for content in ('[]', '{"version":1,"packs":{}}', 'x' * 65537):
      with self.subTest(content=content[:20]):
        self.catalog.write_text(content)
        with self.assertRaises(SoundDownloadError) as caught:
          SoundDownloads(lambda: True, self.root, self.catalog)
        self.assertEqual(caught.exception.status, 503)


if __name__ == "__main__":
  unittest.main()
