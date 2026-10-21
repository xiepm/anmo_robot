/**
 * 上下位机 WebSocket 协议类型
 * 依据 docs/希康智能_上下位机通讯协议与状态机_V0.1.md，字段名与报文保持一致
 */

/** 人体部位 */
export type Region = 'BACK' | 'UPPER_LIMB' | 'LOWER_LIMB'

/** 侧别，按被服务者自身左右定义 */
export type Side = 'LEFT' | 'RIGHT' | 'MIDLINE'

/** 下位机主状态 */
export type RobotState = 'IDLE' | 'SCANNING' | 'READY' | 'RUNNING' | 'STOPPING' | 'ERROR'

/** 执行阶段，无动作时为 NONE */
export type RobotStep = 'NONE' | 'APPROACH' | 'DWELL' | 'RETRACT' | 'TRANSFER'

/** 结束结果，保留到下次提交方案 */
export type RunResult = 'NONE' | 'COMPLETED' | 'STOPPED' | 'FAILED'

/** 能量控制类型 */
export type EnergyType = 'TEMPERATURE' | 'FORCE'

/** 协议命令 */
export type Command = 'GET_STATUS' | 'SCAN' | 'SET_PLAN' | 'START' | 'STOP'

/** 需等待 REPLY 且同一时刻仅允许一条的普通控制命令 */
export type ControlCommand = 'SCAN' | 'SET_PLAN' | 'START'

/** 穴位：名称 + 侧别，不含坐标与编号 */
export interface ProtocolPoint {
  name: string
  side: Side
}

/** SET_PLAN 方案参数 */
export interface PlanData {
  region: Region
  /** 数组顺序即执行顺序 */
  points: ProtocolPoint[]
  /** 总有效工作秒数，需能被穴位数整除 */
  total_time_s: number
  energy: { type: EnergyType; value: number; unit: 'C' | 'N' }
  /** 艾灸必填，按摩省略 */
  gap_mm?: number
  dwell_mode: 'STATIC'
  transition_mode: 'LIFT_MOVE_APPROACH'
}

/** 下位机完整状态 */
export interface RobotStatus {
  mode: 'SIMULATION' | 'REAL'
  robot_connected: boolean
  robot_feedback_valid: boolean
  state: RobotState
  step: RobotStep
  region: Region | null
  positioning_valid: boolean
  available_points: ProtocolPoint[]
  /** TRANSFER 阶段表示即将接近的下一目标 */
  current_point: ProtocolPoint | null
  /** 由下位机计算的剩余有效秒数 */
  remaining_time_s: number
  /** 实测值；无数据时 value 为 null 且 valid 为 false，不得用目标值代替 */
  actual_energy: {
    type: EnergyType | null
    value: number | null
    unit: 'C' | 'N' | null
    valid: boolean
  }
  actual_gap_mm: number | null
  gap_valid: boolean
  result: RunResult
  error: string
}

/** 命令回复；ok 仅表示受理结果，动作进展以 STATUS 为准 */
export interface ReplyMessage {
  type: 'REPLY'
  cmd: Command | null
  ok: boolean
  message: string
  data: Record<string, unknown>
}

/** 状态上报，定时推送或响应 GET_STATUS */
export interface StatusMessage {
  type: 'STATUS'
  data: RobotStatus
}

/** 下位机发往上位机的消息 */
export type RobotMessage = ReplyMessage | StatusMessage
