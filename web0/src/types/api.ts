/** 后端统一响应结构 */
export interface ApiResult<T> {
  code: number
  message: string
  data: T
  traceId?: string
}
