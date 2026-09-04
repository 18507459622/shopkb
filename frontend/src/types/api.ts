// 与后端 Pydantic 模型对应的类型（snake_case，保持一致）

export interface User {
  id: number
  username: string
  role: 'admin' | 'user'
  nickname: string | null
  created_at: string
}

export interface TokenResponse {
  access_token: string
  token_type: string
  user: User
}

export interface Conversation {
  id: number
  title: string
  status: string
  last_message_at: string | null
  created_at: string
}

export interface Source {
  chunk_id: string
  doc_id: string
  doc_title: string
  snippet: string
  page: number | null
  section: string
  score: number
}

export interface ChatMessage {
  id: number
  conversation_id: number
  role: 'user' | 'assistant'
  content: string
  status: 'complete' | 'error' | 'aborted'
  sources: Source[] | null
  created_at: string
}

export interface DocumentItem {
  id: number
  filename: string
  title: string
  doc_type: string
  file_size: number
  chunk_count: number
  status: string
  created_at: string
}

export interface IngestionJob {
  id: number
  document_id: number
  trigger: string
  stage: string
  progress: number
  error_message: string | null
  created_at: string
}

export interface KbStats {
  total_documents: number
  total_chunks: number
  completed: number
  pending: number
  failed: number
}

export interface UserAdmin {
  id: number
  username: string
  role: string
  nickname: string | null
  is_active: boolean
  last_login_at: string | null
  created_at: string
}

export interface AdminStats {
  total_users: number
  active_users: number
  admin_count: number
  total_documents: number
  total_chunks: number
}

export interface Envelope<T> {
  code: string
  message: string
  data: T
  request_id: string
}
