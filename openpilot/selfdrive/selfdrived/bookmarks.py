"""Per-drive bookmark feedback, shared by every bookmark source and display."""
from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.selfdrived.events import ET, EventName, NormalPermanentAlert


class BookmarkCounter:
  def __init__(self, checkpoint=None):
    self.checkpoint = checkpoint
    self.count = 0
    self.drive_id = None

  def update(self, *, started: bool, drive_id: int, bookmarks: int) -> bool:
    if not started:
      if self.drive_id is not None:
        self._remove_saved()
      self.count = 0
      self.drive_id = None
      return False
    if drive_id != self.drive_id:
      self.count = self._restore(drive_id)
      self.drive_id = drive_id
    self.count += bookmarks
    if bookmarks and self.checkpoint is not None:
      try:
        self.checkpoint.write({"drive_id": drive_id, "count": self.count})
      except Exception:
        cloudlog.exception("Unable to preserve bookmark count across selfdrived restart")
    return bookmarks > 0

  def _restore(self, drive_id: int) -> int:
    if self.checkpoint is None or drive_id <= 0:
      return 0
    try:
      saved = self.checkpoint.read()
      if (isinstance(saved, dict) and type(saved.get("drive_id")) is int and saved["drive_id"] == drive_id and
          type(saved.get("count")) is int and 0 <= saved["count"] <= 1_000_000):
        return saved["count"]
    except Exception:
      cloudlog.exception("Unable to restore bookmark count")
    return 0

  def _remove_saved(self):
    if self.checkpoint is not None:
      try:
        self.checkpoint.clear()
      except Exception:
        cloudlog.exception("Unable to clear bookmark count")

  def alert(self, paired: bool):
    return NormalPermanentAlert(f"Bookmark #{self.count} Saved", "" if paired else "pair to connect\nto view", duration=1.5)

  def event_rules(self):
    return {
      EventName.userBookmark: {ET.PERMANENT: lambda *_: self.alert(paired=True)},
      EventName.userBookmarkNotPaired: {ET.PERMANENT: lambda *_: self.alert(paired=False)},
    }
