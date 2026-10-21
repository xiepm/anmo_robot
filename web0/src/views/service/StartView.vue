<script setup lang="ts">
/** 服务首页：方案选择双入口 */
import { useRouter } from 'vue-router'
import AppIcon from '@/components/common/AppIcon.vue'
import { useFlowStore } from '@/stores/flow'

const router = useRouter()
const flow = useFlowStore()

const aiSteps = [
  '确认艾灸与按摩禁忌',
  '了解部位、冷热与疲劳状态',
  '生成并确认本次服务方案',
]

const manualSteps = [
  '查看全部预设方案',
  '选择艾灸或按摩方案',
  '确认后进入人体定位',
]

function onAiPlan() {
  flow.startAiPlan()
  router.push({ name: 'service-survey' })
}

function onManualPlan() {
  flow.startManualPlan()
  router.push({ name: 'service-plan' })
}
</script>

<template>
  <section class="start-screen">
    <div class="entry-grid">
      <article class="panel entry-card">
        <div class="entry-top">
          <div class="entry-icon">
            <AppIcon name="aiPlan" :size="26" color="var(--brand)" />
          </div>
          <span class="privacy-badge">
            <AppIcon name="shield" :size="15" color="var(--muted)" />
            不记录身份信息
          </span>
        </div>
        <h1 class="page-title entry-title">AI 生成方案</h1>
        <p class="page-desc">
          协助顾客完成 6 道单选题，包含禁忌筛查。完成后系统生成建议方案，也可再手动调整。
        </p>
        <ul class="step-list">
          <li v-for="(text, i) in aiSteps" :key="text">
            <span class="step-index">{{ i + 1 }}</span>
            <span>{{ text }}</span>
          </li>
        </ul>
        <div class="entry-action">
          <button class="btn-primary" type="button" @click="onAiPlan">
            开始问卷
            <AppIcon name="arrowRight" :size="18" />
          </button>
        </div>
      </article>

      <article class="panel entry-card">
        <div class="entry-top">
          <div class="entry-icon">
            <AppIcon name="manualPlan" :size="26" color="var(--brand)" />
          </div>
          <span class="privacy-badge">
            <AppIcon name="shield" :size="15" color="var(--muted)" />
            不记录身份信息
          </span>
        </div>
        <h1 class="page-title entry-title">人工选方案</h1>
        <p class="page-desc">
          跳过问卷，直接从全部预设方案中选择。适合需求明确或需要快速开单的场景。
        </p>
        <ul class="step-list">
          <li v-for="(text, i) in manualSteps" :key="text">
            <span class="step-index">{{ i + 1 }}</span>
            <span>{{ text }}</span>
          </li>
        </ul>
        <div class="entry-action">
          <button class="btn-secondary" type="button" @click="onManualPlan">
            查看全部方案
            <AppIcon name="arrowRight" :size="18" />
          </button>
        </div>
      </article>
    </div>
  </section>
</template>

<style scoped lang="scss">
.start-screen {
  max-width: 1100px;
  margin: 0 auto;
  height: 100%;
  display: flex;
  align-items: center;
}

.entry-grid {
  width: 100%;
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 20px;
}

.entry-card {
  min-height: 400px;
  padding: 24px;
  display: flex;
  flex-direction: column;
  text-align: left;
}

.entry-top {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
}

.entry-icon {
  width: 40px;
  height: 40px;
  border-radius: 12px;
  background: var(--brand-soft);
  color: var(--brand);
  display: flex;
  align-items: center;
  justify-content: center;
}

.privacy-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--muted);
  flex-shrink: 0;
}

.entry-title {
  margin-top: 20px;
}

.step-list {
  margin: 20px 0 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 12px;
  font-size: 14px;
  color: var(--ink);
  flex: 1;
}

.step-list li {
  display: flex;
  align-items: flex-start;
  gap: 12px;
}

.step-index {
  width: 24px;
  height: 24px;
  border-radius: 999px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: 600;
  background: var(--brand-soft);
  color: var(--brand);
}

.entry-action {
  margin-top: 24px;
}
</style>
