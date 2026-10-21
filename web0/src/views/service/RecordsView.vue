<script setup lang="ts">
/** 匿名服务记录列表 */
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useFlowStore } from '@/stores/flow'
import type { ServiceRecord } from '@/types/record'

const router = useRouter()
const flow = useFlowStore()

const filters = ['全部', '艾灸', '按摩', '已完成', '提前结束'] as const
const activeFilter = ref<(typeof filters)[number]>('全部')
const detail = ref<ServiceRecord | null>(null)

const list = computed(() => {
  const f = activeFilter.value
  return flow.records.filter(
    (r) => f === '全部' || r.type === f || r.status === f,
  )
})

function goBack() {
  router.push({ name: flow.resolveRecordsReturn() })
}

function openDetail(record: ServiceRecord) {
  detail.value = record
}

function closeDetail() {
  detail.value = null
}
</script>

<template>
  <section class="records-screen">
    <div class="records-head">
      <div>
        <h1 class="title">匿名服务记录</h1>
        <p class="desc">不属于当前服务流程 · 仅匿名归档信息</p>
      </div>
      <button class="back-btn" type="button" @click="goBack">返回</button>
    </div>

    <div class="filters">
      <button
        v-for="item in filters"
        :key="item"
        class="filter-btn"
        :class="{ 'is-active': item === activeFilter }"
        type="button"
        @click="activeFilter = item"
      >
        {{ item }}
      </button>
    </div>

    <div class="panel table-wrap">
      <div class="table-head">
        <span>匿名服务单 ID</span>
        <span>时间 / 方案</span>
        <span>类型</span>
        <span>实际时长</span>
        <span>参数档</span>
        <span>状态</span>
        <span>感受</span>
      </div>
      <button
        v-for="item in list"
        :key="item.id"
        class="table-row"
        type="button"
        @click="openDetail(item)"
      >
        <span>{{ item.id }}</span>
        <span>{{ item.time }} / {{ item.plan }}</span>
        <span>{{ item.type }}</span>
        <span>{{ item.duration }}</span>
        <span>{{ item.param }}</span>
        <span>{{ item.status }}</span>
        <span>{{ item.feeling }}</span>
      </button>
      <div v-if="list.length === 0" class="empty">暂无服务记录</div>
    </div>

    <div v-if="detail" class="panel detail-card">
      <div class="detail-head">
        <h2>服务单详情 · {{ detail.id }}</h2>
        <button class="close-btn" type="button" @click="closeDetail">关闭</button>
      </div>
      <div class="detail-grid">
        <p>时间：{{ detail.time }}</p>
        <p>方案：{{ detail.plan }}</p>
        <p>实际时长：{{ detail.duration }}</p>
        <p>参数档：{{ detail.param }}</p>
        <p>完成状态：{{ detail.status }}</p>
        <p>异常摘要：{{ detail.abnormal || '无' }}</p>
        <p>顾客感受：{{ detail.feeling }}</p>
        <p>备注：{{ detail.note || '—' }}</p>
      </div>
    </div>
  </section>
</template>

<style scoped lang="scss">
.records-screen {
  max-width: 1400px;
  margin: 0 auto;
}

.records-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 24px;
  gap: 12px;
}

.title {
  margin: 0;
  font-size: 24px;
  font-weight: 600;
  color: var(--ink);
}

.desc {
  margin: 8px 0 0;
  font-size: 14px;
  color: #64748b;
}

.back-btn {
  padding: 12px 20px;
  border-radius: 12px;
  border: 1px solid var(--line);
  background: #fff;
  cursor: pointer;
}

.filters {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-bottom: 20px;
}

.filter-btn {
  padding: 8px 16px;
  border-radius: 12px;
  border: 1px solid var(--line);
  background: #fff;
  color: var(--ink);
  cursor: pointer;
  font-size: 14px;
}

.filter-btn.is-active {
  border: none;
  color: #fff;
  background: linear-gradient(
    135deg,
    var(--brand-2) 0%,
    var(--brand) 60%,
    var(--brand-deep) 100%
  );
  box-shadow: 0 6px 16px rgba(255, 106, 26, 0.25);
}

.table-wrap {
  overflow: hidden;
  padding: 0;
}

.table-head,
.table-row {
  display: grid;
  grid-template-columns: 1.2fr 1.5fr 0.7fr 0.8fr 1fr 0.8fr 0.7fr;
  gap: 12px;
  padding: 16px 20px;
  text-align: left;
}

.table-head {
  background: var(--soft);
  font-size: 12px;
  color: #64748b;
}

.table-row {
  width: 100%;
  border: none;
  border-top: 1px solid rgba(255, 160, 110, 0.2);
  background: #fff;
  font-size: 14px;
  color: var(--ink);
  cursor: pointer;
}

.table-row:hover {
  background: var(--soft);
}

.empty {
  padding: 32px;
  text-align: center;
  color: var(--muted);
  font-size: 14px;
}

.detail-card {
  margin-top: 20px;
  padding: 24px;
}

.detail-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.detail-head h2 {
  margin: 0;
  font-size: 18px;
}

.close-btn {
  border: none;
  background: transparent;
  color: #64748b;
  cursor: pointer;
}

.detail-grid {
  margin-top: 20px;
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 16px;
  font-size: 14px;
  color: var(--ink);
}
</style>
