<template>
  <el-dialog v-model="visible" :title="domainId ? '批量添加本域成员' : '批量导入用户'" width="620px">
    <el-alert type="info" :closable="false" class="ui-import__help">
      <template #title>
        {{ domainId
          ? '支持 CSV/XLSX，列名：username；只能绑定已有启用用户。'
          : '支持 CSV/XLSX，列名：username、display_name、domain；只创建普通用户。' }}
      </template>
    </el-alert>
    <input type="file" accept=".csv,.xlsx" @change="selectFile" />
    <div v-if="file" class="ui-import__file">{{ file.name }}</div>
    <div v-if="plan" class="ui-import__summary">
      <el-tag>总行数 {{ plan.total_rows }}</el-tag>
      <el-tag type="success">新增 {{ plan.new_users.length }}</el-tag>
      <el-tag type="success">补绑定 {{ plan.bindings.length }}</el-tag>
      <el-tag :type="plan.errors.length ? 'danger' : 'info'">错误 {{ plan.errors.length }}</el-tag>
      <el-table v-if="plan.errors.length" :data="plan.errors" size="small" max-height="220">
        <el-table-column prop="row" label="行" width="70" />
        <el-table-column prop="username" label="用户名" />
        <el-table-column label="错误">
          <template #default="{ row }">{{ managementErrorCodeMessage(row.code) }}</template>
        </el-table-column>
      </el-table>
    </div>
    <template #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button :loading="loading" :disabled="!file" @click="preview">预检</el-button>
      <el-button
        type="primary"
        :loading="loading"
        :disabled="!file || !plan || plan.errors.length > 0"
        @click="applyImport"
      >确认导入</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { useAuthApi } from '@/api/auth'
import {
  managementErrorCodeMessage,
  managementErrorMessage,
} from '@/utils/managementError'
import type { UserImportPlan } from '@/types/auth'

const props = defineProps<{ domainId?: string }>()
const emit = defineEmits<{ imported: [] }>()
const api = useAuthApi()
const visible = ref(false)
const file = ref<File | null>(null)
const plan = ref<UserImportPlan | null>(null)
const loading = ref(false)

function open() {
  file.value = null
  plan.value = null
  visible.value = true
}

function selectFile(event: Event) {
  file.value = (event.target as HTMLInputElement).files?.[0] ?? null
  plan.value = null
}

async function run(dryRun: boolean): Promise<UserImportPlan> {
  if (!file.value) throw new Error('请选择文件')
  return props.domainId
    ? api.importDomainUsers(props.domainId, file.value, dryRun)
    : api.importUsers(file.value, dryRun)
}

async function preview() {
  loading.value = true
  try {
    plan.value = await run(true)
  } catch (error) {
    ElMessage.error(await managementErrorMessage(error, '预检失败'))
  } finally {
    loading.value = false
  }
}

async function applyImport() {
  if (!plan.value || plan.value.errors.length) return
  loading.value = true
  try {
    const applied = await run(false)
    plan.value = applied
    if (!applied.applied) {
      ElMessage.error('导入未执行，请检查错误行')
      return
    }
    ElMessage.success('导入完成')
    visible.value = false
    emit('imported')
  } catch (error) {
    ElMessage.error(await managementErrorMessage(error, '导入失败'))
  } finally {
    loading.value = false
  }
}

defineExpose({ open })
</script>

<style scoped>
.ui-import__help { margin-bottom: 14px; }
.ui-import__file { margin-top: 8px; color: var(--kb-text-secondary); }
.ui-import__summary { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 16px; }
.ui-import__summary .el-table { width: 100%; margin-top: 8px; }
</style>
