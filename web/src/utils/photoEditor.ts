import type { ProtocolPoint, RobotStatus } from '@/types/robot'

export type Capture = NonNullable<RobotStatus['scan_snapshot']>
export interface PhotoAdjustment extends ProtocolPoint { x: number; y: number }
export interface PhotoPoint extends PhotoAdjustment { coordinate_valid: boolean }
export interface PhotoData {
  frame_number: number
  received_monotonic_s: number
  width: number
  height: number
  points: PhotoPoint[]
}
export const pointKey = (p: ProtocolPoint) => `${p.side}:${p.name}`

/** Reject old captures, out-of-frame pixels and ambiguous point identities. */
export function parsePhotoData(raw: unknown, capture: Capture): PhotoData {
  const data = raw as PhotoData | null
  if (!data || data.frame_number !== capture.frame_number || data.received_monotonic_s !== capture.received_monotonic_s
    || !Number.isInteger(data.width) || !Number.isInteger(data.height) || data.width < 1 || data.height < 1
    || !Array.isArray(data.points)) throw new Error('照片与本次扫描不匹配，请重新扫描')
  const seen = new Set<string>()
  for (const p of data.points) {
    if (!p || typeof p.name !== 'string' || !['LEFT', 'RIGHT', 'MIDLINE'].includes(p.side)
      || typeof p.coordinate_valid !== 'boolean' || !Number.isFinite(p.x) || !Number.isFinite(p.y)
      || p.x < 0 || p.x >= data.width || p.y < 0 || p.y >= data.height || seen.has(pointKey(p))) {
      throw new Error('本次照片的点位数据无效，请重新扫描')
    }
    seen.add(pointKey(p))
  }
  if (capture.available_points.some(p => !data.points.some(q => pointKey(q) === pointKey(p) && q.coordinate_valid))) {
    throw new Error('照片点位与扫描结果不一致，请重新扫描')
  }
  return data
}

export function clampPixel(x: number, y: number, width: number, height: number) {
  return { x: Math.round(Math.max(0, Math.min(width - 1, x)) * 10) / 10,
    y: Math.round(Math.max(0, Math.min(height - 1, y)) * 10) / 10 }
}

/** Do not bridge across a missing route point or invent a substitute. */
export function routeSegments(points: Array<PhotoAdjustment | null>) {
  return points.flatMap((p, i) => {
    const q = points[i + 1]
    return p && q ? [{ from: p, to: q }] : []
  })
}
