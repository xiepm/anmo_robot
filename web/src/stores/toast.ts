/**
 * 轻量 Toast 提示
 */
import { defineStore } from 'pinia'
import { ref } from 'vue'

export const useToastStore = defineStore('toast', () => {
  const message = ref('')
  const visible = ref(false)
  let timer: ReturnType<typeof setTimeout> | null = null

  function hide() {
    visible.value = false
    if (timer) {
      clearTimeout(timer)
      timer = null
    }
  }

  function show(text: string, duration = 2200) {
    const content = text.trim()
    if (!content) return
    message.value = content
    visible.value = true
    if (timer) clearTimeout(timer)
    timer = setTimeout(() => {
      visible.value = false
      timer = null
    }, duration)
  }

  return { message, visible, show, hide }
})
