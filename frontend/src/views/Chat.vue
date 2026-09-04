<template>
  <div class="chat-layout">
    <!-- 左：会话列表 -->
    <aside class="session-sidebar">
      <div class="sidebar-top">
        <el-button type="primary" style="width: 100%" @click="newConversation">+ 新建对话</el-button>
      </div>
      <div class="session-list">
        <div
          v-for="c in store.conversations"
          :key="c.id"
          class="session-item"
          :class="{ active: c.id === store.activeConversationId }"
          @click="selectConversation(c.id)"
        >
          <span class="session-title">{{ c.title }}</span>
          <el-icon class="del" @click.stop="removeConversation(c.id)"><Close /></el-icon>
        </div>
      </div>
      <div class="sidebar-bottom">
        <el-dropdown @command="onUserCommand">
          <span class="username">{{ auth.user?.username }} ({{ auth.user?.role === 'admin' ? '管理员' : '用户' }})</span>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item v-if="auth.isAdmin" command="kb">知识库管理</el-dropdown-item>
              <el-dropdown-item v-if="auth.isAdmin" command="users">用户管理</el-dropdown-item>
              <el-dropdown-item command="profile">修改密码</el-dropdown-item>
              <el-dropdown-item command="logout" divided>退出登录</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </div>
    </aside>

    <!-- 中：消息区 -->
    <main class="chat-main">
      <div class="message-list" ref="listRef">
        <el-empty v-if="!store.activeMessages.length" description="开始提问吧" />
        <div v-for="(m, i) in store.activeMessages" :key="i" class="message" :class="m.role">
          <div class="avatar">{{ m.role === 'user' ? '我' : 'AI' }}</div>
          <div class="bubble">
            <div v-if="m.role === 'assistant'" class="markdown-body" v-html="renderMarkdown(m.content)"></div>
            <div v-else class="plain">{{ m.content }}</div>
            <div v-if="m.sources && m.sources.length" class="sources">
              <span class="src-label">引用：</span>
              <el-tag
                v-for="(s, idx) in m.sources"
                :key="s.chunk_id"
                size="small"
                class="src-chip"
                @click="openSource(s)"
              >
                [{{ idx + 1 }}] {{ s.doc_title }}{{ s.page ? ` P${s.page}` : '' }}
              </el-tag>
            </div>
          </div>
        </div>
      </div>
      <div class="composer">
        <el-input
          v-model="input"
          type="textarea"
          :rows="2"
          placeholder="输入商品相关问题，例如：这款手机多少钱？售后政策是什么？"
          :disabled="store.streaming"
          @keydown.enter.exact.prevent="send"
        />
        <el-button type="primary" :loading="store.streaming" @click="send">发送</el-button>
      </div>
    </main>

    <!-- 引用片段抽屉 -->
    <el-drawer v-model="drawerVisible" title="引用片段" size="40%">
      <div v-if="currentSource">
        <el-descriptions :column="1" border>
          <el-descriptions-item label="来源文档">{{ currentSource.doc_title }}</el-descriptions-item>
          <el-descriptions-item label="页码">{{ currentSource.page ?? '-' }}</el-descriptions-item>
          <el-descriptions-item label="相关度">{{ (currentSource.score * 100).toFixed(1) }}%</el-descriptions-item>
        </el-descriptions>
        <h4>片段内容</h4>
        <p class="snippet">{{ currentSource.snippet }}</p>
      </div>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { nextTick, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Close } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import { streamChat } from '@/api/sse'
import { useAuthStore } from '@/stores/auth'
import { useChatStore } from '@/stores/chat'
import type { Source } from '@/types/api'
import { renderMarkdown } from '@/utils/markdown'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const store = useChatStore()

const input = ref('')
const listRef = ref<HTMLElement>()
const drawerVisible = ref(false)
const currentSource = ref<Source | null>(null)

onMounted(async () => {
  await store.loadConversations()
  const sessionId = route.params.sessionId ? Number(route.params.sessionId) : null
  const target = sessionId ?? store.conversations[0]?.id ?? null
  if (target != null) {
    store.setActive(target)
    await store.loadMessages(target)
  } else {
    await store.createConversation()
    await router.replace(`/chat/${store.activeConversationId}`)
  }
})

async function newConversation() {
  const conv = await store.createConversation()
  await router.replace(`/chat/${conv.id}`)
}

async function selectConversation(id: number) {
  if (id === store.activeConversationId) return
  store.setActive(id)
  await store.loadMessages(id)
  router.replace(`/chat/${id}`)
}

async function removeConversation(id: number) {
  await ElMessageBox.confirm('删除后无法恢复，确定删除该会话？', '提示', { type: 'warning' })
  await store.deleteConversation(id)
}

function openSource(s: Source) {
  currentSource.value = s
  drawerVisible.value = true
}

function onUserCommand(cmd: string) {
  if (cmd === 'kb') router.push('/admin/kb')
  else if (cmd === 'users') router.push('/admin/users')
  else if (cmd === 'profile') router.push('/profile')
  else if (cmd === 'logout') logout()
}

async function logout() {
  await auth.logout()
  router.push('/login')
}

function scrollToBottom() {
  nextTick(() => {
    listRef.value?.scrollTo({ top: listRef.value.scrollHeight })
  })
}

async function send() {
  const content = input.value.trim()
  if (!content || store.streaming) return
  input.value = ''

  let convId = store.activeConversationId
  if (convId == null) {
    const conv = await store.createConversation()
    convId = conv.id
    await router.replace(`/chat/${convId}`)
  }

  store.appendUserMessage(convId, content)
  const idx = store.appendAssistantPlaceholder(convId)
  store.streaming = true
  scrollToBottom()

  let answer = ''
  let sources: Source[] = []

  try {
    await streamChat(
      convId,
      content,
      {
        onToken: (delta) => {
          answer += delta
          store.updateAssistant(convId, idx, answer, sources)
          scrollToBottom()
        },
        onCitations: (s) => {
          sources = s
          store.updateAssistant(convId, idx, answer, sources)
        },
        onDone: (data) => {
          store.finalizeAssistant(convId, idx, data.message_id)
        },
        onError: (_code, message) => {
          store.updateAssistant(convId, idx, answer || '回答失败', sources)
          ElMessage.error(message || '回答生成失败')
        },
      },
    )
  } catch {
    store.updateAssistant(convId, idx, answer || '连接中断，请重试', sources)
    ElMessage.error('连接中断')
  } finally {
    store.streaming = false
    scrollToBottom()
    await store.loadConversations()
  }
}
</script>

<style scoped>
.chat-layout {
  display: flex;
  height: 100%;
}
.session-sidebar {
  width: 260px;
  background: #fff;
  border-right: 1px solid #e4e7ed;
  display: flex;
  flex-direction: column;
}
.sidebar-top {
  padding: 12px;
}
.session-list {
  flex: 1;
  overflow-y: auto;
}
.session-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  cursor: pointer;
  border-bottom: 1px solid #f5f7fa;
}
.session-item.active {
  background: #ecf5ff;
}
.session-title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 14px;
}
.del {
  color: #c0c4cc;
  opacity: 0;
}
.session-item:hover .del {
  opacity: 1;
}
.sidebar-bottom {
  padding: 12px;
  border-top: 1px solid #e4e7ed;
  cursor: pointer;
}
.chat-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  background: #f5f7fa;
}
.message-list {
  flex: 1;
  overflow-y: auto;
  padding: 24px;
}
.message {
  display: flex;
  gap: 12px;
  margin-bottom: 20px;
}
.message.user {
  flex-direction: row-reverse;
}
.avatar {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  background: #409eff;
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.message.user .avatar {
  background: #67c23a;
}
.bubble {
  max-width: 70%;
  padding: 12px 16px;
  border-radius: 10px;
  background: #fff;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.06);
}
.message.user .bubble {
  background: #409eff;
  color: #fff;
}
.plain {
  white-space: pre-wrap;
  word-break: break-word;
}
.sources {
  margin-top: 8px;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
}
.src-label {
  font-size: 12px;
  color: #909399;
}
.src-chip {
  cursor: pointer;
}
.composer {
  display: flex;
  gap: 12px;
  padding: 16px 24px;
  background: #fff;
  border-top: 1px solid #e4e7ed;
  align-items: flex-end;
}
.snippet {
  background: #f5f7fa;
  padding: 12px;
  border-radius: 6px;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
