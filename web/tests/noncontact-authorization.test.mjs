import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import { compileScript, parse } from '@vue/compiler-sfc'
import * as vue from 'vue'
import ts from 'typescript'

// Execute the actual confirmation-page setup with fake stores and lifecycle
// hooks. No browser, websocket, SDK, or hardware connection is created.
const source = await readFile(new URL('../src/views/service/ConfirmView.vue', import.meta.url), 'utf8')
const descriptor = parse(source).descriptor
const script = compileScript(descriptor, { id: 'noncontact-authorization-test' })
const compiled = ts.transpileModule(script.content, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText
const point = { name: '大椎', side: 'MIDLINE' }
const pixels = { ...point, x: 120, y: 180 }
const revision = '00000000-0000-0000-0000-000000000003'

function setup(options = {}) {
  const sent = [], waits = [], stops = []
  const noncontact = { available: false, authorization_available: true, active: false, phase: 'NONE',
    blockers: ['当前路线尚未现场确认'], targets: [], ...options.noncontact }
  const snapshot = { region: 'BACK', frame_number: 42, received_monotonic_s: 10, available_points: [point] }
  const flow = vue.reactive({ activePoints: [['大椎']], scanSnapshot: { ...snapshot },
    photoAdjustments: [{ ...pixels }] })
  const robot = vue.reactive({
    online: true, deviceHealthy: true, pendingCmd: null, pendingStop: false,
    commandEpoch: 1, statusRevision: 1,
    status: { state: 'IDLE', error: '', noncontact, scan_snapshot: { ...snapshot },
      vision_feedback_valid: true, point_confirmation: {
      state: 'CONFIRMED', revision, frame_number: 42, received_monotonic_s: 10,
      requested_points: [pixels], points: [{ ...pixels, base_point_mm: [1, 2, 3] }],
    } },
    async request(cmd, data) {
      sent.push({ cmd, data })
      return options.reply ? options.reply(cmd, robot) : { ok: true }
    },
    async waitForState(...args) { waits.push(args); return { state: 'READY' } },
    async waitForPointConfirmation() { return robot.status.point_confirmation },
    async stop() { stops.push(true); return { ok: true } },
  })
  const modules = {
    vue: { ...vue, onMounted() {}, onBeforeUnmount() {} },
    'vue-router': { useRouter: () => ({ push() {} }), onBeforeRouteLeave() {} },
    '@/stores/flow': { useFlowStore: () => flow, levelRange: () => ({ min: 1, max: 10 }) },
    '@/stores/robot': { useRobotStore: () => robot },
    '@/stores/toast': { useToastStore: () => ({ show() {} }) },
    '@/data/points': { selectRecognizedPoints: () => ({ missing: [] }), toPointLabel: p => p.name,
      toProtocolPoint: () => ({ ...point }) },
    '@/utils/controlChecks': { planBlockReason: () => '' },
    '@/utils/speech': { prefetchSpeech() {}, servicePointLine() {}, unlockSpeech() {} },
  }
  const module = { exports: {} }
  const require = name => {
    if (name.startsWith('@/components/')) return { default: {} }
    if (!(name in modules)) throw new Error(`Unexpected dependency: ${name}`)
    return modules[name]
  }
  new Function('require', 'module', 'exports', compiled)(require, module, module.exports)
  const page = module.exports.default.setup({}, { expose() {} })
  page.finalPixels.value = [{ ...pixels }]
  page.photoReady.value = true
  return { page, flow, robot, sent, waits, stops }
}

test('current-route authorization requires an explicit page action and never auto-starts', async () => {
  const { page, sent, waits } = setup()
  assert.equal(page.needsSiteAuthorization.value, true)
  assert.equal(page.canStart.value, true)
  assert.deepEqual(sent, [])
  await page.prepareNoncontact()
  assert.deepEqual(sent, [
    { cmd: 'AUTHORIZE_NONCONTACT', data: { revision, points: [point],
      operator_observed_no_obstacles: true, reuse_existing_calibration: true } },
    { cmd: 'PREPARE_NONCONTACT', data: { revision, points: [point] } },
  ])
  assert.equal(waits.length, 1)
  assert.equal(waits[0][0], 'READY')
  assert.equal(page.showStartModal.value, false)
  assert.equal(sent.some(message => message.cmd === 'START_NONCONTACT'), false)
})

test('an already authorized route prepares without another authorization command', async () => {
  const { page, sent } = setup({ noncontact: { available: true, blockers: [] } })
  assert.equal(page.needsSiteAuthorization.value, false)
  await page.prepareNoncontact()
  assert.deepEqual(sent.map(message => message.cmd), ['PREPARE_NONCONTACT'])
})

test('missing backend eligibility, health, or matching confirmed coordinates prevents authorization', async () => {
  for (const change of [
    ({ robot }) => { robot.status.noncontact.authorization_available = false },
    ({ robot }) => { delete robot.status.noncontact.authorization_available },
    ({ robot }) => { robot.deviceHealthy = false },
    ({ robot }) => { robot.status.state = 'ERROR' },
    ({ robot }) => { robot.status.point_confirmation.frame_number++ },
    ({ page }) => { page.finalPixels.value[0].x++ },
  ]) {
    const context = setup()
    change(context)
    assert.equal(context.page.canStart.value, false)
    await context.page.prepareNoncontact()
    assert.deepEqual(context.sent, [])
  }
})

test('a failed current photo shows the controller reason and cannot prepare motion', async () => {
  const { page, robot, sent } = setup()
  robot.status.point_confirmation.state = 'FAILED'
  robot.status.point_confirmation.error = '本次照片超过五分钟，请重新扫描'
  assert.equal(page.startBlocker.value, '本次照片超过五分钟，请重新扫描')
  assert.equal(page.canStart.value, false)
  await page.prepareNoncontact()
  assert.deepEqual(sent, [])
})

test('failure from a different photo does not replace the current confirmation prompt', () => {
  for (const key of ['frame_number', 'received_monotonic_s']) {
    const { page, robot } = setup()
    robot.status.point_confirmation.state = 'FAILED'
    robot.status.point_confirmation.error = '旧照片错误'
    robot.status.point_confirmation[key]++
    assert.equal(page.startBlocker.value, '请先确认最终穴位坐标，控制端确认后再开始')
    assert.equal(page.canStart.value, false)
  }
})

test('rejected authorization cannot issue PREPARE', async () => {
  const { page, sent, waits } = setup({ reply: () => ({ ok: false, message: '本次路线已经失效' }) })
  await page.prepareNoncontact()
  assert.deepEqual(sent.map(message => message.cmd), ['AUTHORIZE_NONCONTACT'])
  assert.equal(waits.length, 0)
  assert.match(page.coordinateError.value, /失效/)
})

test('STOP or coordinate changes while authorization is pending prevent subsequent PREPARE', async () => {
  for (const change of [
    robot => { robot.commandEpoch++ },
    robot => { robot.pendingStop = true },
    robot => { robot.deviceHealthy = false },
    robot => { robot.status.point_confirmation.revision = 'different' },
  ]) {
    const { page, sent } = setup({ reply: (cmd, robot) => { change(robot); return { ok: true } } })
    await page.prepareNoncontact()
    assert.deepEqual(sent.map(message => message.cmd), ['AUTHORIZE_NONCONTACT'])
    assert.match(page.coordinateError.value, /已变化/)
  }
})

test('controller-retired photo disables confirmation and old receipt without erasing doctor edits', async () => {
  const { page, flow, robot, sent } = setup()
  delete robot.status.scan_snapshot
  assert.match(page.photoStatusBlocker.value, /控制端已清除.*重新扫描/)
  assert.equal(page.receipt.value, null)
  assert.equal(page.editorLocked.value, true)
  assert.equal(page.canConfirmPoints.value, false)
  assert.equal(page.canStart.value, false)
  await page.confirmPoints()
  await page.prepareNoncontact()
  assert.deepEqual(sent, [])
  assert.equal(flow.scanSnapshot.frame_number, 42)
  assert.deepEqual(flow.photoAdjustments, [pixels])
  assert.deepEqual(page.finalPixels.value, [pixels])
})

test('different controller photo identity requires rescanning rather than reusing visible pixels', async () => {
  for (const patch of [{ frame_number: 43 }, { received_monotonic_s: 11 }, { region: 'UPPER_LIMB' }]) {
    const { page, robot, sent } = setup()
    Object.assign(robot.status.scan_snapshot, patch)
    assert.match(page.photoStatusBlocker.value, /照片不一致.*重新扫描/)
    assert.equal(page.canConfirmPoints.value, false)
    assert.equal(page.receipt.value, null)
    await page.confirmPoints()
    assert.deepEqual(sent, [])
  }
})

test('stale control or transient vision feedback waits for recovery and retains the same photo edits', async () => {
  for (const key of ['online', 'vision_feedback_valid']) {
    const { page, flow, robot, sent } = setup()
    delete robot.status.point_confirmation
    if (key === 'online') robot.online = false
    else robot.status.vision_feedback_valid = false
    assert.match(page.photoStatusBlocker.value, /等待/)
    assert.doesNotMatch(page.photoStatusBlocker.value, /重新扫描/)
    assert.equal(page.canConfirmPoints.value, false)
    await page.confirmPoints()
    assert.deepEqual(sent, [])
    if (key === 'online') robot.online = true
    else robot.status.vision_feedback_valid = true
    assert.equal(page.photoStatusBlocker.value, '')
    assert.equal(page.canConfirmPoints.value, true)
    assert.equal(page.editorLocked.value, false)
    assert.deepEqual(flow.photoAdjustments, [pixels])
    assert.deepEqual(page.finalPixels.value, [pixels])
  }
})

test('whole-photo expiry blocks retry but a depth hole remains adjustable on the same photo', async () => {
  for (const error of ['本次照片超过五分钟，请重新扫描', '标定已改变，请重新扫描',
    '拍照后机械臂位置已改变，请重新扫描', '确认坐标的照片已失效，请重新扫描',
    '相机已断开或照片已更新，请重新扫描']) {
    const { page, robot, sent } = setup()
    Object.assign(robot.status.point_confirmation, { state: 'FAILED', error })
    assert.equal(page.photoStatusBlocker.value, error)
    assert.equal(page.canConfirmPoints.value, false)
    await page.confirmPoints()
    assert.deepEqual(sent, [])
  }
  const { page, robot } = setup()
  Object.assign(robot.status.point_confirmation, {
    state: 'FAILED', error: '大椎调整位置深度无效，请换点或重新扫描',
  })
  assert.equal(page.photoStatusBlocker.value, '')
  assert.equal(page.canConfirmPoints.value, true)
  assert.equal(page.editorLocked.value, false)
  assert.equal(page.canStart.value, false)
})

test('soft-delay cancellation can reconfirm the retained edited photo after vision recovers', async () => {
  const { page, flow, robot, sent } = setup()
  Object.assign(robot.status.point_confirmation, { state: 'FAILED', points: [],
    error: '实时画面暂时延迟，本次确认已撤销；恢复后请重新确认' })
  robot.status.vision_feedback_valid = false
  assert.equal(page.canConfirmPoints.value, false)
  assert.match(page.photoStatusBlocker.value, /等待/)
  robot.status.vision_feedback_valid = true
  assert.equal(page.photoStatusBlocker.value, '')
  assert.equal(page.editorLocked.value, false)
  assert.equal(page.canConfirmPoints.value, true)
  assert.equal(page.canStart.value, false)
  await page.confirmPoints()
  assert.deepEqual(sent.map(message => message.cmd), ['CONFIRM_POINTS'])
  assert.notEqual(sent[0].data.revision, revision)
  assert.equal(sent[0].data.frame_number, 42)
  assert.equal(sent[0].data.received_monotonic_s, 10)
  assert.deepEqual(sent[0].data.points, [pixels])
  assert.deepEqual(flow.photoAdjustments, [pixels])
  assert.equal(flow.scanSnapshot.frame_number, 42)
})

test('a rejected expired capture cannot be repeatedly submitted before status catches up', async () => {
  const { page, flow, robot, sent } = setup({ reply: () => ({ ok: false, message: '没有可确认的本次照片坐标' }) })
  delete robot.status.point_confirmation
  await page.confirmPoints()
  assert.deepEqual(sent.map(message => message.cmd), ['CONFIRM_POINTS'])
  assert.match(page.photoStatusBlocker.value, /控制端已清除.*重新扫描/)
  assert.equal(page.canConfirmPoints.value, false)
  await page.confirmPoints()
  assert.equal(sent.length, 1)
  const next = { ...flow.scanSnapshot, frame_number: 43, received_monotonic_s: 11 }
  flow.scanSnapshot = next
  robot.status.scan_snapshot = { ...next }
  assert.equal(page.coordinateError.value, '')
  assert.equal(page.photoStatusBlocker.value, '')
  assert.equal(page.canConfirmPoints.value, false) // Wait for the new editor's pixels.
  page.finalPixels.value = [{ ...pixels, x: 121 }]
  page.photoReady.value = true
  assert.equal(page.canConfirmPoints.value, true)
})

test('a late refusal for an old photo cannot retire a replacement photo', async () => {
  let reply
  const { page, flow, robot, sent } = setup({ reply: () => new Promise(resolve => { reply = resolve }) })
  delete robot.status.point_confirmation
  const pending = page.confirmPoints()
  const next = { ...flow.scanSnapshot, frame_number: 43, received_monotonic_s: 11 }
  flow.scanSnapshot = next
  robot.status.scan_snapshot = { ...next }
  reply({ ok: false, message: '确认坐标的照片已失效，请重新扫描' })
  await pending
  assert.equal(page.coordinateError.value, '')
  assert.equal(page.photoStatusBlocker.value, '')
  page.finalPixels.value = [{ ...pixels, x: 121 }]
  page.photoReady.value = true
  assert.equal(page.canConfirmPoints.value, true)
  assert.deepEqual(sent.map(message => message.cmd), ['CONFIRM_POINTS'])
})

test('clearing a valid confirmation preserves the photo and permits manual editing again', async () => {
  const { page, robot, sent } = setup({ reply: (cmd, robot) => {
    if (cmd === 'CLEAR_POINTS') delete robot.status.point_confirmation
    return { ok: true }
  } })
  await page.confirmPoints()
  assert.deepEqual(sent.map(message => message.cmd), ['CLEAR_POINTS'])
  assert.equal(robot.status.scan_snapshot.frame_number, 42)
  assert.equal(page.photoStatusBlocker.value, '')
  assert.equal(page.editorLocked.value, false)
  assert.equal(page.canConfirmPoints.value, true)
})
