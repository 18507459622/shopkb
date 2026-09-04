<template>
  <AppShell>
    <el-card class="profile-card">
      <h3>修改密码</h3>
      <el-form label-width="100px" style="max-width: 440px">
        <el-form-item label="原密码">
          <el-input v-model="oldPassword" type="password" show-password />
        </el-form-item>
        <el-form-item label="新密码">
          <el-input v-model="newPassword" type="password" show-password />
        </el-form-item>
        <el-form-item label="确认新密码">
          <el-input v-model="confirm" type="password" show-password />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="loading" @click="onSubmit">确认修改</el-button>
        </el-form-item>
      </el-form>
    </el-card>
  </AppShell>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'

import AppShell from '@/components/AppShell.vue'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const auth = useAuthStore()

const oldPassword = ref('')
const newPassword = ref('')
const confirm = ref('')
const loading = ref(false)

async function onSubmit() {
  if (newPassword.value !== confirm.value) {
    ElMessage.warning('两次新密码不一致')
    return
  }
  if (newPassword.value.length < 6) {
    ElMessage.warning('新密码至少 6 位')
    return
  }
  loading.value = true
  try {
    await auth.changePassword(oldPassword.value, newPassword.value)
    ElMessage.success('密码已修改，请重新登录')
    await auth.logout()
    router.push('/login')
  } catch {
    ElMessage.error('修改失败（原密码可能错误）')
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.profile-card {
  max-width: 600px;
}
</style>
