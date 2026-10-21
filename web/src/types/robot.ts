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
export type Command = 'GET_STATUS' | 'SCAN' | 'SET_PLAN' | 'START' | 'STOP' | 'CONFIRM_POINTS' | 'CLEAR_POINTS' | 'AUTHORIZE_NONCONTACT' | 'PREPARE_NONCONTACT' | 'START_NONCONTACT' | 'CHECK_NONCONTACT_STOP'

/** 需等待 REPLY 且同一时刻仅允许一条的普通控制命令 */
export type ControlCommand = 'SCAN' | 'SET_PLAN' | 'START' | 'CONFIRM_POINTS' | 'CLEAR_POINTS' | 'AUTHORIZE_NONCONTACT' | 'PREPARE_NONCONTACT' | 'START_NONCONTACT' | 'CHECK_NONCONTACT_STOP'

/** 穴位：名称 + 侧别，不含坐标与编号 */
export interface ProtocolPoint {
  name: string
  side: Side
}

/** SET_PLAN 方案参数 */
export interface PlanData {
  confirmation_revision?: string
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
  noncontact?: {
    kind: 'NONCONTACT_COMMISSIONING'
    available: boolean
    authorization_available?: boolean
    blockers: string[]
    active: boolean
    phase: string
    returning_to_start?: boolean
    returned_to_start?: boolean
    revision: string | null
    plan_sha256: string | null
    stop_confirmed: boolean
    targets: Array<ProtocolPoint & { surface_base_mm: number[]; tool_tip_base_mm: number[]; flange_base_mm: number[] }>
    error: string
    note: string
    stop_preflight?: {
      state: 'NONE' | 'CHECKING' | 'PASSED' | 'FAILED'
      check_id: string
      stop_command_sent: boolean
      stop_confirmed: boolean
      checked_monotonic_s: number | null
      samples_after_stop: number
      start_pose: number[] | null
      final_pose: number[] | null
      reason: string
      execution_eligible: false
    }
    route_review?: {
      state: 'CHECKING' | 'BLOCKED'
      revision: string
      frame_number: number
      execution_eligible: false
      reason: string
      blockers: string[]
      targets: Array<ProtocolPoint & { surface_base_mm: number[]; tool_tip_base_mm: number[]; flange_base_mm: number[] }>
      waypoint_count: number
      sample_count: number
      model_position_error_mm: number | null
      model_rotation_error_deg: number | null
      model_check_state?: 'NOT_CHECKED' | 'DEFERRED' | 'CONSISTENT' | 'MISMATCH' | 'UNAVAILABLE'
      model_check_note?: string
      kinematics_checked: boolean
      self_collision_checked: boolean
      observed_collision_checked: boolean
      full_collision_checked: false
      transfer_height_mm: number | null
      unknown_cells: number
      depth_visibility?: {
        assessment: 'UNAVAILABLE' | 'UNRESOLVED' | 'OBSERVED_CLEAR' | 'NOT_REQUIRED'
        cell_count: number
        unresolved_cells: number
        checked_intervals: number
        minimum_depth_margin_mm: number | null
        required_depth_margin_mm: number
        reason: string
      }
      unknown_route_clearance?: {
        assessment: 'INCOMPLETE' | 'NO_UNKNOWN_REPORTED' | 'CENTERLINE_NEAR_UNKNOWN' | 'UNKNOWN_OFF_CENTERLINE'
        unknown_cells: number
        checked_cells: number
        all_unknown_cells_included: boolean
        minimum_xy_distance_lower_bound_mm: number | null
        nearest_cell_base_xy_mm: number[] | null
        possibly_intersecting_cells: number | null
        execution_eligible: false
      }
      attempts: Array<{ transfer_height_mm: number; samples: number; error: string }>
    }
    scene_analysis?: {
      revision: string
      frame_number: number
      state: 'NO_DATA' | 'PARTIAL' | 'OBSERVED'
      cell_size_mm: number
      corridor_radius_mm: number
      valid_depth_pixels: number
      image_pixels: number
      corridor_cells: number
      unknown_cells: number
      coverage_diagnostics?: {
        incomplete_quadrant_cells: number
        invalid_ray_cells: number
        overlapping_cause_cells: number
        note: string
      }
      surface_ceiling_base_mm: number | null
      safe_transfer_height_base_mm: number | null
      workspace_base_mm: { min: number[]; max: number[] } | null
      collision_checked: false
      execution_eligible: false
    }
  }
  mode: 'SIMULATION' | 'REAL'
  robot_connected: boolean
  robot_feedback_valid: boolean
  /** Live vision readiness; frozen photo existence is reported separately. */
  vision_feedback_valid?: boolean
  state: RobotState
  step: RobotStep
  region: Region | null
  positioning_valid: boolean
  /** 单次拍照结果；与可执行定位分开，不能据此发送 START。 */
  scan_snapshot?: {
    region: Region
    frame_number: number
    received_monotonic_s: number
    available_points: ProtocolPoint[]
    execution_eligible: false
    execution_blocker: string
  }
  point_confirmation?: {
    revision: string
    frame_number: number
    received_monotonic_s: number
    state: 'PENDING' | 'CONFIRMED' | 'FAILED'
    requested_points: Array<ProtocolPoint & { x: number; y: number }>
    points: Array<ProtocolPoint & { x: number; y: number; camera_point_mm: number[]; base_point_mm: number[] }>
    error: string
    execution_eligible: false
  }
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
