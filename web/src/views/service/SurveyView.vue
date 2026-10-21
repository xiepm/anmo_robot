<script setup lang="ts">
/** 服务适配问卷 */
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import AppIcon from '@/components/common/AppIcon.vue'
import { surveyQuestions } from '@/data/survey'
import { useFlowStore } from '@/stores/flow'

const router = useRouter()
const flow = useFlowStore()

const index = ref(0)
const answers = ref<string[]>([...flow.surveyAnswers])

const total = surveyQuestions.length
const question = computed(() => surveyQuestions[index.value])
const currentAnswer = computed(() => answers.value[index.value])

function ensureAnswer() {
  if (!answers.value[index.value]) {
    answers.value[index.value] = question.value.opts[0]
  }
}

ensureAnswer()

function selectOption(opt: string) {
  answers.value[index.value] = opt
}

function goBack() {
  if (index.value === 0) {
    router.push({ name: 'service-start' })
    return
  }
  index.value -= 1
  ensureAnswer()
}

function goNext() {
  if (!answers.value[index.value]) return
  if (index.value < total - 1) {
    index.value += 1
    ensureAnswer()
    return
  }
  flow.completeSurvey([...answers.value])
  router.push({ name: 'service-plan' })
}
</script>

<template>
  <section class="survey-screen">
    <div class="survey-head">
      <div>
        <h1 class="survey-title">服务适配问卷</h1>
        <p class="survey-desc">请根据当下状态选择，结果仅用于本次服务推荐。</p>
      </div>
      <div class="survey-progress">第 {{ index + 1 }} / {{ total }} 题</div>
    </div>

    <div class="glass-strong survey-card">
      <div class="q-label">问题 {{ String(index + 1).padStart(2, '0') }}</div>
      <h2 class="q-title">{{ question.title }}</h2>
      <div class="q-options">
        <label
          v-for="opt in question.opts"
          :key="opt"
          class="survey-option"
        >
          <input
            class="heacon-radio"
            type="radio"
            name="survey-q"
            :value="opt"
            :checked="currentAnswer === opt"
            @change="selectOption(opt)"
          />
          <span>{{ opt }}</span>
        </label>
      </div>
    </div>

    <div class="survey-actions">
      <button class="btn-ghost" type="button" @click="goBack">
        {{ index === 0 ? '返回首页' : '上一题' }}
      </button>
      <button class="btn-primary" type="button" @click="goNext">
        {{ index === total - 1 ? '完成问卷' : '下一题' }}
        <AppIcon :name="index === total - 1 ? 'check' : 'arrowRight'" :size="18" />
      </button>
    </div>
  </section>
</template>

<style scoped lang="scss">
.survey-screen {
  max-width: 1120px;
  margin: 0 auto;
}

.survey-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  margin-bottom: 20px;
}

.survey-title {
  margin: 8px 0 0;
  font-size: 24px;
  font-weight: 600;
  color: var(--ink);
}

.survey-desc {
  margin: 8px 0 0;
  font-size: 14px;
  color: #64748b;
}

.survey-progress {
  font-size: 14px;
  color: #94a3b8;
}

.glass-strong {
  background: #fff;
  border: 1.5px solid rgba(255, 150, 100, 0.38);
  border-radius: 16px;
  box-shadow:
    0 1px 0 rgba(255, 255, 255, 0.95) inset,
    0 10px 26px rgba(255, 120, 50, 0.08),
    0 2px 8px rgba(255, 140, 70, 0.04);
}

.survey-card {
  padding: 24px;
}

.q-label {
  font-size: 12px;
  font-weight: 500;
  color: var(--brand);
}

.q-title {
  margin: 16px 0 0;
  font-size: 24px;
  font-weight: 600;
  color: var(--ink);
}

.q-options {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  margin-top: 20px;
}

.survey-option {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 16px;
  border-radius: 12px;
  background: #fff;
  border: 1px solid var(--line);
  cursor: pointer;
  font-size: 14px;
  transition: border-color 0.15s, background 0.15s, box-shadow 0.15s;
}

.survey-option:hover {
  border-color: rgba(255, 106, 26, 0.4);
}

.survey-option:has(input:checked) {
  border-color: var(--brand);
  background: var(--brand-soft);
  box-shadow: 0 4px 14px rgba(255, 106, 26, 0.08);
}

.heacon-radio {
  appearance: none;
  width: 20px;
  height: 20px;
  margin: 0;
  flex-shrink: 0;
  border: 2px solid #e8d5c6;
  border-radius: 999px;
  background: #fff;
  cursor: pointer;
  display: grid;
  place-content: center;
  transition: border-color 0.15s, box-shadow 0.15s;
}

.heacon-radio::before {
  content: '';
  width: 10px;
  height: 10px;
  border-radius: 999px;
  background: var(--brand);
  transform: scale(0);
  transition: transform 0.12s ease;
}

.heacon-radio:checked {
  border-color: var(--brand);
  box-shadow: 0 0 0 3px rgba(255, 106, 26, 0.12);
}

.heacon-radio:checked::before {
  transform: scale(1);
}

.survey-actions {
  margin-top: 24px;
  display: flex;
  justify-content: space-between;
  align-items: center;
}
</style>
