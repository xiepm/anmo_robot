/** 穴位讲解摘要 */
export const pointTalk: Record<
  string,
  { meridian: string; talk: string; tips: string[] }
> = {
  大椎: {
    meridian: '督脉要穴',
    talk: '可疏风散寒、温通阳气，对肩颈僵紧与怕冷尤为常用。',
    tips: ['温通督脉', '疏风散寒'],
  },
  左肩井: {
    meridian: '足少阳胆经',
    talk: '有助于缓解肩颈板紧、促进局部气血流通。',
    tips: ['舒筋活络', '放松肩颈'],
  },
  右肩井: {
    meridian: '足少阳胆经',
    talk: '有助于缓解肩颈板紧、促进局部气血流通。',
    tips: ['舒筋活络', '放松肩颈'],
  },
  命门: {
    meridian: '督脉要穴',
    talk: '温补命门之火，适合腰府发凉、久坐疲惫之象。',
    tips: ['温补命门', '培元固本'],
  },
  左肾俞: {
    meridian: '足太阳膀胱经',
    talk: '可温肾助阳、缓解腰骶酸软沉重。',
    tips: ['温肾助阳', '缓解酸软'],
  },
  右肾俞: {
    meridian: '足太阳膀胱经',
    talk: '可温肾助阳、缓解腰骶酸软沉重。',
    tips: ['温肾助阳', '缓解酸软'],
  },
  腰阳关: {
    meridian: '督脉要穴',
    talk: '有助于温通腰脊、祛散寒湿凝滞。',
    tips: ['温通腰脊', '祛散寒湿'],
  },
  左大肠俞: {
    meridian: '足太阳膀胱经',
    talk: '可疏导腰背经络，减轻僵痛不适。',
    tips: ['疏导经络', '缓解僵痛'],
  },
  右大肠俞: {
    meridian: '足太阳膀胱经',
    talk: '可疏导腰背经络，减轻僵痛不适。',
    tips: ['疏导经络', '缓解僵痛'],
  },
  左风池: {
    meridian: '足少阳胆经',
    talk: '有助于缓解头颈紧张、疏通上部经气。',
    tips: ['疏通经气', '放松头颈'],
  },
  右风池: {
    meridian: '足少阳胆经',
    talk: '有助于缓解头颈紧张、疏通上部经气。',
    tips: ['疏通经气', '放松头颈'],
  },
  左天宗: {
    meridian: '手太阳小肠经',
    talk: '可松解肩胛筋结，改善肩背板紧感。',
    tips: ['松解筋结', '改善板紧'],
  },
  右天宗: {
    meridian: '手太阳小肠经',
    talk: '可松解肩胛筋结，改善肩背板紧感。',
    tips: ['松解筋结', '改善板紧'],
  },
  左腰背: {
    meridian: '足太阳膀胱经腰段',
    talk: '沿腰背经络揉压点按，帮助松筋活血。',
    tips: ['松筋活血', '缓解酸痛'],
  },
  右腰背: {
    meridian: '足太阳膀胱经腰段',
    talk: '沿腰背经络揉压点按，帮助松筋活血。',
    tips: ['松筋活血', '缓解酸痛'],
  },
}

export function formatDuration(sec: number) {
  const value = Math.max(0, Math.round(sec))
  const m = Math.floor(value / 60)
  const s = value % 60
  if (m <= 0) return `${s} 秒`
  if (s === 0) return `${m} 分`
  return `${m} 分 ${String(s).padStart(2, '0')} 秒`
}
