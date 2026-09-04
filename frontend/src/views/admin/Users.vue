<template>
  <AppShell>
    <el-row :gutter="16" class="stats">
      <el-col :span="8">
        <el-card><div class="stat-label">用户总数</div><div class="stat-value">{{ stats?.total_users ?? 0 }}</div></el-card>
      </el-col>
      <el-col :span="8">
        <el-card><div class="stat-label">活跃用户</div><div class="stat-value">{{ stats?.active_users ?? 0 }}</div></el-card>
      </el-col>
      <el-col :span="8">
        <el-card><div class="stat-label">文档总数</div><div class="stat-value">{{ stats?.total_documents ?? 0 }}</div></el-card>
      </el-col>
    </el-row>

    <el-card>
      <el-table :data="users" v-loading="loading">
        <el-table-column prop="id" label="ID" width="70" />
        <el-table-column prop="username" label="用户名" min-width="160" />
        <el-table-column label="角色" width="120">
          <template #default="{ row }">
            <el-select :model-value="row.role" size="small" :disabled="row.username === 'admin'" @change="(v: string) => changeRole(row, v)">
              <el-option label="admin" value="admin" />
              <el-option label="user" value="user" />
            </el-select>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="120">
          <template #default="{ row }">
            <el-switch
              :model-value="row.is_active"
              :disabled="row.username === 'admin'"
              @change="(v: boolean) => toggleActive(row, v)"
            />
          </template>
        </el-table-column>
        <el-table-column label="最近登录" width="180">
          <template #default="{ row }">{{ row.last_login_at ? new Date(row.last_login_at).toLocaleString('zh-CN') : '-' }}</template>
        </el-table-column>
        <el-table-column label="注册时间" width="180">
          <template #default="{ row }">{{ new Date(row.created_at).toLocaleString('zh-CN') }}</template>
        </el-table-column>
      </el-table>
    </el-card>
  </AppShell>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'

import AppShell from '@/components/AppShell.vue'
import { adminApi } from '@/api'
import type { AdminStats, UserAdmin } from '@/types/api'

const users = ref<UserAdmin[]>([])
const stats = ref<AdminStats | null>(null)
const loading = ref(false)

async function load() {
  loading.value = true
  try {
    const [list, st] = await Promise.all([adminApi.listUsers({ size: 200 }), adminApi.stats()])
    users.value = list.items
    stats.value = st
  } finally {
    loading.value = false
  }
}

async function toggleActive(row: UserAdmin, v: boolean) {
  await adminApi.updateUser(row.id, { is_active: v })
  ElMessage.success(v ? '已启用' : '已禁用')
  await load()
}

async function changeRole(row: UserAdmin, v: string) {
  await adminApi.updateUser(row.id, { role: v })
  ElMessage.success('角色已更新')
  await load()
}

onMounted(load)
</script>

<style scoped>
.stats {
  margin-bottom: 18px;
}
</style>
