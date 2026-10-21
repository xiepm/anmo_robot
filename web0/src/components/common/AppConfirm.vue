<script setup lang="ts">
/** 系统风格确认弹窗（替代浏览器 confirm） */
withDefaults(
  defineProps<{
    title: string
    description?: string
    cancelText?: string
    confirmText?: string
    /** brand 主操作 / danger 危险结束 / soft 次强调确认 */
    tone?: 'brand' | 'danger' | 'soft'
    width?: string
    closeOnMask?: boolean
  }>(),
  {
    description: '',
    cancelText: '取消',
    confirmText: '确认',
    tone: 'brand',
    width: '480px',
    closeOnMask: true,
  },
)

const emit = defineEmits<{
  cancel: []
  confirm: []
}>()

function onMask() {
  emit('cancel')
}
</script>

<template>
  <Teleport to="#heacon-app-canvas">
    <div
      class="confirm-mask"
      @click.self="closeOnMask ? onMask() : undefined"
    >
      <div
        class="confirm-card"
        :style="{ width }"
        role="dialog"
        aria-modal="true"
        :aria-label="title"
      >
        <h2 class="confirm-title">{{ title }}</h2>
        <p v-if="description" class="confirm-desc">{{ description }}</p>
        <div v-if="$slots.default" class="confirm-body">
          <slot />
        </div>
        <div class="confirm-actions">
          <button class="btn-cancel" type="button" @click="emit('cancel')">
            {{ cancelText }}
          </button>
          <button
            class="btn-confirm"
            :class="`is-${tone}`"
            type="button"
            @click="emit('confirm')"
          >
            {{ confirmText }}
          </button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped lang="scss">
.confirm-mask {
  position: absolute;
  inset: 0;
  z-index: 60;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(61, 56, 52, 0.22);
  backdrop-filter: blur(2px);
}

.confirm-card {
  max-width: calc(100vw - 32px);
  padding: 24px;
  border-radius: 16px;
  background: #fff;
  box-shadow: 0 20px 50px rgba(61, 56, 52, 0.18);
  border: 1px solid rgba(255, 160, 110, 0.12);
}

.confirm-title {
  margin: 0;
  font-size: 20px;
  font-weight: 600;
  color: var(--ink);
  letter-spacing: -0.01em;
}

.confirm-desc {
  margin: 10px 0 0;
  font-size: 14px;
  line-height: 1.65;
  color: #64748b;
}

.confirm-body {
  margin-top: 16px;
}

.confirm-actions {
  display: flex;
  gap: 12px;
  margin-top: 22px;
}

.btn-cancel,
.btn-confirm {
  flex: 1;
  padding: 12px 20px;
  border-radius: 12px;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
}

.btn-cancel {
  border: 1px solid var(--line);
  background: #fff;
  color: #64748b;
}

.btn-cancel:hover {
  border-color: rgba(255, 106, 26, 0.35);
  color: var(--brand);
}

.btn-confirm {
  border: none;
  color: #fff;
}

.btn-confirm.is-brand {
  background: var(--brand);
}

.btn-confirm.is-brand:hover {
  filter: brightness(0.96);
}

.btn-confirm.is-danger {
  background: #d85f4d;
}

.btn-confirm.is-danger:hover {
  filter: brightness(0.96);
}

.btn-confirm.is-soft {
  background: #fff5ed;
  color: #bd6332;
}

.btn-confirm.is-soft:hover {
  filter: brightness(0.98);
}
</style>
