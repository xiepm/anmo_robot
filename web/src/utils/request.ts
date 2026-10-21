/** HTTP 请求封装 */
import axios from 'axios'
import type { AxiosInstance } from 'axios'
import type { ApiResult } from '@/types/api'

const request: AxiosInstance = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL,
  timeout: 15000,
})

request.interceptors.response.use(
  (response) => {
    const body = response.data as ApiResult<unknown>
    if (body && typeof body === 'object' && 'code' in body && body.code !== 200) {
      return Promise.reject(body)
    }
    if (body && typeof body === 'object' && 'data' in body) {
      return body.data as never
    }
    return response.data
  },
  (error) => Promise.reject(error),
)

export default request
