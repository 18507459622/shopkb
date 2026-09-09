import type { Source } from '@/types/api'

import { getAccessToken } from './http'

export interface ClarifyPayload {
  question: string
  category: string | null
  candidates: string[]
}

export interface RetrievalPayload {
  query: string
  rewritten: boolean
  mode: string
  sources: Source[]
}

export interface StreamHandlers {
  onToken: (delta: string) => void
  onCitations: (sources: Source[]) => void
  onClarify: (payload: ClarifyPayload) => void
  onRetrieval: (payload: RetrievalPayload) => void
  onDone: (data: { message_id: number; sources: Source[] }) => void
  onError: (code: string, message: string) => void
}

interface ParsedEvent {
  event: string
  data: string
}

function parseBlock(block: string): ParsedEvent | null {
  let event = ''
  let data = ''
  for (const line of block.split('\n')) {
    if (line.startsWith('event:')) event = line.slice(6).trim()
    else if (line.startsWith('data:')) data += line.slice(5).trim()
    else if (line.startsWith(':')) continue // 心跳注释
  }
  if (!event || !data) return null
  return { event, data }
}

/** fetch + ReadableStream 解析 SSE（POST + body + Authorization，EventSource 不支持）。 */
export async function streamChat(
  conversationId: number,
  content: string,
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const token = getAccessToken()
  const resp = await fetch(`/api/v1/chat/conversations/${conversationId}/stream`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({ content }),
    signal,
  })

  if (!resp.ok || !resp.body) {
    throw new Error(`HTTP ${resp.status}`)
  }

  const reader = resp.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const blocks = buffer.split('\n\n')
    buffer = blocks.pop() ?? ''
    for (const block of blocks) {
      const parsed = parseBlock(block)
      if (!parsed) continue
      let payload: Record<string, unknown>
      try {
        payload = JSON.parse(parsed.data) as Record<string, unknown>
      } catch {
        continue
      }
      switch (parsed.event) {
        case 'token':
          handlers.onToken(String(payload.delta ?? ''))
          break
        case 'citations':
          handlers.onCitations((payload.sources as Source[]) ?? [])
          break
        case 'clarify':
          handlers.onClarify(payload as unknown as ClarifyPayload)
          break
        case 'retrieval':
          handlers.onRetrieval(payload as unknown as RetrievalPayload)
          break
        case 'done':
          handlers.onDone(payload as unknown as { message_id: number; sources: Source[] })
          break
        case 'error':
          handlers.onError(String(payload.code ?? 'ERROR'), String(payload.message ?? ''))
          break
      }
    }
  }
}
