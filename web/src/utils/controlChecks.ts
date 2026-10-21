import type { PlanData, ReplyMessage, RobotStatus, Region } from '@/types/robot'

export function planBlockReason(plan: PlanData | null, status: RobotStatus | null, online: boolean) {
  if (!online) return '控制连接断开或状态已过期'
  if (!status?.robot_connected || !status.robot_feedback_valid) return '机械臂反馈无效'
  if (!plan) return '尚未选择路线'
  if (!['IDLE', 'READY'].includes(status.state)) return status.error || '设备尚未准备就绪'
  if (!status.positioning_valid || status.region !== plan.region) return status.scan_snapshot?.execution_blocker || '当前部位定位无效，请重新扫描'
  const missing = plan.points.filter(p => !status.available_points.some(q => q.name === p.name && q.side === p.side))
  if (missing.length) return `本次定位缺少：${missing.map(p => `${p.side === 'LEFT' ? '左' : p.side === 'RIGHT' ? '右' : ''}${p.name}`).join('、')}`
  if (!plan.points.length || !Number.isInteger(plan.total_time_s) || plan.total_time_s <= 0 || plan.total_time_s % plan.points.length) {
    return '总时长必须能按路线穴位数均分'
  }
  return ''
}

type ScanOutcome = { done: boolean; error: string }

/** Record every STATUS synchronously; never promote an old IDLE or a REPLY to success. */
export class ScanTracker {
  private region: Region
  private scanning = false
  private accepted = false
  private canceled = false
  private latest: RobotStatus | null = null
  constructor(region: Region) { this.region = region }
  cancel() { this.canceled = true }
  observe(status: RobotStatus) {
    if (status.state === 'SCANNING') this.scanning = true
    this.latest = status
    return this.outcome()
  }
  acknowledge(reply: ReplyMessage): ScanOutcome {
    if (this.canceled) return { done: false, error: '' }
    if (!reply.ok) return { done: false, error: reply.message }
    this.accepted = true
    return this.outcome()
  }
  private outcome(): ScanOutcome {
    const s = this.latest
    if (this.canceled || !this.accepted || !s) return { done: false, error: '' }
    if (s.state === 'ERROR') return { done: false, error: s.error || '设备报告故障' }
    if (!this.scanning || s.state === 'SCANNING') return { done: false, error: '' }
    const photo = s.scan_snapshot
    if (s.state === 'IDLE' && photo?.region === this.region && photo.available_points.length && s.robot_feedback_valid) return { done: true, error: '' }
    if (s.state === 'IDLE' && s.positioning_valid && s.region === this.region && s.robot_feedback_valid && s.available_points.length) {
      return { done: true, error: '' }
    }
    return { done: false, error: s.error || '定位失败或已失效，请重新扫描' }
  }
}
