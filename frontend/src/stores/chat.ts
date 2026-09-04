import { defineStore } from 'pinia'

import { chatApi } from '@/api'
import type { ChatMessage, Conversation, Source } from '@/types/api'

export interface UiMessage extends ChatMessage {
  sources: Source[] | null
}

export const useChatStore = defineStore('chat', {
  state: () => ({
    conversations: [] as Conversation[],
    messages: {} as Record<number, UiMessage[]>,
    activeConversationId: null as number | null,
    streaming: false,
  }),
  getters: {
    activeMessages: (s) =>
      s.activeConversationId != null ? s.messages[s.activeConversationId] ?? [] : [],
  },
  actions: {
    async loadConversations() {
      const data = await chatApi.listConversations()
      this.conversations = data.items
    },
    async createConversation() {
      const conv = await chatApi.createConversation()
      this.conversations.unshift(conv)
      this.activeConversationId = conv.id
      this.messages[conv.id] = []
      return conv
    },
    async loadMessages(convId: number) {
      const msgs = await chatApi.listMessages(convId)
      this.messages[convId] = msgs.map((m) => ({ ...m, sources: m.sources ?? [] }))
    },
    async deleteConversation(convId: number) {
      await chatApi.deleteConversation(convId)
      this.conversations = this.conversations.filter((c) => c.id !== convId)
      delete this.messages[convId]
      if (this.activeConversationId === convId) {
        this.activeConversationId = null
      }
    },
    setActive(convId: number | null) {
      this.activeConversationId = convId
    },
    appendUserMessage(convId: number, content: string) {
      const msg: UiMessage = {
        id: 0,
        conversation_id: convId,
        role: 'user',
        content,
        status: 'complete',
        sources: [],
        created_at: new Date().toISOString(),
      }
      this.messages[convId] = [...(this.messages[convId] ?? []), msg]
    },
    appendAssistantPlaceholder(convId: number) {
      const msg: UiMessage = {
        id: 0,
        conversation_id: convId,
        role: 'assistant',
        content: '',
        status: 'complete',
        sources: [],
        created_at: new Date().toISOString(),
      }
      this.messages[convId] = [...(this.messages[convId] ?? []), msg]
      return this.messages[convId]!.length - 1
    },
    updateAssistant(convId: number, index: number, content: string, sources: Source[] | null) {
      const list = this.messages[convId]
      if (!list || !list[index]) return
      list[index] = { ...list[index]!, content, sources: sources ?? list[index]!.sources }
    },
    finalizeAssistant(convId: number, index: number, id: number) {
      const list = this.messages[convId]
      if (!list || !list[index]) return
      list[index] = { ...list[index]!, id }
    },
  },
})
