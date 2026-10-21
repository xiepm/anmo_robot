/**
 * 开发/预览态神经语音代理
 * 使用微软 Edge 朗读接口合成 zh-CN-XiaoxiaoNeural
 */
import { Readable } from 'node:stream'
import type { Plugin, Connect } from 'vite'
import { MsEdgeTTS, OUTPUT_FORMAT } from 'msedge-tts'

const TTS_PATH = '/heacon-tts'
const VOICE = 'zh-CN-XiaoxiaoNeural'
const SYNTH_TIMEOUT_MS = 10000
const PROSODY = { rate: '-8%', pitch: '+2Hz' }

/** 与界面穴位标签一致，服务启动后后台合成，进入运行页时直接返回缓存 */
const WARM_LINES = [
  '语音播报已开启，服务过程中将播报当前穴位。',
  ...[
    '大椎',
    '左肩井',
    '右肩井',
    '命门',
    '左肾俞',
    '右肾俞',
    '腰阳关',
    '左大肠俞',
    '右大肠俞',
    '左风池',
    '右风池',
    '左天宗',
    '右天宗',
    '左腰背',
    '右腰背',
  ].map((name) => `正在服务${name}，请保持自然呼吸。`),
]

function escapeXml(text: string) {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&apos;')
}

function readBody(req: Connect.IncomingMessage): Promise<string> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = []
    req.on('data', (c) => chunks.push(Buffer.isBuffer(c) ? c : Buffer.from(c)))
    req.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')))
    req.on('error', reject)
  })
}

function withTimeout<T>(promise: Promise<T>, ms: number, label: string): Promise<T> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`${label} timeout`)), ms)
    promise.then(
      (value) => {
        clearTimeout(timer)
        resolve(value)
      },
      (error) => {
        clearTimeout(timer)
        reject(error)
      },
    )
  })
}

let client: MsEdgeTTS | null = null
let connected: Promise<void> | null = null
let queue: Promise<void> = Promise.resolve()
const cache = new Map<string, Buffer>()

function resetClient() {
  try {
    client?.close()
  } catch {
    /* 连接已断开时关闭失败可忽略 */
  }
  client = null
  connected = null
}

function ensureClient() {
  if (!connected) {
    client = new MsEdgeTTS()
    connected = client
      .setMetadata(VOICE, OUTPUT_FORMAT.AUDIO_24KHZ_48KBITRATE_MONO_MP3)
      .catch((error: unknown) => {
        resetClient()
        throw error
      })
  }
  return connected
}

function readStream(stream: Readable): Promise<Buffer> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = []
    stream.on('data', (chunk) => {
      chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk))
    })
    stream.once('end', () => resolve(Buffer.concat(chunks)))
    stream.once('error', reject)
  })
}

/** 复用同一条语音连接；同一句只合成一次 */
async function synthesize(text: string): Promise<Buffer> {
  const hit = cache.get(text)
  if (hit) return hit

  const run = queue.then(async () => {
    const cached = cache.get(text)
    if (cached) return cached
    try {
      await ensureClient()
      const { audioStream } = client!.toStream(escapeXml(text), PROSODY)
      const audio = await withTimeout(readStream(audioStream), SYNTH_TIMEOUT_MS, 'tts')
      if (!audio.length) throw new Error('empty audio')
      cache.set(text, audio)
      return audio
    } catch (error) {
      resetClient()
      throw error
    }
  })
  queue = run.then(
    () => undefined,
    () => undefined,
  )
  return run
}

function warmCache() {
  void (async () => {
    for (const line of WARM_LINES) {
      try {
        await synthesize(line)
      } catch (error) {
        console.error('[heacon-tts] warmup', error)
        return
      }
    }
  })()
}

function createHandler(): Connect.NextHandleFunction {
  return async (req, res, next) => {
    const url = req.url?.split('?')[0] ?? ''
    if (url !== TTS_PATH || req.method !== 'POST') {
      next()
      return
    }

    try {
      const raw = await readBody(req)
      const payload = JSON.parse(raw || '{}') as { text?: string }
      const text = String(payload.text ?? '').trim().slice(0, 200)
      if (!text) {
        res.statusCode = 400
        res.end('text required')
        return
      }

      const audio = await synthesize(text)
      res.statusCode = 200
      res.setHeader('Content-Type', 'audio/mpeg')
      res.setHeader('Cache-Control', 'private, max-age=86400')
      res.setHeader('Content-Length', String(audio.length))
      res.end(audio)
    } catch (error) {
      console.error('[heacon-tts]', error)
      res.statusCode = 502
      res.setHeader('Content-Type', 'application/json; charset=utf-8')
      res.end(JSON.stringify({ message: 'TTS synthesize failed' }))
    }
  }
}

export function heaconTtsPlugin(): Plugin {
  const handler = createHandler()
  return {
    name: 'heacon-tts',
    configureServer(server) {
      server.middlewares.use(handler)
      warmCache()
    },
    configurePreviewServer(server) {
      server.middlewares.use(handler)
      warmCache()
    },
  }
}
