import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import { compileScript, parse } from '@vue/compiler-sfc'
import * as vue from 'vue'
import * as pinia from 'pinia'
import ts from 'typescript'

// Run the actual page setup and flow store against isolated status objects.
// No browser, WebSocket, controller or SDK is used by this test.
async function compile(relative) {
  const source = await readFile(new URL(relative, import.meta.url), 'utf8')
  const content = relative.endsWith('.vue')
    ? compileScript(parse(source).descriptor, { id: relative }).content : source
  return ts.transpileModule(content, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText
}
function evaluate(compiled, modules = {}) {
  const module = { exports: {} }
  const require = name => {
    if (name.startsWith('@/components/')) return { default: {} }
    if (!(name in modules)) throw new Error(`Unexpected dependency: ${name}`)
    return modules[name]
  }
  new Function('require', 'module', 'exports', compiled)(require, module, module.exports)
  return module.exports
}
const plans = evaluate(await compile('../src/data/plans.ts'))
const points = evaluate(await compile('../src/data/points.ts'))
const runData = evaluate(await compile('../src/data/run.ts'))
const { useFlowStore, levelRange } = evaluate(await compile('../src/stores/flow.ts'), {
  vue, pinia, '@/data/plans': plans, '@/data/points': points,
  '@/types/record': { loadRecords: () => [], saveRecords() {}, createRecordId: () => 'test-record' },
})
const pages = Object.fromEntries(await Promise.all(['ConfirmView', 'RunView', 'FinishView'].map(async name =>
  [name, await compile(`../src/views/service/${name}.vue`)])))

function setup(name = 'ConfirmView') {
  pinia.setActivePinia(pinia.createPinia())
  const flow = useFlowStore()
  flow.startManualPlan()
  flow.selectPlan('p3')
  flow.confirmPlan()
  const selected = flow.activePoints.map(([label]) => points.toProtocolPoint(label))
  const pixels = selected.map((point, i) => ({ ...point, x: 100 + i * 10, y: 200 }))
  flow.completeScan({ frame_number: 42, received_monotonic_s: 10, available_points: selected })
  flow.setPhotoAdjustments(pixels)
  const sent = [], navigations = []
  const robot = vue.reactive({ online: true, deviceHealthy: true, pendingCmd: null, pendingStop: false,
    commandEpoch: 1, statusRevision: 1, stale: false,
    status: { state: 'IDLE', result: 'COMPLETED', error: '', step: 'NONE', remaining_time_s: 0,
      noncontact: { phase: 'COMPLETED', active: false, returned_to_start: true,
        returning_to_start: false, stop_confirmed: true, available: false, targets: [], blockers: [] },
      point_confirmation: { state: 'CONFIRMED', revision: 'test-revision', frame_number: 42,
        received_monotonic_s: 10, requested_points: pixels,
        points: pixels.map(p => ({ ...p, base_point_mm: [1, 2, 3] })) },
    },
    async request(cmd) { sent.push(cmd); return { ok: true } },
    async stop() { sent.push('STOP'); return { ok: true } },
  })
  const settings = vue.reactive({ muted: true, forceLimit: 20, tempLimit: 50 })
  const modules = {
    vue: { ...vue, onMounted() {}, onBeforeUnmount() {}, watch() {} },
    pinia: { storeToRefs: vue.toRefs },
    'vue-router': { useRouter: () => ({ push: target => navigations.push(target) }), onBeforeRouteLeave() {} },
    '@/stores/flow': { useFlowStore: () => flow, levelRange },
    '@/stores/robot': { useRobotStore: () => robot },
    '@/stores/settings': { useSettingsStore: () => settings },
    '@/stores/toast': { useToastStore: () => ({ show() {} }) },
    '@/data/points': points, '@/data/run': runData,
    '@/utils/controlChecks': { planBlockReason: () => '' },
    '@/utils/speech': { prefetchSpeech() {}, servicePointLine() {}, unlockSpeech() {},
      speakPreview() {}, speakServicePoint() {}, stopSpeech() {} },
  }
  const page = evaluate(pages[name], modules).default.setup({}, { expose() {} })
  if (name === 'ConfirmView') {
    page.photoReady.value = true
    page.finalPixels.value = pixels
  }
  return { page, flow, robot, sent, navigations }
}

test('return movement remains active, offers STOP, and cannot rescan or continue', async () => {
  const { page, robot, sent, navigations, flow } = setup()
  Object.assign(robot.status, { state: 'RUNNING', result: 'NONE' })
  Object.assign(robot.status.noncontact, { phase: 'RUNNING', active: true,
    returning_to_start: true, returned_to_start: false, stop_confirmed: false })
  assert.match(page.noncontactProgress.value, /正在返回本次起始点位/)
  assert.equal(page.canContinue.value, false)
  assert.equal(page.canRescan.value, false)
  assert.equal(page.canStart.value, false)
  page.continueUsing()
  page.rescan()
  assert.deepEqual(navigations, [])
  assert.equal(flow.scanSnapshot.frame_number, 42)
  await page.stopNoncontact()
  assert.deepEqual(sent, ['STOP'])
  robot.status.state = 'STOPPING'
  assert.match(page.noncontactProgress.value, /正在停止/)
})

test('completed return unlocks a fresh scan, preserving the plan but clearing old execution permission', async () => {
  const { page, flow, sent, navigations } = setup()
  flow.startService()
  flow.finishService(false)
  assert.equal(page.canContinue.value, true)
  assert.equal(page.canStart.value, false)
  await page.prepareNoncontact()
  assert.deepEqual(sent, [])
  page.continueUsing()
  assert.deepEqual(navigations, [{ name: 'service-position' }])
  assert.equal(flow.selectedPlanId, 'p3')
  assert.equal(flow.planConfirmed, true)
  assert.equal(flow.currentStage, 1)
  assert.equal(flow.unlockedStage, 1)
  assert.equal(flow.scanSnapshot, null)
  assert.deepEqual(flow.photoAdjustments, [])
  assert.equal(flow.scanComplete, false)
  assert.equal(flow.routeConfirmed, false)
  assert.equal(flow.serviceStarted, false)
  assert.equal(flow.finishSummary, null)
  assert.equal(flow.totalSec, 0)
  assert.equal(flow.remainSec, 0)
  assert.equal(flow.canEnter('service-position'), true)
  assert.equal(flow.canEnter('service-confirm'), false)
  assert.equal(flow.canEnter('service-run'), false)
  assert.deepEqual(sent, [])
})

test('missing return confirmation, STOP, errors and stale feedback never unlock the next-use action', () => {
  for (const change of [
    r => { delete r.status.noncontact.returned_to_start },
    r => { r.status.noncontact.returned_to_start = false },
    r => { r.status.noncontact.stop_confirmed = false },
    r => { r.status.noncontact.active = true },
    r => { r.status.noncontact.returning_to_start = true },
    r => { r.status.noncontact.phase = 'STOPPED' },
    r => { r.status.noncontact.phase = 'ERROR'; r.status.state = 'ERROR' },
    r => { r.status.state = 'STOPPING' },
    r => { r.deviceHealthy = false },
    r => { r.pendingStop = true },
  ]) {
    const { page, robot, sent, navigations } = setup()
    change(robot)
    assert.equal(page.canContinue.value, false)
    page.continueUsing()
    assert.deepEqual(navigations, [])
    assert.deepEqual(sent, [])
  }
})

test('run page waits for complete return feedback before normal finish', () => {
  const { page, robot, flow, navigations } = setup('RunView')
  flow.startService()
  Object.assign(robot.status, { state: 'RUNNING', result: 'NONE' })
  Object.assign(robot.status.noncontact, { active: true, returning_to_start: true, returned_to_start: false })
  page.handleStatus(robot.status)
  assert.equal(page.returningToStart.value, true)
  assert.equal(page.stepText.value, '返回起始点位')
  Object.assign(robot.status, { state: 'IDLE', result: 'COMPLETED' })
  Object.assign(robot.status.noncontact, { active: false, returning_to_start: false })
  page.handleStatus(robot.status)
  assert.equal(flow.finishSummary, null)
  assert.deepEqual(navigations, [])
  robot.status.noncontact.returned_to_start = true
  page.handleStatus(robot.status)
  assert.equal(flow.finishSummary.endedEarly, false)
  assert.deepEqual(navigations, [{ name: 'service-finish' }])
})

test('a stopped run ends early without returning automatically', () => {
  const { page, flow, robot, sent, navigations } = setup('RunView')
  flow.startService()
  robot.status.state = 'RUNNING'
  page.handleStatus(robot.status)
  Object.assign(robot.status, { state: 'IDLE', result: 'STOPPED' })
  Object.assign(robot.status.noncontact, { phase: 'STOPPED', returned_to_start: false })
  page.handleStatus(robot.status)
  assert.equal(flow.finishSummary.endedEarly, true)
  assert.deepEqual(navigations, [{ name: 'service-finish' }])
  assert.deepEqual(sent, [])
})

test('finish page archives before continuing the same plan with no previous photo or route permission', () => {
  const { page, flow, sent, navigations } = setup('FinishView')
  flow.startService()
  flow.finishService(false)
  page.note.value = ' 本次记录 '
  assert.equal(page.canContinue.value, true)
  page.archiveAndContinue()
  assert.equal(flow.records.length, 1)
  assert.equal(flow.records[0].plan, plans.getPlanById('p3').name)
  assert.equal(flow.records[0].note, '本次记录')
  assert.equal(flow.selectedPlanId, 'p3')
  assert.equal(flow.planMode, 'manual')
  assert.equal(flow.planConfirmed, true)
  assert.equal(flow.scanSnapshot, null)
  assert.deepEqual(flow.photoAdjustments, [])
  assert.equal(flow.canEnter('service-position'), true)
  assert.equal(flow.canEnter('service-confirm'), false)
  assert.equal(flow.canEnter('service-run'), false)
  assert.equal(flow.finishSummary, null)
  assert.deepEqual(navigations, [{ name: 'service-position' }])
  assert.deepEqual(sent, [])
})

test('finish-page next-use action does nothing for an interrupted or unconfirmed return', () => {
  for (const endedEarly of [true, false]) {
    const { page, flow, robot, sent, navigations } = setup('FinishView')
    flow.startService()
    flow.finishService(endedEarly)
    robot.status.noncontact.returned_to_start = false
    assert.equal(page.canContinue.value, false)
    page.archiveAndContinue()
    assert.equal(flow.records.length, 0)
    assert.notEqual(flow.finishSummary, null)
    assert.deepEqual(navigations, [])
    assert.deepEqual(sent, [])
  }
})
