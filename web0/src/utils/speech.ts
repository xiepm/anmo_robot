/**
 * 服务语音播报
 * 流程短句只由晓晓神经音播放一次，不使用本机系统语音
 */
import { useSettingsStore } from '@/stores/settings'

/** 最短有效静音 wav，在用户点击时解锁后续神经音播放 */
const SILENT_WAV =
  'data:audio/wav;base64,UklGRigAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQQAAAAAAA=='

let player: HTMLAudioElement | null = null
let audioCtx: AudioContext | null = null
let objectUrl: string | null = null
let speakSeq = 0
let lastPointAnnounce = { name: '', at: 0 }
const audioCache = new Map<string, Promise<Blob>>()

function getVolume() {
  const settings = useSettingsStore()
  return Math.max(0, Math.min(1, settings.volume / 100))
}

function isMuted() {
  return useSettingsStore().muted
}

function ensurePlayer() {
  player ??= new Audio()
  return player
}

function releaseUrl() {
  if (!objectUrl) return
  URL.revokeObjectURL(objectUrl)
  objectUrl = null
}

/** 停掉正在播放的神经音 */
export function stopSpeech() {
  speakSeq += 1
  if (!player) return
  player.pause()
  releaseUrl()
}

/** 在用户手势中解锁音频，必须同步调用 */
export function unlockSpeech() {
  try {
    const AC =
      window.AudioContext ||
      (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
    if (AC) {
      audioCtx ??= new AC()
      void audioCtx.resume()
    }
  } catch {
    /* 浏览器不允许时保持静音 */
  }

  const audio = ensurePlayer()
  audio.src = SILENT_WAV
  audio.volume = 0.01
  void audio.play().then(() => {
    if (audio.src.startsWith('data:')) audio.pause()
  })
}

/** 穴位切换时的固定句式，供预取和播报共用 */
export function servicePointLine(pointName: string) {
  return `正在服务${pointName}，请保持自然呼吸。`
}

function loadNeuralAudio(text: string): Promise<Blob> {
  const cached = audioCache.get(text)
  if (cached) return cached
  const pending = fetch('/heacon-tts', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text }),
  }).then(async (response) => {
    if (!response.ok) throw new Error(`tts ${response.status}`)
    const blob = await response.blob()
    if (!blob.size) throw new Error('empty audio')
    return blob
  })
  const guarded = pending.catch((error: unknown) => {
    audioCache.delete(text)
    throw error
  })
  audioCache.set(text, guarded)
  return guarded
}

/** 提前合成，进入运行页时直接播放已缓存的音频 */
export function prefetchSpeech(text: string) {
  const content = text.trim()
  if (!content || typeof window === 'undefined') return
  void loadNeuralAudio(content).catch(() => {
    /* 预取失败时，正式播报会再请求一次 */
  })
}

/** 播报流程短句；静音或神经音失败时不改用系统语音 */
export function speak(text: string) {
  const content = text.trim()
  if (!content || typeof window === 'undefined' || isMuted()) return

  const token = ++speakSeq
  if (player) player.pause()
  releaseUrl()

  void (async () => {
    try {
      const blob = await loadNeuralAudio(content)
      if (token !== speakSeq || isMuted()) return
      const url = URL.createObjectURL(blob)
      objectUrl = url
      const audio = ensurePlayer()
      audio.src = url
      audio.volume = getVolume()
      await audio.play()
    } catch {
      /* 神经音未就绪时保持安静 */
    }
  })()
}

/** 穴位切换时的流程播报 */
export function speakServicePoint(pointName: string) {
  if (!pointName || pointName === '—') return
  const now = Date.now()
  if (pointName === lastPointAnnounce.name && now - lastPointAnnounce.at < 1500) return
  lastPointAnnounce = { name: pointName, at: now }
  speak(servicePointLine(pointName))
}

/** 打开播报时的试听 */
export function speakPreview(text = '语音播报已开启，服务过程中将播报当前穴位。') {
  unlockSpeech()
  lastPointAnnounce = { name: '', at: 0 }
  speak(text)
}
