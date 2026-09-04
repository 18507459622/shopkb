import { defineStore } from 'pinia'

import { authApi } from '@/api'
import { api, setAccessToken } from '@/api/http'
import type { TokenResponse, User } from '@/types/api'

export const useAuthStore = defineStore('auth', {
  state: () => ({
    user: null as User | null,
    booted: false,
  }),
  getters: {
    isAuthenticated: (s) => !!s.user,
    isAdmin: (s) => s.user?.role === 'admin',
  },
  actions: {
    async bootstrap() {
      if (this.booted) return
      try {
        // 用 HttpOnly cookie 里的 refresh token 换取新 access token
        const data = await api.post<TokenResponse>('/auth/refresh', null)
        setAccessToken(data.access_token)
        this.user = data.user
      } catch {
        setAccessToken(null)
        this.user = null
      } finally {
        this.booted = true
      }
    },
    async login(username: string, password: string) {
      const data = await authApi.login(username, password)
      setAccessToken(data.access_token)
      this.user = data.user
    },
    async register(username: string, password: string) {
      await authApi.register(username, password)
    },
    async logout() {
      try {
        await authApi.logout()
      } catch {
        /* ignore */
      }
      setAccessToken(null)
      this.user = null
    },
    async changePassword(oldPassword: string, newPassword: string) {
      await authApi.changePassword(oldPassword, newPassword)
    },
  },
})
