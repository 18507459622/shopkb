import axios, { AxiosError, type AxiosRequestConfig } from 'axios'

import type { Envelope } from '@/types/api'

// access token 仅内存持有；refresh token 走 HttpOnly cookie（withCredentials）
let accessToken: string | null = null
let refreshPromise: Promise<string | null> | null = null

export function setAccessToken(token: string | null): void {
  accessToken = token
}

export function getAccessToken(): string | null {
  return accessToken
}

export const http = axios.create({
  baseURL: '/api/v1',
  timeout: 30000,
  withCredentials: true,
})

http.interceptors.request.use((config) => {
  if (accessToken) {
    config.headers.Authorization = `Bearer ${accessToken}`
  }
  return config
})

async function refreshAccessToken(): Promise<string | null> {
  try {
    const resp = await axios.post<Envelope<{ access_token: string }>>('/api/v1/auth/refresh', null, {
      withCredentials: true,
    })
    setAccessToken(resp.data.data.access_token)
    return resp.data.data.access_token
  } catch {
    setAccessToken(null)
    return null
  }
}

// 401 单飞行刷新：并发 401 共享同一次 refresh
http.interceptors.response.use(
  (resp) => resp,
  async (error: AxiosError) => {
    const original = error.config as (AxiosRequestConfig & { _retried?: boolean }) | undefined
    const status = error.response?.status
    if (status === 401 && original && !original._retried && !original.url?.includes('/auth/')) {
      original._retried = true
      if (!refreshPromise) {
        refreshPromise = refreshAccessToken().finally(() => {
          refreshPromise = null
        })
      }
      const token = await refreshPromise
      if (token) {
        original.headers = { ...original.headers, Authorization: `Bearer ${token}` }
        return http(original)
      }
      window.dispatchEvent(new CustomEvent('auth:logout'))
    }
    return Promise.reject(error)
  },
)

// 统一 envelope 解包
export const api = {
  get: <T>(url: string, config?: AxiosRequestConfig) =>
    http.get<Envelope<T>>(url, config).then((r) => r.data.data),
  post: <T>(url: string, data?: unknown, config?: AxiosRequestConfig) =>
    http.post<Envelope<T>>(url, data, config).then((r) => r.data.data),
  patch: <T>(url: string, data?: unknown, config?: AxiosRequestConfig) =>
    http.patch<Envelope<T>>(url, data, config).then((r) => r.data.data),
  delete: <T>(url: string, config?: AxiosRequestConfig) =>
    http.delete<Envelope<T>>(url, config).then((r) => r.data.data),
}
