/** Resolve explicitly configured same-origin paths without changing local defaults. */
const LOCAL_CONTROL = 'ws://127.0.0.1:8765'
const LOCAL_PREVIEW = 'http://127.0.0.1:8767/frame.jpg'

function browserUrl(pageUrl?: string) {
  return pageUrl ?? globalThis.location?.href
}

export function robotWebSocketUrl(configured?: string, pageUrl?: string): string {
  if (!configured) return LOCAL_CONTROL
  const url = new URL(configured, browserUrl(pageUrl))
  if (url.protocol === 'https:') url.protocol = 'wss:'
  else if (url.protocol === 'http:') url.protocol = 'ws:'
  if (!['ws:', 'wss:'].includes(url.protocol)) throw new Error('控制地址必须使用 ws/wss 或同源路径')
  return url.href
}

export function visionResourceUrl(configured?: string, resource?: string, pageUrl?: string): URL {
  const frame = new URL(configured || LOCAL_PREVIEW, browserUrl(pageUrl))
  if (!['http:', 'https:'].includes(frame.protocol)) throw new Error('视觉地址必须使用 http/https 或同源路径')
  // A /vision/frame.jpg proxy must retain /vision/ for frozen-photo resources.
  return resource ? new URL(resource.replace(/^\/+/, ''), frame) : frame
}
