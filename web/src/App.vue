<script setup lang="ts">
/** 根节点：设计稿画布等比缩放，并启动下位机通讯链路 */
import AppToast from '@/components/common/AppToast.vue'
import { useDesignScale } from '@/composables/useDesignScale'
import { useRobotStore } from '@/stores/robot'

const { scale, frameWidth, frameHeight, designWidth, designHeight } = useDesignScale()

useRobotStore().start()
</script>

<template>
  <div class="scale-viewport">
    <div
      class="scale-frame"
      :style="{ width: `${frameWidth}px`, height: `${frameHeight}px` }"
    >
      <div
        id="heacon-app-canvas"
        class="scale-canvas"
        :style="{
          width: `${designWidth}px`,
          height: `${designHeight}px`,
          transform: `scale(${scale})`,
        }"
      >
        <router-view />
        <AppToast />
      </div>
    </div>
  </div>
</template>

<style scoped lang="scss">
.scale-viewport {
  width: 100vw;
  height: 100vh;
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #2a221c;
}

.scale-frame {
  position: relative;
  overflow: hidden;
  flex-shrink: 0;
  background: var(--canvas);
}

.scale-canvas {
  position: absolute;
  inset: 0 auto auto 0;
  transform-origin: left top;
  overflow: hidden;
  background: var(--canvas);
}
</style>
