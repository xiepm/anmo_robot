import type { IconName } from '@/icons'
import type { Region } from '@/types/robot'

/** 预设服务方案 */
export interface ServicePlan {
  id: string
  code: string
  name: string
  type: '艾灸' | '按摩'
  /** 协议部位 */
  region: Region
  /** 艾灸工作间隙（毫米），固定为 30；按摩不设置 */
  gapMm?: number
  duration: string
  params: string
  route: string
  method: string
  icon: IconName
  benefit: string
  benefitPoints: string[]
}

export const servicePlans: ServicePlan[] = [
  {
    id: 'p1',
    code: 'P-01',
    name: '肩颈温阳灸',
    type: '艾灸',
    region: 'BACK',
    gapMm: 30,
    duration: '30 / 35 / 40 分钟',
    params: '40–50℃ · 灸距 3–5 cm',
    route: '大椎 → 左肩井 → 右肩井',
    method: '回旋默认 / 雀啄',
    icon: 'fire',
    benefit:
      '取大椎、肩井等阳经要穴施灸，以温和艾热温通督脉与少阳经气，散寒除湿、舒筋活络。适用于风寒外袭、久坐僵凝所致的肩颈酸紧、怕冷畏风；灸感由浅入深，逐步松解筋结，助气血循行更畅，肩背更暖、转动更轻松。施术时请俯卧放松，便于经气顺达。',
    benefitPoints: ['温通督脉少阳', '散寒舒筋活络', '减轻肩颈畏冷'],
  },
  {
    id: 'p2',
    code: 'P-02',
    name: '温肾祛寒灸',
    type: '艾灸',
    region: 'BACK',
    gapMm: 30,
    duration: '30 / 35 / 40 分钟',
    params: '40–50℃ · 灸距 4–6 cm',
    route: '命门 → 双侧肾俞 → 腰阳关 → 双侧大肠俞',
    method: '悬停 + 往复扫描',
    icon: 'flame',
    benefit:
      '聚焦命门、肾俞、腰阳关等补肾壮阳之穴，艾灸温补命门之火，祛散腰府寒湿，培元固本。适合腰骶发凉、久坐疲惫、肾气不足之象；热力沿膀胱经与督脉循行，助阳气升发、腰脊得养，缓解酸软沉重。请保持呼吸平和、腰背贴床，以利灸感透达深层。',
    benefitPoints: ['温补命门肾气', '祛散腰府寒湿', '缓解酸软疲惫'],
  },
  {
    id: 'p3',
    code: 'P-03',
    name: '肩颈放松按摩',
    type: '按摩',
    region: 'BACK',
    duration: '15 / 20 / 25 分钟',
    params: '10 / 15 / 20 N · 高频点按',
    route: '左风池 → 左肩井 → 左天宗 → 右风池 → 右肩井 → 右天宗',
    method: '高频点按 + 向心推滑',
    icon: 'handShake',
    benefit:
      '循足少阳胆经与手太阳小肠经走向，点按风池、肩井、天宗等要穴，配合向心推法疏通经络、松解筋结。针对低头久坐、气滞血瘀之肩颈板紧与酸胀；以柔中带刚的手法调和气血，令经脉通畅、筋骨舒展，头颈转动更轻快。施术中请面部入托、肩颈勿对抗，便于经气畅行。',
    benefitPoints: ['疏通少阳太阳', '松解筋结瘀滞', '改善肩颈板紧'],
  },
  {
    id: 'p4',
    code: 'P-04',
    name: '腰背舒缓按摩',
    type: '按摩',
    region: 'BACK',
    duration: '15 / 20 / 25 分钟',
    params: '10 / 15 / 20 N · 揉压点按',
    route: '腰背穴位序列 · 按顺序执行',
    method: '揉压 + 点按',
    icon: 'body',
    benefit:
      '沿足太阳膀胱经腰段与督脉附近穴位依次揉压点按，疏导腰背经络，松筋解结、活血化瘀。适用于久坐劳损、寒湿凝滞所致的腰脊酸痛与僵硬；手法由轻入深，兼顾舒适与通经效果，帮助腰肌得养、气机舒畅。请俯卧居中、腰腹放松；若局部敏感，可由技师微调力度，使补泻得宜。',
    benefitPoints: ['疏导膀胱经气', '松筋活血化瘀', '缓解腰脊僵痛'],
  },
]

export function getPlanById(id: string) {
  return servicePlans.find((p) => p.id === id)
}
