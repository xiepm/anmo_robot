/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_APP_TITLE: string
  readonly VITE_API_BASE_URL: string
  /** 下位机 ws/wss 地址或同源路径（例如 /control） */
  readonly VITE_ROBOT_WS_URL: string
  /** 预览帧 http/https 地址或同源路径（例如 /vision/frame.jpg） */
  readonly VITE_VISION_PREVIEW_URL: string
  /** 显式构建为无相机、无实机的模拟体验 */
  readonly VITE_DEMO_MODE: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
