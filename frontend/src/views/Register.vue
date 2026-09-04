<template>
  <div class="auth-wrap">
    <el-card class="auth-card">
      <h2 class="title">注册账号</h2>
      <el-form @submit.prevent="onSubmit">
        <el-form-item>
          <el-input v-model="username" placeholder="用户名（3-64 字符）" size="large" />
        </el-form-item>
        <el-form-item>
          <el-input
            v-model="password"
            type="password"
            placeholder="密码（至少 6 位）"
            size="large"
            show-password
          />
        </el-form-item>
        <el-form-item>
          <el-input
            v-model="confirm"
            type="password"
            placeholder="确认密码"
            size="large"
            show-password
            @keyup.enter="onSubmit"
          />
        </el-form-item>
        <el-button type="primary" size="large" :loading="loading" style="width: 100%" @click="onSubmit">
          注册
        </el-button>
      </el-form>
      <div class="footer">
        已有账号？
        <router-link to="/login">登录</router-link>
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'

import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const auth = useAuthStore()

const username = ref('')
const password = ref('')
const confirm = ref('')
const loading = ref(false)

async function onSubmit() {
  if (password.value !== confirm.value) {
    ElMessage.warning('两次密码不一致')
    return
  }
  if (password.value.length < 6) {
    ElMessage.warning('密码至少 6 位')
    return
  }
  loading.value = true
  try {
    await auth.register(username.value, password.value)
    ElMessage.success('注册成功，请登录')
    router.push('/login')
  } catch (e) {
    ElMessage.error('注册失败（用户名可能已被占用）')
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
