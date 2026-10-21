import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import { compileScript, parse } from '@vue/compiler-sfc'
import * as vue from 'vue'
import * as pinia from 'pinia'
import ts from 'typescript'

async function compile(relative) {
  const source = await readFile(new URL(relative, import.meta.url), 'utf8')
  const script = relative.endsWith('.vue')
    ? compileScript(parse(source).descriptor, { id: 'plan-switch' }).content : source
  return ts.transpileModule(script, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText
}
function evaluate(source, modules = {}) {
  const module = { exports: {} }
  const require = name => {
    if (name.startsWith('@/components/')) return { default: {} }
    if (!(name in modules)) throw new Error(`Unexpected dependency: ${name}`)
    return modules[name]
  }
  new Function('require', 'module', 'exports', source)(require, module, module.exports)
  return module.exports
}
const plans = evaluate(await compile('../src/data/plans.ts'))
const points = evaluate(await compile('../src/data/points.ts'))
const { useFlowStore, levelRange } = evaluate(await compile('../src/stores/flow.ts'), {
  vue, pinia, '@/data/plans': plans, '@/data/points': points,
  '@/types/record': { loadRecords: () => [], saveRecords() {}, createRecordId: () => 'fake-record' },
})
const confirm = await compile('../src/views/service/ConfirmView.vue')
function setup() {
  pinia.setActivePinia(pinia.createPinia())
  const flow = useFlowStore()
  flow.startManualPlan()
  flow.confirmPlan()
  return flow
}
function capture(flow, frame) {
  const available = flow.activePoints.map(([label]) => points.toProtocolPoint(label))
  const pixels = available.map((point, i) => ({ ...point, x: 100 + frame + i, y: 200 + i }))
  flow.completeScan({ region: 'BACK', frame_number: frame, received_monotonic_s: frame,
    available_points: available })
  flow.setPhotoAdjustments(pixels)
  return pixels
}

test('switching 3 → 6 → 3 points retires every previous photo and execution permission immediately', () => {
  const flow = setup()
  for (const [id, count, frame, next] of [['p1', 3, 10, 'p2'], ['p2', 6, 20, 'p1']]) {
    assert.equal(flow.selectedPlanId, id)
    const pixels = capture(flow, frame)
    assert.equal(pixels.length, count)
    flow.startService()
    flow.finishService(false)
    flow.selectPlan(next)
    assert.equal(flow.selectedPlanId, next)
    assert.equal(flow.scanSnapshot, null)
    assert.deepEqual(flow.photoAdjustments, [])
    assert.equal(flow.scanComplete, false)
    assert.equal(flow.routeConfirmed, false)
    assert.equal(flow.serviceStarted, false)
    assert.equal(flow.finishSummary, null)
    assert.equal(flow.planConfirmed, false)
    assert.equal(flow.currentStage, 0)
    assert.equal(flow.unlockedStage, 0)
    assert.equal(flow.canEnter('service-position'), false)
    assert.equal(flow.canEnter('service-confirm'), false)
    assert.equal(flow.canEnter('service-run'), false)
    flow.confirmPlan()
    assert.equal(flow.canEnter('service-position'), true)
    assert.equal(flow.canEnter('service-confirm'), false)
  }
  const last = capture(flow, 30)
  assert.equal(last.length, 3)
  assert.equal(flow.scanSnapshot.frame_number, 30)
  assert.deepEqual(flow.scanSnapshot.available_points, points.pointSets.p1.map(([label]) => points.toProtocolPoint(label)))
})

test('clicking the already selected plan does not erase its photo adjustments', () => {
  const flow = setup()
  const pixels = capture(flow, 10)
  flow.selectPlan('p1')
  assert.equal(flow.planConfirmed, true)
  assert.equal(flow.scanSnapshot.frame_number, 10)
  assert.deepEqual(flow.photoAdjustments, pixels)
})

test('after switching back, a previous three-point confirmation cannot authorize the new photo', async () => {
  const flow = setup()
  const oldPixels = capture(flow, 10)
  flow.selectPlan('p2'); flow.confirmPlan(); capture(flow, 20)
  flow.selectPlan('p1'); flow.confirmPlan()
  const currentPixels = capture(flow, 30)
  const sent = []
  const robot = vue.reactive({ online: true, deviceHealthy: true, pendingCmd: null, pendingStop: false,
    status: { state: 'IDLE', error: '', scan_snapshot: JSON.parse(JSON.stringify(flow.scanSnapshot)),
      noncontact: { phase: 'NONE', active: false,
      available: true, blockers: [], targets: [] }, point_confirmation: {
      state: 'CONFIRMED', revision: 'retired-three-point-revision', frame_number: 10,
      received_monotonic_s: 10, requested_points: oldPixels, points: oldPixels,
    } }, async request(cmd) { sent.push(cmd); return { ok: true } },
  })
  const page = evaluate(confirm, {
    vue: { ...vue, onMounted() {}, onBeforeUnmount() {} },
    'vue-router': { useRouter: () => ({ push() {} }), onBeforeRouteLeave() {} },
    '@/stores/flow': { useFlowStore: () => flow, levelRange },
    '@/stores/robot': { useRobotStore: () => robot },
    '@/stores/toast': { useToastStore: () => ({ show() {} }) },
    '@/data/points': points,
    '@/utils/controlChecks': { planBlockReason: () => '' },
    '@/utils/speech': { prefetchSpeech() {}, servicePointLine() {}, unlockSpeech() {} },
  }).default.setup({}, { expose() {} })
  page.finalPixels.value = currentPixels
  page.photoReady.value = true
  assert.equal(page.photoPoints.value.length, 3)
  assert.equal(page.receipt.value, null)
  assert.equal(page.canStart.value, false)
  await page.prepareNoncontact()
  assert.deepEqual(sent, [])
})
