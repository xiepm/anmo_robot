<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { ProtocolPoint } from '@/types/robot'
import { toPointLabel } from '@/data/points'
import { clampPixel, parsePhotoData, pointKey, routeSegments, type Capture, type PhotoAdjustment, type PhotoData } from '@/utils/photoEditor'

const props = defineProps<{ capture: Capture; points: ProtocolPoint[]; adjustments: PhotoAdjustment[]; locked?: boolean; confirmed?: boolean }>()
const emit = defineEmits<{ change: [points: PhotoAdjustment[]]; positions: [points: PhotoAdjustment[]]; ready: [value: boolean]; editing: [value: boolean] }>()
const data = ref<PhotoData | null>(null)
const imageUrl = ref('')
const loaded = ref(false)
const error = ref('')
const editing = ref(true)
const selected = ref('')
const draft = ref<PhotoAdjustment[]>(props.adjustments.map(p => ({ ...p })))
const svgRef = ref<SVGSVGElement>()
const feedback = ref('可直接拖动圆点微调；调整后点击“确认穴位坐标”')
const abort = new AbortController()
let disposed = false
let drag: { key: string; pointer: number; before: PhotoAdjustment[]; dx: number; dy: number } | null = null
const dragging = ref('')
const available = computed(() => new Set(props.capture.available_points.map(pointKey)))
const route = computed(() => props.points.map((p, index) => {
  const key = pointKey(p)
  const original = data.value?.points.find(q => pointKey(q) === key && q.coordinate_valid && available.value.has(key))
  const adjustment = draft.value.find(q => pointKey(q) === key)
  return { ...p, key, number: index + 1, label: toPointLabel(p), original,
    point: original ? (adjustment ?? original) : null, adjusted: Boolean(adjustment) }
}))
const segments = computed(() => routeSegments(route.value.map(p => p.point)))
const current = computed(() => route.value.find(p => p.key === selected.value))
const canEdit = computed(() => loaded.value && !error.value && !props.locked)
watch(() => props.locked, value => { if (value) { cancelDrag(); editing.value = false } })

function endpoint(path: string) {
  const url = new URL(import.meta.env.VITE_VISION_PREVIEW_URL || 'http://127.0.0.1:8767/frame.jpg')
  url.pathname = path
  url.search = new URLSearchParams({ frame: String(props.capture.frame_number), received: String(props.capture.received_monotonic_s) }).toString()
  return url.href
}
onMounted(async () => {
  emit('ready', false)
  const timeout = setTimeout(() => abort.abort(), 8000)
  try {
    const [json, photo] = await Promise.all(['/snapshot/points.json', '/snapshot/raw.jpg'].map(path =>
      fetch(endpoint(path), { cache: 'no-store', signal: abort.signal })))
    if (!json!.ok || !photo!.ok || !photo!.headers.get('Content-Type')?.startsWith('image/jpeg')) throw new Error('本次照片已失效，请重新扫描')
    const payload = parsePhotoData(await json!.json(), props.capture)
    const blob = await photo!.blob()
    if (disposed) return
    data.value = payload
    imageUrl.value = URL.createObjectURL(blob)
    selected.value = route.value.find(p => p.point)?.key ?? ''
  } catch (e) {
    if (!disposed) error.value = e instanceof Error && e.name !== 'AbortError' ? e.message : '照片加载超时，请重新扫描'
  } finally { clearTimeout(timeout) }
})
onBeforeUnmount(() => {
  disposed = true
  abort.abort()
  if (imageUrl.value) URL.revokeObjectURL(imageUrl.value)
})
function publishPositions() {
  emit('positions', route.value.flatMap(p => p.point ? [{ name:p.name, side:p.side, x:p.point.x, y:p.point.y }] : []))
}
function imageLoaded() { loaded.value = true; emit('ready', true); publishPositions() }
function imageFailed() { error.value = '照片加载失败，请重新扫描'; loaded.value = false; emit('ready', false) }
function toPixel(event: PointerEvent) {
  const svg = svgRef.value
  const matrix = svg?.getScreenCTM()
  if (!svg || !matrix) return null
  const p = svg.createSVGPoint(); p.x = event.clientX; p.y = event.clientY
  return p.matrixTransform(matrix.inverse())
}
function updatePoint(key: string, x: number, y: number) {
  const item = route.value.find(p => p.key === key)
  if (!data.value || !item?.original) return
  const pixel = clampPixel(x, y, data.value.width, data.value.height)
  draft.value = draft.value.filter(p => pointKey(p) !== key)
  if (Math.abs(pixel.x - item.original.x) >= .05 || Math.abs(pixel.y - item.original.y) >= .05) {
    draft.value.push({ name: item.name, side: item.side, ...pixel })
  }
}
function commit() {
  emit('change', draft.value.map(p => ({ ...p })))
  publishPositions()
  feedback.value = draft.value.length ? `已调整 ${draft.value.length} 个点，请确认穴位坐标后下发` : '已恢复识别位置，请确认穴位坐标'
}
function pointerDown(key: string, event: PointerEvent) {
  selected.value = key
  if (!canEdit.value || drag || event.button !== 0) return
  editing.value = true
  const pixel = toPixel(event), point = route.value.find(p => p.key === key)?.point
  if (!pixel || !point) return
  event.preventDefault()
  ;(event.currentTarget as SVGGElement).setPointerCapture(event.pointerId)
  drag = { key, pointer: event.pointerId, before: draft.value.map(p => ({ ...p })), dx: point.x - pixel.x, dy: point.y - pixel.y }
  dragging.value = key
  emit('editing', true)
}
function pointerMove(event: PointerEvent) {
  if (!drag || event.pointerId !== drag.pointer || !canEdit.value) return
  const pixel = toPixel(event)
  if (pixel) updatePoint(drag.key, pixel.x + drag.dx, pixel.y + drag.dy)
}
function pointerUp(event: PointerEvent) {
  if (!drag || event.pointerId !== drag.pointer) return
  pointerMove(event)
  drag = null; dragging.value = ''
  commit()
  emit('editing', false)
}
function cancelDrag() {
  if (drag) draft.value = drag.before
  drag = null; dragging.value = ''
  emit('editing', false)
}
function toggleEditing() {
  cancelDrag()
  editing.value = !editing.value
  feedback.value = editing.value ? '拖动圆点调整；方向键微调，Shift + 方向键加快' : '按编号顺序连线；确认穴位坐标后下发'
}
function keyDown(key: string, event: KeyboardEvent) {
  if (event.key === 'Escape') { cancelDrag(); editing.value = false; return }
  if (!canEdit.value) return
  selected.value = key
  if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); editing.value = true; return }
  const directions: Record<string, [number, number]> = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }
  const delta = directions[event.key], point = current.value?.point
  if (!delta || !point) return
  event.preventDefault()
  const step = event.shiftKey ? 5 : 1
  updatePoint(key, point.x + delta[0] * step, point.y + delta[1] * step)
  commit()
}
function restore() { cancelDrag(); draft.value = []; commit() }
</script>

<template>
  <div class="photo-editor">
    <div class="editor-tools">
      <span>{{ editing ? '手动调整' : '穴位路线' }} <small>按方案顺序</small></span>
      <div>
        <button type="button" :disabled="!canEdit" :aria-pressed="editing" @click="toggleEditing">{{ editing ? '完成调整' : '调整穴位' }}</button>
        <button type="button" :disabled="!canEdit || !draft.length" @click="restore">恢复识别位置</button>
      </div>
    </div>
    <div class="photo-stage">
      <p v-if="error" class="editor-message" role="alert">{{ error }}</p>
      <p v-else-if="!data" class="editor-message" role="status">正在加载本次照片与穴位…</p>
      <svg v-if="data && !error" ref="svgRef" class="photo-overlay" :class="{ editable: editing }"
        :viewBox="`0 0 ${data.width} ${data.height}`" preserveAspectRatio="xMidYMid meet"
        aria-label="本次扫描照片与可调整穴位路线" @keydown.esc="cancelDrag(); editing = false">
        <image :href="imageUrl" x="0" y="0" :width="data.width" :height="data.height" @load="imageLoaded" @error="imageFailed" />
        <g v-if="loaded">
          <line v-for="(segment, index) in segments" :key="index" class="route-line"
            :x1="segment.from.x" :y1="segment.from.y" :x2="segment.to.x" :y2="segment.to.y" />
          <template v-for="item in route" :key="item.key">
            <g v-if="item.point" class="photo-point" :class="{ selected: selected === item.key, adjusted: item.adjusted, dragging: dragging === item.key }"
              :transform="`translate(${item.point.x} ${item.point.y})`" tabindex="0" role="button"
              :aria-label="`调整${item.label}`" :aria-disabled="!canEdit"
              @focus="selected = item.key" @pointerdown="pointerDown(item.key, $event)" @pointermove="pointerMove"
              @pointerup="pointerUp" @pointercancel="cancelDrag" @lostpointercapture="cancelDrag" @keydown="keyDown(item.key, $event)">
              <circle class="hit-target" r="22" /><circle class="halo" r="17" /><circle class="dot" r="10" />
              <text class="number" text-anchor="middle" dominant-baseline="central">{{ item.number }}</text>
              <text class="name" :x="item.point.x > data.width - 110 ? -17 : 17" :text-anchor="item.point.x > data.width - 110 ? 'end' : 'start'"
                :y="item.point.y < 24 ? 25 : -13">{{ item.label }}</text>
            </g>
          </template>
        </g>
      </svg>
    </div>
    <div class="point-list" aria-label="方案穴位顺序">
      <button v-for="item in route" :key="item.key" type="button" :disabled="!item.point || !loaded || locked"
        :class="{ active: selected === item.key }" @click="selected = item.key">
        {{ item.number }} {{ item.label }}<small>{{ !item.point ? '未识别' : item.adjusted ? '已调整' : '' }}</small>
      </button>
    </div>
    <p class="editor-feedback" aria-live="polite">{{ confirmed ? '最终穴位坐标已由控制端确认；如需修改，请点击“重新调整穴位”' : feedback }}</p>
    <p v-if="current?.point" class="pixel-position">{{ current.label }} · 照片位置 X {{ current.point.x.toFixed(1) }} / Y {{ current.point.y.toFixed(1) }} px</p>
  </div>
</template>

<style scoped>
.photo-editor { height: 100%; min-height: 0; display: flex; flex-direction: column; gap: 8px; }
.editor-tools { display: flex; justify-content: space-between; align-items: center; gap: 8px; font-size: 14px; color: var(--ink); flex-wrap: wrap; }
.editor-tools small { color: var(--muted); margin-left: 4px; font-size: 11px; }
.editor-tools > div { display: flex; gap: 6px; }
button { border: 1px solid var(--line); border-radius: 9px; background: #fff; color: var(--ink); cursor: pointer; padding: 6px 9px; font: inherit; font-size: 12px; }
button[aria-pressed="true"], button.active { color: var(--brand); border-color: var(--brand); background: #fff4ec; }
button:disabled { opacity: .5; cursor: default; }
button:focus-visible { outline: 2px solid var(--brand); outline-offset: 2px; }
.photo-stage { flex: 1; min-height: 0; position: relative; background: #fff9f4; border-radius: 10px; overflow: hidden; }
.photo-overlay { width: 100%; height: 100%; display: block; user-select: none; }
.photo-overlay.editable { touch-action: none; }
.photo-overlay image, .route-line { pointer-events: none; }
.route-line { stroke: #ff9b39; stroke-width: 3; stroke-dasharray: 8 5; vector-effect: non-scaling-stroke; }
.photo-point { cursor: pointer; outline: none; touch-action: none; }
.editable .photo-point { cursor: grab; }
.editable .photo-point.dragging { cursor: grabbing; }
.hit-target { fill: transparent; }
.halo { fill: #ff782b; opacity: .2; pointer-events: none; }
.selected .halo, .photo-point:focus-visible .halo { opacity: .6; }
.dot { fill: #ff7019; stroke: white; stroke-width: 2; }
.adjusted .dot { fill: #157ca5; }
.number { fill: white; font-size: 12px; font-weight: 700; pointer-events: none; }
.name { fill: white; stroke: #462b21; stroke-width: 3px; paint-order: stroke; font-size: 14px; font-weight: 600; pointer-events: none; }
.point-list { display: flex; flex-wrap: wrap; gap: 5px; }
.point-list small { margin-left: 5px; color: #7c8187; }
.editor-feedback, .pixel-position { margin: 0; font-size: 12px; line-height: 1.4; color: var(--brand); }
.pixel-position { color: var(--muted); }
.editor-message { padding: 20px; color: var(--muted); font-size: 14px; }
</style>
