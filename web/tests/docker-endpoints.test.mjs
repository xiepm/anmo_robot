import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

const compile = (source, module = ts.ModuleKind.ESNext) => ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module },
}).outputText
const source = await readFile(new URL('../src/utils/endpoints.ts', import.meta.url), 'utf8')
const { robotWebSocketUrl, visionResourceUrl } = await import(`data:text/javascript;base64,${Buffer.from(compile(source)).toString('base64')}`)

test('local defaults remain independent of the browser URL', () => {
  assert.equal(robotWebSocketUrl(undefined, 'http://demo.local:8080/confirm'), 'ws://127.0.0.1:8765')
  assert.equal(visionResourceUrl(undefined, undefined, 'http://demo.local:8080/confirm').href,
    'http://127.0.0.1:8767/frame.jpg')
  assert.equal(visionResourceUrl(undefined, '/snapshot/raw.jpg').href, 'http://127.0.0.1:8767/snapshot/raw.jpg')
})

test('same-origin WebSocket paths keep host and port and follow HTTP or HTTPS', () => {
  assert.equal(robotWebSocketUrl('/control', 'http://windows-demo:8080/confirm'), 'ws://windows-demo:8080/control')
  assert.equal(robotWebSocketUrl('/control', 'https://demo.example/position'), 'wss://demo.example/control')
  assert.equal(robotWebSocketUrl('ws://127.0.0.1:8765'), 'ws://127.0.0.1:8765/')
  assert.equal(robotWebSocketUrl('wss://control.example/ui?mode=test'), 'wss://control.example/ui?mode=test')
  assert.throws(() => robotWebSocketUrl('file:///control'), /控制地址/)
})

test('frozen photos retain the same-origin proxy prefix and bind exact photo identity', () => {
  const frame = '/vision/frame.jpg'
  const page = 'https://demo.example:8443/confirm'
  assert.equal(visionResourceUrl(frame, undefined, page).href, 'https://demo.example:8443/vision/frame.jpg')
  for (const resource of ['/snapshot/points.json', '/snapshot/raw.jpg']) {
    const url = visionResourceUrl(frame, resource, page)
    url.search = new URLSearchParams({ frame: '12', received: '123.456' }).toString()
    assert.equal(url.origin, 'https://demo.example:8443')
    assert.equal(url.pathname, '/vision'+resource)
    assert.equal(url.searchParams.get('frame'), '12')
    assert.equal(url.searchParams.get('received'), '123.456')
  }
  assert.throws(() => visionResourceUrl('ws://camera/frame.jpg'), /视觉地址/)
})

function speechFixture(demoMode) {
  const calls = []
  const module = { exports: {} }
  const require = name => {
    assert.equal(name, '@/stores/settings')
    return { useSettingsStore: () => ({ muted: false, volume: 50 }) }
  }
  const fetch = (...args) => { calls.push(args); return Promise.resolve({ ok: true, blob: async () => ({ size: 1 }) }) }
  const Audio = class { constructor() { calls.push('audio-created') } }
  return readFile(new URL('../src/utils/speech.ts', import.meta.url), 'utf8').then(source => {
    const code = compile(source.replace('import.meta.env.VITE_DEMO_MODE', JSON.stringify(demoMode ? 'true' : 'false')), ts.ModuleKind.CommonJS)
    new Function('module', 'exports', 'require', 'window', 'fetch', 'Audio', code)(module, module.exports, require, {}, fetch, Audio)
    return { ...module.exports, calls }
  })
}

test('static Docker demo does not request a missing TTS API or create audio playback', async () => {
  const speech = await speechFixture(true)
  speech.unlockSpeech()
  speech.prefetchSpeech('模拟测试')
  speech.speak('模拟测试')
  speech.speakPreview()
  speech.speakServicePoint('大椎')
  await Promise.resolve()
  assert.deepEqual(speech.calls, [])
})

test('normal builds keep the existing same-origin neural speech endpoint', async () => {
  const speech = await speechFixture(false)
  speech.prefetchSpeech('本机测试')
  await Promise.resolve()
  assert.equal(speech.calls.length, 1)
  assert.equal(speech.calls[0][0], '/heacon-tts')
  assert.equal(speech.calls[0][1].method, 'POST')
})
