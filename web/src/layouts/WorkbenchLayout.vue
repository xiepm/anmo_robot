<script setup lang="ts">
/** 服务工作台布局：侧栏流程导航、顶栏状态、主内容区 */
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'
import AppIcon from '@/components/common/AppIcon.vue'
import DeviceModal from '@/components/management/DeviceModal.vue'
import SettingsModal from '@/components/management/SettingsModal.vue'
import { useFlowStore, type FlowStage } from '@/stores/flow'
import { useRobotStore } from '@/stores/robot'
import { useToastStore } from '@/stores/toast'
import heaconLogo from '@/assets/images/heacon-logo.png'

const route = useRoute()
const flow = useFlowStore()
const toast = useToastStore()

const crumb = computed(() => (route.meta.crumb as string) || '服务流程')
const showDeviceModal = ref(false)
const showSettingsModal = ref(false)

interface FlowStep {
  stage: FlowStage
  title: string
  lockedHint: string
}

const steps: FlowStep[] = [
  { stage: 0, title: '选择方案', lockedHint: '' },
  { stage: 1, title: '人体定位', lockedHint: '完成方案后解锁' },
  { stage: 2, title: '开始工作', lockedHint: '完成人体定位后解锁' },
]

function stepStatus(stage: FlowStage) {
  const locked = stage > flow.unlockedStage
  const active = stage === flow.currentStage && route.name !== 'service-records'
  const done = !locked && stage < flow.currentStage
  return { locked, active, done }
}

function statusText(stage: FlowStage, lockedHint: string) {
  const { locked, active, done } = stepStatus(stage)
  if (locked) return lockedHint
  if (active) return '当前步骤'
  if (done) return '已完成'
  return ''
}

function onStepClick(stage: FlowStage) {
  if (stage > flow.unlockedStage) {
    toast.show(
      stage === 2 ? '请先完成问卷并确认服务方案' : '请先完成人体定位并确认路线',
    )
    return
  }
  toast.show(
    stage === flow.currentStage && route.name !== 'service-records'
      ? '当前正在进行此步骤'
      : '流程步骤仅显示进度，请使用页面按钮继续',
  )
}

const robot = useRobotStore()

/** 顶栏设备状态：区分通讯断开、状态过期与设备反馈异常 */
const deviceBadge = computed(() => {
  if (!robot.connected) return { ok: false, text: '下位机未连接' }
  if (robot.stale) return { ok: false, text: '状态已过期' }
  if (!robot.deviceHealthy) return { ok: false, text: '设备反馈异常' }
  const mode = robot.status?.mode === 'SIMULATION' ? ' · 模拟' : ' · 实机'
  return { ok: true, text: `设备已连接${mode}` }
})

function openDevice() {
  showSettingsModal.value = false
  showDeviceModal.value = true
}

function openSettings() {
  showDeviceModal.value = false
  showSettingsModal.value = true
}
</script>

<template>
  <div class="workbench">
    <aside class="sidebar">
      <div class="sidebar__brand">
        <div class="logo-plate" aria-hidden="true">
          <img class="logo-img" :src="heaconLogo" alt="HEACON" />
        </div>
        <div class="brand-lockup">
          <div class="brand-cn" aria-label="希康智能">
            <span>希</span><span>康</span><span>智</span><span>能</span>
          </div>
          <div class="brand-en" aria-label="HEACON">
            <span>H</span><span>E</span><span>A</span><span>C</span><span>O</span><span>N</span>
          </div>
        </div>
      </div>

      <div class="sidebar__flow">
        <div class="flow-label">当前服务流程</div>
        <nav class="flow-nav" aria-label="服务流程步骤">
          <div
            v-for="item in steps"
            :key="item.stage"
            class="flow-step"
            :class="{
              'is-active': stepStatus(item.stage).active,
              'is-done': stepStatus(item.stage).done,
              'is-locked': stepStatus(item.stage).locked,
            }"
            role="button"
            tabindex="0"
            @click="onStepClick(item.stage)"
            @keydown.enter.prevent="onStepClick(item.stage)"
          >
            <span class="step-num">{{ item.stage + 1 }}</span>
            <span class="step-body">
              <span class="step-title">{{ item.title }}</span>
              <span
                class="step-status"
                :class="{ 'is-current': stepStatus(item.stage).active }"
              >
                {{ statusText(item.stage, item.lockedHint) }}
              </span>
            </span>
            <AppIcon
              v-if="stepStatus(item.stage).locked"
              name="lock"
              :size="15"
              color="var(--muted)"
            />
          </div>
        </nav>
      </div>
    </aside>

    <div class="workbench__main">
      <header class="header">
        <div class="crumb icon-text">
          <AppIcon name="home" :size="18" color="var(--muted)" />
          <span class="crumb-sep">/</span>
          <span>{{ crumb }}</span>
        </div>

        <div class="header__actions">
          <div class="device-badge icon-text" :class="{ 'is-warn': !deviceBadge.ok }">
            <AppIcon :name="deviceBadge.ok ? 'shield' : 'warning'" :size="16" />
            {{ deviceBadge.text }}
          </div>
          <button
            class="icon-btn"
            type="button"
            title="设备管理"
            @click="openDevice"
          >
            <AppIcon name="device" :size="20" />
          </button>
          <button
            class="icon-btn"
            type="button"
            title="设置"
            @click="openSettings"
          >
            <AppIcon name="settings" :size="20" />
          </button>
        </div>
      </header>

      <main class="main-pane">
        <router-view />
      </main>
    </div>

    <DeviceModal v-if="showDeviceModal" @close="showDeviceModal = false" />
    <SettingsModal v-if="showSettingsModal" @close="showSettingsModal = false" />
  </div>
</template>

<style scoped lang="scss">
.workbench {
  display: flex;
  width: 100%;
  height: 100%;
}

.sidebar {
  width: var(--sidebar-width);
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  padding: 16px;
  background: linear-gradient(180deg, #fff7f0 0%, #ffe8d4 100%);
  border-right: 1px solid rgba(255, 140, 80, 0.18);
  box-shadow: 4px 0 20px rgba(255, 106, 26, 0.06);
}

.sidebar__brand {
  display: flex;
  align-items: center;
  gap: 12px;
  padding-bottom: 16px;
  border-bottom: 1px solid var(--line);
}

.logo-plate {
  width: 40px;
  height: 40px;
  border-radius: 12px;
  background: #fff;
  border: 1px solid rgba(255, 140, 80, 0.2);
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 2px 8px rgba(255, 106, 26, 0.1);
  flex-shrink: 0;
  overflow: hidden;
}

.logo-img {
  width: 28px;
  height: 28px;
  object-fit: contain;
}

.brand-lockup {
  display: inline-grid;
  grid-template-columns: max-content;
}

.brand-cn,
.brand-en {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  width: 100%;
  line-height: 1;
}

.brand-cn {
  font-size: 15px;
  font-weight: 600;
  color: var(--ink);
  gap: 0.14em;
  width: max-content;
}

.brand-en {
  margin-top: 6px;
  font-size: 11px;
  font-weight: 500;
  color: var(--muted);
}

.sidebar__flow {
  margin-top: 20px;
  flex: 1;
}

.flow-label {
  font-size: 11px;
  color: var(--muted);
  letter-spacing: 0.14em;
  margin-bottom: 10px;
  padding: 0 4px;
}

.flow-nav {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.flow-step {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border-radius: 14px;
  color: var(--ink);
  cursor: pointer;
  user-select: none;
}

.flow-step.is-active {
  background: #fff;
  box-shadow:
    0 1px 4px rgba(255, 106, 26, 0.08),
    0 0 0 1px rgba(255, 106, 26, 0.25);
}

.step-num {
  width: 28px;
  height: 28px;
  border-radius: 999px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 14px;
  font-weight: 600;
  flex-shrink: 0;
  background: #fff;
  color: var(--brand);
  border: 2px solid #ffb07a;
}

.flow-step.is-active .step-num {
  background: var(--brand);
  color: #fff;
  border-color: transparent;
  box-shadow: 0 4px 10px rgba(255, 106, 26, 0.35);
}

.flow-step.is-done .step-num {
  background: #ffe0c8;
  color: var(--brand);
  border-color: transparent;
}

.step-body {
  flex: 1;
  min-width: 0;
}

.step-title {
  display: block;
  font-size: 14px;
  font-weight: 500;
}

.step-status {
  display: block;
  font-size: 12px;
  margin-top: 2px;
  color: var(--muted);
}

.step-status.is-current {
  color: var(--brand);
}

.workbench__main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.header {
  height: var(--header-height);
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
  background: #fff;
  border-bottom: 1px solid rgba(255, 170, 120, 0.2);
}

.crumb {
  font-size: 14px;
  color: var(--muted);
}

.crumb-sep {
  color: var(--muted);
}

.header__actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.device-badge {
  padding: 8px 12px;
  border-radius: 8px;
  background: var(--brand-soft);
  color: var(--brand);
  font-size: 12px;
}

.device-badge.is-warn {
  background: #fff0ed;
  color: var(--error);
}

.icon-btn {
  width: 40px;
  height: 40px;
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #fff;
  border: 1px solid rgba(255, 160, 110, 0.28);
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.04);
  color: var(--brand);
  cursor: pointer;
}

.icon-btn:hover {
  border-color: rgba(255, 106, 26, 0.35);
}

.main-pane {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: 24px 28px;
  background:
    radial-gradient(720px 340px at 100% 0%, rgba(255, 140, 70, 0.08), transparent 55%),
    linear-gradient(180deg, #fffdfb 0%, #fff8f2 100%);
}
</style>
