<script setup lang="ts">
/** SCAN starts a new acquisition; only its terminal STATUS unlocks confirmation. */
import { onMounted, onBeforeUnmount, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useFlowStore } from '@/stores/flow'
import { useRobotStore } from '@/stores/robot'
import { ScanTracker } from '@/utils/controlChecks'

const router = useRouter()
const flow = useFlowStore()
const robot = useRobotStore()
const statusText = ref('正在发送扫描请求')
const failed = ref(false)
let tracker: ScanTracker | null = null
let disposed = false
let leaving = false
let timeout: ReturnType<typeof setTimeout> | undefined

function fail(message: string) {
  if (disposed || leaving) return
  failed.value = true
  statusText.value = message
  tracker?.cancel()
  clearTimeout(timeout)
}
function handle(outcome: {done: boolean; error: string}) {
  if (disposed || leaving || failed.value) return
  if (outcome.error) { fail(outcome.error); return }
  if (outcome.done) {
    clearTimeout(timeout)
    flow.completeScan(robot.status?.scan_snapshot)
    leaving = true
    router.push({ name: 'service-confirm' })
  }
}
async function leave() {
  if (leaving) return
  leaving = true
  tracker?.cancel()
  clearTimeout(timeout)
  if (!failed.value || robot.status?.state === 'SCANNING') {
    const reply = await robot.stop()
    if (!reply.ok) {
      leaving = false
      fail(reply.message)
      return
    }
  }
  router.push({ name: 'service-position' })
}

onMounted(async () => {
  flow.beginScan()
  const region = flow.selectedPlan?.region
  if (!region) return fail('未选择服务方案')
  if (!robot.deviceHealthy) return fail('控制连接或机械臂反馈无效')
  tracker = new ScanTracker(region)
  timeout = setTimeout(() => {
    fail('扫描结果等待超时，已请求取消，请核对控制状态')
    robot.stop()
  }, 35000)
  const reply = await robot.request('SCAN', { region })
  if (disposed || leaving || failed.value) return
  handle(tracker.acknowledge(reply))
  if (!failed.value && !leaving) statusText.value = '正在识别人体轮廓'
})

watch(() => robot.status, status => {
  if (status && tracker) handle(tracker.observe(status))
}, { flush: 'sync' })
watch(() => robot.online, online => {
  if (!online) fail('控制连接断开或状态已过期，扫描结果未确认')
}, { flush: 'sync' })
onBeforeUnmount(() => {
  disposed = true
  tracker?.cancel()
  clearTimeout(timeout)
})
</script>

<template>
  <section class="scan-screen">
    <div class="scan-body">
      <h1 class="scan-title">正在拍照并识别穴位</h1>
      <p class="scan-desc">请保持姿势，正在采集本次图像并识别穴位，图像仅在本次流程中使用。</p>

      <div class="scan-track">
        <div class="scan-bar" :class="{ 'is-failed': failed }" />
      </div>
      <div class="scan-meta" :class="{ 'is-failed': failed }">
        <span>{{ statusText }}</span>
      </div>

      <button class="cancel-btn" type="button" @click="leave">
        {{ failed ? "返回就位" : "取消扫描" }}
      </button>
    </div>
  </section>
</template>

<style scoped lang="scss">
.scan-screen {
  max-width: 720px;
  margin: 0 auto;
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  padding: 32px 0;
}

.scan-body {
  width: 100%;
}

.scan-title {
  margin: 0;
  font-size: 24px;
  font-weight: 600;
  color: var(--ink);
}

.scan-desc {
  margin: 12px 0 0;
  font-size: 14px;
  color: #64748b;
}

.scan-track {
  margin-top: 40px;
  height: 12px;
  border-radius: 999px;
  background: #fff3e8;
  overflow: hidden;
}

.scan-bar {
  width: 35%;
  height: 100%;
  border-radius: 999px;
  background: var(--brand);
  animation: scan-slide 1.6s ease-in-out infinite;
}

.scan-bar.is-failed {
  width: 100%;
  background: var(--error);
  animation: none;
}

@keyframes scan-slide {
  from {
    transform: translateX(-100%);
  }
  to {
    transform: translateX(290%);
  }
}

.scan-meta {
  margin-top: 12px;
  font-size: 12px;
  color: #94a3b8;
}

.scan-meta.is-failed {
  color: var(--error);
}

.cancel-btn {
  margin-top: 48px;
  padding: 12px 28px;
  border-radius: 12px;
  border: 1px solid var(--line);
  background: #fff;
  color: #64748b;
  font-size: 14px;
  cursor: pointer;
}

.cancel-btn:hover {
  border-color: rgba(255, 106, 26, 0.4);
  color: var(--brand);
}
</style>
