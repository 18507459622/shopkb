import type {
  AdminStats,
  ChatMessage,
  Conversation,
  DocumentItem,
  IngestionJob,
  KbStats,
  TokenResponse,
  User,
  UserAdmin,
} from '@/types/api'

import { api, http } from './http'

export const authApi = {
  me: () => api.get<User>('/auth/me'),
  register: (username: string, password: string) =>
    api.post<User>('/auth/register', { username, password }),
  login: (username: string, password: string) =>
    api.post<TokenResponse>('/auth/login', { username, password }),
  logout: () => api.post<null>('/auth/logout'),
  changePassword: (old_password: string, new_password: string) =>
    api.post<null>('/auth/change-password', { old_password, new_password }),
}

export const chatApi = {
  listConversations: () =>
    api.get<{ items: Conversation[]; total: number }>('/chat/conversations'),
  createConversation: () => api.post<Conversation>('/chat/conversations', {}),
  renameConversation: (id: number, title: string) =>
    api.patch<Conversation>(`/chat/conversations/${id}`, { title }),
  deleteConversation: (id: number) => api.delete<null>(`/chat/conversations/${id}`),
  listMessages: (convId: number) =>
    api.get<ChatMessage[]>(`/chat/conversations/${convId}/messages`),
}

export const kbApi = {
  upload: (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return http
      .post<{ data: { document: DocumentItem; job: IngestionJob } }>('/kb/documents/upload', form)
      .then((r) => r.data.data)
  },
  listDocuments: (params?: { status?: string; page?: number; size?: number }) =>
    api.get<{ items: DocumentItem[]; total: number }>('/kb/documents', { params }),
  deleteDocument: (id: number) => api.delete<null>(`/kb/documents/${id}`),
  reingest: (id: number) => api.post<IngestionJob>(`/kb/documents/${id}/reingest`),
  stats: () => api.get<KbStats>('/kb/stats'),
}

export const adminApi = {
  listUsers: (params?: { page?: number; size?: number }) =>
    api.get<{ items: UserAdmin[]; total: number }>('/admin/users', { params }),
  updateUser: (id: number, data: { is_active?: boolean; role?: string }) =>
    api.patch<UserAdmin>(`/admin/users/${id}`, data),
  stats: () => api.get<AdminStats>('/admin/stats'),
}
