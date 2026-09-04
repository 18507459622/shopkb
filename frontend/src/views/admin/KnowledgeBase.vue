<template>
  <AppShell>
    <div class="kb-page">
      <el-row :gutter="16" class="stats">
        <el-col :span="6" v-for="s in statCards" :key="s.label">
          <el-card>
            <div class="stat-label">{{ s.label }}</div>
            <div class="stat-value">{{ s.value }}</div>
          </el-card>
        </el-col>
      </el-row>

      <el-card class="table-card">
        <div class="toolbar">
          <el-upload :show-file-list="false" :http-request="onUpload" accept=".pdf,.docx,.txt,.md,.csv">
            <el-button type="primary">上传文档</el-button>
          </el-upload>
          <el-button :loading="loading" @click="load">刷新</el-button>
          <span class="hint">支持 .pdf / .docx / .txt / .md / .csv，单个 ≤20MB</span>
        </div>

        <el-table :data="documents" v-loading="loading">
          <el-table-column prop="id" label="ID" width="70" />
          <el-table-column prop="filename" label="文件名" min-width="200" />
          <el-table-column prop="doc_type" label="类型" width="80" />
          <el-table-column label="大小" width="100">
            <template #default="{ row }">{{ formatSize(row.file_size) }}</template>
          </el-table-column>
          <el-table-column prop="chunk_count" label="分块数" width="90" />
          <el-table-column label="状态" width="120">
            <template #default="{ row }">
              <el-tag :type="statusType(row.status)">{{ statusText(row.status) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="上传时间" width="180">
            <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="200" fixed="right">
            <template #default="{ row }">
              <el-button size="small" @click="reingest(row)">重新入库</el-button>
              <el-button size="small" type="danger" @click="remove(row)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-card>
    </div>
  </AppShell>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { ElMessage, ElMessageBox, type UploadRequestOptions } from 'element-plus'

import AppShell from '@/components/AppShell.vue'
import { kbApi } from '@/api'
import type { DocumentItem, KbStats } from '@/types/api'

const documents = ref<DocumentItem[]>([])
const stats = ref<KbStats | null>(null)
const loading = ref(false)
let timer: ReturnType<typeof setInterval> | null = null

const statCards = computed(() => [
  { label: '文档总数', value: stats.value?.total_documents ?? 0 },
  { label: '已入库', value: stats.value?.completed ?? 0 },
  { label: '处理中', value: stats.value?.pending ?? 0 },
  { label: '总分块数', value: stats.value?.total_chunks ?? 0 },
])

async function load() {
  loading.value = true
  try {
    const [list, st] = await Promise.all([kbApi.listDocuments({ size: 100 }), kbApi.stats()])
    documents.value = list.items
    stats.value = st
    // 有非终态文档时继续轮询
    const busy = list.items.some((d) => ['pending', 'parsing', 'chunking', 'embedding', 'indexing'].includes(d.status))
    if (busy && !timer) startPolling()
    else if (!busy && timer) stopPolling()
  } finally {
    loading.value = false
  }
}

function startPolling() {
  timer = setInterval(load, 3000)
}
function stopPolling() {
  if (timer) clearInterval(timer)
  timer = null
}

async function onUpload(options: UploadRequestOptions) {
  const file = options.file
  if (file.size > 20 * 1024 * 1024) {
    ElMessage.warning('文件超过 20MB')
    return
  }
  try {
    await kbApi.upload(file)
    ElMessage.success('上传成功，正在入库…')
    options.onSuccess({})
    await load()
    startPolling()
  } catch {
    ElMessage.error('上传失败')
  }
}

async function reingest(row: DocumentItem) {
  await kbApi.reingest(row.id)
  ElMessage.success('已触发重新入库')
  await load()
}

async function remove(row: DocumentItem) {
  await ElMessageBox.confirm(`确定删除「${row.filename}」？`, '提示', { type: 'warning' })
  await kbApi.deleteDocument(row.id)
  ElMessage.success('已删除')
  await load()
}

function statusType(s: string) {
  if (s === 'completed') return 'success'
  if (s === 'failed') return 'danger'
  if (s === 'deleted') return 'info'
  return 'warning'
}
function statusText(s: string) {
  const map: Record<string, string> = {
    pending: '等待中',
    parsing: '解析中',
    chunking: '切分中',
    embedding: '向量化中',
    indexing: '写入索引',
    completed: '已完成',
    failed: '失败',
    deleted: '已删除',
  }
  return map[s] ?? s
}
function formatSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}
function formatTime(t: string) {
  return new Date(t).toLocaleString('zh-CN')
}

onMounted(load)
onUnmounted(stopPolling)
</script>

<style scoped>
.stats {
  margin-bottom: 18px;
}
.toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
}
.hint {
  font-size: 12px;
  color: var(--ink-3);
}
</style>
