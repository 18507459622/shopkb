<template>
  <div class="auth-wrap">
    <el-card class="auth-card">
      <h2 class="title">商品知识库问答系统</h2>
      <el-form @submit.prevent="onSubmit">
        <el-form-item>
          <el-input v-model="username" placeholder="用户名" size="large" />
        </el-form-item>
        <el-form-item>
          <el-input
            v-model="password"
            type="password"
            placeholder="密码"
            size="large"
            show-password
            @keyup.enter="onSubmit"
          />
        </el-form-item>
        <el-button type="primary" size="large" :loading="loading" style="width: 100%" @click="onSubmit">
          登录
        </el-button>
      </el-form>
      <div class="footer">
        还没有账号？
        <router-link to="/register">注册</router-link>
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'

import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const route = useRoute()
const auth = useAuthStore()

const username = ref('')
const password = ref('')
const loading = ref(false)

async function onSubmit() {
  if (!username.value || !password.value) {
    ElMessage.warning('请输入用户名和密码')
    return
  }
  loading.value = true
  try {
    await auth.login(username.value, password.value)
    ElMessage.success('登录成功')
    const redirect = (route.query.redirect as string) || '/chat'
    router.push(redirect)
  } catch (e) {
    ElMessage.error('用户名或密码错误')
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.auth-wrap {
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
}
.auth-card {
  width: 380px;
  padding: 8px 12px;
}
.title {
  text-align: center;
  margin-bottom: 24px;
}
.footer {
  margin-top: 16px;
  text-align: center;
  font-size: 14px;
}
</style>
