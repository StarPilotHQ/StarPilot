import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { normalizeSnapshot, processFeature, processRows, processState, displayNumber, vital } from "../web/js/system-monitor-data.js"

const fixture = JSON.parse(readFileSync(new URL("../web/data/system-monitor.sample.json", import.meta.url), "utf8"))
const sample = normalizeSnapshot(fixture)
assert.equal(sample.mode, undefined)
assert.equal(sample.processCount, 6)
assert.equal(sample.cpuPercent, 27.4)
assert.equal(processRows(sample).length, 4)
assert.deepEqual(processRows(sample).map(p => p.pid), [102, 101, 104, 103])
assert.deepEqual(processRows(sample, { scope: "users" }).map(p => p.pid), [102, 101, 105, 104, 103])
assert.equal(processRows(sample, { scope: "all" }).length, 6)
assert.deepEqual(processRows(sample, { query: "driving model" }).map(p => p.pid), [102])
assert.deepEqual(processRows(sample, { query: "sleeping", scope: "all" }).map(p => p.pid), [102, 104, 2, 103])
assert.deepEqual(processRows(sample, { sort: "cpu", descending: false, scope: "all" }).map(p => p.pid), [104, 105, 101, 102, 2, 103])
assert.equal(processFeature(sample.processes.at(-1)), "")
assert.equal(processState("R"), "Running")
assert.equal(processState("?"), "?")
assert.equal(processState("constructor"), "constructor")
assert.equal(processState("toString"), "toString")
assert.equal(displayNumber(null, "%"), "—")
assert.equal(displayNumber(0, "%"), "0.0%")
assert.equal(vital(sample, "cpuTempC", "onboardMaxAgeMs", 2499), 51.2)
assert.equal(vital(sample, "cpuTempC", "onboardMaxAgeMs", 2500), null)
assert.equal(vital(sample, "hotspotTempC", "maxAgeMs"), null)
assert.throws(() => normalizeSnapshot({ ...fixture, cores: null }), /Invalid/)
assert.throws(() => normalizeSnapshot({ ...fixture, processes: [{ pid: 1 }] }), /Invalid/)
assert.throws(() => normalizeSnapshot({ ...fixture, cores: [fixture.cores[0], fixture.cores[0]] }), /Duplicate/)
assert.throws(() => normalizeSnapshot({ ...fixture, processes: [fixture.processes[0], fixture.processes[0]] }), /Duplicate/)
const withUnknowns = normalizeSnapshot({ ...fixture, cpuPercent: -1,
  processes: [{ ...fixture.processes[0], cpu: Infinity, memoryMiB: "0" }] })
assert.equal(withUnknowns.cpuPercent, null)
assert.equal(withUnknowns.processes[0].cpu, null)
assert.equal(withUnknowns.processes[0].memoryMiB, null)
const impossible = normalizeSnapshot({ ...fixture,
  memory: { ...fixture.memory, usedMiB: fixture.memory.totalMiB + 1 },
  storage: { usedGiB: 200, totalGiB: 100 },
  vitals: { ...fixture.vitals, memoryUsedBytes: 200, memoryTotalBytes: 100, memoryMaxAgeMs: 2500 } })
assert.equal(impossible.memory.usedMiB, null)
assert.equal(impossible.memory.percent, null)
assert.equal(impossible.storage.usedGiB, null)
const twoFilesystems = normalizeSnapshot({ ...fixture, deviceType: "tici",
  storage: { usedGiB: 20.6, totalGiB: 29.8, freeGiB: 7.6 },
  recordingStorage: { usedGiB: 598, totalGiB: 916, freeGiB: 273 } })
assert.deepEqual(twoFilesystems.storage, { usedGiB: 20.6, totalGiB: 29.8, freeGiB: 7.6 })
assert.deepEqual(twoFilesystems.recordingStorage, { usedGiB: 598, totalGiB: 916, freeGiB: 273 })
for (const deviceType of ["tizi", "mici", "pc", "tici2", undefined]) {
  assert.equal(normalizeSnapshot({ ...fixture, deviceType,
    recordingStorage: { usedGiB: 598, totalGiB: 916, freeGiB: 273 } }).recordingStorage, null)
}
for (const absent of [undefined, null, [], "SSD"]) {
  assert.equal(normalizeSnapshot({ ...fixture, deviceType: "tici", recordingStorage: absent }).recordingStorage, null)
}
for (const bad of [{}, { usedGiB: "598", totalGiB: Infinity, freeGiB: -1 },
  { usedGiB: -1, totalGiB: NaN, freeGiB: "273" }]) {
  assert.deepEqual(normalizeSnapshot({ ...fixture, deviceType: "tici", recordingStorage: bad }).recordingStorage,
    { usedGiB: null, totalGiB: null, freeGiB: null })
}
assert.deepEqual(normalizeSnapshot({ ...fixture, deviceType: "tici",
  recordingStorage: { usedGiB: 200, totalGiB: 100, freeGiB: 101 } }).recordingStorage,
  { usedGiB: null, totalGiB: 100, freeGiB: null })
assert.equal(normalizeSnapshot({ ...fixture, deviceType: "tici",
  recordingStorage: { usedGiB: 90, totalGiB: 100, freeGiB: 0 } }).recordingStorage.freeGiB, 0)
assert.equal(vital(impossible, "memoryUsedBytes", "memoryMaxAgeMs"), null)
assert.equal(vital(impossible, "memoryTotalBytes", "memoryMaxAgeMs"), null)
console.log("System Monitor data contract: pass")

assert.equal(normalizeSnapshot({...fixture,batteryVoltage:12.64}).batteryVoltage,12.64)
for (const bad of [null,undefined,'12.6',NaN,Infinity,0,-12,10000]) {
  assert.equal(normalizeSnapshot({...fixture,batteryVoltage:bad}).batteryVoltage,null)
}
