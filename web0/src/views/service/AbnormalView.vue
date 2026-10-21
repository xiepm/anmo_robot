<script setup lang="ts">
/**
 * 设备故障处理
 * 下位机进入 ERROR 后不自动恢复，需现场处理并重新初始化，本页只提供收尾
 */
import { computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import AppIcon from '@/components/common/AppIcon.vue'
import { useFlowStore } from '@/stores/flow'
import { speak, unlockSpeech } from '@/utils/speech'

const router = useRouter()
const flow = useFlowStore()

const timeText = computed(
  () => flow.abnormalTimeText || new Date().toLocaleString('zh-CN', { hour12: false }),
)

onMounted(() => {
  unlockSpeech()
  speak(`检测到异常。${flow.abnormalDetail}。请确认现场状态。`)
})

function endAfterAbnormal() {
  flow.finishService(true, flow.abnormalDetail || '安全异常')
  router.push({ name: 'service-finish' })
}
</script>

<template>
  <section class="abnormal-screen">
    <div class="status-icon">
      <AppIcon name="error" :size="43" color="#d85f4d" />
    </div>
    <h1 class="title">检测到异常</h1>
    <p class="desc">设备已进入故障状态，实际停止情况请以现场确认为准，处理后需重新初始化设备。</p>

    <div class="detail-card">
      <span class="dot" />
      <div>
        <div class="detail-text">{{ flow.abnormalDetail }}</div>
        <div class="detail-time">已语音播报 · {{ timeText }}</div>
      </div>
    </div>

    <div class="actions">
      <button class="danger-btn" type="button" @click="endAfterAbnormal">
        确认并进入收尾
      </button>
    </div>
  </section>
</template>

<style scoped lang="scss">
.abnormal-screen {
  max-width: 760px;
  margin: 0 auto;
  padding-top: 40px;
  text-align: center;
}

.status-icon {
  width: 80px;
  height: 80px;
  margin: 0 auto;
  border-radius: 999px;
  background: #fff0ed;
  display: flex;
  align-items: center;
  justify-content: center;
}

.title {
  margin: 8px 0 0;
  font-size: 24px;
  font-weight: 600;
  color: var(--ink);
}

.desc {
  margin: 12px 0 0;
  font-size: 14px;
  color: #64748b;
}

.detail-card {
  margin-top: 32px;
  padding: 20px;
  text-align: left;
  display: flex;
  align-items: center;
  gap: 16px;
  background: #fff;
  border: 1.5px solid #f0d8d2;
  border-radius: 16px;
  box-shadow: 0 8px 22px rgba(255, 120, 50, 0.07);
}

.dot {
  width: 12px;
  height: 12px;
  border-radius: 999px;
  background: #d85f4d;
  flex-shrink: 0;
}

.detail-text {
  font-weight: 500;
  color: var(--ink);
}

.detail-time {
  margin-top: 4px;
  font-size: 12px;
  color: #94a3b8;
}

.actions {
  margin-top: 20px;
  display: flex;
  justify-content: center;
}

.danger-btn {
  padding: 10px 20px;
  border-radius: 12px;
  border: 1px solid #efc9b6;
  background: #fff;
  color: #bd6332;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
}

.danger-btn:hover {
  background: #fff0ed;
}
</style>
