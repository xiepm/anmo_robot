import type { ProtocolPoint } from '@/types/robot'

/** 方案穴位：界面标签 + 示意坐标，坐标仅用于界面展示，不发送给下位机 */
export type Acupoint = [name: string, x: number, y: number]

export const pointSets: Record<string, Acupoint[]> = {
  p1: [
    ['大椎', 270, 250],
    ['左肩井', 155, 275],
    ['右肩井', 385, 275],
  ],
  p2: [
    ['命门', 270, 445],
    ['左肾俞', 195, 435],
    ['右肾俞', 345, 435],
    ['腰阳关', 270, 515],
    ['左大肠俞', 185, 530],
    ['右大肠俞', 355, 530],
  ],
  p3: [
    ['左风池', 220, 235],
    ['左肩井', 155, 275],
    ['左天宗', 180, 350],
    ['右风池', 320, 235],
    ['右肩井', 385, 275],
    ['右天宗', 360, 350],
  ],
  p4: [
    ['命门', 270, 445],
    ['左肾俞', 195, 435],
    ['右肾俞', 345, 435],
    ['腰阳关', 270, 515],
    ['左大肠俞', 185, 530],
    ['右大肠俞', 355, 530],
  ],
}

/** 示意坐标系宽高，用于换算百分比 */
export const POINT_FRAME = { width: 540, height: 720 }

/** 界面标签转协议穴位：「左」「右」前缀转为侧别，其余视为中线 */
export function toProtocolPoint(label: string): ProtocolPoint {
  if (label.startsWith('左')) return { name: label.slice(1), side: 'LEFT' }
  if (label.startsWith('右')) return { name: label.slice(1), side: 'RIGHT' }
  return { name: label, side: 'MIDLINE' }
}

/** 协议穴位转界面标签，与 pointSets 命名保持一致 */
export function toPointLabel(point: ProtocolPoint) {
  if (point.side === 'LEFT') return `左${point.name}`
  if (point.side === 'RIGHT') return `右${point.name}`
  return point.name
}

/** Match by name AND anatomical side; preserve the selected route order. */
export function selectRecognizedPoints(route: ProtocolPoint[], available: ProtocolPoint[]) {
  const has = (point: ProtocolPoint) => available.some(p => p.name === point.name && p.side === point.side)
  return { recognized: route.filter(has), missing: route.filter(p => !has(p)) }
}
