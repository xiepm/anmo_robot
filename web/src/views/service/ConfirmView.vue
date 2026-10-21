<script setup lang="ts">
/**
 * 路线与参数确认
 * 「确认路线并开始」先发送 SET_PLAN，设备接受后再发送 START
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { onBeforeRouteLeave, useRouter } from 'vue-router'
import AppConfirm from '@/components/common/AppConfirm.vue'
import AppIcon from '@/components/common/AppIcon.vue'
import BodyMap from '@/components/service/BodyMap.vue'
import PhotoRouteEditor from '@/components/service/PhotoRouteEditor.vue'
import { selectRecognizedPoints, toPointLabel, toProtocolPoint } from '@/data/points'
import { levelRange, useFlowStore } from '@/stores/flow'
import { useRobotStore } from '@/stores/robot'
import { useToastStore } from '@/stores/toast'
import type { Capture, PhotoAdjustment } from '@/utils/photoEditor'
import { planBlockReason } from '@/utils/controlChecks'
import { prefetchSpeech, servicePointLine, unlockSpeech } from '@/utils/speech'

const router = useRouter()
const flow = useFlowStore()
const robot = useRobotStore()
const toast = useToastStore()

const showStartModal = ref(false)
const submitting = ref(false)
const photoReady = ref(false)
const photoEditing = ref(false)
const confirming = ref(false)
const coordinateError = ref('')
const finalPixels = ref<PhotoAdjustment[]>([])
type PhotoIdentity = Pick<Capture, 'region' | 'frame_number' | 'received_monotonic_s'>
const rejectedPhoto = ref<{ photo: PhotoIdentity; reason: string } | null>(null)
watch(() => {
  const photo = flow.scanSnapshot
  return photo ? `${photo.region}:${photo.frame_number}:${photo.received_monotonic_s}` : ''
}, () => {
  rejectedPhoto.value = null
  coordinateError.value = ''
  photoReady.value = false
  finalPixels.value = []
}, { flush: 'sync' })
function samePhoto(a: PhotoIdentity | null | undefined, b: PhotoIdentity | null | undefined) {
  return Boolean(a && b && a.region === b.region && a.frame_number === b.frame_number
    && a.received_monotonic_s === b.received_monotonic_s)
}
function invalidPhotoReason(error = '') {
  if (error === '没有可确认的本次照片坐标') return '控制端已清除本次照片坐标，请重新扫描'
  if (['坐标确认不属于当前照片', '返回坐标不是本次确认照片'].includes(error)) return `${error}，请重新扫描`
  // A point-level depth hole also suggests rescanning, but remains editable on
  // this photo. Only explicit whole-photo invalidation retires its coordinates.
  return ['本次照片超过五分钟，请重新扫描', '标定已改变，请重新扫描',
    '拍照后机械臂位置已改变，请重新扫描', '确认坐标的照片已失效，请重新扫描',
    '相机已断开或照片已更新，请重新扫描'].includes(error) ? error : ''
}
const photoStatusBlocker = computed(() => {
  const photo = flow.scanSnapshot, status = robot.status
  if (!photo) return ''
  if (!robot.online || !status) return '控制连接或状态尚未恢复，正在等待最新状态'
  if (!status.scan_snapshot) return '控制端已清除本次照片坐标，请重新扫描'
  if (!samePhoto(photo, status.scan_snapshot)) return '控制端照片与当前显示的照片不一致，请重新扫描'
  if (samePhoto(photo, rejectedPhoto.value?.photo)) return rejectedPhoto.value!.reason
  const result = status.point_confirmation
  if (result?.state === 'FAILED' && result.frame_number === photo.frame_number
    && result.received_monotonic_s === photo.received_monotonic_s) {
    const reason = invalidPhotoReason(result.error)
    if (reason) return reason
  }
  if (status.vision_feedback_valid === false) return '视觉画面暂未恢复，请等待；本次照片与穴位调整已保留'
  return ''
})
const noncontact = computed(() => robot.status?.noncontact)
const routeReady = computed(() => noncontact.value?.active && noncontact.value.phase === 'READY')
const noncontactCompleted = computed(() => noncontact.value?.phase === 'COMPLETED')
const returnedToStart = computed(() => noncontactCompleted.value
  && noncontact.value?.returned_to_start === true && noncontact.value.stop_confirmed === true
  && !noncontact.value.active && robot.status?.state === 'IDLE')
const canRescan = computed(() => !confirming.value && !submitting.value && !noncontact.value?.active
  && !noncontact.value?.returning_to_start && !robot.pendingCmd && !robot.pendingStop
  && (!noncontact.value || ['IDLE', 'READY'].includes(robot.status?.state ?? '')))
const canContinue = computed(() => returnedToStart.value && robot.deviceHealthy && canRescan.value)
const noncontactProgress = computed(() => {
  if (robot.status?.state === 'STOPPING') return '正在停止，等待实际反馈'
  if (noncontact.value?.returning_to_start) return '穴位路线已执行，正在返回本次起始点位，请等待停稳'
  if (routeReady.value) return '路线已准备，请核对目标后开始'
  return robot.status?.state === 'RUNNING' ? '正在执行无接触路线' : '正在校验路线与停止通路'
})
const needsSiteAuthorization = computed(() => !noncontact.value?.available
  && noncontact.value?.authorization_available === true && !noncontact.value.active)
const checkingStop = ref(false)
async function checkStopPath() {
  checkingStop.value = true
  coordinateError.value = ''
  try {
    const response = await robot.request('CHECK_NONCONTACT_STOP')
    if (!response.ok) throw new Error(response.message)
  } catch (error) {
    coordinateError.value = error instanceof Error ? error.message : '停止通路检查失败'
  } finally { checkingStop.value = false }
}
async function prepareNoncontact() {
  if (!receipt.value || !canStart.value) return
  submitting.value = true
  coordinateError.value = ''
  let accepted = false
  try {
    const epoch = robot.commandEpoch
    const route = { revision: receipt.value.revision, points: photoPoints.value.map(p => ({ ...p })) }
    if (needsSiteAuthorization.value) {
      const authorization = await robot.request('AUTHORIZE_NONCONTACT', {
        ...route, operator_observed_no_obstacles: true, reuse_existing_calibration: true,
      })
      if (!authorization.ok) throw new Error(authorization.message)
      if (disposed || robot.commandEpoch !== epoch || robot.pendingStop || !robot.deviceHealthy
        || receipt.value?.revision !== route.revision) throw new Error('现场确认后路线或设备状态已变化，请重新确认')
    }
    const after = robot.statusRevision
    const response = await robot.request('PREPARE_NONCONTACT', route)
    if (!response.ok) throw new Error(response.message)
    accepted = true
    await robot.waitForState('READY', after, epoch, 10000)
  } catch (e) {
    coordinateError.value = e instanceof Error ? e.message : '路线准备失败'
    if (accepted) await robot.stop()
  } finally { submitting.value = false }
}
async function stopNoncontact() {
  const response = await robot.stop()
  if (!response.ok) coordinateError.value = response.message
}
function requestStart() {
  if (noncontact.value && !routeReady.value) { prepareNoncontact(); return }
  showStartModal.value = true
}
const receipt = computed(() => {
  const result = robot.status?.point_confirmation, photo = flow.scanSnapshot
  if (!photo || photoStatusBlocker.value || result?.state !== 'CONFIRMED' || result.frame_number !== photo.frame_number
    || result.received_monotonic_s !== photo.received_monotonic_s) return null
  const points = result.requested_points
  if (points.length !== finalPixels.value.length || points.some((p,i) => {
    const q = finalPixels.value[i]
    return !q || p.name !== q.name || p.side !== q.side || p.x !== q.x || p.y !== q.y
  })) return null
  return result
})
const editorLocked = computed(() => submitting.value || confirming.value || noncontactCompleted.value || Boolean(receipt.value)
  || Boolean(photoStatusBlocker.value) || !robot.online || !['IDLE','READY'].includes(robot.status?.state ?? ''))
const canConfirmPoints = computed(() => photoReady.value && !photoStatusBlocker.value && !photoEditing.value && !confirming.value
  && !submitting.value && !noncontactCompleted.value && !noncontact.value?.active && !robot.pendingCmd && !robot.pendingStop && robot.deviceHealthy
  && ['IDLE','READY'].includes(robot.status?.state ?? '') && finalPixels.value.length === photoPoints.value.length
  && finalPixels.value.length > 0)

async function confirmPoints() {
  const photo = flow.scanSnapshot
  if (!photo || !canConfirmPoints.value) return
  const photoIdentity = { region: photo.region, frame_number: photo.frame_number,
    received_monotonic_s: photo.received_monotonic_s }
  confirming.value = true
  coordinateError.value = ''
  const epoch = robot.commandEpoch
  try {
    if (receipt.value) {
      const after = robot.statusRevision
      const reply = await robot.request('CLEAR_POINTS')
      if (!reply.ok) throw new Error(reply.message)
      await robot.waitForState('IDLE', after, epoch)
    } else {
      const revision = crypto.randomUUID()
      const reply = await robot.request('CONFIRM_POINTS', {
        revision, frame_number: photo.frame_number, received_monotonic_s: photo.received_monotonic_s,
        points: finalPixels.value.map(p => ({ ...p })),
      })
      if (!reply.ok) throw new Error(reply.message)
      await robot.waitForPointConfirmation(revision, epoch)
    }
  } catch (error) {
    if (disposed || !samePhoto(photoIdentity, flow.scanSnapshot)) return
    coordinateError.value = error instanceof Error ? error.message : '坐标确认失败'
    const reason = invalidPhotoReason(coordinateError.value)
    if (reason) rejectedPhoto.value = { photo: photoIdentity, reason }
  } finally { confirming.value = false }
}
const photoPoints = computed(() => flow.activePoints.map(([name]) => toProtocolPoint(name)))
const photoSelection = computed(() => selectRecognizedPoints(photoPoints.value, flow.scanSnapshot?.available_points ?? []))
const missingPhotoPoints = computed(() => {
  if (!flow.scanSnapshot || !photoSelection.value.missing.length) return ''
  return `本次未识别：${photoSelection.value.missing.map(toPointLabel).join('、')}`
})
const hasRegionPoints = computed(() => photoPoints.value.some(p => p.name === '腰背'))
let disposed = false
onBeforeUnmount(() => { disposed = true })
onBeforeRouteLeave(() => !submitting.value && !confirming.value && !noncontact.value?.active)


const startBlocker = computed(() => {
  if (noncontactCompleted.value) return '本次路线已结束，请重新扫描后开始下一次使用'
  if (flow.scanSnapshot) {
    if (photoStatusBlocker.value) return photoStatusBlocker.value
    if (!photoReady.value) return '照片与穴位尚未就绪，请等待或重新扫描'
    if (photoEditing.value) return '请先完成穴位调整'
    if (!receipt.value) {
      const result = robot.status?.point_confirmation
      if (result?.state === 'FAILED' && result.frame_number === flow.scanSnapshot.frame_number
        && result.received_monotonic_s === flow.scanSnapshot.received_monotonic_s && result.error) {
        return result.error
      }
      return coordinateError.value || '请先确认最终穴位坐标，控制端确认后再开始'
    }
  }
  if (noncontact.value) {
    if (!robot.deviceHealthy) return '控制或机械臂反馈无效'
    if (!['IDLE','READY'].includes(robot.status?.state ?? '')) return robot.status?.error || '无接触运动处理中'
    if (noncontact.value.active && !routeReady.value) return '正在准备无接触路线，请等待或停止'
    if (missingPhotoPoints.value) return missingPhotoPoints.value
    if (noncontact.value.available || needsSiteAuthorization.value) return ''
    return noncontact.value.blockers[0] || '本次无接触路线尚未通过执行检查'
  }
  return missingPhotoPoints.value || planBlockReason(flow.planRequest, robot.status, robot.online)
})
const canStart = computed(() => !submitting.value && !robot.pendingCmd && !robot.pendingStop && !startBlocker.value)

const durationRange = computed(() =>
  flow.isMassage ? { min: 15, max: 25 } : { min: 30, max: 40 },
)

const energyRange = computed(() => levelRange(flow.isMassage))
const paramLabel = computed(() => (flow.isMassage ? '目标力度' : '目标温度'))
const energyUnit = computed(() => (flow.isMassage ? ' N' : '℃'))

function fillOf(value: number, min: number, max: number) {
  return `${((value - min) / (max - min)) * 100}%`
}

const durationFill = computed(() => {
  const { min, max } = durationRange.value
  return fillOf(flow.serviceParams.duration, min, max)
})

const levelFill = computed(() => {
  const { min, max } = energyRange.value
  return fillOf(flow.serviceParams.level, min, max)
})

onMounted(() => {
  flow.initServiceParams()
  flow.resetAdjustedPoints()
})

function onDurationInput(e: Event) {
  const value = Number((e.target as HTMLInputElement).value)
  flow.setDuration(value)
}

function onLevelInput(e: Event) {
  const value = Number((e.target as HTMLInputElement).value)
  flow.setLevel(value)
}

function rescan() {
  if (!canRescan.value) return
  flow.beginScan()
  router.push({ name: 'service-position' })
}

function continueUsing() {
  if (!canContinue.value) return
  rescan()
}

async function startService() {
  showStartModal.value = false
  if (!canStart.value) { toast.show(startBlocker.value || '设备正在处理请求'); return }
  if (noncontact.value) {
    const sha = noncontact.value.plan_sha256
    if (!routeReady.value || !sha) return
    submitting.value = true
    let sent = false
    try {
      const after = robot.statusRevision, epoch = robot.commandEpoch
      sent = true
      const response = await robot.request('START_NONCONTACT', { plan_sha256: sha })
      if (!response.ok) throw new Error(response.message)
      await robot.waitForState('RUNNING', after, epoch, 5000)
    } catch (e) {
      coordinateError.value = e instanceof Error ? e.message : '启动未确认'
      if (sent) await robot.stop()
    } finally { submitting.value = false }
    return
  }
  const plan = flow.planRequest ? { ...flow.planRequest, ...(receipt.value ? { confirmation_revision: receipt.value.revision } : {}) } : null
  if (!plan) return
  submitting.value = true
  const epoch = robot.commandEpoch
  let startSent = false
  try {
    const readyAfter = robot.statusRevision
    const saved = await robot.request('SET_PLAN', JSON.parse(JSON.stringify(plan)))
    if (!saved.ok) throw new Error(saved.message)
    await robot.waitForState('READY', readyAfter, epoch)
    const blocker = startBlocker.value || planBlockReason(plan, robot.status, robot.online)
    if (blocker || disposed || robot.commandEpoch !== epoch) throw new Error(blocker || '启动流程已中断')
    const runningAfter = robot.statusRevision
    startSent = true
    const started = await robot.request('START')
    if (!started.ok) throw new Error(started.message)
    await robot.waitForState('RUNNING', runningAfter, epoch)
    if (disposed) return
    unlockSpeech()
    const firstPoint = flow.activePoints[0]?.[0]
    if (firstPoint) prefetchSpeech(servicePointLine(firstPoint))
    flow.startService()
    submitting.value = false
    await router.push({ name: 'service-run' })
  } catch (error) {
    // START may have reached the controller even when its reply/status was lost.
    if (startSent) await robot.stop()
    toast.show(error instanceof Error ? error.message : '启动状态未确认')
  } finally {
    submitting.value = false
  }
}

</script>

<template>
  <section class="confirm-screen">
    <div class="confirm-head">
      <div>
        <h1 class="confirm-title">确认穴位路线与参数</h1>
        <p v-if="flow.scanSnapshot" class="confirm-desc">本方案已识别 {{ photoSelection.recognized.length }} / {{ photoPoints.length }} 个点位</p>
        <p v-else class="confirm-desc">俯视俯卧示意 · 拖动仅调整示意位置，设备按定位结果执行</p>
        <p v-if="flow.scanSnapshot && hasRegionPoints" class="confirm-desc">左／右腰背是区域名称，尚未定义具体穴位。</p>
      </div>
      <button class="rescan-btn" type="button" :disabled="!canRescan" @click="rescan">重新扫描</button>
    </div>

    <div class="confirm-layout">
      <div class="panel confirm-body-card">
        <PhotoRouteEditor v-if="flow.scanSnapshot"
          :key="`${flow.scanSnapshot.frame_number}:${flow.scanSnapshot.received_monotonic_s}:${flow.selectedPlanId}`"
          :capture="flow.scanSnapshot" :points="photoPoints" :adjustments="flow.photoAdjustments" :locked="editorLocked" :confirmed="Boolean(receipt)"
          @change="flow.setPhotoAdjustments" @positions="finalPixels = $event" @ready="photoReady = $event" @editing="photoEditing = $event"
        />
        <BodyMap v-else
          :points="flow.activePoints"
          @update:points="flow.setAdjustedPoints"
        />
      </div>

      <aside class="panel confirm-side">
        <div class="confirm-side-body">
          <div class="side-top">
            <h3 class="plan-name">{{ flow.selectedPlan?.name }}</h3>
            <div class="benefit-card">
              <div class="benefit-head icon-text">
                <AppIcon name="leaf" :size="17" color="var(--brand)" />
                <span>功效</span>
              </div>
              <p class="benefit-text">{{ flow.selectedPlan?.benefit }}</p>
              <div class="benefit-tags">
                <span
                  v-for="tag in flow.selectedPlan?.benefitPoints ?? []"
                  :key="tag"
                  class="tag"
                >
                  {{ tag }}
                </span>
              </div>
            </div>
          </div>

          <div v-if="!noncontact" class="params">
            <label class="param-field">
              <div class="param-row">
                <span>服务时长</span>
                <span class="param-value">{{ flow.serviceParams.duration }} 分钟</span>
              </div>
              <input
                class="heacon-range"
                type="range"
                step="1"
                :min="durationRange.min"
                :max="durationRange.max"
                :value="flow.serviceParams.duration"
                :style="{ '--range-fill': durationFill }"
                @input="onDurationInput"
              />
              <div class="param-ends">
                <span>{{ durationRange.min }} 分</span>
                <span>{{ durationRange.max }} 分</span>
              </div>
            </label>

            <label class="param-field">
              <div class="param-row">
                <span>{{ paramLabel }}</span>
                <span class="param-value">{{ flow.serviceParams.level }}{{ energyUnit }}</span>
              </div>
              <input
                class="heacon-range"
                type="range"
                step="1"
                :min="energyRange.min"
                :max="energyRange.max"
                :value="flow.serviceParams.level"
                :style="{ '--range-fill': levelFill }"
                @input="onLevelInput"
              />
              <div class="param-ends">
                <span>{{ energyRange.min }}{{ energyUnit }}</span>
                <span>{{ energyRange.max }}{{ energyUnit }}</span>
              </div>
            </label>
          </div>

          <div class="side-actions">
            <template v-if="noncontact">
              <details class="confirmed-coordinates" open>
                <summary>执行前检查 · 普通停止通路</summary>
                <p>在机械臂静止时发送普通停止命令，核对停止反馈；本检查不启动运动。</p>
                <button type="button" :disabled="checkingStop || noncontact.active || noncontact.stop_preflight?.state === 'CHECKING' || noncontact.route_review?.state === 'CHECKING'" @click="checkStopPath">{{ noncontact.stop_preflight?.state === 'CHECKING' ? '正在检查停止通路…' : '检查停止通路' }}</button>
                <p v-if="noncontact.stop_preflight?.reason" role="status">{{ noncontact.stop_preflight.reason }}</p>
              </details>
              <details v-if="noncontact.route_review" class="confirmed-coordinates" open>
                <summary>无接触路线预检 · {{ noncontact.route_review.state === 'CHECKING' ? '计算中' : noncontact.route_review.kinematics_checked ? (noncontact.available ? 'SDK 已通过，本次现场确认已记录' : 'SDK 已通过，执行条件待核实') : '尚未通过' }}</summary>
                <p v-if="noncontact.route_review.kinematics_checked">控制器逆解及正解复核通过 · {{ noncontact.route_review.sample_count }} 个采样位置</p>
                <p v-else>控制器路线可达性：{{ noncontact.route_review.state === 'CHECKING' ? noncontact.route_review.reason : '未通过或未完成' }}</p>
                <p v-if="noncontact.route_review.transfer_height_mm !== null">候选过渡高度（工具尖端 Z）：{{ noncontact.route_review.transfer_height_mm.toFixed(1) }} mm · {{ noncontact.route_review.waypoint_count }} 个路线节点</p>
                <p v-if="noncontact.route_review.model_position_error_mm !== null">机器人模型与反馈偏差：{{ noncontact.route_review.model_position_error_mm.toFixed(2) }} mm</p>
                <p v-if="noncontact.route_review.model_check_state === 'DEFERRED'">虚拟模型检查已暂缓，当前按实机 SDK 计算路线。</p>
                <template v-if="noncontact.route_review.unknown_route_clearance">
                  <p v-if="noncontact.route_review.unknown_route_clearance.assessment === 'UNKNOWN_OFF_CENTERLINE'">本次未知栅格位于候选工具尖端中心线外围，平面距离保守下界 {{ noncontact.route_review.unknown_route_clearance.minimum_xy_distance_lower_bound_mm?.toFixed(1) }} mm。</p>
                  <template v-else-if="noncontact.route_review.unknown_route_clearance.assessment === 'CENTERLINE_NEAR_UNKNOWN'">
                    <p v-if="noncontact.route_review.depth_visibility?.assessment === 'OBSERVED_CLEAR'">三维深度核对通过：平面重叠的 {{ noncontact.route_review.depth_visibility.cell_count }} 处区域与实际路线高度不同，路线位于本次有效观测的表面前方。</p>
                    <p v-else-if="noncontact.route_review.depth_visibility">三维深度核对：{{ noncontact.route_review.depth_visibility.reason }}</p>
                    <p v-else>工具尖端路线的平面投影经过深度未测清的区域；这不是检测到实体障碍物，需补充观测。</p>
                  </template>
                  <p v-else-if="noncontact.route_review.unknown_route_clearance.assessment === 'INCOMPLETE'">未知栅格位置记录不完整，不能判定它们与路线的距离。</p>
                  <p v-if="noncontact.route_review.unknown_route_clearance.nearest_cell_base_xy_mm">最近未知栅格中心 Base XY：[{{ noncontact.route_review.unknown_route_clearance.nearest_cell_base_xy_mm.map(v => v.toFixed(1)).join(', ') }}] mm。上述平面距离不是实物通行间隙。</p>
                </template>
                <p v-if="noncontact.available">本次按现场无障碍确认进行假人无接触联调；自动碰撞检查与独立精度验收仍未完成。</p>
                <p v-for="reason in noncontact.available ? [] : noncontact.route_review.blockers" :key="reason">{{ reason }}</p>
                <p v-for="(target, i) in noncontact.route_review.targets" :key="`${target.name}-${target.side}`">{{ i + 1 }} {{ toPointLabel(target) }} · 工具尖端 [{{ target.tool_tip_base_mm.map(v => v.toFixed(1)).join(', ') }}] mm · 法兰 [{{ target.flange_base_mm.map(v => v.toFixed(1)).join(', ') }}] mm</p>
                <p>预检不启动机械臂；表面点、工具尖端和法兰目标分别计算。</p>
              </details>
              <details v-if="noncontact.scene_analysis" class="confirmed-coordinates" open>
                <summary>本次深度场景 · {{ noncontact.scene_analysis.state === 'NO_DATA' ? '无有效深度' : noncontact.scene_analysis.unknown_cells ? '存在未知区域' : '可见表面已分析' }}</summary>
                <p>照片 {{ noncontact.scene_analysis.frame_number }} · 路线周边 {{ noncontact.scene_analysis.corridor_radius_mm }} mm · 栅格 {{ noncontact.scene_analysis.cell_size_mm }} mm</p>
                <p>可见最高表面 Z：{{ noncontact.scene_analysis.surface_ceiling_base_mm?.toFixed(1) ?? '—' }} mm</p>
                <p>本次已复核候选高度（工具尖端 Z）：{{ noncontact.scene_analysis.safe_transfer_height_base_mm?.toFixed(1) ?? '—' }} mm</p>
                <p v-if="noncontact.scene_analysis.workspace_base_mm">自动运动边界（法兰 XYZ）：[{{ noncontact.scene_analysis.workspace_base_mm.min.map(v => v.toFixed(1)).join(', ') }}] ～ [{{ noncontact.scene_analysis.workspace_base_mm.max.map(v => v.toFixed(1)).join(', ') }}] mm</p>
                <p>未知栅格：{{ noncontact.scene_analysis.unknown_cells }} / {{ noncontact.scene_analysis.corridor_cells }}；表示深度覆盖不足，不是实体障碍物数量。</p>
                <p v-if="noncontact.scene_analysis.coverage_diagnostics">缺测象限影响 {{ noncontact.scene_analysis.coverage_diagnostics.incomplete_quadrant_cells }} 格；无效深度射线影响 {{ noncontact.scene_analysis.coverage_diagnostics.invalid_ray_cells }} 格；其中 {{ noncontact.scene_analysis.coverage_diagnostics.overlapping_cause_cells }} 格重叠。</p>
                <p>以上为可见表面分析；整臂碰撞与盲区检查尚未完成。</p>
              </details>
              <p class="confirm-desc"><strong>假人无接触联调</strong> · {{ noncontact.note }}</p>
              <details v-if="noncontact.blockers.length" class="confirmed-coordinates">
                <summary>执行条件尚未齐备（{{ noncontact.blockers.length }} 项）</summary>
                <p v-for="reason in noncontact.blockers" :key="reason">{{ reason }}</p>
              </details>
              <details v-if="noncontact.targets.length" class="confirmed-coordinates" open>
                <summary>本次无接触路线 · Base mm</summary>
                <div v-for="p in noncontact.targets" :key="`${p.side}:${p.name}`">
                  {{ toPointLabel(p) }}<br>
                  工具尖端：{{ p.tool_tip_base_mm.map(v => v.toFixed(1)).join(' / ') }}<br>
                  法兰目标：{{ p.flange_base_mm.map(v => v.toFixed(1)).join(' / ') }}
                </div>
              </details>
              <p v-if="noncontact.active" class="confirm-desc" role="status">{{ noncontactProgress }}</p>
              <p v-if="noncontactCompleted" class="confirm-desc" role="status">{{ returnedToStart ? '本次路线已完成，机械臂已回到本次起始点位并停稳。可继续使用，重新拍照确认下一次穴位。' : '本次路线已结束，尚未收到回到起始点位并停稳的确认。' }}</p>
              <p v-if="noncontact.stop_confirmed" class="confirm-desc">机械臂停止反馈已确认</p>
              <p v-if="noncontact.error" class="confirm-desc" role="alert">{{ noncontact.error }}</p>
              <button v-if="noncontact.active" class="coordinate-confirm-btn" type="button" @click="stopNoncontact">停止无接触运动</button>
            </template>
            <template v-if="flow.scanSnapshot">
              <button v-if="!noncontactCompleted" class="coordinate-confirm-btn" type="button" :disabled="!canConfirmPoints" @click="confirmPoints">
                {{ confirming ? '正在确认坐标…' : receipt ? '重新调整穴位' : '确认穴位坐标' }}
              </button>
              <p v-if="!noncontactCompleted && (photoStatusBlocker || coordinateError)" class="confirm-desc" role="alert">{{ photoStatusBlocker || coordinateError }}</p>
              <details v-if="receipt" class="confirmed-coordinates">
                <summary>控制端已确认 {{ receipt.points.length }} 个最终坐标</summary>
                <div v-for="(p,index) in receipt.points" :key="`${p.side}:${p.name}`">
                  {{ index + 1 }} {{ toPointLabel(p) }}：{{ p.base_point_mm.map(v => v.toFixed(1)).join(' / ') }} mm
                </div>
                <small>机械臂基座坐标 X / Y / Z · 表面点</small>
              </details>
            </template>
            <p v-if="needsSiteAuthorization" class="confirm-desc" role="status">假人/工装；沿用现有标定；现场确认当前路线无障碍；固定姿态，10 mm/s 指令，无接触。</p>
            <p v-if="startBlocker && startBlocker !== photoStatusBlocker && startBlocker !== coordinateError && !noncontactCompleted" class="confirm-desc" role="status">{{ startBlocker }}</p>
            <button v-if="noncontactCompleted" class="btn-primary" type="button" :disabled="!canContinue" @click="continueUsing">
              继续使用 · 重新扫描
            </button>
            <button
              v-else
              class="btn-primary"
              type="button"
              :disabled="!canStart"
              :title="startBlocker"
              @click="requestStart"
            >
              {{ noncontact ? (routeReady ? '开始无接触运动' : needsSiteAuthorization ? '确认现场无障碍并准备路线' : '准备无接触路线') : '确认路线并开始' }}
            </button>
          </div>
        </div>
      </aside>
    </div>

    <AppConfirm
      v-if="showStartModal"
      :title="noncontact ? '确认开始无接触运动？' : '确认开始服务？'"
      :description="noncontact ? '请核对上方工具尖端和法兰目标。仅在假人或工装上执行，不加热、不输出按摩力；可随时点击停止。' : '路线与参数已确认。点击开始后设备将进入服务流程。'"
      cancel-text="返回调整"
      confirm-text="开始"
      tone="brand"
      width="440px"
      @cancel="showStartModal = false"
      @confirm="startService"
    />
  </section>
</template>

<style scoped lang="scss">
.coordinate-confirm-btn { margin-bottom: 8px; border: 1px solid var(--brand); color: var(--brand); background: #fff4ec; border-radius: 12px; cursor: pointer; }
.coordinate-confirm-btn:disabled { opacity: .5; cursor: default; }
.confirmed-coordinates { font-size: 12px; color: #277450; margin-bottom: 8px; line-height: 1.7; max-height: 110px; overflow: auto; }
.confirmed-coordinates summary { cursor: pointer; }
.confirmed-coordinates div { color: var(--ink); }
.confirm-screen {
  max-width: 1200px;
  margin: 0 auto;
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.confirm-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  margin-bottom: 12px;
  flex-shrink: 0;
  gap: 12px;
}

.confirm-title {
  margin: 0;
  font-size: 20px;
  font-weight: 600;
  color: var(--ink);
}

.confirm-desc {
  margin: 4px 0 0;
  font-size: 14px;
  color: #64748b;
}

.rescan-btn {
  padding: 8px 16px;
  border-radius: 12px;
  border: 1px solid var(--line);
  background: #fff;
  font-size: 14px;
  color: var(--ink);
  cursor: pointer;
}

.rescan-btn:hover {
  border-color: rgba(255, 106, 26, 0.4);
  color: var(--brand);
}

.confirm-layout {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: minmax(0, 1.25fr) 300px;
  gap: 14px;
  align-items: stretch;
}

.confirm-body-card {
  padding: 10px;
  display: flex;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
}

.confirm-side {
  padding: 16px;
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.confirm-side-body {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.side-top {
  flex-shrink: 0;
}

.plan-name {
  margin: 0;
  font-size: 17px;
  font-weight: 600;
  color: var(--ink);
}

.benefit-card {
  margin-top: 12px;
  padding: 16px;
  border-radius: 12px;
  background: #fff8f2;
  border: 1px solid rgba(255, 140, 80, 0.22);
}

.benefit-head {
  color: var(--brand);
  font-size: 14px;
  font-weight: 600;
}

.benefit-text {
  margin: 10px 0 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--ink);
}

.benefit-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 12px;
}

.tag {
  padding: 4px 8px;
  border-radius: 999px;
  background: #fff;
  font-size: 11px;
  color: var(--brand);
  border: 1px solid rgba(255, 140, 80, 0.25);
}

.params {
  margin-top: auto;
  padding-top: 16px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.param-field {
  display: block;
  font-size: 14px;
}

.param-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.param-value {
  color: var(--brand);
  font-weight: 500;
}

.param-ends {
  display: flex;
  justify-content: space-between;
  margin-top: 6px;
  font-size: 12px;
  color: var(--muted);
}

.heacon-range {
  margin-top: 10px;
}

.side-actions {
  margin-top: 16px;
  flex-shrink: 0;
}

.side-actions button {
  width: 100%;
  padding: 12px;
}
</style>
