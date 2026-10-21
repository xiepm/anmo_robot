<script setup lang="ts">
/** 服务暂停 */
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import AppConfirm from '@/components/common/AppConfirm.vue'
import AppIcon from '@/components/common/AppIcon.vue'
import { useFlowStore } from '@/stores/flow'
import { unlockSpeech } from '@/utils/speech'

const router = useRouter()
const flow = useFlowStore()

const showEndModal = ref(false)

const progressText = computed(() => `${flow.runProgress}% · 已暂停`)

const endModalText = computed(() =>
  flow.isMassage
    ? '机器人将安全停止并退回安全位。'
    : '机器人将安全停止并退回安全位。艾灸方案还会进行熄火复核。',
)

function resumeService() {
  unlockSpeech()
  router.push({ name: 'service-run' })
}

function openEndConfirm() {
  showEndModal.value = true
}

function closeEndConfirm() {
  showEndModal.value = false
}

function confirmEnd() {
  showEndModal.value = false
  flow.finishService(true)
  router.push({ name: 'service-finish' })
}

function reportAbnormal() {
  flow.reportAbnormal(
    '暂停期间检测到安全窗触发，请确认现场状态后再继续。',
    'service-pause',
  )
  router.push({ name: 'service-abnormal' })
}
</script>

<template>
  <section class="pause-screen">
    <div class="status-icon">
      <AppIcon name="pause" :size="43" color="#db853e" />
    </div>
    <h1 class="pause-title">服务已暂停</h1>
    <p class="pause-desc">设备已退至安全距离，确认状态后可继续。</p>

    <div class="surface-card">
      <div class="progress-row">
        <span>当前进度</span>
        <span class="strong">{{ progressText }}</span>
      </div>
      <div class="progress-track">
        <div class="progress-bar" :style="{ width: `${flow.runProgress}%` }" />
      </div>
    </div>

    <div class="pause-actions">
      <button class="btn-primary" type="button" @click="resumeService">继续服务</button>
      <button class="abnormal-btn" type="button" @click="reportAbnormal">上报安全异常</button>
      <button class="end-btn" type="button" @click="openEndConfirm">结束服务</button>
    </div>

    <AppConfirm
      v-if="showEndModal"
      title="确认结束服务？"
      :description="endModalText"
      cancel-text="继续服务"
      confirm-text="安全停止并结束"
      tone="danger"
      @cancel="closeEndConfirm"
      @confirm="confirmEnd"
    />
  </section>
</template>

<style scoped lang="scss">
.pause-screen {
  max-width: 760px;
  margin: 0 auto;
  padding-top: 48px;
  text-align: center;
}

.status-icon {
  width: 80px;
  height: 80px;
  margin: 0 auto;
  border-radius: 999px;
  background: #fff2df;
  display: flex;
  align-items: center;
  justify-content: center;
}

.pause-title {
  margin: 8px 0 0;
  font-size: 24px;
  font-weight: 600;
  color: var(--ink);
}

.pause-desc {
  margin: 12px 0 0;
  font-size: 14px;
  color: #64748b;
}

.surface-card {
  margin-top: 36px;
  padding: 24px;
  text-align: left;
  background: #fff;
  border: 1.5px solid rgba(255, 150, 100, 0.38);
  border-radius: 16px;
  box-shadow: 0 8px 22px rgba(255, 120, 50, 0.07);
}

.progress-row {
  display: flex;
  justify-content: space-between;
  font-size: 14px;
  color: #64748b;
}

.strong {
  font-weight: 500;
  color: var(--ink);
}

.progress-track {
  margin-top: 16px;
  height: 8px;
  border-radius: 999px;
  background: #ebeceb;
  overflow: hidden;
}

.progress-bar {
  height: 100%;
  border-radius: 999px;
  background: var(--brand);
}

.pause-actions {
  margin-top: 20px;
  display: grid;
  grid-template-columns: 1fr 1fr 1fr;
  gap: 12px;
}

.abnormal-btn,
.end-btn {
  padding: 10px 20px;
  border-radius: 12px;
  border: 1px solid var(--line);
  background: #fff;
  color: #64748b;
  font-size: 14px;
  cursor: pointer;
}

.abnormal-btn {
  border-color: #f0d0c6;
  background: #fff7f4;
  color: #c45b45;
}

.end-btn:hover {
  color: var(--error);
  border-color: rgba(216, 95, 77, 0.4);
}
</style>
