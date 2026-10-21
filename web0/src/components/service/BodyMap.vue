<script setup lang="ts">
/** 俯卧示意穴位图：可拖动调整落点 */
import { computed, ref, watch } from 'vue'
import bodyProneImg from '@/assets/images/body-prone.png'
import { POINT_FRAME, type Acupoint } from '@/data/points'

const props = defineProps<{
  points: Acupoint[]
}>()

const emit = defineEmits<{
  'update:points': [points: Acupoint[]]
}>()

const localPoints = ref<Acupoint[]>(clonePoints(props.points))
const feedback = ref('拖动穴位即可调整落点')
const draggingIndex = ref<number | null>(null)
const svgRef = ref<SVGSVGElement | null>(null)

watch(
  () => props.points,
  (value) => {
    localPoints.value = clonePoints(value)
    feedback.value = '拖动穴位即可调整落点'
  },
  { deep: true },
)

const routePoints = computed(() =>
  localPoints.value.map(([, x, y]) => `${x},${y}`).join(' '),
)

function clonePoints(list: Acupoint[]): Acupoint[] {
  return list.map(([name, x, y]) => [name, x, y])
}

function clamp(x: number, y: number) {
  return {
    x: Math.max(120, Math.min(420, x)),
    y: Math.max(210, Math.min(540, y)),
  }
}

function toSvgPoint(event: PointerEvent) {
  const svg = svgRef.value
  if (!svg) return { x: 0, y: 0 }
  const pt = svg.createSVGPoint()
  pt.x = event.clientX
  pt.y = event.clientY
  const ctm = svg.getScreenCTM()
  if (!ctm) return { x: 0, y: 0 }
  const local = pt.matrixTransform(ctm.inverse())
  return { x: local.x, y: local.y }
}

function labelX(x: number) {
  return x + (x < POINT_FRAME.width / 2 ? -88 : 16)
}

function onPointerDown(index: number, event: PointerEvent) {
  event.preventDefault()
  const target = event.currentTarget as SVGGElement
  target.setPointerCapture(event.pointerId)
  draggingIndex.value = index
  feedback.value = `正在移动 ${localPoints.value[index][0]}，松手保存位置`
}

function onPointerMove(index: number, event: PointerEvent) {
  if (draggingIndex.value !== index) return
  const { x, y } = clamp(toSvgPoint(event).x, toSvgPoint(event).y)
  const next = clonePoints(localPoints.value)
  next[index] = [next[index][0], Math.round(x), Math.round(y)]
  localPoints.value = next
}

function onPointerUp(index: number) {
  if (draggingIndex.value !== index) return
  draggingIndex.value = null
  feedback.value = '已保留新位置，可继续核对其他穴位'
  emit('update:points', clonePoints(localPoints.value))
}
</script>

<template>
  <div class="body-map-wrap">
    <div class="body-stage">
      <div class="body-map">
        <img class="body-img" :src="bodyProneImg" alt="俯卧背部示意" draggable="false" />
        <svg
          ref="svgRef"
          class="overlay"
          :viewBox="`0 0 ${POINT_FRAME.width} ${POINT_FRAME.height}`"
          preserveAspectRatio="xMidYMid meet"
          role="img"
          aria-label="穴位路线叠加层，可拖动调整"
        >
          <polyline
            class="route-line"
            fill="none"
            :points="routePoints"
            pointer-events="none"
          />
          <g
            v-for="(pt, index) in localPoints"
            :key="`${pt[0]}-${index}`"
            class="acupoint"
            :class="{ 'is-dragging': draggingIndex === index }"
            tabindex="0"
            role="button"
            :aria-label="`拖动${pt[0]}`"
            @pointerdown="onPointerDown(index, $event)"
            @pointermove="onPointerMove(index, $event)"
            @pointerup="onPointerUp(index)"
            @pointercancel="onPointerUp(index)"
          >
            <circle class="point-glow" :cx="pt[1]" :cy="pt[2]" r="28" />
            <circle class="point-dot" :cx="pt[1]" :cy="pt[2]" r="12" />
            <text
              class="point-label"
              :x="labelX(pt[1])"
              :y="pt[2] - 14"
            >
              {{ pt[0] }}
            </text>
          </g>
        </svg>
      </div>
    </div>
    <p class="feedback" aria-live="polite">{{ feedback }}</p>
  </div>
</template>

<style scoped lang="scss">
.body-map-wrap {
  width: 100%;
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.body-stage {
  flex: 1;
  min-height: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #fff9f4;
  border-radius: 14px;
  padding: 4px;
  overflow: hidden;
}

.body-map {
  position: relative;
  width: auto;
  height: 100%;
  max-height: 100%;
  max-width: min(420px, 100%);
  aspect-ratio: 3 / 4;
}

.body-img {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: contain;
  user-select: none;
  -webkit-user-drag: none;
  pointer-events: none;
}

.overlay {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  touch-action: none;
  user-select: none;
}

.route-line {
  stroke: var(--brand);
  stroke-width: 3;
  stroke-dasharray: 7 5;
}

.acupoint {
  outline: none;
  cursor: grab;
}

.acupoint.is-dragging {
  cursor: grabbing;
}

.point-glow {
  fill: #ff6a1a;
  opacity: 0.18;
}

.point-dot {
  fill: #ff6a1a;
  stroke: #fff;
  stroke-width: 4;
}

.acupoint.is-dragging .point-glow {
  opacity: 0.5;
}

.acupoint.is-dragging .point-dot {
  fill: #ff9a4a;
  stroke: #fff8f0;
  stroke-width: 6;
}

.point-label {
  fill: #8a3f1c;
  font-size: 15px;
  font-weight: 700;
  stroke: #fff9f4;
  stroke-width: 5;
  paint-order: stroke;
  pointer-events: none;
}

.feedback {
  margin: 4px 0 0;
  min-height: 20px;
  text-align: center;
  font-size: 14px;
  color: var(--brand);
  flex-shrink: 0;
}
</style>
