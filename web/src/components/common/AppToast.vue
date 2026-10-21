<script setup lang="ts">
/** 全局轻提示 */
import { storeToRefs } from 'pinia'
import { useToastStore } from '@/stores/toast'

const toast = useToastStore()
const { message, visible } = storeToRefs(toast)
</script>

<template>
  <Transition name="toast">
    <div v-if="visible" class="toast" role="status" aria-live="polite">
      {{ message }}
    </div>
  </Transition>
</template>

<style scoped lang="scss">
.toast {
  position: absolute;
  left: 50%;
  bottom: 36px;
  z-index: 80;
  transform: translateX(-50%);
  max-width: min(480px, calc(100% - 32px));
  padding: 12px 18px;
  border-radius: 14px;
  background: rgba(61, 56, 52, 0.92);
  color: #fff;
  font-size: 14px;
  line-height: 1.45;
  text-align: center;
  box-shadow: 0 12px 28px rgba(61, 56, 52, 0.22);
  pointer-events: none;
}

.toast-enter-active,
.toast-leave-active {
  transition: opacity 0.2s ease, transform 0.2s ease;
}

.toast-enter-from,
.toast-leave-to {
  opacity: 0;
  transform: translate(-50%, 8px);
}
</style>
