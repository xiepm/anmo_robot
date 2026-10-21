import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import { compileScript, parse } from '@vue/compiler-sfc'
import * as vue from 'vue'
import ts from 'typescript'

const transpile = source => ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText
const componentSource = await readFile(new URL('../src/components/service/LiveAcupointPreview.vue', import.meta.url), 'utf8')
function compileComponent(demoMode) {
  const source = componentSource.replace('import.meta.env.VITE_VISION_PREVIEW_URL', 'undefined')
    .replace('import.meta.env.VITE_DEMO_MODE', JSON.stringify(demoMode ? 'true' : 'false'))
  return transpile(compileScript(parse(source).descriptor, { id: 'preview-recovery-test' }).content)
}
const componentCodes = [compileComponent(false), compileComponent(true)]
const abortableModule = { exports: {} }
new Function('module', 'exports', transpile(await readFile(new URL('../src/utils/abortable.ts', import.meta.url), 'utf8')))(abortableModule, abortableModule.exports)
const endpointsModule = { exports: {} }
new Function('module', 'exports', transpile(await readFile(new URL('../src/utils/endpoints.ts', import.meta.url), 'utf8')))(endpointsModule, endpointsModule.exports)

function deferred() {
  let resolve, reject
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
async function flush() {
  for (let i = 0; i < 20; i++) await Promise.resolve()
}

// Run the real component setup with an in-memory clock, HTTP responses and JPEG
// decoder. No browser, localhost service, camera or robot is contacted.
function fixture(demoMode = false) {
  let now = 0, timerId = 0, sequence = 0, urlId = 0
  const timers = new Map(), listeners = new Set(), activeUrls = new Set()
  const calls = [], createdUrls = [], revokedUrls = [], images = []
  const fetchQueue = [], decodeQueue = []
  const document = {
    hidden: false,
    addEventListener(name, listener) { assert.equal(name, 'visibilitychange'); listeners.add(listener) },
    removeEventListener(name, listener) { assert.equal(name, 'visibilitychange'); listeners.delete(listener) },
  }
  const setTimeout = (callback, delay) => {
    const id = ++timerId
    timers.set(id, { callback, at: now + delay })
    return id
  }
  const clearTimeout = id => timers.delete(id)
  const response = (seq = String(++sequence), overrides = {}) => ({
    ok: true,
    headers: { get: name => name.toLowerCase() === 'content-type' ? 'image/jpeg'
      : name.toLowerCase() === 'x-preview-sequence' ? seq : null },
    blob: () => Promise.resolve({ seq }),
    ...overrides,
  })
  const errorResponse = (body, overrides = {}) => response('', {
    ok: false,
    headers: { get: name => name.toLowerCase() === 'content-type' ? 'application/json; charset=utf-8' : null },
    json: () => Promise.resolve(body),
    ...overrides,
  })
  const fetch = (url, options) => {
    calls.push({ url, ...options })
    return fetchQueue.length ? fetchQueue.shift()(options) : Promise.resolve(response())
  }
  class Image {
    src = ''
    constructor() { images.push(this) }
    decode() { return decodeQueue.length ? decodeQueue.shift()() : Promise.resolve() }
  }
  const URL = {
    createObjectURL() {
      const url = `blob:test/${++urlId}`
      createdUrls.push(url); activeUrls.add(url)
      return url
    },
    revokeObjectURL(url) { revokedUrls.push(url); activeUrls.delete(url) },
  }
  function instance() {
    const mounts = [], unmounts = []
    const modules = {
      vue: { ...vue, onMounted: callback => mounts.push(callback), onBeforeUnmount: callback => unmounts.push(callback) },
      '@/utils/abortable': abortableModule.exports,
      '@/utils/endpoints': endpointsModule.exports,
      '@/components/common/AppIcon.vue': { default: {} },
    }
    const module = { exports: {} }
    const require = name => {
      assert.ok(name in modules, `Unexpected module: ${name}`)
      return modules[name]
    }
    new Function('require', 'module', 'exports', 'fetch', 'Image', 'URL', 'document',
      'setTimeout', 'clearTimeout', 'performance', componentCodes[Number(demoMode)])(require, module, module.exports,
      fetch, Image, URL, document, setTimeout, clearTimeout, { now: () => now })
    const page = module.exports.default.setup({}, { expose() {} })
    return {
      page,
      async mount() { mounts.forEach(callback => callback()); await flush() },
      async unmount() { unmounts.forEach(callback => callback()); await flush() },
    }
  }
  return {
    instance, response, errorResponse, fetchQueue, decodeQueue, calls, createdUrls, revokedUrls, activeUrls, images, listeners, timers, document,
    async visibility(hidden) {
      document.hidden = hidden
      for (const listener of listeners) listener()
      await flush()
    },
    async advance(milliseconds) {
      const end = now + milliseconds
      while (true) {
        const due = [...timers.entries()].filter(([, timer]) => timer.at <= end).sort((a, b) => a[1].at - b[1].at)[0]
        if (!due) break
        timers.delete(due[0]); now = due[1].at; due[1].callback()
        await flush()
      }
      now = end
      await flush()
    },
  }
}

test('Docker demo explicitly explains synthetic preview without requests, timers or fake video', async () => {
  const f = fixture(true), component = f.instance()
  await component.mount()
  await f.advance(10000)
  await f.visibility(true)
  await f.visibility(false)
  assert.equal(component.page.demoMode, true)
  assert.equal(component.page.ready.value, false)
  assert.equal(component.page.connecting.value, false)
  assert.equal(component.page.message.value, '模拟演示，不提供真实相机画面')
  assert.equal(f.calls.length, 0)
  assert.equal(f.timers.size, 0)
  assert.equal(f.listeners.size, 0)
  assert.equal(f.createdUrls.length, 0)
  await component.unmount()
})

test('camera unavailable and HTTP errors clear the old frame and recover without a page reload', async () => {
  const f = fixture(), component = f.instance()
  f.fetchQueue.push(() => Promise.resolve(f.response('1')),
    () => Promise.resolve(f.response('2', { ok: false })),
    () => Promise.reject(new TypeError('service unavailable')),
    () => Promise.resolve(f.response('3')))
  await component.mount()
  assert.equal(component.page.ready.value, true)
  const initialUrl = component.page.imageUrl.value
  await f.advance(200)
  assert.equal(component.page.ready.value, false)
  assert.ok(f.revokedUrls.includes(initialUrl))
  await f.advance(200)
  assert.equal(component.page.ready.value, false)
  assert.equal(component.page.message.value, '视觉预览服务未连接，正在自动重试')
  await f.advance(200)
  assert.equal(component.page.ready.value, true)
  assert.equal(component.page.failureReason.value, '')
  assert.notEqual(component.page.imageUrl.value, initialUrl)
  assert.equal(f.calls.length, 4)
  assert.ok(f.calls.every(call => call.cache === 'no-store'))
  await component.unmount()
  assert.equal(f.activeUrls.size, 0)
})

test('503 camera status shows the specific reason as text and clears it after recovery', async () => {
  const f = fixture(), component = f.instance()
  const reason = '未检测到 RealSense 摄像头，等待 USB 重新接入 <设备状态>'
  f.fetchQueue.push(() => Promise.resolve(f.errorResponse({ camera_state: 'RECONNECTING', camera_error: reason })))
  await component.mount()
  assert.equal(component.page.ready.value, false)
  assert.equal(component.page.message.value, reason)
  assert.equal(component.page.failureReason.value, reason)
  assert.match(componentSource, /<strong>\{\{ message \}\}<\/strong>/)
  assert.doesNotMatch(componentSource, /v-html/)
  await f.advance(200)
  assert.equal(component.page.ready.value, true)
  assert.equal(component.page.failureReason.value, '')
  assert.ok(!component.page.message.value.includes(reason))
  assert.equal(f.calls.length, 2)
  await component.unmount()
})

test('a hanging error JSON body times out and its late reason cannot replace recovered video', async () => {
  const f = fixture(), component = f.instance(), body = deferred()
  f.fetchQueue.push(() => Promise.resolve(f.errorResponse({}, { json: () => body.promise })))
  await component.mount()
  await f.advance(1000)
  assert.equal(f.calls[0].signal.aborted, true)
  assert.equal(component.page.message.value, '视觉预览请求超时，正在自动重试')
  await f.advance(200)
  assert.equal(component.page.ready.value, true)
  assert.equal(component.page.failureReason.value, '')
  const url = component.page.imageUrl.value
  body.resolve({ camera_error: '过期响应，不能覆盖新画面' })
  await flush()
  assert.equal(component.page.imageUrl.value, url)
  assert.equal(component.page.failureReason.value, '')
  assert.equal(f.calls.length, 2)
  await component.unmount()
})

test('invalid JSON, missing JSON and malformed camera status use a retryable fallback', async () => {
  const f = fixture(), component = f.instance()
  f.fetchQueue.push(
    () => Promise.resolve(f.errorResponse({}, { json: () => Promise.reject(new SyntaxError('bad JSON')) })),
    () => Promise.resolve(f.response('', { ok: false, headers: { get: () => 'text/html' } })),
    () => Promise.resolve(f.errorResponse({ camera_error: { message: 'not a string' }, camera_state: 'UNKNOWN' })),
    () => Promise.resolve(f.errorResponse({ camera_error: '  ', camera_state: 'RECONNECTING' })),
  )
  await component.mount()
  assert.equal(component.page.message.value, '视觉预览暂无新画面，正在自动重试')
  await f.advance(200)
  assert.equal(component.page.message.value, '视觉预览暂无新画面，正在自动重试')
  await f.advance(200)
  assert.equal(component.page.message.value, '视觉预览暂无新画面，正在自动重试')
  await f.advance(200)
  assert.equal(component.page.message.value, '摄像头已断开，正在自动重连')
  await f.advance(200)
  assert.equal(component.page.ready.value, true)
  assert.equal(component.page.failureReason.value, '')
  await component.unmount()
})

test('a hidden-page error-body attempt cannot overwrite the current status after returning', async () => {
  const f = fixture(), component = f.instance(), old = deferred()
  f.fetchQueue.push(() => Promise.resolve(f.errorResponse({}, { json: () => old.promise })))
  await component.mount()
  await f.visibility(true)
  assert.equal(f.calls[0].signal.aborted, true)
  await f.visibility(false)
  assert.equal(component.page.ready.value, true)
  old.resolve({ camera_state: 'RECONNECTING', camera_error: '上一次隐藏前的错误' })
  await flush()
  assert.equal(component.page.ready.value, true)
  assert.equal(component.page.failureReason.value, '')
  await component.unmount()
})

test('a hanging fetch is cancelled and retried even if its promise ignores abort', async () => {
  const f = fixture(), component = f.instance(), old = deferred()
  let oldBodyReads = 0
  f.fetchQueue.push(() => old.promise)
  await component.mount()
  await f.advance(1000)
  assert.equal(f.calls[0].signal.aborted, true)
  assert.equal(component.page.ready.value, false)
  await f.advance(200)
  assert.equal(component.page.ready.value, true)
  const currentUrl = component.page.imageUrl.value
  old.resolve(f.response('old', { blob: () => { oldBodyReads++; return Promise.resolve({}) } }))
  await flush()
  assert.equal(component.page.imageUrl.value, currentUrl)
  assert.equal(oldBodyReads, 0)
  await component.unmount()
})

test('a hanging response body cannot prevent subsequent fresh frames', async () => {
  const f = fixture(), component = f.instance(), body = deferred()
  f.fetchQueue.push(() => Promise.resolve(f.response('body-hung', { blob: () => body.promise })))
  await component.mount()
  await f.advance(1200)
  assert.equal(component.page.ready.value, true)
  assert.equal(f.calls.length, 2)
  const url = component.page.imageUrl.value
  body.resolve({ old: true })
  await flush()
  assert.equal(component.page.imageUrl.value, url)
  assert.equal(f.createdUrls.length, 1)
  await component.unmount()
})

test('a hanging JPEG decode times out, releases its blob and retries; late rejection cannot clear the new frame', async () => {
  const f = fixture(), component = f.instance(), decode = deferred()
  f.decodeQueue.push(() => decode.promise)
  await component.mount()
  assert.equal(f.createdUrls.length, 1)
  await f.advance(1000)
  assert.equal(component.page.ready.value, false)
  assert.equal(f.activeUrls.size, 0)
  assert.equal(f.images[0].src, '')
  await f.advance(200)
  assert.equal(component.page.ready.value, true)
  const url = component.page.imageUrl.value
  decode.reject(new Error('late decode failure'))
  await flush()
  assert.equal(component.page.imageUrl.value, url)
  assert.equal(component.page.ready.value, true)
  await component.unmount()
  assert.equal(f.activeUrls.size, 0)
})

test('hiding the page cancels polling; becoming visible reconnects immediately and ignores old attempts', async () => {
  const f = fixture(), component = f.instance(), old = deferred()
  f.fetchQueue.push(() => old.promise)
  await component.mount()
  await f.visibility(true)
  assert.equal(f.calls[0].signal.aborted, true)
  await f.advance(5000)
  assert.equal(f.calls.length, 1)
  assert.equal(f.timers.size, 0)
  await f.visibility(false)
  assert.equal(f.calls.length, 2)
  assert.equal(component.page.ready.value, true)
  const url = component.page.imageUrl.value
  old.resolve(f.response('old'))
  await flush()
  assert.equal(component.page.imageUrl.value, url)
  await f.visibility(true)
  assert.equal(component.page.ready.value, false)
  assert.equal(f.activeUrls.size, 0)
  await component.unmount()
})

test('unmount releases pending decode and listeners; remount starts fresh without the old response taking over', async () => {
  const f = fixture(), oldDecode = deferred(), first = f.instance()
  f.decodeQueue.push(() => oldDecode.promise)
  await first.mount()
  assert.equal(f.listeners.size, 1)
  await first.unmount()
  assert.equal(f.listeners.size, 0)
  assert.equal(f.activeUrls.size, 0)
  assert.equal(f.calls[0].signal.aborted, true)
  await f.advance(5000)
  assert.equal(f.calls.length, 1)
  const second = f.instance()
  await second.mount()
  assert.equal(second.page.ready.value, true)
  const url = second.page.imageUrl.value
  oldDecode.resolve()
  await flush()
  assert.equal(second.page.imageUrl.value, url)
  assert.equal(first.page.ready.value, false)
  assert.equal(f.listeners.size, 1)
  await second.unmount()
  assert.equal(f.activeUrls.size, 0)
  assert.equal(f.listeners.size, 0)
  assert.equal(f.timers.size, 0)
})

test('a frozen sequence disappears until the server produces a new frame', async () => {
  const f = fixture(), component = f.instance()
  for (let i = 0; i < 9; i++) f.fetchQueue.push(() => Promise.resolve(f.response('frozen')))
  await component.mount()
  await f.advance(1600)
  assert.equal(component.page.ready.value, false)
  assert.equal(f.activeUrls.size, 0)
  f.fetchQueue.length = 0
  f.fetchQueue.push(() => Promise.resolve(f.response('new-frame')))
  await f.advance(200)
  assert.equal(component.page.ready.value, true)
  await component.unmount()
})

test('a component mounted while hidden starts only when shown', async () => {
  const f = fixture()
  f.document.hidden = true
  const component = f.instance()
  await component.mount()
  await f.advance(5000)
  assert.equal(f.calls.length, 0)
  await f.visibility(false)
  assert.equal(f.calls.length, 1)
  assert.equal(component.page.ready.value, true)
  await component.unmount()
})

test('invalid JPEG and displayed-image errors do not stop the retry loop', async () => {
  const f = fixture(), component = f.instance()
  f.decodeQueue.push(() => Promise.reject(new Error('invalid JPEG')))
  await component.mount()
  assert.equal(component.page.ready.value, false)
  assert.equal(f.activeUrls.size, 0)
  await f.advance(200)
  assert.equal(component.page.ready.value, true)
  component.page.clearImage()
  assert.equal(component.page.ready.value, false)
  await f.advance(200)
  assert.equal(component.page.ready.value, true)
  await component.unmount()
})
