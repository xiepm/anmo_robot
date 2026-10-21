<script setup lang="ts">
/**
 * 服务运行监控
 * 当前穴位、剩余时间与实测能量均取自下位机 STATUS；结束、停止与故障以设备状态为准
 */
import { computed, onMounted, onBeforeUnmount, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useRouter } from 'vue-router'
import AppConfirm from '@/components/common/AppConfirm.vue'
import { toPointLabel } from '@/data/points'
import { formatDuration, pointTalk } from '@/data/run'
import { useFlowStore } from '@/stores/flow'
import { useRobotStore } from '@/stores/robot'
import { useSettingsStore } from '@/stores/settings'
import { useToastStore } from '@/stores/toast'
import type { RobotStatus, RobotStep } from '@/types/robot'
import { speakPreview, speakServicePoint, stopSpeech } from '@/utils/speech'

const STEP_TEXT: Record<RobotStep, string> = {
  NONE: '准备中',
  APPROACH: '接近中',
  DWELL: '停留施术',
  RETRACT: '抬离中',
  TRANSFER: '过渡中',
}

const router = useRouter()
const flow = useFlowStore()
const robot = useRobotStore()
const settings = useSettingsStore()
const toast = useToastStore()
const { muted } = storeToRefs(settings)

const showEndModal = ref(false)
/** 已发送停止请求，等待设备确认停止 */
const stopping = ref(false)
/** 已观测到运行态，避免把开始前的旧状态误判为结束 */
let seenActive = false
let leaving = false
let announcedPoint = ''

const status = computed(() => robot.status)
const returningToStart = computed(() => status.value?.state === 'RUNNING'
  && status.value.noncontact?.returning_to_start === true)
const points = computed(() => flow.activePoints.map(([name]) => name))
const massage = computed(() => flow.isMassage)
const unit = computed(() => (massage.value ? ' N' : '℃'))
const safetyLimit = computed(() => (massage.value ? settings.forceLimit : settings.tempLimit))
const perPointSec = computed(() => flow.totalSec / Math.max(1, points.value.length))

const currentIndex = computed(() => {
  const point = status.value?.current_point
  return point ? points.value.indexOf(toPointLabel(point)) : -1
})

const currentPoint = computed(() => points.value[currentIndex.value] ?? '—')

const stepText = computed(() => {
  if (status.value?.state === 'STOPPING') return '停止中'
  if (returningToStart.value) return '返回起始点位'
  return STEP_TEXT[status.value?.step ?? 'NONE']
})

const routeText = computed(
  () =>
    `路线 ${String(currentIndex.value + 1).padStart(2, '0')} / ${points.value.length} · ${stepText.value}`,
)

const timerText = computed(() => {
  const m = Math.floor(flow.remainSec / 60)
  const s = flow.remainSec % 60
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
})

const summary = computed(() => {
  const name = currentPoint.value
  const method = flow.selectedPlan?.method ?? ''
  const info = pointTalk[name] ?? {
    meridian: '经络要位',
    talk: '正在对本部位进行专业施术，疏通经络、调和气血，帮助您获得更舒适有效的体验。',
    tips: ['疏经通络', '调和气血'],
  }
  const verb = massage.value ? `以「${method}」手法` : `以「${method}」灸法`
  return {
    title: `正在服务 · ${name}`,
    text: `${name}属${info.meridian}。${info.talk} ${verb}专注此处，请保持俯卧放松、自然呼吸，让经气得以顺达，效果更易感知。`,
    tips: [...info.tips, massage.value ? '避免主动发力' : '保持热感舒适', '自然呼吸'],
  }
})

/** 实测能量；无有效反馈时为 null，不以目标值代替 */
const liveValue = computed(() => {
  const energy = status.value?.actual_energy
  return energy?.valid ? energy.value : null
})

const liveDelta = computed(() =>
  liveValue.value == null ? null : Number((liveValue.value - flow.serviceParams.level).toFixed(1)),
)

const liveInBand = computed(() => {
  if (liveValue.value == null || liveDelta.value == null) return false
  const band = massage.value ? 0.8 : 0.6
  return Math.abs(liveDelta.value) <= band && liveValue.value <= safetyLimit.value
})

const deltaText = computed(() => {
  if (liveDelta.value == null) return '无实测数据'
  const sign = liveDelta.value > 0 ? '+' : ''
  return `${sign}${liveDelta.value}${unit.value} · ${liveInBand.value ? '安全带内' : '偏离目标'}`
})

const gapText = computed(() => {
  const s = status.value
  return s?.gap_valid && s.actual_gap_mm != null ? `${s.actual_gap_mm} mm` : '无实测数据'
})

const routeItems = computed(() =>
  points.value.map((name, i) => {
    const active = i === currentIndex.value
    const done = i < currentIndex.value
    const remain = flow.remainSec - perPointSec.value * (points.value.length - 1 - i)
    const timeText = active
      ? `剩余 ${formatDuration(remain)}`
      : done
        ? `已完成 · ${formatDuration(perPointSec.value)}`
        : `预计 ${formatDuration(perPointSec.value)}`
    return { name, active, done, state: active ? stepText.value : done ? '已完成' : '待执行', timeText }
  }),
)

const endModalText = computed(() =>
  massage.value
    ? '将向设备发送停止请求，实际停止结果以设备状态为准。'
    : '将向设备发送停止请求，实际停止结果以设备状态为准。艾条不会随设备停止而熄灭，请人工复核熄火。',
)

function leaveTo(name: 'service-finish' | 'service-abnormal') {
  leaving = true
  stopSpeech()
  router.push({ name })
}

function handleStatus(s: RobotStatus) {
  if (s.state === 'RUNNING' || s.state === 'STOPPING' || s.state === 'ERROR') {
    flow.syncRemain(s.remaining_time_s)
  }
  if (s.state === 'RUNNING' || s.state === 'STOPPING') {
    seenActive = true
    return
  }
  if (s.state === 'ERROR') {
    flow.reportAbnormal(s.error || '设备报告故障，实际停止状态未知')
    leaveTo('service-abnormal')
    return
  }
  if (s.state !== 'IDLE' || !seenActive) return
  if (s.result === 'COMPLETED' && s.noncontact && (s.noncontact.active
    || s.noncontact.phase !== 'COMPLETED' || s.noncontact.returning_to_start === true
    || s.noncontact.returned_to_start !== true || s.noncontact.stop_confirmed !== true)) return
  if (s.result === 'COMPLETED') flow.finishService(false)
  else flow.finishService(true, s.error || (s.result === 'FAILED' ? '设备报告执行失败' : '无异常'))
  leaveTo('service-finish')
}

async function confirmEnd() {
  showEndModal.value = false
  stopping.value = true
  const reply = await robot.stop()
  if (!reply.ok) {
    stopping.value = false
    toast.show(reply.message)
  }
}

function toggleMute() {
  if (settings.muted) {
    settings.setMuted(false)
    speakPreview()
    return
  }
  stopSpeech()
  settings.setMuted(true)
}

function announceCurrentPoint() {
  const name = currentPoint.value
  if (name === '—' || name === announcedPoint) return
  announcedPoint = name
  speakServicePoint(name)
}

watch(status, (s) => {
  if (s && !leaving) handleStatus(s)
}, { immediate: true, flush: 'sync' })

watch(currentPoint, announceCurrentPoint)

onMounted(announceCurrentPoint)

onBeforeUnmount(stopSpeech)
</script>

<template>
  <section class="run-screen">
    <div class="run-head">
      <div>
        <div class="section-label">运行监控</div>
        <h1 class="run-title">{{ returningToStart ? '正在返回本次起始点位' : '服务运行中' }}</h1>
        <p class="run-desc" :class="{ 'is-warn': robot.stale }">
          {{ robot.stale ? '状态已过期，设备实际进展未知' : returningToStart ? '穴位路线已执行，回到起始点位并停稳后完成本次流程' : '实时运行状态与参数监控' }}
        </p>
      </div>
      <div class="run-actions">
        <button class="ghost-btn" type="button" @click="toggleMute">
          {{ muted ? '已静音' : '语音播报' }}
        </button>
      </div>
    </div>

    <div class="run-layout">
      <div class="panel run-main">
        <div class="run-main-body">
          <div class="hero-row">
            <div>
              <p class="muted">当前穴位</p>
              <h2 class="point-name">{{ currentPoint }}</h2>
              <p class="muted route-line">{{ routeText }}</p>
            </div>
            <div class="timer-box">
              <p class="muted">剩余有效时间</p>
              <div class="timer-value">{{ timerText }}</div>
            </div>
          </div>

          <div class="progress-block">
            <div class="progress-row">
              <span>服务进度</span>
              <span>{{ flow.runProgress }}%</span>
            </div>
            <div class="progress-track">
              <div class="progress-bar" :style="{ width: `${Math.max(3, flow.runProgress)}%` }" />
            </div>
          </div>

          <div class="spacer" aria-hidden="true" />

          <div class="run-summary">
            <p class="summary-title">{{ summary.title }}</p>
            <p class="summary-text">{{ summary.text }}</p>
            <div class="summary-tips">
              <span v-for="tip in summary.tips" :key="tip">{{ tip }}</span>
            </div>
          </div>

          <div class="spacer" aria-hidden="true" />

          <div class="run-metrics">
            <div class="inner-card">
              <p class="metric-label">{{ massage ? '实测力度' : '实测温度' }}</p>
              <div class="metric-hero">
                <p class="metric-value" :class="{ 'is-warn': !liveInBand }">
                  {{ liveValue == null ? '—' : `${liveValue.toFixed(1)}${unit}` }}
                </p>
              </div>
              <div class="metric-fields">
                <div class="metric-row">
                  <span class="k">目标</span>
                  <span class="v accent">{{ flow.levelLabel }}</span>
                </div>
                <div class="metric-row">
                  <span class="k">与目标偏差</span>
                  <span class="v" :class="liveInBand ? 'ok' : 'warn'">{{ deltaText }}</span>
                </div>
                <div class="metric-row">
                  <span class="k">安全上限</span>
                  <span class="v">{{ safetyLimit }}{{ unit }}</span>
                </div>
              </div>
            </div>

            <div class="inner-card">
              <p class="metric-label">运行状态</p>
              <div class="metric-hero">
                <p class="metric-value" :class="robot.deviceHealthy ? 'is-ok' : 'is-warn'">
                  {{ stepText }}
                </p>
              </div>
              <div class="metric-fields">
                <div class="metric-row">
                  <span class="k">设备反馈</span>
                  <span class="v" :class="robot.deviceHealthy ? 'ok' : 'warn'">
                    {{ robot.deviceHealthy ? '反馈正常' : '反馈异常' }}
                  </span>
                </div>
                <div v-if="!massage" class="metric-row">
                  <span class="k">实测间隙</span>
                  <span class="v">{{ gapText }}</span>
                </div>
                <div class="metric-row">
                  <span class="k">运行模式</span>
                  <span class="v">{{ status?.mode === 'SIMULATION' ? '模拟' : '实机' }}</span>
                </div>
              </div>
            </div>
          </div>

          <div class="spacer" aria-hidden="true" />
        </div>
      </div>

      <div class="run-side">
        <div class="panel route-panel">
          <h3>路线进度</h3>
          <div class="route-list">
            <div v-for="(item, i) in routeItems" :key="`${item.name}-${i}`" class="route-item">
              <div class="route-marker">
                <span class="route-num" :class="{ 'is-active': item.active, 'is-done': item.done }">
                  {{ String(i + 1).padStart(2, '0') }}
                </span>
                <span class="route-rail" aria-hidden="true" />
              </div>
              <div class="route-info">
                <div class="route-name-row">
                  <span class="route-name">{{ item.name }}</span>
                  <span class="route-state" :class="{ 'is-active': item.active }">
                    {{ item.state }}
                  </span>
                </div>
                <div class="route-time">{{ item.timeText }}</div>
              </div>
            </div>
          </div>
        </div>

        <div class="panel ops-panel">
          <h3>运行操作</h3>
          <div class="ops-actions">
            <button
              class="ops-end"
              type="button"
              :disabled="stopping || !robot.connected"
              @click="showEndModal = true"
            >
              {{ stopping ? '停止请求已发送，等待设备确认' : '结束服务' }}
            </button>
          </div>
        </div>
      </div>
    </div>

    <AppConfirm
      v-if="showEndModal"
      title="确认结束服务？"
      :description="endModalText"
      cancel-text="继续服务"
      confirm-text="发送停止"
      tone="danger"
      @cancel="showEndModal = false"
      @confirm="confirmEnd"
    />
  </section>
</template>

<style scoped lang="scss">
.run-screen {
  max-width: 1280px;
  margin: 0 auto;
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.run-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  margin-bottom: 12px;
  flex-shrink: 0;
  gap: 12px;
}

.run-title {
  margin: 6px 0 0;
  font-size: 22px;
  font-weight: 600;
  color: var(--ink);
}

.run-desc {
  margin: 4px 0 0;
  font-size: 13px;
  color: var(--muted);
}

.run-desc.is-warn {
  color: var(--error);
}

.run-actions {
  display: flex;
  gap: 10px;
}

.ghost-btn {
  padding: 10px 16px;
  border-radius: 12px;
  border: 1px solid var(--line);
  background: #fff;
  font-size: 14px;
  color: var(--ink);
  cursor: pointer;
}

.ghost-btn:hover {
  border-color: rgba(255, 106, 26, 0.4);
  color: var(--brand);
}

.run-layout {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: minmax(0, 1.25fr) minmax(280px, 34%);
  gap: 14px;
  align-items: stretch;
}

.run-main {
  display: flex;
  flex-direction: column;
  min-height: 0;
  height: 100%;
  padding: 16px 18px;
}

.run-main-body {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.hero-row {
  display: flex;
  justify-content: space-between;
  gap: 24px;
  flex-shrink: 0;
}

.muted {
  margin: 0;
  font-size: 14px;
  color: var(--muted);
}

.point-name {
  margin: 4px 0 0;
  font-size: 24px;
  font-weight: 600;
  letter-spacing: -0.02em;
  color: var(--ink);
}

.route-line {
  margin-top: 4px;
}

.timer-box {
  text-align: right;
}

.timer-value {
  margin-top: 4px;
  font-size: 34px;
  font-weight: 600;
  line-height: 1;
  color: var(--brand);
  font-variant-numeric: tabular-nums;
}

.progress-block {
  margin-top: 14px;
  flex-shrink: 0;
}

.progress-row {
  display: flex;
  justify-content: space-between;
  font-size: 14px;
  color: var(--muted);
}

.progress-track {
  margin-top: 8px;
  height: 8px;
  border-radius: 999px;
  background: #fff3e8;
  overflow: hidden;
}

.progress-bar {
  height: 100%;
  background: var(--brand);
  border-radius: 999px;
}

.spacer {
  flex: 1 1 0;
  min-height: 12px;
}

.run-summary {
  flex: 0 0 auto;
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px 16px;
  border-radius: 14px;
  background: #fff8f2;
  border: 1px solid rgba(255, 140, 80, 0.22);
}

.summary-title {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: var(--ink);
}

.summary-text {
  margin: 0;
  font-size: 13px;
  line-height: 1.65;
  color: var(--ink);
}

.summary-tips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.summary-tips span {
  font-size: 12px;
  color: var(--brand);
  background: #fff;
  border: 1px solid rgba(255, 140, 80, 0.25);
  border-radius: 999px;
  padding: 4px 10px;
}

.run-metrics {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  flex: 0 0 auto;
}

.inner-card {
  display: flex;
  flex-direction: column;
  padding: 12px 16px 14px;
  background: #fff9f4;
  border: 1.5px solid rgba(255, 160, 110, 0.28);
  border-radius: 14px;
}

.metric-label {
  margin: 0;
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.04em;
  color: var(--muted);
}

.metric-hero {
  display: flex;
  align-items: center;
  justify-content: center;
  text-align: center;
  padding: 14px 0 16px;
}

.metric-value {
  margin: 0;
  font-size: clamp(36px, 5.5vh, 54px);
  line-height: 1;
  font-weight: 700;
  letter-spacing: -0.03em;
  color: var(--brand);
  font-variant-numeric: tabular-nums;
}

.metric-value.is-ok {
  color: var(--ok);
}

.metric-value.is-warn {
  color: #d97706;
}

.metric-fields {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding-top: 10px;
  border-top: 1px solid rgba(255, 160, 110, 0.22);
}

.metric-row {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 12px;
  font-size: 12.5px;
  line-height: 1.3;
}

.metric-row .k {
  color: var(--muted);
  flex-shrink: 0;
}

.metric-row .v {
  color: var(--ink);
  font-weight: 600;
  text-align: right;
}

.metric-row .v.accent {
  color: var(--brand);
}

.metric-row .v.ok {
  color: var(--ok);
}

.metric-row .v.warn {
  color: #d97706;
}

.run-side {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-height: 0;
  height: 100%;
}

.route-panel,
.ops-panel {
  padding: 16px;
}

.route-panel {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.route-panel h3,
.ops-panel h3 {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: var(--ink);
}

.route-list {
  margin-top: 12px;
  flex: 1;
  min-height: 0;
  overflow: auto;
  display: flex;
  flex-direction: column;
}

.route-item {
  display: flex;
  gap: 10px;
  min-height: 56px;
}

.route-marker {
  width: 28px;
  display: flex;
  flex-direction: column;
  align-items: center;
  flex-shrink: 0;
}

.route-num {
  width: 28px;
  height: 28px;
  border-radius: 999px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: 600;
  background: #fff5ec;
  color: var(--muted);
  flex-shrink: 0;
}

.route-num.is-active {
  background: var(--brand);
  color: #fff;
}

.route-num.is-done {
  background: #ffe8d4;
  color: var(--brand);
}

.route-rail {
  flex: 1;
  width: 2px;
  margin: 4px 0;
  background: rgba(255, 160, 110, 0.35);
}

.route-item:last-child .route-rail {
  display: none;
}

.route-info {
  flex: 1;
  min-width: 0;
  align-self: center;
  padding-bottom: 10px;
}

.route-name-row {
  display: flex;
  justify-content: space-between;
  gap: 8px;
}

.route-name {
  font-size: 14px;
  font-weight: 500;
  color: var(--ink);
}

.route-state {
  font-size: 12px;
  color: var(--muted);
}

.route-state.is-active {
  color: var(--brand);
  font-weight: 500;
}

.route-time {
  margin-top: 2px;
  font-size: 12px;
  color: var(--muted);
}

.ops-panel {
  flex-shrink: 0;
}

.ops-actions {
  margin-top: 10px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.ops-end {
  width: 100%;
  padding: 12px;
  border-radius: 12px;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  border: 1px solid #efc9b6;
  background: #fff;
  color: #bd6332;
}
</style>
