<script setup lang="ts">
/** 设备管理弹窗，状态全部来自下位机 STATUS */
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AppIcon from '@/components/common/AppIcon.vue'
import AppModal from '@/components/common/AppModal.vue'
import { useFlowStore } from '@/stores/flow'
import { useRobotStore } from '@/stores/robot'
import type { RobotState, RobotStep } from '@/types/robot'

const emit = defineEmits<{
  close: []
}>()

const route = useRoute()
const router = useRouter()
const flow = useFlowStore()
const robot = useRobotStore()

const STATE_TEXT: Record<RobotState, string> = {
  IDLE: '空闲',
  SCANNING: '定位中',
  READY: '方案已确认',
  RUNNING: '服务进行中',
  STOPPING: '停止中',
  ERROR: '故障',
}

const STEP_TEXT: Record<RobotStep, string> = {
  NONE: '无动作',
  APPROACH: '接近中',
  DWELL: '定点停留',
  RETRACT: '抬离中',
  TRANSFER: '过渡中',
}

const rows = computed(() => {
  const status = robot.status
  if (!robot.connected || !status) {
    return [{ label: '通讯', value: robot.connected ? '状态尚未到达' : '下位机未连接' }]
  }
  const energy = status.actual_energy
  const energyText =
    energy.valid && energy.value != null && energy.unit
      ? `${energy.value} ${energy.unit === 'C' ? '℃' : 'N'}`
      : '无有效测量'
  return [
    { label: '运行模式', value: status.mode === 'SIMULATION' ? '模拟' : '实机' },
    { label: '机械臂', value: status.robot_connected ? '已连接' : '未连接' },
    { label: '设备反馈', value: status.robot_feedback_valid ? '有效' : '无效' },
    { label: '主状态', value: STATE_TEXT[status.state] },
    { label: '动作阶段', value: STEP_TEXT[status.step] },
    { label: '实测能量', value: energyText },
    { label: '故障说明', value: status.error || '无' },
  ]
})

function openRecords() {
  const from = typeof route.name === 'string' ? route.name : 'service-start'
  flow.prepareRecordsReturn(from)
  emit('close')
  router.push({ name: 'service-records' })
}
</script>

<template>
  <AppModal title="设备管理" subtitle="以下位机当前状态为准" width="560px" @close="emit('close')">
    <template #close-icon>
      <AppIcon name="close" :size="24" />
    </template>

    <div class="device-list">
      <div class="block">
        <div v-for="row in rows" :key="row.label" class="row">
          <span class="label">{{ row.label }}</span>
          <span class="value">{{ row.value }}</span>
        </div>
      </div>

      <button class="records-entry" type="button" @click="openRecords">
        <div class="records-icon">
          <AppIcon name="document" :size="22" color="var(--brand)" />
        </div>
        <div class="records-text">
          <p class="records-title">查看服务记录</p>
          <p class="records-desc">匿名归档查询 · 独立于当前服务流程</p>
        </div>
        <AppIcon name="arrowRight" :size="18" color="var(--muted)" />
      </button>
    </div>
  </AppModal>
</template>

<style scoped lang="scss">
.device-list {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.block {
  padding: 8px 20px;
  border-radius: 16px;
  border: 1px solid var(--line);
}

.row {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  padding: 14px 0;
  border-bottom: 1px solid var(--line);
  font-size: 14px;
}

.row:last-child {
  border-bottom: none;
}

.label {
  color: #64748b;
}

.value {
  color: var(--ink);
  font-weight: 600;
  text-align: right;
}

.records-entry {
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
  padding: 20px;
  border-radius: 16px;
  border: 1px solid var(--line);
  background: #fff;
  text-align: left;
  cursor: pointer;
}

.records-entry:hover {
  border-color: rgba(255, 106, 26, 0.4);
}

.records-icon {
  width: 40px;
  height: 40px;
  border-radius: 12px;
  background: var(--brand-soft);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.records-text {
  flex: 1;
  min-width: 0;
}

.records-title {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  color: var(--ink);
}

.records-desc {
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--muted);
}
</style>
