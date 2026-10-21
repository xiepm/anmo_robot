/**
 * 设备偏好与安全上限
 * 持久化到 localStorage
 */
import { defineStore } from 'pinia'
import { ref } from 'vue'

const KEYS = {
  volume: 'heaconVolume',
  muted: 'heaconMuted',
  brightness: 'heaconBrightness',
  tempLimit: 'heaconTempLimit',
  forceLimit: 'heaconForceLimit',
} as const

function readNumber(key: string, fallback: number) {
  const raw = localStorage.getItem(key)
  if (raw == null) return fallback
  const n = Number(raw)
  return Number.isFinite(n) ? n : fallback
}

function readBool(key: string, fallback: boolean) {
  const raw = localStorage.getItem(key)
  if (raw == null) return fallback
  return raw === '1' || raw === 'true'
}

function writeValue(key: string, value: string | number | boolean) {
  localStorage.setItem(key, String(value))
}

export const useSettingsStore = defineStore('settings', () => {
  const volume = ref(readNumber(KEYS.volume, 70))
  const muted = ref(readBool(KEYS.muted, false))
  const brightness = ref(readNumber(KEYS.brightness, 80))
  const tempLimit = ref(Math.min(58, Math.max(50, readNumber(KEYS.tempLimit, 56))))
  const forceLimit = ref(Math.min(25, Math.max(10, readNumber(KEYS.forceLimit, 20))))

  function setVolume(value: number) {
    volume.value = Math.max(0, Math.min(100, value))
    writeValue(KEYS.volume, volume.value)
  }

  function setMuted(value: boolean) {
    muted.value = value
    writeValue(KEYS.muted, value ? '1' : '0')
  }

  function setBrightness(value: number) {
    brightness.value = Math.max(30, Math.min(100, value))
    writeValue(KEYS.brightness, brightness.value)
  }

  function setTempLimit(value: number) {
    tempLimit.value = Math.min(58, Math.max(50, value))
  }

  function setForceLimit(value: number) {
    forceLimit.value = Math.min(25, Math.max(10, value))
  }

  /** 二次确认后保存安全上限 */
  function saveSafetyLimits() {
    writeValue(KEYS.tempLimit, tempLimit.value)
    writeValue(KEYS.forceLimit, forceLimit.value)
  }

  return {
    volume,
    muted,
    brightness,
    tempLimit,
    forceLimit,
    setVolume,
    setMuted,
    setBrightness,
    setTempLimit,
    setForceLimit,
    saveSafetyLimits,
  }
})
