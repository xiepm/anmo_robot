<script setup lang="ts">
/** 设置弹窗：设备偏好与安全上限 */
import { computed, ref } from 'vue'
import AppConfirm from '@/components/common/AppConfirm.vue'
import AppIcon from '@/components/common/AppIcon.vue'
import AppModal from '@/components/common/AppModal.vue'
import { useSettingsStore } from '@/stores/settings'
import { useToastStore } from '@/stores/toast'
import { stopSpeech, speakPreview } from '@/utils/speech'

const emit = defineEmits<{
  close: []
}>()

const settings = useSettingsStore()
const toast = useToastStore()
const savedTip = ref('')
const showSafetyConfirm = ref(false)

const volumeFill = computed(() => `${settings.volume}%`)
const brightnessFill = computed(() => `${((settings.brightness - 30) / 70) * 100}%`)
const tempFill = computed(() => `${((settings.tempLimit - 50) / 8) * 100}%`)
const forceFill = computed(() => `${((settings.forceLimit - 10) / 15) * 100}%`)

const safetyConfirmDesc = computed(
  () =>
    `确认将安全温度上限设为 ${settings.tempLimit}℃、力度上限设为 ${settings.forceLimit} N？保存后将写入设备偏好并留痕。`,
)

function onVolume(e: Event) {
  settings.setVolume(Number((e.target as HTMLInputElement).value))
}

function onBrightness(e: Event) {
  settings.setBrightness(Number((e.target as HTMLInputElement).value))
}

function onTemp(e: Event) {
  settings.setTempLimit(Number((e.target as HTMLInputElement).value))
}

function onForce(e: Event) {
  settings.setForceLimit(Number((e.target as HTMLInputElement).value))
}

function openSafetyConfirm() {
  showSafetyConfirm.value = true
}

function closeSafetyConfirm() {
  showSafetyConfirm.value = false
}

function saveSafetyLimits() {
  settings.saveSafetyLimits()
  showSafetyConfirm.value = false
  savedTip.value = '安全上限已保存并留痕'
  toast.show('安全上限已保存并留痕')
}

function onMuteChange(e: Event) {
  const checked = (e.target as HTMLInputElement).checked
  if (checked) {
    stopSpeech()
    settings.setMuted(true)
    return
  }
  settings.setMuted(false)
  speakPreview()
}
</script>

<template>
  <AppModal title="设置" subtitle="设备偏好与安全上限" width="520px" @close="emit('close')">
    <template #close-icon>
      <AppIcon name="close" :size="24" />
    </template>

    <div class="settings-list">
      <div class="block">
        <h3 class="block-title">设备偏好</h3>

        <label class="field">
          <div class="row">
            <span>语音音量</span>
            <span class="value">{{ settings.volume }}%</span>
          </div>
          <input
            class="heacon-range"
            type="range"
            min="0"
            max="100"
            :value="settings.volume"
            :style="{ '--range-fill': volumeFill }"
            @input="onVolume"
          />
        </label>

        <label class="check-row">
          <span>静音</span>
          <input
            class="accent-check"
            type="checkbox"
            :checked="settings.muted"
            @change="onMuteChange"
          />
        </label>

        <label class="field">
          <div class="row">
            <span>屏幕亮度</span>
            <span class="value">{{ settings.brightness }}%</span>
          </div>
          <input
            class="heacon-range"
            type="range"
            min="30"
            max="100"
            :value="settings.brightness"
            :style="{ '--range-fill': brightnessFill }"
            @input="onBrightness"
          />
        </label>
      </div>

      <div class="block">
        <h3 class="block-title">安全温度与力度上限</h3>

        <div class="field">
          <div class="row">
            <span>安全温度上限</span>
            <b class="value">{{ settings.tempLimit }}℃</b>
          </div>
          <input
            class="heacon-range"
            type="range"
            min="50"
            max="58"
            :value="settings.tempLimit"
            :style="{ '--range-fill': tempFill }"
            @input="onTemp"
          />
          <p class="hint">可调范围 50–58℃</p>
        </div>

        <div class="field">
          <div class="row">
            <span>安全力度上限</span>
            <b class="value">{{ settings.forceLimit }} N</b>
          </div>
          <input
            class="heacon-range"
            type="range"
            min="10"
            max="25"
            :value="settings.forceLimit"
            :style="{ '--range-fill': forceFill }"
            @input="onForce"
          />
          <p class="hint">可调范围 10–25 N</p>
        </div>

        <button class="save-btn" type="button" @click="openSafetyConfirm">二次确认并保存</button>
        <p v-if="savedTip" class="saved-tip">{{ savedTip }}</p>
      </div>
    </div>
  </AppModal>

  <AppConfirm
    v-if="showSafetyConfirm"
    title="确认保存安全上限？"
    :description="safetyConfirmDesc"
    cancel-text="再想想"
    confirm-text="确认保存"
    tone="soft"
    width="460px"
    @cancel="closeSafetyConfirm"
    @confirm="saveSafetyLimits"
  />
</template>

<style scoped lang="scss">
.settings-list {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.block {
  padding: 20px;
  border-radius: 16px;
  border: 1px solid var(--line);
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.block-title {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: var(--ink);
}

.field {
  display: block;
  font-size: 14px;
}

.row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}

.value {
  color: var(--brand);
  font-weight: 600;
}

.hint {
  margin: 4px 0 0;
  font-size: 12px;
  color: #94a3b8;
}

.check-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 14px;
}

.accent-check {
  width: 20px;
  height: 20px;
  accent-color: var(--brand);
}

.save-btn {
  width: 100%;
  padding: 12px;
  border: none;
  border-radius: 12px;
  background: #fff5ed;
  color: #bd6332;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
}

.save-btn:hover {
  filter: brightness(0.98);
}

.saved-tip {
  margin: 0;
  font-size: 13px;
  color: var(--ok);
  text-align: center;
}
</style>
