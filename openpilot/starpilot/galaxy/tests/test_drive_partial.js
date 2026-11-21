import assert from 'node:assert/strict'
import { validDriveStats } from '../web/js/drive-stats.js'
import { driveSummary, Home } from '../web/js/home.js'

const route = { routeId: '2026-11-18--10-00-00', startTime: 1794996000, endTime: 1794996060,
  distanceMeters: 600, durationSeconds: 60, engagedSeconds: 0, engagedPercent: 0,
  aolSeconds: 60, aolPercent: 100, model: null, distractedMoments: null, unresponsiveMoments: null,
  complete: false, partial: true, reason: 'Recorded portion only; final segment unreadable', segmentCount: 2, ignored: false }
const data = { schemaVersion: 1, isMetric: true,
  analysis: { running: false, pending: 0, failed: 0, scanIncomplete: false },
  totals: { distanceMeters: 600, durationSeconds: 60, engagedSeconds: 0, drives: 1 },
  week: { days: Array.from({length: 7}, (_, i) => ({ date: `2026-11-${16 + i}`, distanceMeters: 0 })),
    distanceMeters: 600, durationSeconds: 60, engagedPercent: 0, drives: 1 },
  recentDrives: [route], lastDrive: route, records: [], models: [], coverage: { partialRoutes: 1 } }
assert.equal(validDriveStats(data), true)
for (const partial of ['true', 1, null]) {
  assert.equal(validDriveStats({ ...data, lastDrive: { ...route, partial } }), false)
}
assert.equal(validDriveStats({ ...data, lastDrive: { ...route, complete: true } }), false)
const summary = driveSummary({status: 'ready', data})
assert.equal(summary.last.partial, true)
assert.equal(summary.last.analysisLabel, 'Recorded portion only')
assert.equal(summary.recent[0].partial, true)
assert.equal(summary.partialRoutes, 1)
assert.match(Home.template, /Recorded portion included in totals/)
console.log('Partial dashboard labels and validation passed')
