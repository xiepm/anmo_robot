/**
 * 下位机通讯链路
 * 维护本机 WebSocket 连接、定时巡查、状态过期判定与命令收发
 * 协议无请求编号：普通控制命令串行等待回复，STOP 可随时打断，被打断命令的迟到回复直接丢弃
 */
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
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
    socket = new WebSocket(WS_URL)
    socket.onopen = () => {
      connected.value = true
      // 初始连接与重连均先读取状态，不重发旧方案或 START
      send('GET_STATUS')
    }
    socket.onmessage = (event) => handleMessage(JSON.parse(event.data) as RobotMessage)
    socket.onclose = () => {
      connected.value = false
      socket = null
      for (const cmd of [...waiters.keys()]) settle(cmd, localFail(cmd, '与下位机的连接已断开'))
      setTimeout(open, RECONNECT_MS)
    }
  }

  function handleMessage(msg: RobotMessage) {
    if (msg.type === 'STATUS') {
      status.value = msg.data
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
        settle(cmd, localFail(cmd, '下位机回复超时，请以设备状态为准'))
        send('GET_STATUS')
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
    if (!connected.value) return Promise.resolve(localFail(cmd, '下位机未连接'))
    if (pendingCmd.value) return Promise.resolve(localFail(cmd, '上一条命令尚未回复'))
    pendingCmd.value = cmd
    return waitReply(cmd, data)
  }

  /** 停止工作或取消扫描；回复成功仅表示受理，结果以后续 STATUS 为准 */
  function stop(): Promise<ReplyMessage> {
    if (!connected.value) return Promise.resolve(localFail('STOP', '下位机未连接'))
    if (pendingCmd.value) settle(pendingCmd.value, localFail(pendingCmd.value, '已被停止请求打断'))
    return waitReply('STOP', {})
  }

  return {
    connected,
    status,
    stale,
    pendingCmd,
    online,
    deviceHealthy,
    start,
    request,
    stop,
  }
})
