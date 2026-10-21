<script setup lang="ts">
/** 人体定位与就位检查 */
import { useRouter } from 'vue-router'
import AppIcon from '@/components/common/AppIcon.vue'
import type { IconName } from '@/icons'
import { useFlowStore } from '@/stores/flow'
import LiveAcupointPreview from '@/components/service/LiveAcupointPreview.vue'

const router = useRouter()
const flow = useFlowStore()

interface CheckItem {
  title: string
  desc: string
  icon: IconName
  tone: 'ok' | 'warn'
}

const checks: CheckItem[] = [
  {
    title: '床面已就绪',
    desc: '服务床锁定，承重检测正常',
    icon: 'success',
    tone: 'ok',
  },
  {
    title: '环境光线合适',
    desc: '可进行背部轮廓扫描',
    icon: 'success',
    tone: 'ok',
  },
  {
    title: '请确认头部朝向前方',
    desc: '下巴自然放松，不要抬头',
    icon: 'warning',
    tone: 'warn',
  },
]

function goBack() {
  router.push({ name: 'service-plan' })
}

function startScan() {
  router.push({ name: 'service-scan' })
}
</script>

<template>
  <section class="position-screen">
    <div class="position-head">
      <div>
        <h1 class="position-title">请按提示完成就位</h1>
        <p class="position-desc">请俯卧于服务床中央，肩颈自然放松，盖好巾毯后点击扫描。</p>
      </div>
      <span class="plan-chip">
        当前：{{ flow.selectedPlan?.name ?? '—' }}
      </span>
    </div>

    <div class="position-layout">
      <div class="body-stage panel">
        <LiveAcupointPreview />
      </div>

      <aside class="position-side panel">
        <div class="side-body">
          <h3 class="side-label">就位检查</h3>
          <div class="check-list">
            <div
              v-for="item in checks"
              :key="item.title"
              class="check-row"
              :class="item.tone === 'warn' ? 'is-warn' : 'is-ok'"
            >
              <AppIcon
                :name="item.icon"
                :size="24"
                :color="item.tone === 'warn' ? 'var(--warn)' : 'var(--ok)'"
              />
              <div>
                <div class="check-title">{{ item.title }}</div>
                <div class="check-desc">{{ item.desc }}</div>
              </div>
            </div>
          </div>

          <div class="pose-tip">
            <div class="tip-title">就位提示</div>
            <p>
              双手自然放松搭在床沿，肩颈放松，面部置于面托中；保持俯卧居中后再开始扫描。
            </p>
          </div>
        </div>

        <div class="side-actions">
          <button class="btn-primary scan-btn" type="button" @click="startScan">
            <AppIcon name="scan" :size="19" />
            开始扫描
          </button>
          <button class="btn-ghost back-btn" type="button" @click="goBack">上一步</button>
        </div>
      </aside>
    </div>
  </section>
</template>

<style scoped lang="scss">
.position-screen {
  max-width: 1200px;
  margin: 0 auto;
  height: 100%;
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.position-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  margin-bottom: 12px;
  flex-shrink: 0;
  gap: 12px;
}

.position-title {
  margin: 0;
  font-size: 20px;
  font-weight: 600;
  color: var(--ink);
}

.position-desc {
  margin: 4px 0 0;
  font-size: 14px;
  color: #64748b;
}

.plan-chip {
  padding: 8px 12px;
  border-radius: 8px;
  background: var(--brand-soft);
  color: var(--brand);
  font-size: 12px;
  white-space: nowrap;
}

.position-layout {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: minmax(0, 1.15fr) minmax(320px, 38%);
  gap: 14px;
  align-items: stretch;
}

.body-stage {
  position: relative;
  min-height: 0;
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  background: #fff9f4;
  padding: 0;
  overflow: hidden;
}

.position-side {
  padding: 12px 10px;
  display: flex;
  flex-direction: column;
  min-height: 0;
  height: 100%;
}

.side-body {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-height: 0;
  overflow: hidden;
}

.side-label {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: var(--ink);
  flex-shrink: 0;
}

.check-list {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-height: 0;
}

.check-row {
  flex: 1 1 0;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
  border-radius: 12px;
  min-height: 0;
}

.check-row.is-ok {
  background: #fff8f2;
}

.check-row.is-warn {
  background: #fff8ef;
}

.check-title {
  font-size: 15px;
  font-weight: 500;
  color: var(--ink);
}

.check-desc {
  margin-top: 4px;
  font-size: 12px;
  color: #94a3b8;
}

.pose-tip {
  flex: 1 1 0;
  display: flex;
  flex-direction: column;
  justify-content: center;
  padding: 14px 16px;
  border-radius: 12px;
  background: #fff8f2;
  border: 1px solid rgba(255, 140, 80, 0.22);
  min-height: 0;
}

.tip-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--brand);
}

.pose-tip p {
  margin: 6px 0 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--ink);
}

.side-actions {
  flex-shrink: 0;
  margin-top: 10px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.back-btn,
.scan-btn {
  width: 100%;
}
</style>
