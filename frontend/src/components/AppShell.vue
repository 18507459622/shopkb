<template>
  <div class="app-shell">
    <header class="app-header">
      <div class="brand" @click="router.push('/chat')">
        <span class="logo">🛒</span>
        <span class="brand-name">商品知识库问答</span>
      </div>
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
.app-shell {
  min-height: 100%;
}
.app-header {
  position: sticky;
  top: 0;
  z-index: 20;
  display: flex;
  align-items: center;
  gap: 28px;
  height: 60px;
  padding: 0 24px;
  background: rgba(255, 255, 255, 0.6);
  -webkit-backdrop-filter: blur(18px) saturate(150%);
  backdrop-filter: blur(18px) saturate(150%);
  border-bottom: 1px solid rgba(255, 255, 255, 0.6);
  box-shadow: 0 4px 24px rgba(80, 95, 180, 0.08);
}
.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  cursor: pointer;
}
.logo {
  width: 32px;
  height: 32px;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--brand-gradient);
  box-shadow: 0 6px 16px rgba(99, 102, 241, 0.35);
  font-size: 17px;
}
.brand-name {
  font-weight: 700;
  font-size: 16px;
}
.nav {
  display: flex;
  gap: 6px;
  flex: 1;
}
.nav a {
  text-decoration: none;
  color: var(--ink-2);
  padding: 7px 14px;
  border-radius: 999px;
  font-size: 14px;
  transition: all 0.2s ease;
}
.nav a:hover {
  color: var(--brand-blue);
  background: rgba(79, 124, 255, 0.08);
}
.nav a.router-link-active {
  color: #fff;
  background: var(--brand-gradient);
  box-shadow: 0 6px 16px rgba(99, 102, 241, 0.3);
  font-weight: 600;
}
.user {
  display: flex;
  align-items: center;
  gap: 8px;
}
.username {
  color: var(--ink-2);
  font-size: 14px;
}
.app-content {
  padding: 24px;
  max-width: 1400px;
  margin: 0 auto;
}
</style>
