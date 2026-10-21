/** 语义化图标名到 Iconify Solar 的映射 */
export const iconMap = {
  home: 'solar:home-2-linear',
  device: 'solar:monitor-smartphone-linear',
  settings: 'solar:settings-linear',
  shield: 'solar:shield-check-linear',
  lock: 'solar:lock-keyhole-linear',
  aiPlan: 'solar:magic-stick-3-linear',
  manualPlan: 'solar:checklist-linear',
  arrowRight: 'solar:arrow-right-linear',
  arrowLeft: 'solar:arrow-left-linear',
  success: 'solar:check-circle-bold',
  check: 'solar:check-circle-linear',
  warning: 'solar:info-circle-bold',
  error: 'solar:danger-triangle-linear',
  pause: 'solar:pause-circle-linear',
  sparkles: 'solar:sparkles-linear',
  scan: 'solar:scan-linear',
  leaf: 'solar:leaf-linear',
  camera: 'solar:camera-linear',
  close: 'solar:close-circle-linear',
  document: 'solar:document-text-linear',
  fire: 'solar:fire-linear',
  flame: 'solar:flame-linear',
  handShake: 'solar:hand-shake-linear',
  body: 'solar:body-linear',
} as const

export type IconName = keyof typeof iconMap
