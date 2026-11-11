import { FRICTION_NAMES, SURFACE_GROUPS, SURFACE_NAMES } from './flm-training-results.js'

export function dialPercent(value) {
  if (!Number.isFinite(value)) return 'Not reported'
  const percent = value * 100
  return `${percent !== 0 && Math.abs(percent) < .000001 ? percent.toPrecision(3) : Number(percent.toFixed(6))}%`
}

export function ideaStatus(row) {
  if (row.target === null) return 'Conflicting findings'
  if (row.reason === 'heldout_profile_does_not_support_training_step') return 'Not confirmed across the drive'
  return 'Not approved for automatic saving'
}

export function ideaExplanation(row) {
  if (row.target === null) return 'Different parts of the drive pointed to different settings. There is no single value to use.'
  if (row.reason === 'heldout_profile_does_not_support_training_step')
    return 'Some driving supported this idea, but other parts did not. Auto Learn kept it out of your saved tune.'
  if (row.reason === 'independent_support_for_frozen_training_profile')
    return 'Separate parts of the recording supported this idea. It still needs to pass the automatic-save checks.'
  return 'The recording suggested a change here. It has not passed all the checks needed to save it automatically.'
}

export function reviewModel(route, historical = false, updating = false) {
  if (!route) return { title: 'Your next drive starts here', description: 'After a completed drive, Auto Learn will show what it found and whether it saved a change.',
    next: 'Wait for a completed-drive report.', tone: 'neutral', manual: [], manualGroups: [], curve: [], other: [], findings: [], saved: [], areas: [], count: 0 }
  const findings = route.suggestions.map(row => ({ ...row,
    kind: SURFACE_NAMES.includes(row.parameter) ? 'manual' : FRICTION_NAMES.includes(row.parameter) ? 'curve' : 'earlier',
    destination: SURFACE_NAMES.includes(row.parameter) ? 'tuner-dial-' + row.parameter : FRICTION_NAMES.includes(row.parameter) ? 'tuner-friction' : 'tuner-earlier-findings',
  }))
  const manual = findings.filter(row => row.kind === 'manual')
  const curve = findings.filter(row => row.kind === 'curve')
  const other = findings.filter(row => row.kind === 'earlier')
  const manualGroups = SURFACE_GROUPS.map(group => ({ ...group, findings: manual.filter(row => group.names.includes(row.parameter)) })).filter(group => group.findings.length)
  const saved = Object.entries(route.savedValues || {}).filter(([, value]) => value !== 0).map(([parameter, value]) => ({ parameter, value }))
  const areas = manualGroups.map(group => group.label)
  if (curve.length) areas.push('Small corrections by speed')
  if (other.length) areas.push('Earlier grouped findings')
  const count = findings.length
  let title = count ? `${count} ${count === 1 ? 'finding' : 'findings'} to review` : 'No new change selected'
  if (['no_new_native_feasible_training_step', 'no_consistent_adjustment_in_completed_drive',
    'recorded_tracking_suggestion_not_yet_qualified_for_automatic_application',
    'original_controller_or_registered_encoder_check_refused'].includes(route.reason)) title = 'Your tune stayed the same'
  let description = count ? 'No change was saved from this drive. You do not need to adjust anything.' :
    'No new change was saved. Your current tune stays in place, and there is nothing you need to do.'
  let tone = count ? 'review' : 'neutral'
  let next = count ? 'Keep driving with your current tune. These findings are optional; they have not been approved for automatic saving.' : 'No action is needed. Auto Learn can review the next completed drive when it is enabled.'
  if (route.configurationState === 'ready_for_next_drive') {
    title = 'A tune is saved for your next drive'
    description = 'Auto Learn will compare it with new driving. A saved trial is not yet a confirmed improvement.'
    tone = 'saved'
    next = 'A new drive is needed to measure the saved trial. Saved does not mean verified or currently in use.'
  } else if (route.configurationState === 'rolled_back') {
    title = 'Your previous tune was restored'
    description = 'The trial was rolled back. Any other ideas below are separate from the restored tune.'
    tone = 'neutral'
    next = 'The restored values are listed in Your tune. Other findings are separate ideas.'
  } else if (['accepted', 'accepted_after_drive'].includes(route.configurationState)) {
    title = 'Your learned tune is saved'
    description = 'Your learned values are saved. In-use status is reported separately during driving.'
    tone = 'saved'
    next = 'The learned values are saved. The controller reports their use separately during driving.'
  }
  if (route.saved && !['ready_for_next_drive', 'accepted', 'accepted_after_drive', 'rolled_back'].includes(route.configurationState)) {
    title = 'A configuration change was reported'
    description = 'The report does not identify its saved state. Check the latest report before treating it as ready for the next drive.'
    tone = 'neutral'
    next = 'Wait for a complete saved-state report before treating the change as ready.'
  }
  if (historical) {
    title = 'Earlier drive findings'
    description = updating ? 'Auto Learn is checking your completed drive. These earlier findings stay available while it runs; they are not a new saved tune.' :
      'These are earlier findings. They do not confirm the current saved tune; reviewing them does not apply a change.'
    tone = 'neutral'
    next = updating ? 'No action is needed while the new analysis runs.' : 'Wait for a current report to confirm the saved state.'
  }
  return { title, description, next, tone, manual, manualGroups, curve, other, findings, saved, areas, count, historical }
}

export function ideaValue(row) {
  if (row.target === null) return 'No single suggested value'
  if (row.target === undefined) return `Change ${row.delta > 0 ? '+' : ''}${dialPercent(row.delta)}`
  return `${dialPercent(row.baseline)} → ${dialPercent(row.target)}`
}


export function ideaHeading(name) {
  const labels = {
    highway_center_taper_max: 'Highway steering smoothness', center_taper_max: 'Small straight-road corrections',
    ff_gain_left: 'Steering response in left turns', ff_gain_right: 'Steering response in right turns',
    turn_in_boost_left: 'Starting left turns', turn_in_boost_right: 'Starting right turns',
    unwind_taper_left: 'Finishing left turns', unwind_taper_right: 'Finishing right turns',
  }
  return labels[name] || SURFACE_GROUPS.find(group => group.names.includes(name))?.label || 'Steering setting to review'
}

export function curvePointLabel(name) {
  return { friction_threshold_crawl: 'Crawling speed', friction_threshold_low: 'Low speed',
    friction_threshold_mid: 'Mid speed', friction_threshold_fast: 'Higher speeds', friction_threshold_highway: 'Highway speed' }[name] || name
}

export function ideaDetails(row) {
  const baseline = Number.isFinite(row.baseline) ? [{ label: 'Recorded baseline', value: dialPercent(row.baseline) }] : []
  if (row.target === null) return [...baseline, { label: 'Recorded candidate', value: 'No single suggested value' }]
  if (row.target === undefined) return [...baseline, { label: 'Recorded change', value: (row.delta > 0 ? '+' : '') + dialPercent(row.delta) }]
  return [...baseline, { label: 'Recorded candidate', value: dialPercent(row.target) }]
}
