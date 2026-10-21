/**
 * 设计稿等比缩放
 * 视口小于设计尺寸时整体缩小，大于时等比放大铺满
 */
import { onBeforeUnmount, onMounted, ref } from 'vue'

export const DESIGN_WIDTH = 1280
export const DESIGN_HEIGHT = 800

export function useDesignScale() {
  const scale = ref(1)
  const frameWidth = ref(DESIGN_WIDTH)
  const frameHeight = ref(DESIGN_HEIGHT)

  function update() {
    const next = Math.min(
      window.innerWidth / DESIGN_WIDTH,
      window.innerHeight / DESIGN_HEIGHT,
    )
    scale.value = next
    frameWidth.value = DESIGN_WIDTH * next
    frameHeight.value = DESIGN_HEIGHT * next
  }

  onMounted(() => {
    update()
    window.addEventListener('resize', update)
  })

  onBeforeUnmount(() => {
    window.removeEventListener('resize', update)
  })

  return {
    scale,
    frameWidth,
    frameHeight,
    designWidth: DESIGN_WIDTH,
    designHeight: DESIGN_HEIGHT,
  }
}
