<script setup lang="ts">
/**
 * 路线与参数确认
 * 「确认路线并开始」先发送 SET_PLAN，设备接受后再发送 START
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import AppConfirm from '@/components/common/AppConfirm.vue'
import AppIcon from '@/components/common/AppIcon.vue'
import BodyMap from '@/components/service/BodyMap.vue'
import { levelRange, useFlowStore } from '@/stores/flow'
import { useRobotStore } from '@/stores/robot'
import { useToastStore } from '@/stores/toast'
import { prefetchSpeech, servicePointLine, unlockSpeech } from '@/utils/speech'

const router = useRouter()
const flow = useFlowStore()
const robot = useRobotStore()
const toast = useToastStore()

const showStartModal = ref(false)

const canStart = computed(() => {
  const state = robot.status?.state
  return !robot.pendingCmd && robot.online && (state === 'IDLE' || state === 'READY')
})

const durationRange = computed(() =>
  flow.isMassage ? { min: 15, max: 25 } : { min: 30, max: 40 },
)

const energyRange = computed(() => levelRange(flow.isMassage))
const paramLabel = computed(() => (flow.isMassage ? '目标力度' : '目标温度'))
const energyUnit = computed(() => (flow.isMassage ? ' N' : '℃'))

function fillOf(value: number, min: number, max: number) {
  return `${((value - min) / (max - min)) * 100}%`
}

const durationFill = computed(() => {
  const { min, max } = durationRange.value
  return fillOf(flow.serviceParams.duration, min, max)
})

const levelFill = computed(() => {
  const { min, max } = energyRange.value
  return fillOf(flow.serviceParams.level, min, max)
})

onMounted(() => {
  flow.initServiceParams()
  flow.resetAdjustedPoints()
})

function onDurationInput(e: Event) {
  const value = Number((e.target as HTMLInputElement).value)
  flow.setDuration(value)
}

function onLevelInput(e: Event) {
  const value = Number((e.target as HTMLInputElement).value)
  flow.setLevel(value)
}

function rescan() {
  router.push({ name: 'service-scan' })
}

async function startService() {
  showStartModal.value = false
  unlockSpeech()
  const firstPoint = flow.activePoints[0]?.[0]
  if (firstPoint) prefetchSpeech(servicePointLine(firstPoint))
  const plan = flow.planRequest
  if (!plan) return
  const saved = await robot.request('SET_PLAN', plan)
  if (!saved.ok) {
    toast.show(saved.message)
    return
  }
  const started = await robot.request('START')
  if (!started.ok) {
    toast.show(started.message)
    return
  }
  flow.startService()
  router.push({ name: 'service-run' })
}
</script>

<template>
  <section class="confirm-screen">
    <div class="confirm-head">
      <div>
        <h1 class="confirm-title">确认穴位路线与参数</h1>
        <p class="confirm-desc">俯视俯卧示意 · 拖动仅调整示意位置，设备按定位结果执行</p>
      </div>
      <button class="rescan-btn" type="button" @click="rescan">重新扫描</button>
    </div>

    <div class="confirm-layout">
      <div class="panel confirm-body-card">
        <BodyMap
          :points="flow.activePoints"
          @update:points="flow.setAdjustedPoints"
        />
      </div>

      <aside class="panel confirm-side">
        <div class="confirm-side-body">
          <div class="side-top">
            <h3 class="plan-name">{{ flow.selectedPlan?.name }}</h3>
            <div class="benefit-card">
              <div class="benefit-head icon-text">
                <AppIcon name="leaf" :size="17" color="var(--brand)" />
                <span>功效</span>
              </div>
              <p class="benefit-text">{{ flow.selectedPlan?.benefit }}</p>
              <div class="benefit-tags">
                <span
                  v-for="tag in flow.selectedPlan?.benefitPoints ?? []"
                  :key="tag"
                  class="tag"
                >
                  {{ tag }}
                </span>
              </div>
            </div>
          </div>

          <div class="params">
            <label class="param-field">
              <div class="param-row">
                <span>服务时长</span>
                <span class="param-value">{{ flow.serviceParams.duration }} 分钟</span>
              </div>
              <input
                class="heacon-range"
                type="range"
                step="1"
                :min="durationRange.min"
                :max="durationRange.max"
                :value="flow.serviceParams.duration"
                :style="{ '--range-fill': durationFill }"
                @input="onDurationInput"
              />
              <div class="param-ends">
                <span>{{ durationRange.min }} 分</span>
                <span>{{ durationRange.max }} 分</span>
              </div>
            </label>

            <label class="param-field">
              <div class="param-row">
                <span>{{ paramLabel }}</span>
                <span class="param-value">{{ flow.serviceParams.level }}{{ energyUnit }}</span>
              </div>
              <input
                class="heacon-range"
                type="range"
                step="1"
                :min="energyRange.min"
                :max="energyRange.max"
                :value="flow.serviceParams.level"
                :style="{ '--range-fill': levelFill }"
                @input="onLevelInput"
              />
              <div class="param-ends">
                <span>{{ energyRange.min }}{{ energyUnit }}</span>
                <span>{{ energyRange.max }}{{ energyUnit }}</span>
              </div>
            </label>
          </div>

          <div class="side-actions">
            <button
              class="btn-primary"
              type="button"
              :disabled="!canStart"
              @click="showStartModal = true"
            >
              确认路线并开始
            </button>
          </div>
        </div>
      </aside>
    </div>

    <AppConfirm
      v-if="showStartModal"
      title="确认开始服务？"
      description="路线与参数已确认。点击开始后设备将进入服务流程。"
      cancel-text="返回调整"
      confirm-text="开始"
      tone="brand"
      width="440px"
      @cancel="showStartModal = false"
      @confirm="startService"
    />
  </section>
</template>

<style scoped lang="scss">
.confirm-screen {
  max-width: 1200px;
  margin: 0 auto;
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.confirm-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  margin-bottom: 12px;
  flex-shrink: 0;
  gap: 12px;
}

.confirm-title {
  margin: 0;
  font-size: 20px;
  font-weight: 600;
  color: var(--ink);
}

.confirm-desc {
  margin: 4px 0 0;
  font-size: 14px;
  color: #64748b;
}

.rescan-btn {
  padding: 8px 16px;
  border-radius: 12px;
  border: 1px solid var(--line);
  background: #fff;
  font-size: 14px;
  color: var(--ink);
  cursor: pointer;
}

.rescan-btn:hover {
  border-color: rgba(255, 106, 26, 0.4);
  color: var(--brand);
}

.confirm-layout {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: minmax(0, 1.25fr) 300px;
  gap: 14px;
  align-items: stretch;
}

.confirm-body-card {
  padding: 10px;
  display: flex;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
}

.confirm-side {
  padding: 16px;
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.confirm-side-body {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.side-top {
  flex-shrink: 0;
}

.plan-name {
  margin: 0;
  font-size: 17px;
  font-weight: 600;
  color: var(--ink);
}

.benefit-card {
  margin-top: 12px;
  padding: 16px;
  border-radius: 12px;
  background: #fff8f2;
  border: 1px solid rgba(255, 140, 80, 0.22);
}

.benefit-head {
  color: var(--brand);
  font-size: 14px;
  font-weight: 600;
}

.benefit-text {
  margin: 10px 0 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--ink);
}

.benefit-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 12px;
}

.tag {
  padding: 4px 8px;
  border-radius: 999px;
  background: #fff;
  font-size: 11px;
  color: var(--brand);
  border: 1px solid rgba(255, 140, 80, 0.25);
}

.params {
  margin-top: auto;
  padding-top: 16px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.param-field {
  display: block;
  font-size: 14px;
}

.param-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.param-value {
  color: var(--brand);
  font-weight: 500;
}

.param-ends {
  display: flex;
  justify-content: space-between;
  margin-top: 6px;
  font-size: 12px;
  color: var(--muted);
}

.heacon-range {
  margin-top: 10px;
}

.side-actions {
  margin-top: 16px;
  flex-shrink: 0;
}

.side-actions button {
  width: 100%;
  padding: 12px;
}
</style>
