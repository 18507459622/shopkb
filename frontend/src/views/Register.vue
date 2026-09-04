<template>
  <div class="auth-wrap">
    <div class="orb orb-a"></div>
    <div class="orb orb-b"></div>
    <div class="orb orb-c"></div>

    <div class="auth-card glass-card">
      <div class="logo">🛒</div>
      <h1 class="title">注册账号</h1>
      <p class="subtitle">创建你的知识库问答账号</p>

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
        <el-button type="primary" size="large" :loading="loading" class="submit" @click="onSubmit">
          注册
        </el-button>
      </el-form>

      <div class="footer">
        已有账号？
        <router-link to="/login">登录</router-link>
      </div>
    </div>
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
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
  background:
    radial-gradient(900px 500px at 15% 15%, rgba(79, 124, 255, 0.35), transparent 60%),
    radial-gradient(800px 500px at 85% 80%, rgba(168, 85, 247, 0.35), transparent 60%),
    linear-gradient(135deg, #eef1ff 0%, #f7f2ff 100%);
}
.orb {
  position: absolute;
  border-radius: 50%;
  filter: blur(60px);
  opacity: 0.55;
}
.orb-a {
  width: 420px;
  height: 420px;
  background: #4f7cff;
  top: -120px;
  left: -80px;
  animation: float 12s ease-in-out infinite;
}
.orb-b {
  width: 360px;
  height: 360px;
  background: #a855f7;
  bottom: -100px;
  right: -60px;
  animation: float 15s ease-in-out infinite reverse;
}
.orb-c {
  width: 240px;
  height: 240px;
  background: #22d3ee;
  top: 40%;
  left: 55%;
  opacity: 0.35;
  animation: float 18s ease-in-out infinite;
}
@keyframes float {
  0%,
  100% {
    transform: translate(0, 0) scale(1);
  }
  50% {
    transform: translate(30px, -30px) scale(1.08);
  }
}
.auth-card {
  position: relative;
  z-index: 2;
  width: 400px;
  padding: 40px 36px 28px;
  border-radius: 22px;
}
.logo {
  width: 56px;
  height: 56px;
  margin: 0 auto 16px;
  border-radius: 16px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--brand-gradient);
  box-shadow: 0 10px 28px rgba(99, 102, 241, 0.4);
  font-size: 26px;
}
.title {
  text-align: center;
  font-size: 22px;
  margin: 0 0 6px;
  font-weight: 700;
  background: var(--brand-gradient);
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
  color: transparent;
}
.subtitle {
  text-align: center;
  color: var(--ink-3);
  font-size: 13px;
  margin: 0 0 26px;
}
.submit {
  width: 100%;
}
.footer {
  margin-top: 18px;
  text-align: center;
  font-size: 14px;
  color: var(--ink-2);
}
.footer a {
  color: var(--brand-blue);
  text-decoration: none;
  font-weight: 600;
}
</style>
