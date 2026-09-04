import { createRouter, createWebHistory } from 'vue-router'

import { useAuthStore } from '@/stores/auth'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/chat' },
    { path: '/login', name: 'login', component: () => import('@/views/Login.vue') },
    { path: '/register', name: 'register', component: () => import('@/views/Register.vue') },
    { path: '/chat', name: 'chat', component: () => import('@/views/Chat.vue'), meta: { requiresAuth: true } },
    { path: '/chat/:sessionId', name: 'chat-session', component: () => import('@/views/Chat.vue'), meta: { requiresAuth: true } },
    { path: '/profile', name: 'profile', component: () => import('@/views/Profile.vue'), meta: { requiresAuth: true } },
    {
      path: '/admin/kb',
      name: 'kb',
      component: () => import('@/views/admin/KnowledgeBase.vue'),
      meta: { requiresAuth: true, roles: ['admin'] },
    },
    {
      path: '/admin/users',
      name: 'users',
      component: () => import('@/views/admin/Users.vue'),
      meta: { requiresAuth: true, roles: ['admin'] },
    },
    { path: '/403', name: '403', component: () => import('@/views/Forbidden.vue') },
  ],
})

router.beforeEach(async (to) => {
  const auth = useAuthStore()
  if (!auth.booted) {
    await auth.bootstrap()
  }
  if (to.meta.requiresAuth && !auth.isAuthenticated) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }
  if (to.meta.roles && !(to.meta.roles as string[]).includes(auth.user?.role ?? '')) {
    return { name: '403' }
  }
  return true
})

export default router
