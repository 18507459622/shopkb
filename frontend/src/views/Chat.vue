<template>
  <div class="chat-layout" :class="{ 'sidebar-hidden': !sidebarVisible }">
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

    <!-- 移动端侧栏遮罩 -->
    <div v-if="isMobile && sidebarVisible" class="sidebar-backdrop" @click="sidebarVisible = false"></div>

    <!-- 中：消息区 -->
    <main class="chat-main">
      <div class="chat-header">
        <el-button class="sidebar-toggle" text @click="toggleSidebar">
          <el-icon><Fold v-if="sidebarVisible" /><Expand v-else /></el-icon>
        </el-button>
        <span class="chat-title">{{ activeTitle }}</span>
      </div>
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
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Close, Expand, Fold } from '@element-plus/icons-vue'
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

const sidebarVisible = ref(true)
const isMobile = ref(false)

const activeTitle = computed(() => {
  const conv = store.conversations.find((c) => c.id === store.activeConversationId)
  return conv?.title ?? '新对话'
})

function updateLayout() {
  isMobile.value = window.innerWidth < 768
}

function toggleSidebar() {
  sidebarVisible.value = !sidebarVisible.value
}

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

onMounted(() => {
  updateLayout()
  if (isMobile.value) sidebarVisible.value = false
  window.addEventListener('resize', updateLayout)
})

onUnmounted(() => {
  window.removeEventListener('resize', updateLayout)
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
  gap: 16px;
  padding: 16px;
  max-width: 1400px;
  margin: 0 auto;
}
.session-sidebar {
  width: 280px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  border-radius: 18px;
  overflow: hidden;
  background: rgba(255, 255, 255, 0.6);
  -webkit-backdrop-filter: blur(18px) saturate(150%);
  backdrop-filter: blur(18px) saturate(150%);
  border: 1px solid rgba(255, 255, 255, 0.7);
  box-shadow: var(--shadow);
  transition: width 0.25s ease, opacity 0.2s ease;
}
.sidebar-hidden .session-sidebar {
  width: 0;
  opacity: 0;
  pointer-events: none;
  border: none;
  box-shadow: none;
}
.sidebar-backdrop {
  position: fixed;
  inset: 0;
  z-index: 35;
  background: rgba(15, 20, 40, 0.4);
  -webkit-backdrop-filter: blur(2px);
  backdrop-filter: blur(2px);
}
.sidebar-top {
  padding: 16px;
}
.session-list {
  flex: 1;
  overflow-y: auto;
  padding: 0 10px 10px;
}
.session-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 14px;
  margin-bottom: 6px;
  cursor: pointer;
  border-radius: 12px;
  transition: all 0.18s ease;
}
.session-item:hover {
  background: rgba(79, 124, 255, 0.08);
}
.session-item.active {
  background: var(--brand-gradient);
  box-shadow: 0 8px 20px rgba(99, 102, 241, 0.28);
}
.session-item.active .session-title {
  color: #fff;
}
.session-title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 14px;
  color: var(--ink-2);
}
.del {
  color: #c0c4cc;
  opacity: 0;
}
.session-item:hover .del {
  opacity: 1;
}
.session-item.active .del {
  color: rgba(255, 255, 255, 0.85);
}
.sidebar-bottom {
  padding: 12px;
  border-top: 1px solid var(--line);
  cursor: pointer;
}
.username {
  font-size: 13px;
  color: var(--ink-2);
}
.chat-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  border-radius: 18px;
  overflow: hidden;
  background: rgba(255, 255, 255, 0.45);
  -webkit-backdrop-filter: blur(18px);
  backdrop-filter: blur(18px);
  border: 1px solid rgba(255, 255, 255, 0.7);
  box-shadow: var(--shadow);
}
.chat-header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 16px;
  border-bottom: 1px solid var(--line);
  background: rgba(255, 255, 255, 0.35);
}
.sidebar-toggle {
  font-size: 18px;
  color: var(--ink-2);
}
.sidebar-toggle:hover {
  color: var(--brand-blue);
}
.chat-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--ink);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.message-list {
  flex: 1;
  overflow-y: auto;
  padding: 28px;
}
.message {
  display: flex;
  gap: 12px;
  margin-bottom: 22px;
}
.message.user {
  flex-direction: row-reverse;
}
.avatar {
  width: 40px;
  height: 40px;
  border-radius: 12px;
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  font-weight: 600;
  font-size: 14px;
  background: var(--brand-gradient);
  box-shadow: 0 6px 16px rgba(99, 102, 241, 0.3);
}
.message.user .avatar {
  background: linear-gradient(135deg, #22d3ee 0%, #3b82f6 100%);
  box-shadow: 0 6px 16px rgba(59, 130, 246, 0.3);
}
.bubble {
  max-width: 70%;
  padding: 13px 16px;
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.85);
  border: 1px solid rgba(255, 255, 255, 0.8);
  box-shadow: 0 4px 16px rgba(80, 95, 180, 0.08);
}
.message.user .bubble {
  background: var(--brand-gradient);
  color: #fff;
  border: none;
  box-shadow: 0 8px 22px rgba(99, 102, 241, 0.28);
}
.plain {
  white-space: pre-wrap;
  word-break: break-word;
}
.sources {
  margin-top: 10px;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
}
.src-label {
  font-size: 12px;
  color: var(--ink-3);
}
.src-chip {
  cursor: pointer;
}
.composer {
  display: flex;
  gap: 12px;
  padding: 16px;
  background: rgba(255, 255, 255, 0.5);
  border-top: 1px solid var(--line);
  align-items: flex-end;
}
.snippet {
  background: rgba(99, 110, 160, 0.06);
  padding: 12px;
  border-radius: 10px;
  white-space: pre-wrap;
  word-break: break-word;
}

@media (max-width: 768px) {
  .chat-layout {
    padding: 8px;
    gap: 0;
  }
  .session-sidebar {
    position: fixed;
    left: 8px;
    top: 8px;
    bottom: 8px;
    z-index: 40;
    transition: transform 0.25s ease;
  }
  .sidebar-hidden .session-sidebar {
    width: 280px;
    opacity: 1;
    border: 1px solid rgba(255, 255, 255, 0.7);
    box-shadow: var(--shadow);
    transform: translateX(calc(-100% - 24px));
    pointer-events: none;
  }
}
</style>
