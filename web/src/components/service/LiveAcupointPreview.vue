<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import AppIcon from '@/components/common/AppIcon.vue'
import { abortable } from '@/utils/abortable'

const endpoint = import.meta.env.VITE_VISION_PREVIEW_URL || 'http://127.0.0.1:8767/frame.jpg'
const imageUrl = ref('')
const ready = ref(false)
const connecting = ref(true)
const failureReason = ref('')
const expanded = ref(false)
const expandButton = ref<HTMLButtonElement>()
const closeButton = ref<HTMLButtonElement>()
const message = computed(() => connecting.value ? '正在连接穴位实时画面…' : failureReason.value || '视觉预览未连接或画面已过期')
let disposed = false
let timer: ReturnType<typeof setTimeout> | undefined
let request: AbortController | undefined
let refreshEpoch = 0
let lastSequence = ''
let lastNewFrame = 0

async function openPreview() {
  expanded.value = true
  await nextTick()
  closeButton.value?.focus()
}
function closePreview() {
  expanded.value = false
  expandButton.value?.focus()
}

function clearImage() {
  ready.value = false
  if (imageUrl.value) URL.revokeObjectURL(imageUrl.value)
  imageUrl.value = ''
}
async function refresh() {
  if (disposed || document.hidden) return
  clearTimeout(timer)
  const epoch = ++refreshEpoch
  request?.abort()
  const controller = new AbortController()
  request = controller
  const current = () => !disposed && !document.hidden && epoch === refreshEpoch
  const timeout = setTimeout(() => controller.abort(), 1000)
  let failureMessage = '视觉预览服务未连接，正在自动重试'
  try {
    const response = await abortable(fetch(endpoint, { cache: 'no-store', signal: controller.signal }), controller.signal)
    const contentType = response.headers.get('Content-Type')?.toLowerCase() || ''
    failureMessage = '视觉预览暂无新画面，正在自动重试'
    if (!response.ok || !contentType.startsWith('image/jpeg')) {
      if (contentType.startsWith('application/json')) {
        const body: unknown = await abortable(response.json(), controller.signal)
        if (!current() || controller.signal.aborted) return
        if (body && typeof body === 'object') {
          const status = body as { camera_error?: unknown; camera_state?: unknown }
          if (typeof status.camera_error === 'string' && status.camera_error.trim()) {
            failureMessage = status.camera_error.trim()
          } else if (status.camera_state === 'RECONNECTING') {
            failureMessage = '摄像头已断开，正在自动重连'
          } else if (status.camera_state === 'WAITING') {
            failureMessage = '等待摄像头输出新画面，正在自动重试'
          }
        }
      }
      throw new Error('no fresh frame')
    }
    failureMessage = '实时画面读取失败，正在自动重试'
    const sequence = response.headers.get('X-Preview-Sequence') || ''
    if (sequence !== lastSequence || !lastSequence) {
      lastSequence = sequence
      lastNewFrame = performance.now()
    } else if (performance.now() - lastNewFrame > 1500) {
      failureMessage = '实时画面已停滞，正在自动重试'
      throw new Error('frame frozen')
    }
    const blob = await abortable(response.blob(), controller.signal)
    if (!current() || controller.signal.aborted) return
    const next = URL.createObjectURL(blob)
    const decoded = new Image()
    let ownsUrl = true
    try {
      // Decode before replacing the visible image, so JPEG loading cannot
      // expose a blank frame between consecutive preview updates.
      decoded.src = next
      await abortable(decoded.decode(), controller.signal)
      if (!current() || controller.signal.aborted) return
      const previous = imageUrl.value
      imageUrl.value = next
      ownsUrl = false // clearImage now owns the visible URL.
      ready.value = true
      failureReason.value = ''
      try { await nextTick() }
      finally { if (previous) URL.revokeObjectURL(previous) }
    } finally {
      decoded.src = ''
      if (ownsUrl) URL.revokeObjectURL(next)
    }
  } catch {
    if (current()) {
      clearImage()
      failureReason.value = controller.signal.aborted ? '视觉预览请求超时，正在自动重试' : failureMessage
    }
  } finally {
    clearTimeout(timeout)
    if (current()) {
      request = undefined
      connecting.value = false
      timer = setTimeout(refresh, 200)
    }
  }
}
function cancelRefresh() {
  ++refreshEpoch
  clearTimeout(timer)
  timer = undefined
  request?.abort()
  request = undefined
}
function visibilityChanged() {
  if (disposed) return
  if (document.hidden) {
    cancelRefresh()
    clearImage()
    connecting.value = false
  } else {
    connecting.value = true
    void refresh()
  }
}
onMounted(() => {
  document.addEventListener('visibilitychange', visibilityChanged)
  visibilityChanged()
})
onBeforeUnmount(() => {
  disposed = true
  document.removeEventListener('visibilitychange', visibilityChanged)
  cancelRefresh()
  clearImage()
})
</script>

<template>
  <div class="live-preview" :class="{ 'is-live': ready }">
    <div class="preview-topbar">
      <span class="preview-title"><AppIcon name="camera" :size="17" />穴位实时定位</span>
      <span class="live-badge" :class="{ online: ready }">{{ ready ? '实时画面' : '等待画面' }}</span>
      <button ref="expandButton" type="button" class="expand-button" :disabled="!ready" @click="openPreview">放大画面</button>
    </div>
    <div class="preview-content">
      <img v-if="ready" :src="imageUrl" class="live-image" alt="D435i 实时穴位定位画面及坐标列表" @error="clearImage" />
      <div v-else class="preview-empty" role="status">
        <AppIcon name="camera" :size="38" color="var(--brand)" />
        <strong>{{ message }}</strong>
        <p>正在自动重连，摄像头重新接入后会恢复画面。</p>
      </div>
    </div>
    <div class="preview-caption"><span>请保持身体居中 · 不保存影像</span><span>比例定位与坐标仅供预览</span></div>
    <Teleport to="#heacon-app-canvas">
    <div v-if="expanded" class="preview-dialog" role="dialog" aria-modal="true" aria-label="穴位实时画面" @keydown.esc.stop.prevent="closePreview" @keydown.tab.prevent="closeButton?.focus()" @click.self="closePreview">
      <div class="dialog-card"><button ref="closeButton" type="button" class="dialog-close" @click="closePreview">关闭放大画面</button>
        <img v-if="ready" :src="imageUrl" alt="放大的实时穴位定位画面" />
        <p v-else role="status">{{ message }}</p>
      </div>
    </div>
    </Teleport>
  </div>
</template>

<style scoped lang="scss">
.live-preview{width:100%;height:100%;min-height:0;display:flex;flex-direction:column;background:#fff9f4}
.preview-topbar{display:flex;align-items:center;gap:10px;padding:14px 16px;background:#fffaf7;border-bottom:1px solid var(--line);flex-shrink:0}
.preview-title{display:flex;align-items:center;gap:7px;font-size:14px;font-weight:600;color:var(--ink)}
.live-badge{margin-left:auto;font-size:11px;color:var(--muted);padding:4px 7px;border-radius:6px;background:#fff0e7}
.live-badge.online{color:#258254;background:#edf8f0}
.expand-button{font-size:11px;border:1px solid var(--line);border-radius:7px;background:white;color:var(--brand);padding:5px 8px;cursor:pointer}
.expand-button:disabled{opacity:.4;cursor:default}
.preview-content{flex:1;min-height:0;display:flex;align-items:center;justify-content:center;overflow:hidden}
.is-live .preview-content{background:#17191d}
.live-image{width:100%;height:100%;object-fit:contain;display:block}
.preview-empty{text-align:center;color:var(--muted);padding:30px 18px;display:flex;flex-direction:column;align-items:center;gap:14px}
.preview-empty strong{font-size:15px;font-weight:500;color:var(--ink)}
.preview-empty p{font-size:12px;line-height:1.8;margin:0}
.preview-caption{display:flex;flex-wrap:wrap;justify-content:space-between;gap:6px;padding:12px 14px;font-size:10px;color:var(--muted);border-top:1px solid var(--line);flex-shrink:0}
.preview-dialog{position:absolute;inset:0;z-index:1000;background:#000a;display:flex;align-items:center;justify-content:center;padding:32px}
.dialog-card{width:100%;height:100%;background:#17191d;border-radius:14px;display:flex;flex-direction:column;align-items:center;overflow:hidden;padding:12px}
.dialog-card img{width:100%;flex:1;min-height:0;object-fit:contain}
.dialog-card p{color:white;margin:auto}
.dialog-close{align-self:flex-end;border:1px solid var(--line);border-radius:8px;background:#fff9f4;color:var(--ink);padding:9px 16px;cursor:pointer;margin-bottom:8px}
</style>
