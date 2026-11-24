"""Capture the camera evidence for a parked motion event."""
from openpilot.starpilot.galaxy.camera_snapshot import SnapshotDenied, SnapshotUnavailable


def record_motion_event(store, camera, kind, stamp, permitted, *, simulated=False):
  try:
    results = camera.capture_many(("wide", "cabin"), permitted=permitted, parked=True)
  except SnapshotDenied:
    raise
  except SnapshotUnavailable:
    results = {}
  images = {name: body for name, body in results.items() if isinstance(body, bytes)}
  return store.record(kind, stamp, permitted=permitted, images=images, simulated=simulated)
