/** 服务适配问卷题目 */
export interface SurveyQuestion {
  key: string
  title: string
  opts: string[]
}

export const surveyQuestions: SurveyQuestion[] = [
  {
    key: 'mox',
    title: '是否存在不适合艾灸服务的情况？',
    opts: ['是，暂不建议艾灸', '否，可以接受艾灸服务'],
  },
  {
    key: 'massage',
    title: '是否存在不适合按摩服务的情况？',
    opts: ['是，暂不建议按摩', '否，可以接受按摩服务'],
  },
  {
    key: 'area',
    title: '本次更想关注哪个部位？',
    opts: ['肩颈', '腰背', '其他'],
  },
  {
    key: 'temp',
    title: '您对温度的感受更接近？',
    opts: ['明显怕冷', '正常', '明显怕热'],
  },
  {
    key: 'energy',
    title: '最近一周的精力状态？',
    opts: ['经常疲惫', '偶尔疲惫', '精力充沛'],
  },
  {
    key: 'sleep',
    title: '最近的睡眠状态？',
    opts: ['差', '一般', '好'],
  },
]
