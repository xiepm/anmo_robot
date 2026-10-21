/**
 * 下位机通讯链路
 * 维护本机 WebSocket 连接、定时巡查、状态过期判定与命令收发
 * 协议无请求编号：普通控制命令串行等待回复，STOP 可随时打断，被打断命令的迟到回复直接丢弃
 */
import { defineStore } from 'pinia'
import { computed, ref, watch } from 'vue'
import type {
  Command,
  ControlCommand,
  ReplyMessage,
  RobotMessage,
  RobotStatus,
} from '@/types/robot'

const WS_URL = import.meta.env.VITE_ROBOT_WS_URL || 'ws://127.0.0.1:8765'
/** GET_STATUS 周期，兼作 UI 存活通知 */
const POLL_INTERVAL_MS = 1000
/** 状态新鲜度巡查周期 */
const STALE_CHECK_MS = 200
/** 超过该时长未收到 STATUS 即视为过期 */
const STALE_AFTER_MS = 1000
/** 等待 REPLY 的上限；超时后改为查询状态，不自动重试 */
const REPLY_TIMEOUT_MS = 3000
/** 断线后重连间隔 */
const RECONNECT_MS = 1000

function localFail(cmd: Command, message: string): ReplyMessage {
  return { type: 'REPLY', cmd, ok: false, message, data: {} }
}

export const useRobotStore = defineStore('robot', () => {
  /** WebSocket 是否已打开 */
  const connected = ref(false)
  /** 最近一次收到的完整状态 */
  const status = ref<RobotStatus | null>(null)
  /** 状态是否已过期 */
  const stale = ref(true)
  const statusRevision = ref(0)
  const commandEpoch = ref(0)
  const pendingStop = ref(false)
  /** 正在等待回复的普通控制命令 */
  const pendingCmd = ref<ControlCommand | null>(null)

  let socket: WebSocket | null = null
  let started = false
  let lastStatusAt = 0
  /** 等待中的回复回调，按命令名匹配 */
  const waiters = new Map<Command, (reply: ReplyMessage) => void>()

  /** 通讯在线且状态新鲜 */
  const online = computed(() => connected.value && !stale.value)
  /** 通讯在线且设备连接与反馈均有效 */
  const deviceHealthy = computed(
    () =>
      online.value &&
      Boolean(status.value?.robot_connected && status.value.robot_feedback_valid),
  )

  /** 启动连接与定时巡查，应用生命周期内调用一次 */
  function start() {
    if (started) return
    started = true
    open()
    setInterval(() => {
      if (connected.value) send('GET_STATUS')
    }, POLL_INTERVAL_MS)
    setInterval(() => {
      stale.value = !connected.value || performance.now() - lastStatusAt > STALE_AFTER_MS
    }, STALE_CHECK_MS)
  }

  function open() {
    const link = new WebSocket(WS_URL)
    socket = link
    link.onopen = () => {
      if (socket !== link) return
      connected.value = true
      // 初始连接与重连均先读取状态，不重发旧方案或 START
      send('GET_STATUS')
    }
    link.onmessage = (event) => {
      if (socket !== link) return
      try { handleMessage(JSON.parse(event.data) as RobotMessage) }
      catch { link.close() }
    }
    link.onclose = () => {
      if (socket !== link) return
      connected.value = false
      stale.value = true
      commandEpoch.value += 1
      socket = null
      for (const cmd of [...waiters.keys()]) settle(cmd, localFail(cmd, '与下位机的连接已断开'))
      setTimeout(open, RECONNECT_MS)
    }
  }

  function handleMessage(msg: RobotMessage) {
    if (msg.type === 'STATUS') {
      status.value = msg.data
      statusRevision.value += 1
      lastStatusAt = performance.now()
      stale.value = false
      return
    }
    if (msg.cmd) settle(msg.cmd, msg)
  }

  function send(cmd: Command, data: object = {}) {
    if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ cmd, data }))
  }

  function settle(cmd: Command, reply: ReplyMessage) {
    const resolve = waiters.get(cmd)
    if (!resolve) return
    waiters.delete(cmd)
    if (pendingCmd.value === cmd) pendingCmd.value = null
    resolve(reply)
  }

  function waitReply(cmd: Command, data: object): Promise<ReplyMessage> {
    settle(cmd, localFail(cmd, '已被新的同名请求替代'))
    return new Promise((resolve) => {
      const timer = setTimeout(() => {
        // Without request IDs, retire the connection before another command can
        // consume a late reply from this uncertain operation. Never retry motion.
        connected.value = false
        stale.value = true
        commandEpoch.value += 1
        socket?.close()
        settle(cmd, localFail(cmd, '下位机回复超时，执行状态未知，正在重新连接'))
      }, REPLY_TIMEOUT_MS)
      waiters.set(cmd, (reply) => {
        clearTimeout(timer)
        resolve(reply)
      })
      send(cmd, data)
    })
  }

  /** 发送普通控制命令，同一时刻仅允许一条等待回复 */
  function request(cmd: ControlCommand, data: object = {}): Promise<ReplyMessage> {
    if (!online.value) return Promise.resolve(localFail(cmd, '控制连接断开或状态已过期'))
    if (pendingStop.value) return Promise.resolve(localFail(cmd, '正在等待停止回复'))
    if (pendingCmd.value) return Promise.resolve(localFail(cmd, '上一条命令尚未回复'))
    pendingCmd.value = cmd
    return waitReply(cmd, data)
  }

  /** 停止工作或取消扫描；回复成功仅表示受理，结果以后续 STATUS 为准 */
  async function stop(): Promise<ReplyMessage> {
    if (!connected.value) return localFail('STOP', '下位机未连接')
    if (pendingStop.value) return localFail('STOP', '停止请求已发送，正在等待回复')
    commandEpoch.value += 1
    pendingStop.value = true
    if (pendingCmd.value) settle(pendingCmd.value, localFail(pendingCmd.value, '已被停止请求打断'))
    try { return await waitReply('STOP', {}) }
    finally { pendingStop.value = false }
  }

  function waitForPointConfirmation(revision: string, epoch: number, timeoutMs = 32000): Promise<NonNullable<RobotStatus['point_confirmation']>> {
    return new Promise((resolve, reject) => {
      let finishWatch = () => {}
      const timer = setTimeout(() => { finishWatch(); reject(new Error('控制端坐标确认超时，请查看设备状态')) }, timeoutMs)
      const check = () => {
        const result = status.value?.point_confirmation
        const error = !online.value || commandEpoch.value !== epoch ? '连接变化或停止请求已中断坐标确认'
          : status.value?.state === 'ERROR' ? status.value.error || '设备故障'
          : result?.revision === revision && result.state === 'FAILED' ? result.error : ''
        if (error) { clearTimeout(timer); finishWatch(); reject(new Error(error)) }
        else if (result?.revision === revision && result.state === 'CONFIRMED') {
          clearTimeout(timer); finishWatch(); resolve(result)
        }
      }
      finishWatch = watch([statusRevision, online, commandEpoch], check, { flush: 'sync' })
      check()
    })
  }

  function waitForState(expected: RobotStatus['state'], afterRevision: number, epoch: number, timeoutMs = 3000): Promise<RobotStatus> {
    return new Promise((resolve, reject) => {
      const inspect = () => {
        if (!online.value || commandEpoch.value !== epoch) throw new Error('连接变化或停止请求已中断当前启动流程')
        if (statusRevision.value <= afterRevision || !status.value) return null
        if (status.value.state === 'ERROR' || status.value.state === 'STOPPING') throw new Error(status.value.error || '设备未进入预期状态')
        return status.value.state === expected ? status.value : null
      }
      let finishWatch = () => {}
      const timer = setTimeout(() => { finishWatch(); reject(new Error(`未确认设备进入 ${expected}，不自动重发命令`)) }, timeoutMs)
      const check = () => {
        try {
          const value = inspect()
          if (value) { clearTimeout(timer); finishWatch(); resolve(value) }
        } catch (error) { clearTimeout(timer); finishWatch(); reject(error) }
      }
      finishWatch = watch([statusRevision, online, commandEpoch], check, { flush: 'sync' })
      check()
    })
  }

  return {
    connected,
    status,
    stale,
    pendingCmd,
    pendingStop,
    commandEpoch,
    statusRevision,
    waitForState,
    waitForPointConfirmation,
    online,
    deviceHealthy,
    start,
    request,
    stop,
  }
})
