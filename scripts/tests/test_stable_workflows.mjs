import fs from 'node:fs'
import vm from 'node:vm'
import assert from 'node:assert/strict'
import test from 'node:test'
const root = process.argv[2] || new URL('../', import.meta.url).pathname
const workflows = ['tests.yaml', 'safety.yaml', 'docs.yaml', 'flm-offroad.yaml', 'galaxy-phone-bridge.yaml', 'simulator-driving.yaml']
for (const workflow of workflows) {
  const source = fs.readFileSync(root + '/.github/workflows/' + workflow, 'utf8')
  const script = source.split('          script: |\n')[1].split('\n').map(line => line.slice(12)).join('\n')
  test(workflow + ' dispatches only the genuine Testing workflow without claiming qualification', async () => {
    const calls = [], notices = []
    const context = {repo: {owner: 'StarPilotHQ', repo: 'StarPilot'}}
    const github = {rest: {
      repos: {getBranch: async args => {calls.push(['branch', args]); return {data: {commit: {sha: 'a'.repeat(40)}}}}},
      actions: {createWorkflowDispatch: async args => {calls.push(['dispatch', args])}},
    }}
    await vm.runInNewContext('(async () => {' + script + '})()', {github, context, core: {notice: message => notices.push(message)}})
    assert.deepEqual(JSON.parse(JSON.stringify(calls)), [
      ['branch', {...context.repo, branch: 'testing'}],
      ['dispatch', {...context.repo, workflow_id: workflow, ref: 'testing'}],
    ])
    assert.match(notices[0], /this job only dispatches/)
    assert.match(source, /actions: write/)
    assert.doesNotMatch(source, /contents: write|schedule:|pull_request:|\bpush:/)
  })
  test(workflow + ' fails before dispatch if Testing is unavailable', async () => {
    let dispatched = false
    const github = {rest: {repos: {getBranch: async () => {throw Error('missing Testing')}}, actions: {createWorkflowDispatch: async () => {dispatched = true}}}}
    await assert.rejects(vm.runInNewContext('(async () => {' + script + '})()', {github, context: {repo: {}}, core: {notice() {}}}), /missing Testing/)
    assert.equal(dispatched, false)
  })
}
