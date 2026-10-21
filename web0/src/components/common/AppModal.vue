<script setup lang="ts">
/** 通用弹层容器 */
defineProps<{
  title: string
  subtitle?: string
  width?: string
}>()

defineEmits<{
  close: []
}>()
</script>

<template>
  <div class="modal-mask" @click.self="$emit('close')">
    <div class="modal-card" :style="{ width: width || '520px' }" role="dialog" aria-modal="true">
      <div class="modal-head">
        <div>
          <h2 class="modal-title">{{ title }}</h2>
          <p v-if="subtitle" class="modal-subtitle">{{ subtitle }}</p>
        </div>
        <button class="close-btn" type="button" title="关闭" @click="$emit('close')">
          <slot name="close-icon" />
        </button>
      </div>
      <div class="modal-body">
        <slot />
      </div>
    </div>
  </div>
</template>

<style scoped lang="scss">
.modal-mask {
  position: absolute;
  inset: 0;
  z-index: 40;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(61, 56, 52, 0.2);
}

.modal-card {
  max-width: calc(100vw - 32px);
  max-height: 85vh;
  overflow-y: auto;
  padding: 24px;
  border-radius: 16px;
  background: #fff;
  box-shadow: 0 20px 50px rgba(61, 56, 52, 0.18);
  scrollbar-width: none;
}

.modal-card::-webkit-scrollbar {
  display: none;
}

.modal-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
}

.modal-title {
  margin: 0;
  font-size: 24px;
  font-weight: 600;
  color: var(--ink);
}

.modal-subtitle {
  margin: 4px 0 0;
  font-size: 14px;
  color: #64748b;
}

.close-btn {
  border: none;
  background: transparent;
  color: #94a3b8;
  cursor: pointer;
  padding: 0;
  display: flex;
}

.close-btn:hover {
  color: var(--brand);
}

.modal-body {
  margin-top: 24px;
}
</style>
