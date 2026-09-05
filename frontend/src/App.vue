<template>
  <router-view />
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const route = useRoute()
const auth = useAuthStore()

function onLogout() {
  auth.user = null
  router.push('/login')
}

// 每页独立背景：路由前缀 → 背景变量（变量定义在 style.css）
const PAGE_BACKGROUNDS: Array<[string, string]> = [
  ['/chat', 'var(--page-bg-chat)'],
  ['/admin/kb', 'var(--page-bg-kb)'],
  ['/admin/users', 'var(--page-bg-users)'],
  ['/profile', 'var(--page-bg-profile)'],
]

function applyBackground(path: string) {
  const entry = PAGE_BACKGROUNDS.find(([prefix]) => path.startsWith(prefix))
  document.body.style.backgroundImage = entry ? entry[1] : ''
}

watch(() => route.path, applyBackground, { immediate: true })

onMounted(() => window.addEventListener('auth:logout', onLogout))
onUnmounted(() => window.removeEventListener('auth:logout', onLogout))
</script>
