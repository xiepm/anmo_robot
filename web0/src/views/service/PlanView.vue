<script setup lang="ts">
/** 确认服务方案 */
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import AppIcon from '@/components/common/AppIcon.vue'
import PlanCard from '@/components/service/PlanCard.vue'
import { servicePlans } from '@/data/plans'
import { useFlowStore } from '@/stores/flow'

const router = useRouter()
const flow = useFlowStore()

/** AI 模式下是否展开全部方案 */
const showAll = ref(flow.planMode === 'manual')

const isManual = computed(() => flow.planMode === 'manual')

const subtitle = computed(() =>
  isManual.value
    ? '请从全部预设方案中选择本次服务。'
    : '根据问卷结果，您可以选择推荐方案或查看全部预设。',
)

const backLabel = computed(() => (isManual.value ? '返回首页' : '返回修改问卷'))

const recommendText = computed(() => {
  const name = flow.selectedPlan?.name ?? ''
  return `结合当前问卷，推荐「${name}」；您仍可手动选择其他方案。`
})

function onSelect(id: string) {
  flow.selectPlan(id)
  if (!isManual.value) {
    showAll.value = false
  }
}

function onShowAll() {
  showAll.value = true
}

function goBack() {
  if (isManual.value) {
    router.push({ name: 'service-start' })
    return
  }
  router.push({ name: 'service-survey' })
}

function onConfirm() {
  flow.confirmPlan()
  router.push({ name: 'service-position' })
}
</script>

<template>
  <section class="plan-screen">
    <div class="plan-head">
      <div>
        <h1 class="plan-title">确认服务方案</h1>
        <p class="plan-desc">{{ subtitle }}</p>
      </div>
      <div v-if="!isManual" class="plan-badge">推荐已生成 · 可手动调整</div>
    </div>

    <div v-if="!isManual && !showAll" class="recommend-bar">
      <AppIcon name="sparkles" :size="21" color="var(--brand)" />
      <span>{{ recommendText }}</span>
      <button class="switch-btn" type="button" @click="onShowAll">换方案</button>
    </div>

    <div v-if="!isManual && !showAll && flow.selectedPlan" class="recommend-card">
      <PlanCard
        :plan="flow.selectedPlan"
        selected
        @select="onShowAll"
      />
    </div>

    <div v-else class="plan-grid">
      <PlanCard
        v-for="plan in servicePlans"
        :key="plan.id"
        :plan="plan"
        :selected="plan.id === flow.selectedPlanId"
        @select="onSelect(plan.id)"
      />
    </div>

    <div class="plan-actions">
      <button class="btn-ghost" type="button" @click="goBack">{{ backLabel }}</button>
      <button class="btn-primary" type="button" @click="onConfirm">
        确认并准备就位
        <AppIcon name="arrowRight" :size="18" />
      </button>
    </div>
  </section>
</template>

<style scoped lang="scss">
.plan-screen {
  max-width: 1280px;
  margin: 0 auto;
}

.plan-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  margin-bottom: 20px;
  gap: 16px;
}

.plan-title {
  margin: 8px 0 0;
  font-size: 24px;
  font-weight: 600;
  color: var(--ink);
}

.plan-desc {
  margin: 8px 0 0;
  font-size: 14px;
  color: #64748b;
}

.plan-badge {
  padding: 8px 12px;
  border-radius: 8px;
  background: var(--brand-soft);
  color: var(--brand);
  font-size: 12px;
  white-space: nowrap;
}

.recommend-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 16px 20px;
  margin-bottom: 20px;
  border-radius: 16px;
  background: #fff;
  border: 1.5px solid rgba(255, 150, 100, 0.38);
  box-shadow: 0 8px 22px rgba(255, 120, 50, 0.07);
  font-size: 14px;
  color: var(--ink);
}

.switch-btn {
  margin-left: auto;
  padding: 8px 16px;
  border: none;
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.8);
  color: var(--ink);
  cursor: pointer;
  font-size: 14px;
}

.switch-btn:hover {
  background: var(--brand-soft);
  color: var(--brand);
}

.recommend-card {
  max-width: 520px;
}

.plan-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 20px;
}

.plan-actions {
  margin-top: 24px;
  display: flex;
  justify-content: space-between;
  align-items: center;
}
</style>
