<template>
  <div class="app-shell">
    <header class="app-header">
      <div class="brand" @click="router.push('/chat')">商品知识库问答</div>
      <nav class="nav">
        <router-link to="/chat">问答</router-link>
        <router-link v-if="auth.isAdmin" to="/admin/kb">知识库管理</router-link>
        <router-link v-if="auth.isAdmin" to="/admin/users">用户管理</router-link>
        <router-link to="/profile">修改密码</router-link>
      </nav>
      <div class="user">
        <span class="username">{{ auth.user?.username }}</span>
        <el-button size="small" text @click="logout">退出</el-button>
      </div>
    </header>
    <main class="app-content">
      <slot />
    </main>
  </div>
</template>

<script setup lang="ts">
import { useRouter } from 'vue-router'

import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const auth = useAuthStore()

async function logout() {
  await auth.logout()
  router.push('/login')
}
</script>

<style scoped>
.app-header {
  display: flex;
  align-items: center;
  gap: 32px;
  height: 56px;
  padding: 0 24px;
  background: #fff;
  border-bottom: 1px solid #e4e7ed;
}
.brand {
  font-weight: 700;
  cursor: pointer;
}
.nav {
  display: flex;
  gap: 20px;
  flex: 1;
}
.nav a {
  text-decoration: none;
  color: #606266;
}
.nav a.router-link-active {
  color: #409eff;
  font-weight: 600;
}
.user {
  display: flex;
  align-items: center;
  gap: 8px;
}
.app-content {
  padding: 20px 24px;
}
</style>
