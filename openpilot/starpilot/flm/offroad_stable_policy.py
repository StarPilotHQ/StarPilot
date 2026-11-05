"""Recovered July FLM proposal policy; no Params, permission or application writer.

Original numeric policy: 4a98785aa3a5b6235a77ca8ec4a56bb5f74292a6.
Classifier and profile-strength rules are reused. Caller supplies an explicitly
bound catalog; legacy generic/friction outputs are not silently converted.
"""

from __future__ import annotations
from dataclasses import dataclass
import json
import math
from typing import Any
import numpy as np

ORIGINAL_COMMIT = '4a98785aa3a5b6235a77ca8ec4a56bb5f74292a6'
FLM_DRIVER_OVERRIDE_PRE_BUFFER_S = 0.35
FLM_DRIVER_OVERRIDE_POST_BUFFER_S = 1.0
FLM_FRICTION_SPEED_KNOTS = [0.0, 5.0, 10.0, 15.0, 25.0]
GENERIC_PARAM_METADATA = {
  'SteerDelay': {'min': 0.01, 'max': 1.0, 'precision': 0.001},
  'SteerFriction': {'min': 0.0, 'max': 1.0, 'precision': 0.001},
  'SteerKP': {'min': 0.1, 'max': 1.5, 'precision': 0.001},
  'SteerLatAccel': {'min': 0.5, 'max': 5.0, 'precision': 0.001},
  'SteerRatio': {'min': 5.0, 'max': 25.0, 'precision': 0.001},
}


@dataclass
class FLMSample:
  route: str
  segment: int
  t: float
  v_ego: float
  lat_active: bool
  steering_pressed: bool
  saturated: bool
  actual_la: float
  desired_la: float
  desired_jerk: float
  error: float
  error_rate: float
  p: float
  i: float
  d: float
  f: float
  output: float
  steering_angle_deg: float
  steering_torque: float
  cmd_torque: float
  out_torque: float
  roll_deg: float


def _speed_band_label(v_ego: float) -> str:
  if v_ego < 6.0:
    return "low"
  if v_ego < 15.0:
    return "mid"
  if v_ego < 25.0:
    return "fast"
  return "highway"


def _route_label(route: str, segment: int) -> str:
  return f"{route}/{segment}"


def _event_direction(samples: list[FLMSample]) -> str:
  mean_desired = float(np.mean([sample.desired_la for sample in samples]))
  if mean_desired > 0.02:
    return "left"
  if mean_desired < -0.02:
    return "right"
  return "center"


def _group_masked_events(samples: list[FLMSample], mask: list[bool], score_series: list[float], min_points: int = 5) -> list[dict[str, Any]]:
  events: list[dict[str, Any]] = []
  start_idx = None
  for idx, active in enumerate(mask + [False]):
    if active and start_idx is None:
      start_idx = idx
      continue
    if active:
      continue
    if start_idx is None:
      continue
    end_idx = idx - 1
    event_samples = samples[start_idx : end_idx + 1]
    if len(event_samples) >= min_points:
      event_scores = score_series[start_idx : end_idx + 1]
      peak_offset = int(np.argmax(event_scores))
      peak_idx = start_idx + peak_offset
      peak_sample = samples[peak_idx]
      direction = _event_direction(event_samples)
      events.append(
        {
          "startIdx": start_idx,
          "endIdx": end_idx,
          "peakIdx": peak_idx,
          "peakScore": float(event_scores[peak_offset]),
          "route": peak_sample.route,
          "segment": peak_sample.segment,
          "speedBand": _speed_band_label(float(np.mean([sample.v_ego for sample in event_samples]))),
          "direction": direction,
          "supportCount": len(event_samples),
        }
      )
    start_idx = None
  return events


def _analysis_eligibility_mask(samples: list[FLMSample]) -> list[bool]:
  eligible = [bool(sample.lat_active) for sample in samples]
  group_start = 0
  while group_start < len(samples):
    group_key = (samples[group_start].route, samples[group_start].segment)
    group_end = group_start + 1
    while group_end < len(samples) and (samples[group_end].route, samples[group_end].segment) == group_key:
      group_end += 1

    last_override = -math.inf
    for idx in range(group_start, group_end):
      sample = samples[idx]
      if sample.steering_pressed:
        last_override = sample.t
      if sample.steering_pressed or (sample.t - last_override) <= FLM_DRIVER_OVERRIDE_POST_BUFFER_S:
        eligible[idx] = False

    next_override = math.inf
    for idx in range(group_end - 1, group_start - 1, -1):
      sample = samples[idx]
      if sample.steering_pressed:
        next_override = sample.t
      if sample.steering_pressed or (next_override - sample.t) <= FLM_DRIVER_OVERRIDE_PRE_BUFFER_S:
        eligible[idx] = False

    # Force an event boundary between route segments even when lateral control stays active.
    eligible[group_start] = False
    eligible[group_end - 1] = False
    group_start = group_end

  return eligible


def _build_plot_data(samples: list[FLMSample], event: dict[str, Any], eligibility: list[bool] | None = None) -> dict[str, Any]:
  start_idx = int(event["startIdx"])
  end_idx = int(event["endIdx"])
  event_route = samples[start_idx].route
  event_segment = samples[start_idx].segment

  # Add a small amount of context without crossing an intervention buffer or
  # segment boundary. The highlighted region remains the classified event.
  for _ in range(12):
    candidate = start_idx - 1
    if candidate < 0 or (eligibility is not None and not eligibility[candidate]):
      break
    if samples[candidate].route != event_route or samples[candidate].segment != event_segment:
      break
    start_idx = candidate
  for _ in range(12):
    candidate = end_idx + 1
    if candidate >= len(samples) or (eligibility is not None and not eligibility[candidate]):
      break
    if samples[candidate].route != event_route or samples[candidate].segment != event_segment:
      break
    end_idx = candidate

  window = samples[start_idx : end_idx + 1]
  if len(window) < 2:
    return {}

  # Keep reports lightweight on unusually long windows while preserving both ends.
  if len(window) > 160:
    indices = np.linspace(0, len(window) - 1, 160, dtype=int)
    window = [window[int(idx)] for idx in indices]

  times = np.array([sample.t for sample in window], dtype=float)
  desired = np.array([sample.desired_la for sample in window], dtype=float)
  actual = np.array([sample.actual_la for sample in window], dtype=float)
  relative_times = times - float(times[0])
  event_start_time = max(float(samples[int(event["startIdx"])].t - times[0]), 0.0)
  event_end_time = max(float(samples[int(event["endIdx"])].t - times[0]), event_start_time)

  return {
    "times": [round(float(value), 3) for value in relative_times],
    "desired": [round(float(value), 4) for value in desired],
    "actual": [round(float(value), 4) for value in actual],
    "windowDurationSec": round(float(relative_times[-1]), 2),
    "eventStartSec": round(event_start_time, 2),
    "eventEndSec": round(event_end_time, 2),
    "eventDurationSec": round(max(event_end_time - event_start_time, 0.0), 2),
    "meanSpeedMph": round(float(np.mean([sample.v_ego for sample in window])) * 2.236936, 1),
    "route": event_route,
    "segment": event_segment,
    "segmentLabel": _route_label(event_route, event_segment),
    "direction": str(event.get("direction", "center")),
    "speedBand": str(event.get("speedBand", "mixed")),
    "driverOverrideFree": bool(eligibility is None or all(eligibility[start_idx : end_idx + 1])),
  }


def _build_plot_svg(plot_data: dict[str, Any]) -> str:
  times = np.array(plot_data.get("times", []), dtype=float)
  desired = np.array(plot_data.get("desired", []), dtype=float)
  actual = np.array(plot_data.get("actual", []), dtype=float)
  if len(times) < 2 or len(desired) != len(times) or len(actual) != len(times):
    return ""

  time_span = max(float(times.max()), 1e-3)
  y_min = float(min(np.min(desired), np.min(actual)))
  y_max = float(max(np.max(desired), np.max(actual)))
  y_pad = max((y_max - y_min) * 0.10, 0.1)
  y_min -= y_pad
  y_max += y_pad
  y_span = max(y_max - y_min, 1e-3)

  def _points(series):
    coords = []
    for t_val, y_val in zip(times, series, strict=True):
      x = (float(t_val) / time_span) * 380.0
      y = 120.0 - (((float(y_val) - y_min) / y_span) * 120.0)
      coords.append(f"{x:.1f},{y:.1f}")
    return " ".join(coords)

  return (
    "<svg viewBox='0 0 380 140' class='flm-plot' preserveAspectRatio='none'>"
    "<rect x='0' y='0' width='380' height='140' rx='8' ry='8' fill='#0f172a'/>"
    "<line x1='0' y1='120' x2='380' y2='120' stroke='#334155' stroke-width='1'/>"
    f"<polyline fill='none' stroke='#ef4444' stroke-width='2' points='{_points(desired)}'/>"
    f"<polyline fill='none' stroke='#38bdf8' stroke-width='2' points='{_points(actual)}'/>"
    "</svg>"
  )


def _build_event_summaries(samples: list[FLMSample]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
  eligibility = _analysis_eligibility_mask(samples)
  active_samples = [sample for sample, allowed in zip(samples, eligibility, strict=True) if allowed]
  if not active_samples:
    return [], {"sampleCount": 0}

  over_error = [abs(sample.actual_la) - abs(sample.desired_la) for sample in samples]
  desired = [sample.desired_la for sample in samples]
  jerk = [sample.desired_jerk for sample in samples]
  angle = [sample.steering_angle_deg for sample in active_samples]
  output = [sample.output for sample in active_samples]

  entry_phase = [(abs(d) > 0.30 and abs(j) > 0.25 and d * j > 0.0) for d, j in zip(desired, jerk, strict=True)]
  unwind_phase = [(abs(d) > 0.25 and abs(j) > 0.20 and d * j < 0.0) for d, j in zip(desired, jerk, strict=True)]
  steady_curve_phase = [(abs(d) > 0.35 and abs(j) < 0.18) for d, j in zip(desired, jerk, strict=True)]
  saturation_phase = [
    bool(allowed and sample.saturated and abs(sample.desired_la) > 0.30 and (abs(sample.desired_jerk) > 0.16 or abs(sample.actual_la) > 0.45))
    for sample, allowed in zip(samples, eligibility, strict=True)
  ]

  base_masks = {
    "understeer": [(allowed and phase and (ov < -0.20)) for allowed, phase, ov in zip(eligibility, steady_curve_phase, over_error, strict=True)],
    "oversteer": [(allowed and phase and (ov > 0.20)) for allowed, phase, ov in zip(eligibility, steady_curve_phase, over_error, strict=True)],
    "late_turn_in": [(allowed and phase and ov < -0.16) for allowed, phase, ov in zip(eligibility, entry_phase, over_error, strict=True)],
    "early_turn_in": [(allowed and phase and ov > 0.16) for allowed, phase, ov in zip(eligibility, entry_phase, over_error, strict=True)],
    "unwind_too_slow": [(allowed and phase and ov > 0.14) for allowed, phase, ov in zip(eligibility, unwind_phase, over_error, strict=True)],
    "unwind_too_fast": [(allowed and phase and ov < -0.14) for allowed, phase, ov in zip(eligibility, unwind_phase, over_error, strict=True)],
    "low_speed_unwillingness": [
      bool(
        allowed
        and sample.v_ego < 6.0
        and abs(sample.desired_la) > 0.30
        and abs(sample.desired_jerk) > 0.18
        and (abs(sample.actual_la) + 0.18) < abs(sample.desired_la)
      )
      for sample, allowed in zip(samples, eligibility, strict=True)
    ],
    "saturation_limited": saturation_phase,
  }
  score_map = {
    "understeer": [max((-ov), 0.0) for ov in over_error],
    "oversteer": [max(ov, 0.0) for ov in over_error],
    "late_turn_in": [max((-ov), 0.0) + abs(j) * 0.1 for ov, j in zip(over_error, jerk, strict=True)],
    "early_turn_in": [max(ov, 0.0) + abs(j) * 0.1 for ov, j in zip(over_error, jerk, strict=True)],
    "unwind_too_slow": [max(ov, 0.0) for ov in over_error],
    "unwind_too_fast": [max((-ov), 0.0) for ov in over_error],
    "low_speed_unwillingness": [max(abs(d) - abs(sample.actual_la), 0.0) for d, sample in zip(desired, samples, strict=True)],
    "saturation_limited": [1.0 if sample.saturated else 0.0 for sample in samples],
  }

  # Straight-road chatter detection uses a simple 4-second window.
  straight_windows = []
  for start_idx in range(0, max(len(samples) - 20, 1), 10):
    window = samples[start_idx : start_idx + 40]
    if len(window) < 20:
      continue
    if not all(eligibility[start_idx : start_idx + len(window)]):
      continue
    if float(np.mean([sample.v_ego for sample in window])) < 20.0:
      continue
    if float(np.mean([abs(sample.desired_la) for sample in window])) > 0.12:
      continue
    centered_angles = np.array([sample.steering_angle_deg for sample in window]) - float(np.mean([sample.steering_angle_deg for sample in window]))
    sign_changes = int(np.sum(np.sign(centered_angles[1:]) != np.sign(centered_angles[:-1])))
    amplitude = float(np.max(centered_angles) - np.min(centered_angles))
    chatter_score = (amplitude * 0.25) + (sign_changes * 0.04)
    if amplitude > 0.45 and sign_changes >= 6:
      straight_windows.append(
        {
          "startIdx": start_idx,
          "endIdx": start_idx + len(window) - 1,
          "peakIdx": start_idx + int(len(window) / 2),
          "peakScore": chatter_score,
          "route": window[0].route,
          "segment": window[0].segment,
          "speedBand": "highway",
          "direction": "center",
          "supportCount": len(window),
        }
      )

  curve_windows = []
  for start_idx in range(0, max(len(samples) - 20, 1), 8):
    window = samples[start_idx : start_idx + 36]
    if len(window) < 18:
      continue
    if not all(eligibility[start_idx : start_idx + len(window)]):
      continue
    if float(np.mean([sample.v_ego for sample in window])) < 15.0:
      continue
    desired_sign = float(np.mean([sample.desired_la for sample in window]))
    if abs(desired_sign) < 0.35:
      continue
    if any((sample.desired_la * desired_sign) < 0.0 for sample in window):
      continue
    error_series = np.array([sample.actual_la - sample.desired_la for sample in window])
    sign_changes = int(np.sum(np.sign(error_series[1:]) != np.sign(error_series[:-1])))
    amplitude = float(np.max(error_series) - np.min(error_series))
    if amplitude > 0.22 and sign_changes >= 4:
      curve_windows.append(
        {
          "startIdx": start_idx,
          "endIdx": start_idx + len(window) - 1,
          "peakIdx": start_idx + int(np.argmax(np.abs(error_series))),
          "peakScore": amplitude + sign_changes * 0.03,
          "route": window[0].route,
          "segment": window[0].segment,
          "speedBand": _speed_band_label(float(np.mean([sample.v_ego for sample in window]))),
          "direction": "left" if desired_sign > 0.0 else "right",
          "supportCount": len(window),
        }
      )

  summaries: list[dict[str, Any]] = []
  for bucket, mask in base_masks.items():
    events = _group_masked_events(samples, mask, score_map[bucket])
    if events:
      summaries.extend(_summaries_from_events(bucket, samples, events, eligibility))
  if straight_windows:
    summaries.extend(_summaries_from_events("center_chatter", samples, straight_windows, eligibility))
  if curve_windows:
    summaries.extend(_summaries_from_events("notchy_mid_curve", samples, curve_windows, eligibility))

  left_errors = [abs(sample.actual_la) - abs(sample.desired_la) for sample in active_samples if sample.desired_la > 0.25]
  right_errors = [abs(sample.actual_la) - abs(sample.desired_la) for sample in active_samples if sample.desired_la < -0.25]
  summary_stats = {
    "sampleCount": len(active_samples),
    "excludedDriverOverrideSamples": sum(1 for sample, allowed in zip(samples, eligibility, strict=True) if sample.lat_active and not allowed),
    "qlogFallback": False,
    "meanDesiredAbs": round(float(np.mean(np.abs([sample.desired_la for sample in active_samples]))), 4),
    "meanErrorAbs": round(float(np.mean(np.abs([sample.actual_la - sample.desired_la for sample in active_samples]))), 4),
    "leftBias": round(float(np.mean(left_errors)), 4) if left_errors else 0.0,
    "rightBias": round(float(np.mean(right_errors)), 4) if right_errors else 0.0,
    "highwayStraightAngleP2P": round(float(np.percentile(np.abs(angle), 95) - np.percentile(np.abs(angle), 5)), 4) if angle else 0.0,
    "meanOutputAbs": round(float(np.mean(np.abs(output))), 4) if output else 0.0,
  }

  if summary_stats["meanErrorAbs"] < 0.08 and not any(summary["severity"] > 0.65 for summary in summaries):
    summaries.append(
      {
        "bucket": "model_limited",
        "dimensionId": "model_limited:overall",
        "direction": "center",
        "speedBand": "mixed",
        "count": 1,
        "severity": 0.25,
        "evidence": {
          "speedBand": "mixed",
          "directionBias": "center",
          "eventCount": 1,
          "segments": [],
        },
        "events": [],
        "plotSvg": "",
        "plotData": {},
      }
    )

  return sorted(summaries, key=lambda item: item["severity"], reverse=True), summary_stats


def _summaries_from_events(bucket: str, samples: list[FLMSample], events: list[dict[str, Any]], eligibility: list[bool] | None = None) -> list[dict[str, Any]]:
  grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
  for event in events:
    key = (bucket, event["direction"])
    grouped.setdefault(key, []).append(event)

  summaries = []
  for (bucket_name, direction), grouped_events in grouped.items():
    grouped_events.sort(key=lambda item: item["peakScore"], reverse=True)
    strongest = grouped_events[:3]
    strongest_labels = [
      {
        "route": event["route"],
        "segment": event["segment"],
        "label": _route_label(event["route"], event["segment"]),
        "score": round(float(event["peakScore"]), 3),
      }
      for event in strongest
    ]
    top_event = strongest[0]
    top_speed_band = top_event["speedBand"]
    plot_data = _build_plot_data(samples, top_event, eligibility)
    summaries.append(
      {
        "bucket": bucket_name,
        "dimensionId": f"{bucket_name}:{direction}:{top_speed_band}",
        "direction": direction,
        "speedBand": top_speed_band,
        "count": len(grouped_events),
        "severity": round(float(min(1.5, np.mean([event["peakScore"] for event in strongest]))), 3),
        "evidence": {
          "speedBand": top_speed_band,
          "directionBias": direction,
          "eventCount": len(grouped_events),
          "segments": strongest_labels,
        },
        "events": grouped_events,
        "plotSvg": _build_plot_svg(plot_data),
        "plotData": plot_data,
      }
    )
  return summaries


def classify_torque_samples(samples: list[FLMSample]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
  return _build_event_summaries(samples)


def _clamp(value: float, lower: float, upper: float) -> float:
  return min(max(float(value), lower), upper)


def _round_to_precision(value: float, precision: float) -> float:
  if precision <= 0:
    return float(value)
  steps = round(float(value) / precision)
  return round(steps * precision, 6)


def _resolve_conflicting_actionable_suggestions(suggestions: list[dict[str, Any]]) -> list[dict[str, Any]]:
  families = {
    "understeer": ("turn_in", "more"),
    "late_turn_in": ("turn_in", "more"),
    "oversteer": ("turn_in", "less"),
    "early_turn_in": ("turn_in", "less"),
    "unwind_too_slow": ("unwind", "more"),
    "unwind_too_fast": ("unwind", "less"),
  }
  grouped: dict[tuple[str, str, str], dict[str, list[dict[str, Any]]]] = {}
  passthrough: list[dict[str, Any]] = []

  for suggestion in suggestions:
    bucket = str(suggestion.get("bucket", ""))
    family_info = families.get(bucket)
    if family_info is None:
      passthrough.append(suggestion)
      continue
    evidence = suggestion.get("evidence", {})
    key = (
      family_info[0],
      str(evidence.get("directionBias", "center")),
      str(evidence.get("speedBand", "mixed")),
    )
    grouped.setdefault(key, {"more": [], "less": []})[family_info[1]].append(suggestion)

  resolved = list(passthrough)
  for polarities in grouped.values():
    more = polarities["more"]
    less = polarities["less"]
    if not more or not less:
      resolved.extend(more or less)
      continue

    def score(items: list[dict[str, Any]]) -> float:
      total = 0.0
      for item in items:
        severity = max(float(item.get("severity", 0.0)), 0.25)
        event_count = max(int(item.get("evidence", {}).get("eventCount", 0)), 1)
        total += severity * math.log1p(event_count)
      return total

    more_score = score(more)
    less_score = score(less)
    if more_score >= less_score * 1.2:
      resolved.extend(more)
    elif less_score >= more_score * 1.2:
      resolved.extend(less)

  return sorted(resolved, key=lambda item: float(item.get("severity", 0.0)), reverse=True)


class StablePolicy:
  """Per-analysis catalog. No shared mutable policy or imported old controller."""

  def __init__(self, catalog, friction_curves):
    # Own copies; another request cannot replace this analysis' vehicle data.
    self._catalog = json.loads(json.dumps(catalog, allow_nan=False))
    self._curves = json.loads(json.dumps(friction_curves, allow_nan=False))
    if not isinstance(self._catalog, dict) or not isinstance(self._curves, dict):
      raise ValueError('Explicit vehicle knob catalog and friction curves required')
    for key, item in self._catalog.items():
      if (
        type(key) is not str
        or not isinstance(item, dict)
        or not all(type(item.get(n)) in (int, float) and math.isfinite(item[n]) for n in ('defaultValue', 'min', 'max', 'precision'))
        or not item['min'] <= item['defaultValue'] <= item['max']
        or item['precision'] <= 0
      ):
        raise ValueError('Finite bounded original knob metadata required')
    for family, values in self._curves.items():
      if (
        family not in ('standard', 'gm', 'hkg_canfd')
        or type(values) is not list
        or len(values) != 5
        or any(type(v) not in (int, float) or not math.isfinite(v) or v < 0.05 for v in values)
      ):
        raise ValueError('Actual finite five-knot original friction curve required')

  def _catalog_copy(self):
    return json.loads(json.dumps(self._catalog))

  def _baseline_family_curve(self, family):
    if family not in self._curves:
      raise ValueError('Missing actual original friction family')
    return list(self._curves[family])

  @staticmethod
  def _normalize(overrides):
    # This internal producer accepts only the numeric object it just built.
    # No lenient legacy text/byte parsing or unknown family fallback.
    if type(overrides) is not dict or set(overrides) != {'schemaVersion', 'baseFrictionThresholds', 'vehicleKnobs'} or overrides['schemaVersion'] != 1:
      raise ValueError('Exact generated legacy proposal required')
    for family, payload in overrides['baseFrictionThresholds'].items():
      if family not in ('standard', 'gm', 'hkg_canfd') or payload['speedKnots'] != FLM_FRICTION_SPEED_KNOTS or len(payload['values']) != 5:
        raise ValueError('Bound original friction proposal required')
      if any(not math.isfinite(v) or v < 0.05 for v in payload['values']):
        raise ValueError('Invalid original friction proposal')
    if any(not math.isfinite(v) for v in overrides['vehicleKnobs'].values()):
      raise ValueError('Invalid original vehicle proposal')
    return overrides if overrides['baseFrictionThresholds'] or overrides['vehicleKnobs'] else {}

  def profiles(self, report_id, summaries, capabilities, current, *, path_key='cleanup_pass'):
    suggestions = []
    for summary in summaries:
      adjustment = self._primary_delta_from_summary(summary, capabilities, current, strategy='baseline' if path_key == 'baseline_fix' else 'cleanup')
      suggestions.append({**summary, 'primaryAdjustmentRaw': adjustment})
    return self.build_trial_profiles(
      report_id, suggestions, {}, capabilities, path_key=path_key, path_label='Baseline Fix' if path_key == 'baseline_fix' else 'Cleanup Pass'
    )

  def _current_family_curve(self, family: str, current: dict[str, Any]) -> list[float]:
    active_overrides = current.get('FLMActiveOverrides', {}) if isinstance(current, dict) else {}
    payload = active_overrides.get('baseFrictionThresholds', {}).get(family, {}) if isinstance(active_overrides, dict) else {}
    values = payload.get('values', []) if isinstance(payload, dict) else []
    if isinstance(values, list) and len(values) == len(FLM_FRICTION_SPEED_KNOTS):
      try:
        return [round(float(value), 4) for value in values]
      except Exception:
        pass
    return self._baseline_family_curve(family)

  def _current_vehicle_knob_value(self, symbol: str, current: dict[str, Any]) -> float | None:
    knob = self._catalog_copy().get(symbol)
    if knob is None:
      return None
    active_overrides = current.get('FLMActiveOverrides', {}) if isinstance(current, dict) else {}
    vehicle_knobs = active_overrides.get('vehicleKnobs', {}) if isinstance(active_overrides, dict) else {}
    try:
      return float(vehicle_knobs.get(symbol, knob['defaultValue']))
    except Exception:
      return float(knob['defaultValue'])

  def _vehicle_knob_adjustment(self, symbol: str, delta: float, current: dict[str, Any] | None = None) -> dict[str, Any] | None:
    knob = self._catalog_copy().get(symbol)
    if knob is None:
      return None
    current_value = self._current_vehicle_knob_value(symbol, current or {})
    if current_value is None:
      return None
    suggested_value = _round_to_precision(_clamp(current_value + delta, knob['min'], knob['max']), knob['precision'])
    if math.isclose(current_value, suggested_value, abs_tol=max(float(knob['precision']) / 2.0, 1e-06)):
      return None
    return {
      'type': 'vehicle_knob',
      'symbol': symbol,
      'current': current_value,
      'suggested': suggested_value,
      'delta': round(suggested_value - current_value, 4),
    }

  def _rich_profile_supports_knob(self, capabilities: dict[str, Any], suffix: str) -> bool:
    rich_profile = capabilities.get('richProfileKey')
    if not rich_profile:
      return False
    return f'{rich_profile}.{suffix}' in self._catalog_copy()

  def _primary_delta_from_summary(
    self, summary: dict[str, Any], capabilities: dict[str, Any], current: dict[str, Any], strategy: str = 'cleanup'
  ) -> dict[str, Any] | None:
    bucket = summary['bucket']
    direction = summary['direction']
    severity = max(float(summary['severity']), 0.2)
    speed_band = str(summary.get('speedBand', 'mixed'))
    rich_profile = capabilities.get('richProfileKey')
    family = capabilities.get('frictionFamily', 'standard')
    side = 'left' if direction != 'right' else 'right'
    curvy_band = speed_band in ('mid', 'fast')
    supports_low_speed_assist = self._rich_profile_supports_knob(capabilities, 'low_speed_angle_assist_max_torque')
    supports_crawl_turn_in = self._rich_profile_supports_knob(capabilities, f'crawl_turn_in_ff_boost_{side}')
    supports_turn_in_boost = self._rich_profile_supports_knob(capabilities, f'turn_in_boost_{side}')
    supports_unwind_taper = self._rich_profile_supports_knob(capabilities, f'unwind_taper_{side}')
    supports_curvy_turn_in_trim = self._rich_profile_supports_knob(capabilities, f'curvy_turn_in_trim_{side}')
    supports_curvy_turn_in_speed = self._rich_profile_supports_knob(capabilities, 'curvy_turn_in_trim_speed_max')
    supports_curvy_speed_max = self._rich_profile_supports_knob(capabilities, 'curvy_speed_max')
    supports_curvy_unwind_extra = self._rich_profile_supports_knob(capabilities, f'curvy_unwind_extra_reduction_{side}')
    supports_curvy_unwind_floor = self._rich_profile_supports_knob(capabilities, f'curvy_unwind_floor_relief_{side}')
    supports_ff_gain = self._rich_profile_supports_knob(capabilities, f'ff_gain_{side}')
    nonlinear_map = capabilities.get('nonlinearTorqueMap', {})
    asymmetric_nonlinear_map = bool(isinstance(nonlinear_map, dict) and nonlinear_map.get('asymmetric'))
    if bucket == 'model_limited':
      return None
    if strategy == 'baseline':
      if bucket in ('center_chatter', 'notchy_mid_curve'):
        current_curve = self._current_family_curve(family, current)
        deltas = [0.0, 0.01, 0.02, 0.025, 0.03] if bucket == 'center_chatter' else [0.0, 0.0, 0.015, 0.02, 0.02]
        scale = min(max(severity, 0.4), 1.2)
        suggested = [round(current_curve[idx] + delta * scale, 4) for idx, delta in enumerate(deltas)]
        return {
          'type': 'friction_curve',
          'symbol': f'base_friction_threshold.{family}',
          'family': family,
          'current': current_curve,
          'suggested': suggested,
          'delta': [round(suggested[idx] - current_curve[idx], 4) for idx in range(len(current_curve))],
        }
      if bucket == 'low_speed_unwillingness':
        current_curve = self._current_family_curve(family, current)
        deltas = [-0.03, -0.025, -0.015, -0.005, 0.0]
        scale = min(max(severity, 0.5), 1.2)
        suggested = [round(max(0.05, current_curve[idx] + delta * scale), 4) for idx, delta in enumerate(deltas)]
        return {
          'type': 'friction_curve',
          'symbol': f'base_friction_threshold.{family}',
          'family': family,
          'current': current_curve,
          'suggested': suggested,
          'delta': [round(suggested[idx] - current_curve[idx], 4) for idx in range(len(current_curve))],
        }
      if bucket in ('understeer', 'late_turn_in', 'saturation_limited'):
        if asymmetric_nonlinear_map and direction in ('left', 'right') and supports_ff_gain:
          adjustment = self._vehicle_knob_adjustment(f'{rich_profile}.ff_gain_{side}', 0.025 * severity, current)
          if adjustment is not None:
            return adjustment
        current_value = float(current['SteerLatAccel'])
        scale = 0.04 if bucket == 'saturation_limited' else 0.03
        suggested_value = round(_clamp(current_value + max(scale, current_value * scale * severity), 0.5, 5.0), 4)
        return {
          'type': 'generic_param',
          'paramKey': 'SteerLatAccel',
          'current': current_value,
          'suggested': suggested_value,
          'delta': round(suggested_value - current_value, 4),
        }
      if bucket in ('oversteer', 'early_turn_in'):
        if asymmetric_nonlinear_map and direction in ('left', 'right') and supports_ff_gain:
          adjustment = self._vehicle_knob_adjustment(f'{rich_profile}.ff_gain_{side}', -0.025 * severity, current)
          if adjustment is not None:
            return adjustment
        current_value = float(current['SteerLatAccel'])
        suggested_value = round(_clamp(current_value - max(0.03, current_value * 0.03 * severity), 0.5, 5.0), 4)
        return {
          'type': 'generic_param',
          'paramKey': 'SteerLatAccel',
          'current': current_value,
          'suggested': suggested_value,
          'delta': round(suggested_value - current_value, 4),
        }
      if bucket in ('unwind_too_slow', 'unwind_too_fast'):
        current_value = float(current['SteerFriction'])
        direction_mult = -1.0 if bucket == 'unwind_too_slow' else 1.0
        suggested_value = round(_clamp(current_value + 0.015 * severity * direction_mult, 0.0, 1.0), 4)
        return {
          'type': 'generic_param',
          'paramKey': 'SteerFriction',
          'current': current_value,
          'suggested': suggested_value,
          'delta': round(suggested_value - current_value, 4),
        }
    if bucket in ('center_chatter', 'notchy_mid_curve'):
      current_curve = self._current_family_curve(family, current)
      if bucket == 'center_chatter':
        deltas = [0.0, 0.01, 0.02, 0.025, 0.03]
      else:
        deltas = [0.0, 0.0, 0.015, 0.02, 0.02]
      scale = min(max(severity, 0.4), 1.2)
      suggested = [round(current_curve[idx] + delta * scale, 4) for idx, delta in enumerate(deltas)]
      return {
        'type': 'friction_curve',
        'symbol': f'base_friction_threshold.{family}',
        'family': family,
        'current': current_curve,
        'suggested': suggested,
        'delta': [round(suggested[idx] - current_curve[idx], 4) for idx in range(len(current_curve))],
      }
    if bucket == 'low_speed_unwillingness':
      current_curve = self._current_family_curve(family, current)
      deltas = [-0.03, -0.025, -0.015, -0.005, 0.0]
      scale = min(max(severity, 0.5), 1.2)
      if supports_low_speed_assist:
        adjustment = self._vehicle_knob_adjustment(f'{rich_profile}.low_speed_angle_assist_max_torque', 0.04 * scale, current)
        if adjustment is not None:
          return adjustment
      if supports_crawl_turn_in:
        adjustment = self._vehicle_knob_adjustment(f'{rich_profile}.crawl_turn_in_ff_boost_{side}', 0.03 * scale, current)
        if adjustment is not None:
          return adjustment
      if rich_profile and supports_turn_in_boost:
        adjustment = self._vehicle_knob_adjustment(f'{rich_profile}.turn_in_boost_{side}', 0.025 * scale, current)
        if adjustment is not None:
          return adjustment
      suggested = [round(max(0.05, current_curve[idx] + delta * scale), 4) for idx, delta in enumerate(deltas)]
      return {
        'type': 'friction_curve',
        'symbol': f'base_friction_threshold.{family}',
        'family': family,
        'current': current_curve,
        'suggested': suggested,
        'delta': [round(suggested[idx] - current_curve[idx], 4) for idx in range(len(current_curve))],
      }
    if bucket in ('understeer', 'late_turn_in'):
      if curvy_band and speed_band == 'fast' and (bucket == 'late_turn_in') and supports_curvy_turn_in_speed:
        adjustment = self._vehicle_knob_adjustment(f'{rich_profile}.curvy_turn_in_trim_speed_max', 1.6 * severity, current)
        if adjustment is not None:
          return adjustment
      if curvy_band and supports_curvy_turn_in_trim:
        adjustment = self._vehicle_knob_adjustment(f'{rich_profile}.curvy_turn_in_trim_{side}', -0.018 * severity, current)
        if adjustment is not None:
          return adjustment
      if rich_profile and supports_turn_in_boost:
        adjustment = self._vehicle_knob_adjustment(f'{rich_profile}.turn_in_boost_{side}', 0.02 * severity, current)
        if adjustment is not None:
          return adjustment
      current_value = float(current['SteerLatAccel'])
      suggested_value = round(_clamp(current_value + max(0.03, current_value * 0.03 * severity), 0.5, 5.0), 4)
      return {
        'type': 'generic_param',
        'paramKey': 'SteerLatAccel',
        'current': current_value,
        'suggested': suggested_value,
        'delta': round(suggested_value - current_value, 4),
      }
    if bucket in ('oversteer', 'early_turn_in'):
      if curvy_band and supports_curvy_turn_in_trim:
        adjustment = self._vehicle_knob_adjustment(f'{rich_profile}.curvy_turn_in_trim_{side}', 0.018 * severity, current)
        if adjustment is not None:
          return adjustment
      if rich_profile and supports_turn_in_boost:
        adjustment = self._vehicle_knob_adjustment(f'{rich_profile}.turn_in_boost_{side}', -0.02 * severity, current)
        if adjustment is not None:
          return adjustment
      current_value = float(current['SteerLatAccel'])
      suggested_value = round(_clamp(current_value - max(0.03, current_value * 0.03 * severity), 0.5, 5.0), 4)
      return {
        'type': 'generic_param',
        'paramKey': 'SteerLatAccel',
        'current': current_value,
        'suggested': suggested_value,
        'delta': round(suggested_value - current_value, 4),
      }
    if bucket in ('unwind_too_slow', 'unwind_too_fast'):
      if curvy_band and supports_curvy_unwind_extra:
        if bucket == 'unwind_too_slow' and speed_band == 'fast' and supports_curvy_speed_max:
          adjustment = self._vehicle_knob_adjustment(f'{rich_profile}.curvy_speed_max', 1.8 * severity, current)
          if adjustment is not None:
            return adjustment
        direction_mult = 1.0 if bucket == 'unwind_too_slow' else -1.0
        adjustment = self._vehicle_knob_adjustment(f'{rich_profile}.curvy_unwind_extra_reduction_{side}', 0.03 * severity * direction_mult, current)
        if adjustment is not None:
          return adjustment
      if rich_profile and supports_unwind_taper:
        direction_mult = 1.0 if bucket == 'unwind_too_slow' else -1.0
        adjustment = self._vehicle_knob_adjustment(f'{rich_profile}.unwind_taper_{side}', 0.08 * severity * direction_mult, current)
        if adjustment is not None:
          return adjustment
      current_value = float(current['SteerFriction'])
      direction_mult = -1.0 if bucket == 'unwind_too_slow' else 1.0
      suggested_value = round(_clamp(current_value + 0.015 * severity * direction_mult, 0.0, 1.0), 4)
      return {
        'type': 'generic_param',
        'paramKey': 'SteerFriction',
        'current': current_value,
        'suggested': suggested_value,
        'delta': round(suggested_value - current_value, 4),
      }
    if bucket == 'saturation_limited':
      if curvy_band and supports_curvy_unwind_floor:
        adjustment = self._vehicle_knob_adjustment(f'{rich_profile}.curvy_unwind_floor_relief_{side}', 0.04 * severity, current)
        if adjustment is not None:
          return adjustment
      current_value = float(current['SteerLatAccel'])
      suggested_value = round(_clamp(current_value + max(0.04, current_value * 0.04 * severity), 0.5, 5.0), 4)
      return {
        'type': 'generic_param',
        'paramKey': 'SteerLatAccel',
        'current': current_value,
        'suggested': suggested_value,
        'delta': round(suggested_value - current_value, 4),
      }
    return None

  def _clamp_generic_param(self, param_key: str, value: float) -> float:
    meta = GENERIC_PARAM_METADATA[param_key]
    return _round_to_precision(_clamp(value, meta['min'], meta['max']), meta['precision'])

  def _merge_primary_adjustments(self, suggestions: list[dict[str, Any]], multiplier: float) -> tuple[dict[str, Any], dict[str, Any], bool]:
    params_delta: dict[str, Any] = {'AdvancedLateralTune': True}
    requires_force_auto_tune_off = False
    generic_targets: dict[str, dict[str, Any]] = {}
    vehicle_targets: dict[str, dict[str, Any]] = {}
    friction_targets: dict[str, dict[str, Any]] = {}
    for suggestion in suggestions:
      adjustment = suggestion.get('primaryAdjustmentRaw')
      if not isinstance(adjustment, dict):
        continue
      weight = max(float(suggestion.get('severity', 0.0)), 0.25)
      if adjustment['type'] == 'generic_param':
        param_key = adjustment['paramKey']
        bucket = generic_targets.setdefault(param_key, {'current': float(adjustment['current']), 'weightedDelta': 0.0, 'weight': 0.0})
        bucket['weightedDelta'] += float(adjustment['delta']) * weight
        bucket['weight'] += weight
        if param_key in ('SteerFriction', 'SteerLatAccel', 'SteerKP', 'SteerDelay', 'SteerRatio'):
          requires_force_auto_tune_off = True
      elif adjustment['type'] == 'vehicle_knob':
        symbol = adjustment['symbol']
        bucket = vehicle_targets.setdefault(symbol, {'current': float(adjustment['current']), 'weightedDelta': 0.0, 'weight': 0.0})
        bucket['weightedDelta'] += float(adjustment['delta']) * weight
        bucket['weight'] += weight
        requires_force_auto_tune_off = True
      elif adjustment['type'] == 'friction_curve':
        family = adjustment['family']
        delta_curve = [float(value) for value in adjustment['delta']]
        bucket = friction_targets.setdefault(
          family, {'current': [float(value) for value in adjustment['current']], 'weightedDelta': [0.0] * len(delta_curve), 'weight': 0.0}
        )
        for idx, value in enumerate(delta_curve):
          bucket['weightedDelta'][idx] += value * weight
        bucket['weight'] += weight
        requires_force_auto_tune_off = True
    overrides: dict[str, Any] = {'schemaVersion': 1, 'baseFrictionThresholds': {}, 'vehicleKnobs': {}}
    for param_key, bucket in generic_targets.items():
      avg_delta = bucket['weightedDelta'] / bucket['weight'] * multiplier if bucket['weight'] > 0 else 0.0
      next_value = self._clamp_generic_param(param_key, float(bucket['current']) + avg_delta)
      precision = float(GENERIC_PARAM_METADATA[param_key]['precision'])
      if not math.isclose(float(bucket['current']), next_value, abs_tol=max(precision / 2.0, 1e-06)):
        params_delta[param_key] = next_value
    if 'SteerDelay' in params_delta:
      params_delta['UseAutoSteerDelay'] = False
    supported_knobs = self._catalog_copy()
    for symbol, bucket in vehicle_targets.items():
      meta = supported_knobs.get(symbol)
      if meta is None or bucket['weight'] <= 0:
        continue
      avg_delta = bucket['weightedDelta'] / bucket['weight'] * multiplier
      next_value = _round_to_precision(_clamp(float(bucket['current']) + avg_delta, meta['min'], meta['max']), meta['precision'])
      if not math.isclose(float(bucket['current']), next_value, abs_tol=max(float(meta['precision']) / 2.0, 1e-06)):
        overrides['vehicleKnobs'][symbol] = next_value
    for family, bucket in friction_targets.items():
      if bucket['weight'] <= 0:
        continue
      avg_delta_curve = [value / bucket['weight'] for value in bucket['weightedDelta']]
      values = [round(max(0.05, float(bucket['current'][idx]) + avg_delta_curve[idx] * multiplier), 4) for idx in range(len(bucket['current']))]
      if any((not math.isclose(float(bucket['current'][idx]), values[idx], abs_tol=1e-06) for idx in range(len(values)))):
        overrides['baseFrictionThresholds'][family] = {'speedKnots': list(FLM_FRICTION_SPEED_KNOTS), 'values': values}
    overrides = self._normalize(overrides)
    return (params_delta, overrides, requires_force_auto_tune_off)

  def build_trial_profiles(
    self,
    report_id: str,
    suggestions: list[dict[str, Any]],
    feedback: dict[str, Any],
    capabilities: dict[str, Any],
    path_key: str = 'cleanup_pass',
    path_label: str = 'Cleanup Pass',
  ) -> list[dict[str, Any]]:
    ignored = set((str(item) for item in feedback.get('ignoredDimensions', [])))
    accepted = set((str(item) for item in feedback.get('acceptedDimensions', [])))
    has_feedback_decisions = bool(ignored or accepted)
    considered = [
      suggestion for suggestion in suggestions if suggestion.get('dimensionId') not in ignored and (not accepted or suggestion.get('dimensionId') in accepted)
    ]
    if not considered and (not has_feedback_decisions):
      considered = [suggestion for suggestion in suggestions if suggestion.get('primaryAdjustmentRaw')]
    actionable = [suggestion for suggestion in considered if suggestion.get('primaryAdjustmentRaw')]
    actionable = _resolve_conflicting_actionable_suggestions(actionable)
    profiles = []
    profile_defs = [('conservative', 'Conservative', 0.6), ('recommended', 'Recommended', 1.0), ('assertive', 'Assertive', 1.35)]
    for suffix, label, multiplier in profile_defs:
      params_delta, overrides, force_auto_tune_off = self._merge_primary_adjustments(actionable, multiplier)
      if not overrides and len(params_delta) <= 1:
        continue
      profile = {
        'id': f'{report_id}:{path_key}:{suffix}',
        'reportId': report_id,
        'label': label,
        'pathKey': path_key,
        'pathLabel': path_label,
        'description': f'{label} {path_label.lower()} trial generated from {len(actionable)} confirmed symptom dimension(s).',
        'genericParams': params_delta,
        'flmOverrides': overrides,
        'requiresForceAutoTuneOff': bool(force_auto_tune_off),
        'capabilities': capabilities,
      }
      if force_auto_tune_off:
        profile['genericParams']['ForceAutoTuneOff'] = True
        profile['genericParams']['ForceAutoTune'] = False
      profiles.append(profile)
    return profiles[:3]


def cadenced_runs(samples):
  """Use genuine nearest source rows on a 100 ms grid, never interpolation.

  July's sample-count windows describe 10 Hz history. Split absent samples,
  source gaps, route/segment boundaries; do not speed those windows up when
  a caller supplies a dense 100 Hz recording. Original stamps stay unchanged.
  """
  from bisect import bisect_left
  from dataclasses import fields, replace

  if type(samples) not in (list, tuple) or len(samples) > 60000:
    raise ValueError('Bounded original historical samples required')
  numeric = tuple(f.name for f in fields(FLMSample) if f.name not in ('route', 'segment', 'lat_active', 'steering_pressed', 'saturated'))
  groups = []
  for sample in samples:
    if (
      type(sample) is not FLMSample
      or type(sample.route) is not str
      or not sample.route
      or type(sample.segment) is not int
      or sample.segment < 0
      or any(type(getattr(sample, n)) not in (int, float) or not math.isfinite(getattr(sample, n)) for n in numeric)
      or any(type(getattr(sample, n)) is not bool for n in ('lat_active', 'steering_pressed', 'saturated'))
    ):
      raise ValueError('Actual finite original FLM sample required')
    previous = groups[-1][-1] if groups else None
    if previous is not None and (sample.route, sample.segment) == (previous.route, previous.segment) and sample.t <= previous.t:
      raise ValueError('Original source time must increase')
    if previous is None or (sample.route, sample.segment) != (previous.route, previous.segment) or sample.t - previous.t > 0.15:
      groups.append([])
    groups[-1].append(sample)
  runs = []
  for group in groups:
    stamps = tuple(row.t for row in group)
    start = math.ceil(stamps[0] * 10 - 1e-8)
    stop = math.floor(stamps[-1] * 10 + 1e-8)
    selected = []
    previous_index = -1
    for tick in range(start, stop + 1):
      wanted = tick / 10
      at = bisect_left(stamps, wanted)
      candidates = tuple(i for i in (at - 1, at) if 0 <= i < len(group))
      nearest = min(candidates, key=lambda i: (abs(stamps[i] - wanted), i))
      if abs(stamps[nearest] - wanted) > 0.03 + 1e-9 or nearest <= previous_index:
        if selected:
          runs.append(tuple(selected))
          selected = []
        previous_index = nearest
        continue
      selected.append(replace(group[nearest]))
      previous_index = nearest
    if selected:
      runs.append(tuple(selected))
  return tuple(runs)


def current_catalog(previous, baseline_revision):
  """Use the actual registered universal constructor, not an old car table.

  The old selector can choose from the fields this controller actually exposes.
  Angle/friction-normalized raw fields need per-frame physical values; they are
  deliberately absent here rather than being mislabeled as fixed coordinates.
  """
  from openpilot.starpilot.flm.canonical_surface_adapter import NAMES, RAW_DOMAINS, DEADBANDS, full_profile_sha256
  from openpilot.starpilot.flm.offroad_surface import RouteSurfaceEffects

  full_profile_sha256(baseline_revision, previous)
  source = RouteSurfaceEffects({NAMES[0]: previous[NAMES[0]]}, baseline_revision, accumulated=previous)
  dynamic = set(DEADBANDS) | {'low_speed_angle_assist_max_torque'}
  return {
    'torque_universal.' + name: {'defaultValue': float(source.surface.knobs[name]), 'min': limits[0], 'max': limits[1], 'precision': 0.001}
    for name, _, limits in RAW_DOMAINS
    if name not in dynamic
  }


def recommended_coordinates(profile, previous, baseline_revision, *, domain):
  """Translate ONLY generated, representable Recommended proposals.

  Numeric legacy base/friction parameters remain a separate controller contract.
  Old writer toggles are NOT executed: automatic learning remains enabled and
  operator/manual settings remain owned by their existing paths. The common
  route step scales a whole profile uniformly, preserving its knob proportions.
  This is a proposal, still owing original held-out/controller/native checks.
  """
  from openpilot.starpilot.flm.canonical_surface_adapter import NAMES, WINDOWS, RAW_DOMAINS, coordinate_bounds, full_profile_sha256
  from openpilot.starpilot.flm.offroad_store import MAX_ROUTE_STEP

  full_profile_sha256(baseline_revision, previous)
  if (
    type(profile) is not dict
    or profile.get('label') != 'Recommended'
    or not str(profile.get('id', '')).endswith(':recommended')
    or profile.get('capabilities', {}).get('richProfileKey') != 'torque_universal'
    or domain not in ('torque', 'torque_pid')
  ):
    raise ValueError('Generated Recommended profile for this registered controller required')
  flags = {'AdvancedLateralTune': True, 'ForceAutoTuneOff': True, 'ForceAutoTune': False}
  params = profile.get('genericParams', {})
  if type(params) is not dict or any(k not in flags or type(v) is not bool or v != flags[k] for k, v in params.items()):
    raise ValueError('Legacy numeric base settings require their own application contract')
  overrides = profile.get('flmOverrides')
  if (
    type(overrides) is not dict
    or overrides.get('schemaVersion') != 1
    or overrides.get('baseFrictionThresholds')
    or type(overrides.get('vehicleKnobs')) is not dict
    or not overrides['vehicleKnobs']
  ):
    raise ValueError('Legacy friction curves are not canonical amplitude knobs')
  catalog = current_catalog(previous, baseline_revision)
  original = {name: value for name, value, _ in RAW_DOMAINS}
  deltas = {}
  for symbol, target in overrides['vehicleKnobs'].items():
    if symbol not in catalog or type(target) not in (int, float) or not math.isfinite(target):
      raise ValueError('Unsupported or nonfinite original-effect knob')
    name = symbol.removeprefix('torque_universal.')
    meta = catalog[symbol]
    if not meta['min'] <= target <= meta['max']:
      raise ValueError('Legacy proposal exceeds actual raw field limits')
    coordinate = target / original[name] - 1 if name in WINDOWS else target
    deltas[name] = coordinate - previous[name]
  peak = max(abs(value) for value in deltas.values())
  if peak <= 1e-12:
    raise ValueError('No numerical change to propose')
  scale = min(1.0, MAX_ROUTE_STEP / peak)
  values = dict(previous)
  for name, delta in deltas.items():
    values[name] += delta * scale
    low, high = coordinate_bounds(name)
    if not low <= values[name] <= high or abs(values[name] - previous[name]) > MAX_ROUTE_STEP + 1e-12:
      raise ValueError('Actual original-effect or per-drive limit exceeded')
  if domain == 'torque_pid':
    from openpilot.starpilot.flm.pid_stages import UNSUPPORTED

    if any(values[name] != 0 for name in UNSUPPORTED):
      raise ValueError('Torque-PID cannot use a friction-only coordinate')
  full_profile_sha256(baseline_revision, values)
  from openpilot.starpilot.flm.offroad_surface import RouteSurfaceEffects

  RouteSurfaceEffects({NAMES[0]: values[NAMES[0]]}, baseline_revision, accumulated=values)
  return {
    'values': values,
    'changed_parameters': tuple(name for name in NAMES if values[name] != previous[name]),
    'profile': 'Recommended',
    'legacy_strength': 1.0,
    'bounded_profile_scale': scale,
    'maximum_coordinate_step': max(abs(values[n] - previous[n]) for n in NAMES),
    'automatic_application_qualified': False,
    'road_improvement_confirmed': False,
  }


def _current_stable_speed_band_label(v_ego: float) -> str:
  if v_ego < 6.0:
    return 'low'
  if v_ego < 15.0:
    return 'mid'
  if v_ego < 25.0:
    return 'fast'
  return 'highway'


def _current_stable_route_label(route: str, segment: int) -> str:
  return f'{route}/{segment}'


def _current_stable_event_direction(samples: list[FLMSample]) -> str:
  mean_desired = float(np.mean([sample.desired_la for sample in samples]))
  if mean_desired > 0.02:
    return 'left'
  if mean_desired < -0.02:
    return 'right'
  return 'center'


def _current_stable_group_masked_events(samples: list[FLMSample], mask: list[bool], score_series: list[float], min_points: int = 5) -> list[dict[str, Any]]:
  events: list[dict[str, Any]] = []
  start_idx = None
  for idx, active in enumerate(mask + [False]):
    if active and start_idx is None:
      start_idx = idx
      continue
    if active:
      continue
    if start_idx is None:
      continue
    end_idx = idx - 1
    event_samples = samples[start_idx : end_idx + 1]
    if len(event_samples) >= min_points:
      event_scores = score_series[start_idx : end_idx + 1]
      peak_offset = int(np.argmax(event_scores))
      peak_idx = start_idx + peak_offset
      peak_sample = samples[peak_idx]
      direction = _current_stable_event_direction(event_samples)
      events.append(
        {
          'startIdx': start_idx,
          'endIdx': end_idx,
          'peakIdx': peak_idx,
          'peakScore': float(event_scores[peak_offset]),
          'route': peak_sample.route,
          'segment': peak_sample.segment,
          'speedBand': _current_stable_speed_band_label(float(np.mean([sample.v_ego for sample in event_samples]))),
          'direction': direction,
          'supportCount': len(event_samples),
        }
      )
    start_idx = None
  return events


def _current_stable_analysis_eligibility_mask(samples: list[FLMSample]) -> list[bool]:
  eligible = [bool(sample.lat_active) for sample in samples]
  group_start = 0
  while group_start < len(samples):
    group_key = (samples[group_start].route, samples[group_start].segment)
    group_end = group_start + 1
    while group_end < len(samples) and (samples[group_end].route, samples[group_end].segment) == group_key:
      group_end += 1
    last_override = -math.inf
    for idx in range(group_start, group_end):
      sample = samples[idx]
      if sample.steering_pressed:
        last_override = sample.t
      if sample.steering_pressed or sample.t - last_override <= FLM_DRIVER_OVERRIDE_POST_BUFFER_S:
        eligible[idx] = False
    next_override = math.inf
    for idx in range(group_end - 1, group_start - 1, -1):
      sample = samples[idx]
      if sample.steering_pressed:
        next_override = sample.t
      if sample.steering_pressed or next_override - sample.t <= FLM_DRIVER_OVERRIDE_PRE_BUFFER_S:
        eligible[idx] = False
    eligible[group_start] = False
    eligible[group_end - 1] = False
    group_start = group_end
  return eligible


def _current_stable_build_plot_data(samples: list[FLMSample], event: dict[str, Any], eligibility: list[bool] | None = None) -> dict[str, Any]:
  start_idx = int(event['startIdx'])
  end_idx = int(event['endIdx'])
  event_route = samples[start_idx].route
  event_segment = samples[start_idx].segment
  for _ in range(12):
    candidate = start_idx - 1
    if candidate < 0 or (eligibility is not None and (not eligibility[candidate])):
      break
    if samples[candidate].route != event_route or samples[candidate].segment != event_segment:
      break
    start_idx = candidate
  for _ in range(12):
    candidate = end_idx + 1
    if candidate >= len(samples) or (eligibility is not None and (not eligibility[candidate])):
      break
    if samples[candidate].route != event_route or samples[candidate].segment != event_segment:
      break
    end_idx = candidate
  window = samples[start_idx : end_idx + 1]
  if len(window) < 2:
    return {}
  if len(window) > 160:
    indices = np.linspace(0, len(window) - 1, 160, dtype=int)
    window = [window[int(idx)] for idx in indices]
  times = np.array([sample.t for sample in window], dtype=float)
  desired = np.array([sample.desired_la for sample in window], dtype=float)
  actual = np.array([sample.actual_la for sample in window], dtype=float)
  relative_times = times - float(times[0])
  event_start_time = max(float(samples[int(event['startIdx'])].t - times[0]), 0.0)
  event_end_time = max(float(samples[int(event['endIdx'])].t - times[0]), event_start_time)
  return {
    'times': [round(float(value), 3) for value in relative_times],
    'desired': [round(float(value), 4) for value in desired],
    'actual': [round(float(value), 4) for value in actual],
    'windowDurationSec': round(float(relative_times[-1]), 2),
    'eventStartSec': round(event_start_time, 2),
    'eventEndSec': round(event_end_time, 2),
    'eventDurationSec': round(max(event_end_time - event_start_time, 0.0), 2),
    'meanSpeedMph': round(float(np.mean([sample.v_ego for sample in window])) * 2.236936, 1),
    'route': event_route,
    'segment': event_segment,
    'segmentLabel': _current_stable_route_label(event_route, event_segment),
    'direction': str(event.get('direction', 'center')),
    'speedBand': str(event.get('speedBand', 'mixed')),
    'driverOverrideFree': bool(eligibility is None or all(eligibility[start_idx : end_idx + 1])),
  }


def _current_stable_build_plot_svg(plot_data: dict[str, Any]) -> str:
  times = np.array(plot_data.get('times', []), dtype=float)
  desired = np.array(plot_data.get('desired', []), dtype=float)
  actual = np.array(plot_data.get('actual', []), dtype=float)
  if len(times) < 2 or len(desired) != len(times) or len(actual) != len(times):
    return ''
  time_span = max(float(times.max()), 0.001)
  y_min = float(min(np.min(desired), np.min(actual)))
  y_max = float(max(np.max(desired), np.max(actual)))
  y_pad = max((y_max - y_min) * 0.1, 0.1)
  y_min -= y_pad
  y_max += y_pad
  y_span = max(y_max - y_min, 0.001)

  def _points(series):
    coords = []
    for t_val, y_val in zip(times, series, strict=True):
      x = float(t_val) / time_span * 380.0
      y = 120.0 - (float(y_val) - y_min) / y_span * 120.0
      coords.append(f'{x:.1f},{y:.1f}')
    return ' '.join(coords)

  return f"<svg viewBox='0 0 380 140' class='flm-plot' preserveAspectRatio='none'><rect x='0' y='0' width='380' height='140' rx='8' ry='8' fill='#0f172a'/><line x1='0' y1='120' x2='380' y2='120' stroke='#334155' stroke-width='1'/><polyline fill='none' stroke='#ef4444' stroke-width='2' points='{_points(desired)}'/><polyline fill='none' stroke='#38bdf8' stroke-width='2' points='{_points(actual)}'/></svg>"


def _current_stable_direction_reversal_count(values: np.ndarray, min_step: float) -> int:
  if len(values) < 3:
    return 0
  deltas = np.diff(values)
  significant = deltas[np.abs(deltas) >= min_step]
  if len(significant) < 2:
    return 0
  return int(np.sum(np.sign(significant[1:]) != np.sign(significant[:-1])))


def _current_stable_build_event_summaries(samples: list[FLMSample]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
  eligibility = _current_stable_analysis_eligibility_mask(samples)
  active_samples = [sample for sample, allowed in zip(samples, eligibility, strict=True) if allowed]
  if not active_samples:
    return ([], {'sampleCount': 0})
  over_error = [abs(sample.actual_la) - abs(sample.desired_la) for sample in samples]
  desired = [sample.desired_la for sample in samples]
  jerk = [sample.desired_jerk for sample in samples]
  angle = [sample.steering_angle_deg for sample in active_samples]
  output = [sample.output for sample in active_samples]
  entry_phase = [abs(d) > 0.3 and abs(j) > 0.25 and (d * j > 0.0) for d, j in zip(desired, jerk, strict=True)]
  unwind_phase = [abs(d) > 0.25 and abs(j) > 0.2 and (d * j < 0.0) for d, j in zip(desired, jerk, strict=True)]
  steady_curve_phase = [abs(d) > 0.35 and abs(j) < 0.18 for d, j in zip(desired, jerk, strict=True)]
  saturation_phase = [
    bool(allowed and sample.saturated and (abs(sample.desired_la) > 0.3) and (abs(sample.desired_jerk) > 0.16 or abs(sample.actual_la) > 0.45))
    for sample, allowed in zip(samples, eligibility, strict=True)
  ]
  base_masks = {
    'understeer': [allowed and phase and (ov < -0.2) for allowed, phase, ov in zip(eligibility, steady_curve_phase, over_error, strict=True)],
    'oversteer': [allowed and phase and (ov > 0.2) for allowed, phase, ov in zip(eligibility, steady_curve_phase, over_error, strict=True)],
    'late_turn_in': [allowed and phase and (ov < -0.16) for allowed, phase, ov in zip(eligibility, entry_phase, over_error, strict=True)],
    'early_turn_in': [allowed and phase and (ov > 0.16) for allowed, phase, ov in zip(eligibility, entry_phase, over_error, strict=True)],
    'unwind_too_slow': [allowed and phase and (ov > 0.14) for allowed, phase, ov in zip(eligibility, unwind_phase, over_error, strict=True)],
    'unwind_too_fast': [allowed and phase and (ov < -0.14) for allowed, phase, ov in zip(eligibility, unwind_phase, over_error, strict=True)],
    'low_speed_unwillingness': [
      bool(
        allowed
        and sample.v_ego < 6.0
        and (abs(sample.desired_la) > 0.3)
        and (abs(sample.desired_jerk) > 0.18)
        and (abs(sample.actual_la) + 0.18 < abs(sample.desired_la))
      )
      for sample, allowed in zip(samples, eligibility, strict=True)
    ],
    'saturation_limited': saturation_phase,
  }
  score_map = {
    'understeer': [max(-ov, 0.0) for ov in over_error],
    'oversteer': [max(ov, 0.0) for ov in over_error],
    'late_turn_in': [max(-ov, 0.0) + abs(j) * 0.1 for ov, j in zip(over_error, jerk, strict=True)],
    'early_turn_in': [max(ov, 0.0) + abs(j) * 0.1 for ov, j in zip(over_error, jerk, strict=True)],
    'unwind_too_slow': [max(ov, 0.0) for ov in over_error],
    'unwind_too_fast': [max(-ov, 0.0) for ov in over_error],
    'low_speed_unwillingness': [max(abs(d) - abs(sample.actual_la), 0.0) for d, sample in zip(desired, samples, strict=True)],
    'saturation_limited': [1.0 if sample.saturated else 0.0 for sample in samples],
  }
  straight_windows = []
  angle_thresholds = {'low': 0.8, 'mid': 0.55, 'fast': 0.38, 'highway': 0.28}
  error_thresholds = {'low': 0.16, 'mid': 0.12, 'fast': 0.09, 'highway': 0.07}
  output_thresholds = {'low': 0.055, 'mid': 0.045, 'fast': 0.035, 'highway': 0.025}
  for start_idx in range(0, max(len(samples) - 20, 1), 10):
    window = samples[start_idx : start_idx + 40]
    if len(window) < 20:
      continue
    if not all(eligibility[start_idx : start_idx + len(window)]):
      continue
    mean_speed = float(np.mean([sample.v_ego for sample in window]))
    if mean_speed < 2.0:
      continue
    speed_band = _current_stable_speed_band_label(mean_speed)
    desired_series = np.array([sample.desired_la for sample in window])
    if float(np.mean(np.abs(desired_series))) > (0.14 if speed_band == 'low' else 0.18):
      continue
    desired_span = float(np.ptp(desired_series))
    desired_reversals = _current_stable_direction_reversal_count(desired_series, 0.008)
    if desired_span > 0.18 or desired_reversals > 3:
      continue
    angle_series = np.array([sample.steering_angle_deg for sample in window])
    angle_trend = np.linspace(angle_series[0], angle_series[-1], len(angle_series))
    centered_angles = angle_series - angle_trend
    error_series = np.array([sample.actual_la - sample.desired_la for sample in window])
    output_series = np.array([sample.output for sample in window])
    angle_p2p = float(np.ptp(centered_angles))
    error_p2p = float(np.ptp(error_series))
    output_p2p = float(np.ptp(output_series))
    angle_reversals = _current_stable_direction_reversal_count(centered_angles, max(angle_thresholds[speed_band] * 0.08, 0.025))
    error_reversals = _current_stable_direction_reversal_count(error_series, max(error_thresholds[speed_band] * 0.08, 0.006))
    output_reversals = _current_stable_direction_reversal_count(output_series, max(output_thresholds[speed_band] * 0.08, 0.002))
    angle_evidence = angle_p2p >= angle_thresholds[speed_band] and angle_reversals >= 3
    error_evidence = error_p2p >= error_thresholds[speed_band] and error_reversals >= 3
    output_evidence = output_p2p >= output_thresholds[speed_band] and output_reversals >= 3
    if angle_evidence and (error_evidence or output_evidence):
      chatter_score = min(
        1.5,
        0.3 * (angle_p2p / angle_thresholds[speed_band])
        + 0.18 * (error_p2p / error_thresholds[speed_band])
        + 0.18 * (output_p2p / output_thresholds[speed_band])
        + 0.025 * min(angle_reversals + error_reversals + output_reversals, 14),
      )
      straight_windows.append(
        {
          'startIdx': start_idx,
          'endIdx': start_idx + len(window) - 1,
          'peakIdx': start_idx + int(len(window) / 2),
          'peakScore': chatter_score,
          'route': window[0].route,
          'segment': window[0].segment,
          'speedBand': speed_band,
          'direction': 'center',
          'supportCount': len(window),
          'metrics': {
            'meanSpeedMps': round(mean_speed, 3),
            'steeringAngleP2P': round(angle_p2p, 4),
            'trackingErrorP2P': round(error_p2p, 4),
            'outputP2P': round(output_p2p, 4),
            'steeringReversals': angle_reversals,
            'trackingErrorReversals': error_reversals,
            'outputReversals': output_reversals,
            'desiredP2P': round(desired_span, 4),
            'desiredReversals': desired_reversals,
          },
        }
      )
  curve_windows = []
  for start_idx in range(0, max(len(samples) - 20, 1), 8):
    window = samples[start_idx : start_idx + 36]
    if len(window) < 18:
      continue
    if not all(eligibility[start_idx : start_idx + len(window)]):
      continue
    if float(np.mean([sample.v_ego for sample in window])) < 15.0:
      continue
    desired_sign = float(np.mean([sample.desired_la for sample in window]))
    if abs(desired_sign) < 0.35:
      continue
    if any((sample.desired_la * desired_sign < 0.0 for sample in window)):
      continue
    error_series = np.array([sample.actual_la - sample.desired_la for sample in window])
    sign_changes = int(np.sum(np.sign(error_series[1:]) != np.sign(error_series[:-1])))
    amplitude = float(np.max(error_series) - np.min(error_series))
    if amplitude > 0.22 and sign_changes >= 4:
      curve_windows.append(
        {
          'startIdx': start_idx,
          'endIdx': start_idx + len(window) - 1,
          'peakIdx': start_idx + int(np.argmax(np.abs(error_series))),
          'peakScore': amplitude + sign_changes * 0.03,
          'route': window[0].route,
          'segment': window[0].segment,
          'speedBand': _current_stable_speed_band_label(float(np.mean([sample.v_ego for sample in window]))),
          'direction': 'left' if desired_sign > 0.0 else 'right',
          'supportCount': len(window),
        }
      )
  summaries: list[dict[str, Any]] = []
  for bucket, mask in base_masks.items():
    events = _current_stable_group_masked_events(samples, mask, score_map[bucket])
    if events:
      summaries.extend(_current_stable_summaries_from_events(bucket, samples, events, eligibility))
  if straight_windows:
    summaries.extend(_current_stable_summaries_from_events('center_chatter', samples, straight_windows, eligibility))
  if curve_windows:
    summaries.extend(_current_stable_summaries_from_events('notchy_mid_curve', samples, curve_windows, eligibility))
  left_errors = [abs(sample.actual_la) - abs(sample.desired_la) for sample in active_samples if sample.desired_la > 0.25]
  right_errors = [abs(sample.actual_la) - abs(sample.desired_la) for sample in active_samples if sample.desired_la < -0.25]
  summary_stats = {
    'sampleCount': len(active_samples),
    'excludedDriverOverrideSamples': sum((1 for sample, allowed in zip(samples, eligibility, strict=True) if sample.lat_active and (not allowed))),
    'qlogFallback': False,
    'meanDesiredAbs': round(float(np.mean(np.abs([sample.desired_la for sample in active_samples]))), 4),
    'meanErrorAbs': round(float(np.mean(np.abs([sample.actual_la - sample.desired_la for sample in active_samples]))), 4),
    'leftBias': round(float(np.mean(left_errors)), 4) if left_errors else 0.0,
    'rightBias': round(float(np.mean(right_errors)), 4) if right_errors else 0.0,
    'highwayStraightAngleP2P': round(float(np.percentile(np.abs(angle), 95) - np.percentile(np.abs(angle), 5)), 4) if angle else 0.0,
    'meanOutputAbs': round(float(np.mean(np.abs(output))), 4) if output else 0.0,
  }
  if summary_stats['meanErrorAbs'] < 0.08 and (not any((summary['severity'] > 0.65 for summary in summaries))):
    summaries.append(
      {
        'bucket': 'model_limited',
        'dimensionId': 'model_limited:overall',
        'direction': 'center',
        'speedBand': 'mixed',
        'count': 1,
        'severity': 0.25,
        'evidence': {'speedBand': 'mixed', 'directionBias': 'center', 'eventCount': 1, 'segments': []},
        'events': [],
        'plotSvg': '',
        'plotData': {},
      }
    )
  return (sorted(summaries, key=lambda item: item['severity'], reverse=True), summary_stats)


def _current_stable_summaries_from_events(
  bucket: str, samples: list[FLMSample], events: list[dict[str, Any]], eligibility: list[bool] | None = None
) -> list[dict[str, Any]]:
  grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
  for event in events:
    event_speed_band = event['speedBand'] if bucket == 'center_chatter' else 'mixed'
    key = (bucket, event['direction'], event_speed_band)
    grouped.setdefault(key, []).append(event)
  summaries = []
  for (bucket_name, direction, _group_speed_band), grouped_events in grouped.items():
    grouped_events.sort(key=lambda item: item['peakScore'], reverse=True)
    strongest = grouped_events[:3]
    strongest_labels = [
      {
        'route': event['route'],
        'segment': event['segment'],
        'label': _current_stable_route_label(event['route'], event['segment']),
        'score': round(float(event['peakScore']), 3),
      }
      for event in strongest
    ]
    top_event = strongest[0]
    top_speed_band = top_event['speedBand']
    plot_data = _current_stable_build_plot_data(samples, top_event, eligibility)
    summaries.append(
      {
        'bucket': bucket_name,
        'dimensionId': f'{bucket_name}:{direction}:{top_speed_band}',
        'direction': direction,
        'speedBand': top_speed_band,
        'count': len(grouped_events),
        'severity': round(float(min(1.5, np.mean([event['peakScore'] for event in strongest]))), 3),
        'evidence': {
          'speedBand': top_speed_band,
          'directionBias': direction,
          'eventCount': len(grouped_events),
          'segments': strongest_labels,
          'chatterMetrics': top_event.get('metrics', {}),
        },
        'events': grouped_events,
        'plotSvg': _current_stable_build_plot_svg(plot_data),
        'plotData': plot_data,
      }
    )
  return summaries


def _current_stable_classify_torque_samples(samples: list[FLMSample]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
  return _current_stable_build_event_summaries(samples)


CURRENT_CLASSIFIER_SOURCE = '4a553da9ac22a14e34513c011aed04c1e93b4619'
CURRENT_CLASSIFIER_BLOB = 'd3be0d72fd4ecdf50c7a456d31b46c0184a45210'
classify_current_stable = _current_stable_classify_torque_samples


def recommended_route_profile(profile, previous, baseline_revision, *, domain):
  from openpilot.starpilot.flm.offroad_surface import NAMES, ROUTE_NAMES, CURVE_NAMES, CURVE_SPEEDS, route_values, route_bounds, RouteSurfaceEffects
  from openpilot.starpilot.flm.canonical_surface_adapter import RAW_DOMAINS, WINDOWS
  from openpilot.starpilot.flm.offroad_store import MAX_ROUTE_STEP

  current = route_values(previous)
  if (
    type(profile) is not dict
    or profile.get('label') != 'Recommended'
    or not str(profile.get('id', '')).endswith(':recommended')
    or profile.get('capabilities', {}).get('richProfileKey') != 'torque_universal'
    or domain not in ('torque', 'torque_pid')
  ):
    raise ValueError('Generated controller-bound Recommended route profile required')
  flags = {'AdvancedLateralTune': True, 'ForceAutoTuneOff': True, 'ForceAutoTune': False}
  params = profile.get('genericParams', {})
  if type(params) is not dict or any(k not in flags or type(v) is not bool or v != flags[k] for k, v in params.items()):
    raise ValueError('Legacy numeric settings cannot bypass their application contract')
  overrides = profile.get('flmOverrides', {})
  if type(overrides) is not dict or set(overrides) != {'schemaVersion', 'baseFrictionThresholds', 'vehicleKnobs'} or overrides['schemaVersion'] != 1:
    raise ValueError('Exact generated route proposal required')
  amp = {name: current[name] for name in NAMES}
  catalog = current_catalog(amp, baseline_revision)
  original = {name: value for name, value, _ in RAW_DOMAINS}
  deltas = {}
  for symbol, target in overrides['vehicleKnobs'].items():
    if symbol not in catalog or type(target) not in (int, float) or not math.isfinite(target):
      raise ValueError('Unsupported original-effect field')
    name = symbol.removeprefix('torque_universal.')
    metadata = catalog[symbol]
    if not metadata['min'] <= target <= metadata['max']:
      raise ValueError('Actual raw field limit')
    coordinate = target / original[name] - 1 if name in WINDOWS else target
    deltas[name] = coordinate - current[name]
  curves = overrides['baseFrictionThresholds']
  if curves:
    family = profile['capabilities'].get('frictionFamily')
    if domain != 'torque' or profile.get('frictionCurveUnits') != 'relative_actual_selected_threshold' or type(curves) is not dict or set(curves) != {family}:
      raise ValueError('Explicit relative actual-threshold curve required')
    curve = curves[family]
    if curve.get('speedKnots') != list(CURVE_SPEEDS) or len(curve.get('values', ())) != 5:
      raise ValueError('Actual five-point threshold curve required')
    for name, target in zip(CURVE_NAMES, curve['values']):
      if type(target) not in (int, float) or not math.isfinite(target) or target <= 0:
        raise ValueError('Finite positive threshold factor required')
      deltas[name] = target - 1.0 - current[name]
  if not deltas or max(abs(v) for v in deltas.values()) <= 1e-12:
    raise ValueError('No proposed numerical change')
  scale = min(1.0, MAX_ROUTE_STEP / max(abs(v) for v in deltas.values()))
  values = dict(current)
  for name, delta in deltas.items():
    values[name] = round(current[name] + delta * scale, 12)
    low, high = route_bounds(name)
    if not low <= values[name] <= high or abs(values[name] - current[name]) > MAX_ROUTE_STEP + 1e-12:
      raise ValueError('Original-effect/per-route envelope')
  if domain == 'torque_pid':
    from openpilot.starpilot.flm.pid_stages import UNSUPPORTED

    if any(values[n] for n in (*UNSUPPORTED, *CURVE_NAMES)):
      raise ValueError('No fabricated PID friction')
  RouteSurfaceEffects({NAMES[0]: values[NAMES[0]]}, baseline_revision, accumulated=values)
  return {
    'values': values,
    'changed_parameters': tuple(n for n in ROUTE_NAMES if values[n] != current[n]),
    'maximum_coordinate_step': max(abs(values[n] - current[n]) for n in ROUTE_NAMES),
    'bounded_profile_scale': scale,
    'automatic_application_qualified': False,
    'road_improvement_confirmed': False,
  }
