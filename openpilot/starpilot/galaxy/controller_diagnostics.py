"""Capture the USB and Bluetooth input inventory without opening any event device."""

from base64 import b64encode
from datetime import UTC, datetime
import hashlib
from io import BytesIO
import json
import secrets
from zipfile import ZIP_DEFLATED, ZipFile

from openpilot.starpilot.controllers import input as controller_input


def controller_input_report():
  """Include interfaces filtered out of Attached Controllers and mask device names per report."""
  devices = []
  groups = {}
  salt = secrets.token_bytes(8)
  truncated = False
  for entry in sorted(controller_input.SYS_ROOT.iterdir()):
    if not controller_input.EVENT_NAME.fullmatch(entry.name):
      continue
    device = entry / 'device'
    match = controller_input.MODALIAS.match(controller_input._text(device / 'modalias'))
    if match is None or int(match[1], 16) not in controller_input.EXTERNAL_BUSES:
      continue
    if len(devices) == controller_input.MAX_CANDIDATES:
      truncated = True
      break
    node = controller_input.DEV_ROOT / entry.name
    identity = controller_input._inspect(node)
    if identity is not None:
      groups.setdefault(identity.device_id, []).append(entry.name)
    name = controller_input._text(device / 'name')
    devices.append({
      'event': entry.name,
      'name': f'masked-{hashlib.sha256(salt + name.encode()).hexdigest()[:8]}' if name else '',
      'inputId': match[0],
      'properties': controller_input._text(device / 'properties'),
      'capabilities': {key: controller_input._text(device / 'capabilities' / key) for key in ('ev', 'key', 'rel', 'abs')},
      'discovery': 'eligible' if identity else 'filtered',
      'eventNodePresent': node.exists(),
    })
  duplicates = {event: events for events in groups.values() if len(events) > 1 for event in events}
  for row in devices:
    if row['event'] in duplicates:
      row.update(discovery='duplicate identity', matchingEvents=duplicates[row['event']])
  return {'externalInputInterfaces': devices, 'truncated': truncated}


def controller_capture():
  report = {'schemaVersion': 1, 'capturedAt': datetime.now(UTC).isoformat(timespec='seconds'), **controller_input_report()}
  output = BytesIO()
  with ZipFile(output, 'w', compression=ZIP_DEFLATED) as archive:
    archive.writestr('bluetooth-log.json', json.dumps(report, indent=2) + '\n')
  return {'schemaVersion': 1, 'zipBase64': b64encode(output.getvalue()).decode('ascii')}
