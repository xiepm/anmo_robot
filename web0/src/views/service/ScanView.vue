<script setup lang="ts">
/** 人体定位扫描：发送 SCAN，定位结果以下位机 STATUS 为准 */
import { onMounted, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { useFlowStore } from "@/stores/flow";
import { useRobotStore } from "@/stores/robot";

const router = useRouter();
const flow = useFlowStore();
const robot = useRobotStore();

const statusText = ref("正在发送扫描请求");
const failed = ref(false);
/** 已观测到 SCANNING，避免把扫描前的旧定位误判为本次结果 */
let scanning = false;

function fail(message: string) {
  failed.value = true;
  statusText.value = message;
}

function leave() {
  if (!failed.value) robot.stop();
  router.push({ name: "service-position" });
}

onMounted(async () => {
  const region = flow.selectedPlan?.region;
  if (!region) return fail("未选择服务方案");
  const reply = await robot.request("SCAN", { region });
  if (!reply.ok) return fail(reply.message);
  statusText.value = "正在识别人体轮廓";
});

watch(
  () => robot.status,
  (status) => {
    if (!status || failed.value) return;
    if (status.state === "SCANNING") {
      scanning = true;
      return;
    }
    if (!scanning) return;
    if (status.state === "IDLE" && status.positioning_valid) {
      flow.completeScan();
      router.push({ name: "service-confirm" });
      return;
    }
    fail(status.error || "定位失败，请调整姿势后重新扫描");
  },
);
</script>

<template>
  <section class="scan-screen">
    <div class="scan-body">
      <h1 class="scan-title">正在扫描背部轮廓</h1>
      <p class="scan-desc">请保持放松，扫描期间请勿移动，不会保存影像。</p>

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
