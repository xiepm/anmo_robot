<script setup lang="ts">
/** 统一图标组件，按语义名渲染 Iconify Solar 图标 */
import { Icon } from '@iconify/vue'
import { iconMap, type IconName } from '@/icons'

withDefaults(
  defineProps<{
    name: IconName
    size?: number
    color?: string
  }>(),
  {
    size: 18,
  },
)
</script>

<template>
  <Icon
    :icon="iconMap[name]"
    :width="size"
    :height="size"
    :style="color ? { color } : undefined"
  />
</template>
