/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_APP_TITLE: string
  readonly VITE_API_BASE_URL: string
  /** 下位机 WebSocket 地址，协议约定为 ws://127.0.0.1:8765 */
  readonly VITE_ROBOT_WS_URL: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
