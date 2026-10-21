/**
 * 服务流程状态
 * FlowStage: 0 选择方案 / 1 人体定位 / 2 开始工作
 */
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { getPlanById } from '@/data/plans'
import { pointSets, toProtocolPoint, type Acupoint } from '@/data/points'
import type { PlanData } from '@/types/robot'
import {
  createRecordId,
  loadRecords,
  saveRecords,
  type ServiceRecord,
} from '@/types/record'

/** 流程阶段 */
export type FlowStage = 0 | 1 | 2

/** 方案选择模式 */
export type PlanMode = 'ai' | 'manual' | null

/** 服务运行参数：时长为分钟，level 为温度℃或力度 N */
export interface ServiceParams {
  duration: number
  level: number
}

/** 收尾摘要 */
export interface FinishSummary {
  planName: string
  durationText: string
  statusText: string
  endedEarly: boolean
  paramLabel: string
  abnormal: string
}

const ROUTE_STAGE: Record<string, FlowStage> = {
  'service-start': 0,
  'service-survey': 0,
  'service-plan': 0,
  'service-position': 1,
  'service-scan': 1,
  'service-confirm': 1,
  'service-run': 2,
  'service-abnormal': 2,
  'service-finish': 2,
}

/** 与确认页滑动条一致：温度 40–50℃，力度 10–20 N，步进均为 1 */
export function levelRange(isMassage: boolean) {
  return isMassage ? { min: 10, max: 20 } : { min: 40, max: 50 }
}

export const useFlowStore = defineStore('flow', () => {
  /** 当前流程阶段 */
  const currentStage = ref<FlowStage>(0)
  /** 已解锁的最高阶段 */
  const unlockedStage = ref<FlowStage>(0)
  /** 方案选择模式 */
  const planMode = ref<PlanMode>(null)
  /** 问卷是否完成 */
  const surveyComplete = ref(false)
  /** 方案是否已确认 */
  const planConfirmed = ref(false)
  /** 扫描是否完成 */
  const scanComplete = ref(false)
  /** 路线是否已确认 */
  const routeConfirmed = ref(false)
  /** 服务是否已开始 */
  const serviceStarted = ref(false)
  /** 当前选中方案 ID */
  const selectedPlanId = ref('p1')
  /** 问卷答案，按题目下标存储 */
  const surveyAnswers = ref<string[]>(Array(6).fill(''))
  /** 本次服务参数 */
  const serviceParams = ref<ServiceParams>({
    duration: 35,
    level: 45,
  })
  /** 可调整后的穴位坐标 */
  const adjustedPoints = ref<Acupoint[]>([])
  /** 运行总秒数 */
  const totalSec = ref(0)
  /** 运行剩余秒数 */
  const remainSec = ref(0)
  /** 异常详情 */
  const abnormalDetail = ref('请检查设备与服务对象的位置')
  /** 异常时间文案 */
  const abnormalTimeText = ref('')
  /** 异常处理后的恢复路由 */
  const abnormalResumeRoute = ref('service-run')
  /** 收尾摘要 */
  const finishSummary = ref<FinishSummary | null>(null)
  /** 服务记录返回目标 */
  const returnRouteName = ref('service-start')
  /** 匿名服务记录 */
  const records = ref<ServiceRecord[]>(loadRecords())

  const selectedPlan = computed(() => getPlanById(selectedPlanId.value))
  const isMassage = computed(() => selectedPlan.value?.type === '按摩')
  const activePoints = computed(
    () =>
      adjustedPoints.value.length
        ? adjustedPoints.value
        : (pointSets[selectedPlanId.value] ?? []),
  )

  const levelLabel = computed(() => {
    const unit = isMassage.value ? ' N' : '℃'
    return `${serviceParams.value.level}${unit}`
  })

  const runProgress = computed(() => {
    if (totalSec.value <= 0) return 0
    return Math.max(0, Math.round((1 - remainSec.value / totalSec.value) * 100))
  })

  /** 按当前草稿组装 SET_PLAN 参数；示意坐标不进入协议 */
  const planRequest = computed<PlanData | null>(() => {
    const plan = selectedPlan.value
    if (!plan) return null
    const value = serviceParams.value.level
    return {
      region: plan.region,
      points: activePoints.value.map(([name]) => toProtocolPoint(name)),
      total_time_s: serviceParams.value.duration * 60,
      energy: isMassage.value
        ? { type: 'FORCE', value, unit: 'N' }
        : { type: 'TEMPERATURE', value, unit: 'C' },
      gap_mm: plan.gapMm,
      dwell_mode: 'STATIC',
      transition_mode: 'LIFT_MOVE_APPROACH',
    }
  })

  function resetPlanSelectionFlags() {
    surveyComplete.value = false
    planConfirmed.value = false
    scanComplete.value = false
    routeConfirmed.value = false
    serviceStarted.value = false
    selectedPlanId.value = 'p1'
    currentStage.value = 0
    unlockedStage.value = 0
    totalSec.value = 0
    remainSec.value = 0
    adjustedPoints.value = []
    finishSummary.value = null
    abnormalDetail.value = '请检查设备与服务对象的位置'
    abnormalTimeText.value = ''
    abnormalResumeRoute.value = 'service-run'
  }

  /** 进入 AI 问卷选方案 */
  function startAiPlan() {
    planMode.value = 'ai'
    surveyAnswers.value = Array(6).fill('')
    resetPlanSelectionFlags()
  }

  /** 进入人工选方案 */
  function startManualPlan() {
    planMode.value = 'manual'
    resetPlanSelectionFlags()
  }

  /** 完成问卷 */
  function completeSurvey(answers: string[]) {
    surveyAnswers.value = answers
    surveyComplete.value = true
  }

  /** 选择方案 */
  function selectPlan(id: string) {
    selectedPlanId.value = id
    resetAdjustedPoints()
  }

  /** 按当前方案重置穴位坐标 */
  function resetAdjustedPoints() {
    const source = pointSets[selectedPlanId.value] ?? []
    adjustedPoints.value = source.map(([name, x, y]) => [name, x, y])
  }

  /** 更新拖动后的穴位坐标 */
  function setAdjustedPoints(points: Acupoint[]) {
    adjustedPoints.value = points.map(([name, x, y]) => [name, x, y])
  }

  /** 确认方案，解锁人体定位阶段 */
  function confirmPlan() {
    planConfirmed.value = true
    unlockedStage.value = 1
    currentStage.value = 1
    initServiceParams()
  }

  /** 按当前方案初始化时长与能量，取各自范围中点 */
  function initServiceParams() {
    const massage = isMassage.value
    const range = levelRange(massage)
    serviceParams.value = {
      duration: massage ? 20 : 35,
      level: Math.round((range.min + range.max) / 2),
    }
  }

  /** 设置服务时长（分钟） */
  function setDuration(minutes: number) {
    serviceParams.value.duration = minutes
  }

  /** 设置目标温度或力度，步进 1，并限制在界面范围内 */
  function setLevel(value: number) {
    const range = levelRange(isMassage.value)
    serviceParams.value.level = Math.max(range.min, Math.min(range.max, value))
  }

  /** 标记扫描完成 */
  function completeScan() {
    scanComplete.value = true
    resetAdjustedPoints()
  }

  /** 下位机受理 START 后进入开始工作阶段 */
  function startService() {
    routeConfirmed.value = true
    serviceStarted.value = true
    unlockedStage.value = 2
    currentStage.value = 2
    totalSec.value = serviceParams.value.duration * 60
    remainSec.value = totalSec.value
  }

  /** 同步下位机计算的剩余有效秒数，UI 不独立倒计时 */
  function syncRemain(sec: number) {
    remainSec.value = sec
  }

  /** 记录异常并进入安全态 */
  function reportAbnormal(
    detail = '请检查设备与服务对象的位置',
    resumeRoute = 'service-run',
  ) {
    abnormalDetail.value = detail
    abnormalTimeText.value = new Date().toLocaleString('zh-CN', { hour12: false })
    abnormalResumeRoute.value = resumeRoute
  }

  /** 结束服务并生成收尾摘要 */
  function finishService(endedEarly: boolean, abnormal = '无异常') {
    const elapsedMin = Math.max(
      1,
      Math.round((totalSec.value - remainSec.value) / 60),
    )
    const durationMin = endedEarly ? elapsedMin : serviceParams.value.duration
    finishSummary.value = {
      planName: selectedPlan.value?.name ?? '—',
      durationText: `${durationMin} 分钟`,
      statusText: endedEarly ? '提前结束 · 待归档' : '已完成 · 待归档',
      endedEarly,
      paramLabel: levelLabel.value,
      abnormal,
    }
    serviceStarted.value = false
  }

  /** 归档匿名服务记录并重置流程 */
  function archiveService(feeling: string, note: string) {
    const summary = finishSummary.value
    const plan = selectedPlan.value
    if (!summary || !plan) {
      resetPlanSelectionFlags()
      planMode.value = null
      return
    }

    const record: ServiceRecord = {
      id: createRecordId(),
      time: new Date().toLocaleString('zh-CN', { hour12: false }),
      type: plan.type,
      plan: summary.planName,
      duration: summary.durationText,
      param: summary.paramLabel,
      status: summary.endedEarly ? '提前结束' : '已完成',
      feeling,
      abnormal: summary.abnormal,
      note,
    }
    records.value = [record, ...records.value].slice(0, 100)
    saveRecords(records.value)
    resetPlanSelectionFlags()
    planMode.value = null
  }

  /** 是否允许进入指定路由 */
  function canEnter(routeName: string) {
    const rules: Record<string, boolean> = {
      'service-survey': planMode.value === 'ai',
      'service-plan': surveyComplete.value || planMode.value === 'manual',
      'service-position': planConfirmed.value,
      'service-scan': planConfirmed.value,
      'service-confirm': scanComplete.value,
      'service-run': routeConfirmed.value && serviceStarted.value,
      'service-abnormal': serviceStarted.value || Boolean(abnormalTimeText.value),
      'service-finish': Boolean(finishSummary.value),
    }
    if (!(routeName in rules)) return true
    return rules[routeName]
  }

  /** 未满足前置步骤时的提示 */
  function lockedMessage(routeName: string) {
    if (['service-position', 'service-scan', 'service-confirm'].includes(routeName)) {
      return '请先完成问卷并确认服务方案'
    }
    if (['service-run', 'service-abnormal'].includes(routeName)) {
      return '请先完成人体定位并确认路线'
    }
    if (routeName === 'service-finish') return '当前没有待归档的服务'
    if (routeName === 'service-plan') return '请先完成问卷，或从首页选择人工选方案'
    if (routeName === 'service-survey') return '请从首页进入 AI 生成方案'
    return '请按当前页面顺序完成前置步骤'
  }

  /** 打开服务记录前记录返回目标 */
  function prepareRecordsReturn(fromRouteName?: string | null) {
    if (fromRouteName === 'service-scan') {
      returnRouteName.value = 'service-position'
      return
    }
    if (fromRouteName && fromRouteName !== 'service-records') {
      returnRouteName.value = fromRouteName
      return
    }
    returnRouteName.value = 'service-start'
  }

  /** 解析服务记录返回目标 */
  function resolveRecordsReturn() {
    const target = returnRouteName.value || 'service-start'
    if (target === 'service-finish' && finishSummary.value) return target
    if (canEnter(target)) return target
    return 'service-start'
  }

  /** 根据路由同步侧栏当前阶段展示 */
  function syncStageByRoute(routeName: string) {
    if (routeName === 'service-records') return
    const stage = ROUTE_STAGE[routeName]
    if (stage != null) currentStage.value = stage
  }

  return {
    currentStage,
    unlockedStage,
    planMode,
    surveyComplete,
    planConfirmed,
    scanComplete,
    routeConfirmed,
    serviceStarted,
    selectedPlanId,
    surveyAnswers,
    serviceParams,
    totalSec,
    remainSec,
    abnormalDetail,
    abnormalTimeText,
    abnormalResumeRoute,
    finishSummary,
    returnRouteName,
    records,
    selectedPlan,
    isMassage,
    activePoints,
    levelLabel,
    runProgress,
    planRequest,
    startAiPlan,
    startManualPlan,
    completeSurvey,
    selectPlan,
    confirmPlan,
    initServiceParams,
    setDuration,
    setLevel,
    completeScan,
    resetAdjustedPoints,
    setAdjustedPoints,
    startService,
    syncRemain,
    reportAbnormal,
    finishService,
    archiveService,
    resetPlanSelectionFlags,
    canEnter,
    lockedMessage,
    prepareRecordsReturn,
    resolveRecordsReturn,
    syncStageByRoute,
  }
})
