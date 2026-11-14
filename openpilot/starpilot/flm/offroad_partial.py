"""Provisional review of intact segments; no repair, completion or auto-save."""

from pathlib import Path
import hashlib
import json
import os
import time

from openpilot.starpilot.flm.log_decode import decode_full_rlog_segment, LogDecodeError, LogDecodeCancelled
from openpilot.starpilot.flm.offroad_route import (
  LOCAL_ID, MAX_SEGMENTS, MAX_TOTAL_BYTES, MAX_ANALYSIS_SECONDS, RETAIN, PartialRouteReceipt,
  RouteInputError, RouteInputCancelled, route_reader, source_revisions,
)
from openpilot.starpilot.galaxy.drive_analysis import _route_names, _closed_source, _Incomplete
from openpilot.starpilot.galaxy.drive_history import _segment_files, DIR_FLAGS, SEGMENT_NAME


def partial_inventory(root, route_id, *, permitted):
  if type(route_id) is not str or not LOCAL_ID.fullmatch(route_id):
    raise RouteInputError(route_id, 'Original local partial recording required')
  names = _route_names(Path(root), route_id, permitted)
  numbers = {}
  for name in names:
    match = SEGMENT_NAME.fullmatch(name)
    assert match is not None
    number = int(match.group('number'))
    if number >= MAX_SEGMENTS or number in numbers:
      raise RouteInputError(route_id, 'Ambiguous or oversized partial recording')
    numbers[number] = name
  if not numbers:
    raise RouteInputError(route_id, 'No original segments available')
  rows = []
  fd = os.open(root, DIR_FLAGS)
  try:
    for number in range(max(numbers) + 1):
      if not permitted():
        raise RouteInputCancelled('Partial review cancelled')
      name = numbers.get(number)
      files, unavailable, _, _ = _segment_files(fd, name) if name is not None else (None, True, None, None)
      rows.append({'number': number, 'segmentName': name, 'files': files or {},
                   'unavailable': unavailable or files is None or files.get('rlog') is not True})
  finally:
    os.close(fd)
  return {'routeId': route_id, 'segmentCount': len(rows), 'segments': rows}


@route_reader
def walk_partial_route(root, route, *, permitted, consume):
  from openpilot.starpilot.flm.runtime_adapter_registry import car_params_digest
  from openpilot.starpilot.flm.offroad_evidence import torque_vehicle

  route_id = route['routeId']
  if partial_inventory(root, route_id, permitted=permitted) != route:
    raise ValueError('Partial inventory changed')
  before = source_revisions(root, route)
  started = time.monotonic()
  receipts, decoded, omitted, contexts, seen = [], [], [], set(), set()
  cp_hash, first, last, wall, total = None, None, 0, 0, 0

  def allowed():
    return permitted() and time.monotonic() - started <= MAX_ANALYSIS_SECONDS

  for segment in route['segments']:
    number, name = segment['number'], segment['segmentName']
    receipts.append((name or '', '', 0))
    if not allowed():
      raise RouteInputCancelled('Partial review cancelled')
    if segment['unavailable']:
      omitted.append((number, 'Segment is open, missing or unavailable'))
      continue
    try:
      with _closed_source(root, name, segment['files'], allowed, prefer_rlog=True) as (raw, codec, current, _):
        total += len(raw)
        if total > MAX_TOTAL_BYTES:
          raise RouteInputCancelled('Partial input byte bound exceeded')
        receipts[-1] = (name, hashlib.sha256(raw).hexdigest(), len(raw))
        if receipts[-1][1] in seen:
          omitted.append((number, 'Original segment bytes already reviewed'))
          continue
        events = decode_full_rlog_segment(raw, codec, cancelled=lambda: not allowed(), retain=RETAIN)
        cp, start, end, clock, endings = None, None, 0, None, []
        for event in events:
          kind, stamp = event.which(), int(event.logMonoTime)
          if kind == 'carParams':
            if not torque_vehicle(event.carParams):
              raise RouteInputError(route_id, 'Non-torque partial vehicle')
            actual = car_params_digest(event.carParams)
            if cp is not None and cp != actual or cp_hash is not None and cp_hash != actual:
              raise RouteInputError(route_id, 'Original partial vehicle changed')
            cp = actual
          elif kind == 'clocks' and event.valid and int(event.clocks.wallTimeNanos) > 946684800_000_000_000:
            clock = stamp, int(event.clocks.wallTimeNanos)
          elif kind == 'sentinel' and str(event.sentinel.type) in ('endOfSegment', 'endOfRoute'):
            endings.append(str(event.sentinel.type))
          if kind not in ('initData', 'carParams', 'sentinel') and stamp > 0:
            start = stamp if start is None else min(start, stamp)
            end = max(end, stamp)
        if 'endOfRoute' in endings and number != len(route['segments']) - 1:
          raise RouteInputError(route_id, 'Route ended before its final original segment')
        if cp is None or start is None or start >= end or len(endings) != 1 or clock is None or not 0 <= end - clock[0] <= 10_000_000_000:
          raise LogDecodeError('Segment has no complete identity, closing record or clock')
        if not current() or not allowed():
          raise RouteInputCancelled('Partial source changed or review cancelled')
        cp_hash = cp
        consume(number, events)
        for event in events:
          if event.which() == 'starpilotLateralState' and event.valid and event.starpilotLateralState.learningEvidence.version == 1:
            evidence = event.starpilotLateralState.learningEvidence
            contexts.add((str(evidence.controllerMode), str(evidence.controllerRevision), str(evidence.baselineRevision)))
        decoded.append(number)
        seen.add(receipts[-1][1])
        first = start if first is None else min(first, start)
        last, wall = max(last, end), max(wall, clock[1])
    except (LogDecodeCancelled, RouteInputCancelled, RouteInputError):
      raise
    except (LogDecodeError, _Incomplete, OSError) as error:
      omitted.append((number, str(error)[:160]))
  if not decoded or cp_hash is None or first is None or not omitted:
    raise ValueError('No independently closed partial segments, or complete input must be retried')
  if (source_revisions(root, route) != before or partial_inventory(root, route_id, permitted=allowed) != route or not allowed()):
    raise RouteInputCancelled('Partial recording changed before review completed')
  digest = hashlib.sha256(json.dumps((route_id, receipts, omitted), separators=(',', ':')).encode()).hexdigest()
  return PartialRouteReceipt(route_id, digest, tuple(receipts), cp_hash, first, last, 0, tuple(sorted(contexts)), tuple(decoded), tuple(omitted), wall)


def analyze_partial(root, route_id, previous, *, permitted):
  from openpilot.starpilot.flm.offroad_processor import collect_route_inputs, _assess_inputs

  route = partial_inventory(root, route_id, permitted=permitted)
  inputs = collect_route_inputs(root, route, permitted=permitted, walker=walk_partial_route, isolate_segments=True)
  inputs.counts.update(evidence_drives=0, evidence_windows=0)
  return _assess_inputs(route, inputs, previous, permitted=permitted)
