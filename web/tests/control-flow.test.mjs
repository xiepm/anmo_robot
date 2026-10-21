import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'
import { createPinia, setActivePinia } from 'pinia'

const endpointsSource = await readFile(new URL('../src/utils/endpoints.ts', import.meta.url), 'utf8')
const endpointsCode = ts.transpileModule(endpointsSource, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText
const endpointsUrl = `data:text/javascript;base64,${Buffer.from(endpointsCode).toString('base64')}`

async function sourceModule(relative, robotUrl) {
  let source = await readFile(new URL(relative, import.meta.url), 'utf8')
  source = source.replace('import.meta.env.VITE_ROBOT_WS_URL', JSON.stringify(robotUrl) ?? 'undefined')
  const output = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText
    .replaceAll("from 'vue'", `from '${import.meta.resolve('vue')}'`)
    .replaceAll("from 'pinia'", `from '${import.meta.resolve('pinia')}'`)
    .replaceAll("from '@/utils/endpoints'", `from '${endpointsUrl}'`)
  return import(`data:text/javascript;base64,${Buffer.from(output).toString('base64')}`)
}
const { ScanTracker, planBlockReason } = await sourceModule('../src/utils/controlChecks.ts')
const { useRobotStore } = await sourceModule('../src/stores/robot.ts')
const point = { name: '大椎', side: 'MIDLINE' }
const status = (extra = {}) => ({ mode: 'SIMULATION', robot_connected: true, robot_feedback_valid: true,
  state: 'IDLE', region: 'BACK', positioning_valid: true, available_points: [point], error: '', ...extra })
const reply = (cmd, ok = true) => ({ type: 'REPLY', cmd, ok, message: ok ? 'accepted' : 'rejected', data: {} })
const plan = { region: 'BACK', points: [point], total_time_s: 30 }

test('scan requires this SCANNING transition and a successful reply, regardless of reply/status ordering', () => {
  const scan = new ScanTracker('BACK')
  assert.equal(scan.observe(status()).done, false)
  assert.equal(scan.acknowledge(reply('SCAN')).done, false)
  scan.observe(status({ state: 'SCANNING', positioning_valid: false }))
  assert.equal(scan.observe(status()).done, true)
  const fast = new ScanTracker('BACK')
  fast.observe(status({ state: 'SCANNING', positioning_valid: false }))
  assert.equal(fast.observe(status()).done, false)
  assert.equal(fast.acknowledge(reply('SCAN')).done, true)
})
test('cancel, rejection, wrong body region and failed coordinates never unlock confirmation', () => {
  for (const last of [status({ positioning_valid: false }), status({ region: 'LOWER_LIMB' }), status({ state: 'ERROR' })]) {
    const scan = new ScanTracker('BACK')
    scan.acknowledge(reply('SCAN'))
    scan.observe(status({ state: 'SCANNING' }))
    assert.equal(scan.observe(last).done, false)
  }
  const scan = new ScanTracker('BACK')
  scan.observe(status({ state: 'SCANNING' }))
  scan.observe(status())
  assert.equal(scan.acknowledge(reply('SCAN', false)).error, 'rejected')
  scan.cancel()
  assert.equal(scan.acknowledge(reply('SCAN')).done, false)
})
test('a captured photo opens confirmation but does not authorize START', () => {
  const photo = status({ positioning_valid: false, scan_snapshot: {
    region: 'BACK', frame_number: 42, available_points: [point],
    execution_eligible: false, execution_blocker: '路径尚未确认' } })
  const scan = new ScanTracker('BACK')
  scan.acknowledge(reply('SCAN'))
  scan.observe(status({ state: 'SCANNING' }))
  assert.equal(scan.observe(photo).done, true)
  assert.equal(planBlockReason(plan, photo, true), '路径尚未确认')
})
test('start blocks stale feedback, invalid positioning, missing real points, and indivisible duration', () => {
  assert.equal(planBlockReason(plan, status(), true), '')
  assert.ok(planBlockReason(plan, status(), false))
  assert.ok(planBlockReason(plan, status({ robot_feedback_valid: false }), true))
  assert.ok(planBlockReason(plan, status({ positioning_valid: false }), true))
  assert.match(planBlockReason({ ...plan, points: [point, { name: '肩井', side: 'LEFT' }] }, status(), true), /左肩井/)
  assert.ok(planBlockReason({ ...plan, total_time_s: 35, points: [point, point] }, status(), true))
})

class FakeSocket {
  static OPEN = 1
  static instances = []
  readyState = 0
  sent = []
  constructor(url) { this.url = url; FakeSocket.instances.push(this) }
  open() { this.readyState = 1; this.onopen() }
  send(raw) { this.sent.push(JSON.parse(raw)) }
  message(value) { this.onmessage({ data: JSON.stringify(value) }) }
  close() { this.readyState = 3; this.onclose() }
}
function setup(t, useStore = useRobotStore) {
  t.mock.timers.enable({ apis: ['setTimeout', 'setInterval'] })
  const originalSocket = globalThis.WebSocket
  globalThis.WebSocket = FakeSocket
  t.after(() => { globalThis.WebSocket = originalSocket })
  FakeSocket.instances = []
  setActivePinia(createPinia())
  const store = useStore()
  store.start()
  const socket = FakeSocket.instances[0]
  socket.open()
  socket.message({ type: 'STATUS', data: status() })
  return { store, socket }
}
test('the actual Docker store opens the same-origin secure proxy and initially sends only GET_STATUS', async t => {
  const originalLocation = Object.getOwnPropertyDescriptor(globalThis, 'location')
  Object.defineProperty(globalThis, 'location', { configurable: true, value: { href: 'https://windows-demo:8443/confirm' } })
  t.after(() => {
    if (originalLocation) Object.defineProperty(globalThis, 'location', originalLocation)
    else delete globalThis.location
  })
  const { useRobotStore: dockerStore } = await sourceModule('../src/stores/robot.ts', '/control')
  const { socket } = setup(t, dockerStore)
  assert.equal(socket.url, 'wss://windows-demo:8443/control')
  assert.deepEqual(socket.sent, [{ cmd: 'GET_STATUS', data: {} }])
})
test('a reply alone never satisfies READY/RUNNING state confirmation', async t => {
  const { store, socket } = setup(t)
  const baseline = store.statusRevision
  const promise = store.waitForState('READY', baseline, store.commandEpoch, 50)
  const rejected = assert.rejects(promise, /READY/)
  socket.message(reply('SET_PLAN'))
  t.mock.timers.tick(51)
  await rejected
  const next = store.waitForState('RUNNING', store.statusRevision, store.commandEpoch)
  socket.message({ type: 'STATUS', data: status({ state: 'RUNNING' }) })
  assert.equal((await next).state, 'RUNNING')
})
test('STOP interrupts a pending plan and blocks START until its reply', async t => {
  const { store, socket } = setup(t)
  const saving = store.request('SET_PLAN', plan)
  const stopping = store.stop()
  assert.equal((await saving).ok, false)
  assert.equal((await store.request('START')).ok, false)
  socket.message(reply('SET_PLAN'))
  socket.message(reply('STOP'))
  assert.equal((await stopping).ok, true)
  assert.equal(socket.sent.some(m => m.cmd === 'START'), false)
})
test('an uncertain command retires its connection and a late reply cannot complete the next command', async t => {
  const { store, socket } = setup(t)
  const pending = store.request('SCAN', { region: 'BACK' })
  t.mock.timers.tick(3001)
  assert.equal((await pending).ok, false)
  assert.equal(socket.readyState, 3)
  t.mock.timers.tick(1001)
  const current = FakeSocket.instances.at(-1)
  current.open()
  current.message({ type: 'STATUS', data: status() })
  const newer = store.request('SCAN', { region: 'BACK' })
  socket.message(reply('SCAN'))
  assert.equal(store.pendingCmd, 'SCAN')
  current.message(reply('SCAN', false))
  assert.equal((await newer).message, 'rejected')
})

test('final coordinates require the matching controller revision, never just a REPLY', async t => {
  const { store, socket } = setup(t)
  let done = false
  const awaiting = store.waitForPointConfirmation('new', store.commandEpoch).then(value => { done = true; return value })
  socket.message(reply('CONFIRM_POINTS'))
  socket.message({ type:'STATUS', data:status({ point_confirmation:{ revision:'old', state:'CONFIRMED' } }) })
  await Promise.resolve(); assert.equal(done,false)
  socket.message({ type:'STATUS', data:status({ point_confirmation:{ revision:'new', state:'PENDING' } }) })
  await Promise.resolve(); assert.equal(done,false)
  socket.message({ type:'STATUS', data:status({ point_confirmation:{ revision:'new', state:'CONFIRMED', points:[{...point,x:120,y:180,base_point_mm:[1,2,3]}] } }) })
  assert.deepEqual((await awaiting).points[0].base_point_mm,[1,2,3])
})
test('failed coordinate conversion and STOP reject pending confirmation', async t => {
  const { store, socket } = setup(t)
  const awaiting = store.waitForPointConfirmation('bad',store.commandEpoch)
  const rejection = assert.rejects(awaiting,/深度无效/)
  socket.message({ type:'STATUS',data:status({ point_confirmation:{revision:'bad',state:'FAILED',error:'深度无效'} }) })
  await rejection
  const second = assert.rejects(store.waitForPointConfirmation('next',store.commandEpoch),/停止请求/)
  const stopping = store.stop()
  socket.message(reply('STOP'))
  await stopping;await second
})

test('noncontact START requires observed RUNNING; STOP interrupts and no motion command replays', async t => {
  const { store, socket } = setup(t)
  const preparing = store.request('PREPARE_NONCONTACT', { revision: 'test', points: [point] })
  socket.message(reply('PREPARE_NONCONTACT')); assert.equal((await preparing).ok, true)
  const after = store.statusRevision
  const running = store.waitForState('RUNNING', after, store.commandEpoch, 50)
  const rejected = assert.rejects(running, /中断/)
  const starting = store.request('START_NONCONTACT', { plan_sha256: 'a'.repeat(64) })
  const stopping = store.stop()
  assert.equal((await starting).ok, false)
  socket.message(reply('START_NONCONTACT')); socket.message(reply('STOP'))
  await stopping; await rejected
  assert.deepEqual(socket.sent.filter(m => m.cmd !== 'GET_STATUS').map(m => m.cmd), ['PREPARE_NONCONTACT','START_NONCONTACT','STOP'])
})
