<script setup lang="ts">
/** 方案卡片 */
import AppIcon from '@/components/common/AppIcon.vue'
import type { ServicePlan } from '@/data/plans'

defineProps<{
  plan: ServicePlan
  selected?: boolean
}>()

defineEmits<{
  select: []
}>()
</script>

<template>
  <button
    class="plan-card"
    :class="{ 'is-selected': selected }"
    type="button"
    @click="$emit('select')"
  >
    <div class="plan-card__head">
      <div class="plan-card__icon" :class="plan.type === '艾灸' ? 'is-mox' : 'is-massage'">
        <AppIcon :name="plan.icon" :size="24" />
      </div>
      <span class="plan-card__type">{{ plan.type }}</span>
    </div>
    <div class="plan-card__name">{{ plan.name }}</div>
    <div class="plan-card__meta">{{ plan.type }} · {{ plan.duration }}</div>
    <div class="plan-card__detail">
      <div><span class="k">参数</span>{{ plan.params }}</div>
      <div><span class="k">路线</span>{{ plan.route }}</div>
      <div><span class="k">手法</span>{{ plan.method }}</div>
    </div>
  </button>
</template>

<style scoped lang="scss">
.plan-card {
  width: 100%;
  text-align: left;
  padding: 20px;
  border-radius: 16px;
  background: #fff;
  border: 1.5px solid rgba(255, 150, 100, 0.38);
  box-shadow: 0 8px 22px rgba(255, 120, 50, 0.07);
  cursor: pointer;
  color: inherit;
  font: inherit;
  transition: border-color 0.15s, box-shadow 0.15s;
}

.plan-card:hover {
  border-color: rgba(255, 106, 26, 0.45);
  box-shadow: 0 10px 26px rgba(255, 106, 26, 0.1);
}

.plan-card.is-selected {
  box-shadow:
    0 8px 22px rgba(255, 120, 50, 0.07),
    0 0 0 2px var(--brand);
}

.plan-card__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
}

.plan-card__icon {
  width: 44px;
  height: 44px;
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.plan-card__icon.is-mox {
  background: var(--brand-soft);
  color: var(--brand);
}

.plan-card__icon.is-massage {
  background: var(--soft);
  color: var(--ink);
}

.plan-card__type {
  padding: 4px 10px;
  border-radius: 999px;
  background: var(--soft);
  color: var(--brand);
  font-size: 12px;
}

.plan-card__name {
  margin-top: 16px;
  font-weight: 600;
  color: var(--ink);
}

.plan-card__meta {
  margin-top: 8px;
  font-size: 12px;
  color: #94a3b8;
}

.plan-card__detail {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid var(--line);
  font-size: 12px;
  line-height: 1.75;
  color: #64748b;
}

.plan-card__detail .k {
  display: inline-block;
  min-width: 2.5em;
  margin-right: 6px;
  color: var(--muted);
}
</style>
