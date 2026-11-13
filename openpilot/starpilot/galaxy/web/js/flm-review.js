import { FRICTION_NAMES, SURFACE_GROUPS, SURFACE_NAMES, isConsistentSuggestion } from './flm-training-results.js'

export function dialPercent(value) {
  if (!Number.isFinite(value)) return 'Not reported'
  const percent = value * 100
  return `${percent !== 0 && Math.abs(percent) < .000001 ? percent.toPrecision(3) : Number(percent.toFixed(6))}%`
}

export function ideaStatus(row) {
  if (row.target === null) return 'Conflicting findings'
  if (row.reason === 'heldout_profile_does_not_support_training_step') return 'Not confirmed across the drive'
  return 'Suggested change'
}

export function ideaExplanation(row) {
  if (!isConsistentSuggestion(row)) return 'Different parts of the drive did not support the same change. Your tune was left unchanged.'
  const next = SURFACE_NAMES.includes(row.parameter) ? ' You can review the suggested value and adjust this setting manually while parked.' :
    ' Auto Learn will review it as more driving is recorded.'
  if (row.reason === 'independent_support_for_frozen_training_profile')
    return 'Separate parts of your drive support this suggestion. Auto Learn still needs to confirm the controller checks before saving it.' + next
  return 'Your drive suggests this change, but Auto Learn needs more evidence before saving it automatically.' + next
}

export function reviewModel(route, historical = false, updating = false) {
  if (!route) return { title: 'Your next drive starts here', description: 'After a completed drive, Auto Learn will show what it found and whether it saved a change.',
    next: 'Wait for a completed-drive report.', tone: 'neutral', manual: [], manualGroups: [], curve: [], other: [], findings: [], saved: [], areas: [], count: 0 }
  const findings = route.suggestions.filter(isConsistentSuggestion).map(row => ({ ...row,
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
  let title = count ? 'Your drive suggestions' : 'Your drive review'
  if (['no_new_native_feasible_training_step', 'no_consistent_adjustment_in_completed_drive',
    'recorded_tracking_suggestion_not_yet_qualified_for_automatic_application',
    'original_controller_or_registered_encoder_check_refused'].includes(route.reason)) title = count ? 'Your drive suggestions' : 'Your tune stayed the same'
  let description = count ? 'Review these suggestions alongside your saved tune. Auto Learn saves changes after confirming them against your driving.' :
    'No new change was saved. Your current tune stays in place, and there is nothing you need to do.'
  let tone = count ? 'review' : 'neutral'
  let next = count ? 'Auto Learn will save changes it can confirm. You can also review these suggestions and adjust the manual settings while parked.' : 'No action is needed. Auto Learn can review the next completed drive when it is enabled.'
  if (route.configurationState === 'ready_for_next_drive') {
    title = 'Auto Learn saved a tune'
    description = 'These are the last reported saved values. The controller checks that they match your setup before using them.'
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
  if (historical && !updating) {
    if (saved.length || ['ready_for_next_drive', 'accepted', 'accepted_after_drive'].includes(route.configurationState)) {
      tone = 'neutral'
      title = 'Previously saved tune'
      description = 'These saved values come from an earlier report. Current use is shown separately during driving.'
      next = 'Review the latest saved state before treating an earlier tune as current.'
    }
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
  const baseline = Number.isFinite(row.baseline) ? [{ label: 'Starting value', value: dialPercent(row.baseline) }] : []
  if (row.target === null) return [...baseline, { label: 'Suggested value', value: 'No single suggested value' }]
  if (row.target === undefined) return [...baseline, { label: 'Suggested change', value: (row.delta > 0 ? '+' : '') + dialPercent(row.delta) }]
  return [...baseline, { label: 'Suggested value', value: dialPercent(row.target) }]
}
