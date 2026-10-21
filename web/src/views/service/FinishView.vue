<script setup lang="ts">
/** 服务收尾与归档 */
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useFlowStore } from '@/stores/flow'
import { useRobotStore } from '@/stores/robot'
import { useToastStore } from '@/stores/toast'

const router = useRouter()
const flow = useFlowStore()
const robot = useRobotStore()
const toast = useToastStore()

const feeling = ref('舒适')
const note = ref('')

const summary = computed(() => flow.finishSummary)
const canContinue = computed(() => {
  const s = robot.status, n = s?.noncontact
  return Boolean(summary.value && !summary.value.endedEarly && robot.deviceHealthy
    && !robot.pendingCmd && !robot.pendingStop && s?.state === 'IDLE'
    && (!n || (!n.active && n.phase === 'COMPLETED' && n.returning_to_start !== true
      && n.returned_to_start === true && n.stop_confirmed === true)))
})

function archive() {
  flow.archiveService(feeling.value, note.value.trim())
  toast.show('已写入服务记录')
  router.push({ name: 'service-start' })
}

function archiveAndContinue() {
  if (!canContinue.value) return
  flow.archiveService(feeling.value, note.value.trim(), true)
  toast.show('已写入服务记录，请重新扫描')
  router.push({ name: 'service-position' })
}

function viewRecords() {
  flow.prepareRecordsReturn('service-finish')
  router.push({ name: 'service-records' })
}
</script>

<template>
  <section class="finish-screen">
    <div class="section-label">服务收尾</div>
    <h1 class="page-title finish-title">服务收尾与归档</h1>
    <p class="page-desc">
      服务已结束。下方感受与备注请交由顾客本人填写，请勿由技师代填。
    </p>

    <div class="summary-grid">
      <div class="panel summary-card">
        <span class="summary-label">方案</span>
        <p class="summary-value">{{ summary?.planName ?? '—' }}</p>
      </div>
      <div class="panel summary-card">
        <span class="summary-label">实际时长</span>
        <p class="summary-value">{{ summary?.durationText ?? '—' }}</p>
      </div>
      <div class="panel summary-card">
        <span class="summary-label">完成状态</span>
        <p class="summary-value">{{ summary?.statusText ?? '—' }}</p>
      </div>
    </div>

    <div class="panel form-card">
      <div class="customer-tip">
        <p class="tip-title">顾客自填区</p>
        <p class="tip-desc">请顾客亲自选择感受并填写备注；按摩 / 艾灸技师请勿代填。</p>
      </div>

      <div class="field">
        <p class="field-label">您本次的感受</p>
        <select v-model="feeling" class="field-control">
          <option>舒适</option>
          <option>一般</option>
          <option>不适</option>
        </select>
      </div>

      <label class="field">
        <span class="field-label">备注（选填，请勿输入身份信息）</span>
        <textarea
          v-model="note"
          class="field-control textarea"
          maxlength="200"
          rows="2"
          placeholder="可由顾客补充本次体验感受"
        />
      </label>
    </div>

    <div class="finish-actions">
      <button v-if="summary && !summary.endedEarly" class="btn-primary" type="button" :disabled="!canContinue" @click="archiveAndContinue">归档并继续使用</button>
      <button class="btn-secondary" type="button" @click="archive">完成并归档</button>
      <button class="records-btn" type="button" @click="viewRecords">查看服务记录</button>
    </div>
  </section>
</template>

<style scoped lang="scss">
.finish-screen {
  max-width: 920px;
  margin: 0 auto;
}

.finish-title {
  margin-top: 8px;
}

.summary-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 16px;
  margin-top: 24px;
}

.summary-card {
  padding: 20px;
  font-size: 14px;
  color: var(--muted);
}

.summary-value {
  margin: 8px 0 0;
  font-size: 16px;
  font-weight: 600;
  color: var(--ink);
}

.form-card {
  margin-top: 20px;
  padding: 24px;
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.customer-tip {
  padding: 12px 16px;
  border-radius: 12px;
  background: var(--brand-soft);
  border: 1px solid rgba(255, 140, 80, 0.2);
}

.tip-title {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  color: var(--brand);
}

.tip-desc {
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--muted);
}

.field {
  display: block;
}

.field-label {
  display: block;
  margin-bottom: 8px;
  font-size: 14px;
  font-weight: 500;
  color: var(--ink);
}

.field-control {
  width: 100%;
  padding: 12px;
  border-radius: 12px;
  border: 1px solid var(--line);
  background: #fff;
  color: var(--ink);
  font: inherit;
  box-sizing: border-box;
}

.textarea {
  margin-top: 8px;
  resize: vertical;
}

.finish-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-top: 24px;
}

.records-btn {
  padding: 14px 24px;
  border-radius: 12px;
  border: 1px solid var(--line);
  background: #fff;
  color: var(--ink);
  font-size: 14px;
  cursor: pointer;
}

.records-btn:hover {
  border-color: rgba(255, 106, 26, 0.4);
  color: var(--brand);
}
</style>
