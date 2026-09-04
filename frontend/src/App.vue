<template>
  <router-view />
</template>

<script setup lang="ts">
import { onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'

import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const auth = useAuthStore()

function onLogout() {
  auth.user = null
  router.push('/login')
}

onMounted(() => window.addEventListener('auth:logout', onLogout))
onUnmounted(() => window.removeEventListener('auth:logout', onLogout))
</script>
