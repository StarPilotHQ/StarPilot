"""The "Black screen while StarPort streams" setting.

While StarPort is sending the drive view to the car screen, the comma's own driving screen can go black and keep only
its alerts: at night the comma stops lighting the cabin, and it stops receiving and drawing the camera twice. It is
the ``blackWhileStreaming`` field of the OnroadCustomizations document; an absent field is off. Every comma has it.
"""

FIELD = "blackWhileStreaming"
KEY = "BlackWhileStreaming"
LABEL = "Black screen while StarPort streams"
DESCRIPTION = ("While StarPort shows the drive on the car screen, the comma's driving screen goes black and keeps " +
               "only its alerts. The normal screen returns within a few seconds of the car screen stopping, and " +
               "whenever a speed limit waits for your answer.")


def enabled(document) -> bool:
  """The saved choice: on only for a document that says so."""
  return isinstance(document, dict) and document.get(FIELD) is True
