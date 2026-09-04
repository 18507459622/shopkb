<template>
  <div class="forbidden">
    <el-result icon="warning" title="403" sub-title="无权限访问该页面" />
    <el-button type="primary" @click="router.push('/chat')">返回问答</el-button>
  </div>
</template>

<script setup lang="ts">
import { useRouter } from 'vue-router'

const router = useRouter()
</script>

<style scoped>
.forbidden {
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
}
</style>
