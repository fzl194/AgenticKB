<template>
  <div class="um">
    <div class="um__bar">
      <el-button size="small" @click="openCreate">新建用户</el-button>
    </div>

    <el-table :data="users" size="small">
      <el-table-column prop="username" label="用户名" />
      <el-table-column prop="display_name" label="显示名" />
      <el-table-column prop="site_role" label="角色" width="90" />
      <el-table-column prop="status" label="状态" width="90" />
      <el-table-column label="操作" width="240">
        <template #default="{ row }">
          <el-button size="small" link @click="resetPw(row)">重置密码</el-button>
          <el-button
            v-if="!isSelf(row)"
            size="small"
            link
            @click="toggleStatus(row)"
          >
            {{ row.status === 'active' ? '禁用' : '启用' }}
          </el-button>
          <el-button
            v-if="!isSelf(row)"
            size="small"
            link
            @click="toggleRole(row)"
          >
            设为{{ row.site_role === 'admin' ? '用户' : '管理员' }}
          </el-button>
          <span v-if="isSelf(row)" class="um__self-mark">（你）</span>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="createVisible" title="新建用户" width="420">
      <el-form label-width="80">
        <el-form-item label="用户名"><el-input v-model="form.username" /></el-form-item>
        <el-form-item label="显示名"><el-input v-model="form.display_name" /></el-form-item>
        <el-form-item label="密码"><el-input v-model="form.password" type="password" /></el-form-item>
        <el-form-item label="角色">
          <el-select v-model="form.site_role">
            <el-option label="管理员" value="admin" />
            <el-option label="用户" value="member" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" @click="confirmCreate">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useAuthApi } from '@/api/auth'
import { useAuthStore } from '@/stores/auth'
import { apiErrorDetail } from '@/api/proxyClient'
import type { AuthUser, SiteRole } from '@/types/auth'

interface UserRow extends AuthUser {
  id: string
  status: string
  has_password?: boolean
}

const api = useAuthApi()
const auth = useAuthStore()
const users = ref<UserRow[]>([])

/** 不能禁用/降级自己（否则把自己锁死）；后端有同义守卫兜底。 */
function isSelf(row: UserRow): boolean {
  return !!auth.user && row.username === auth.user.username
}
const createVisible = ref(false)
const form = ref<{ username: string; display_name: string; password: string; site_role: SiteRole }>({
  username: '', display_name: '', password: '', site_role: 'member',
})

async function load(): Promise<void> {
  try {
    users.value = await api.listUsers()
  } catch (e) {
    ElMessage.error((await apiErrorDetail(e)) || '加载失败')
  }
}

function openCreate(): void {
  form.value = { username: '', display_name: '', password: '', site_role: 'member' }
  createVisible.value = true
}

async function createUser(body: {
  username: string; password: string; site_role: SiteRole; display_name?: string
}): Promise<void> {
  await api.createUser(body)
}

async function confirmCreate(): Promise<void> {
  if (form.value.password.length < 8) {
    ElMessage.warning('密码至少 8 位')
    return
  }
  try {
    await createUser({
      username: form.value.username,
      password: form.value.password,
      site_role: form.value.site_role,
      display_name: form.value.display_name || undefined,
    })
    createVisible.value = false
    await load()
    ElMessage.success('已创建')
  } catch (e) {
    ElMessage.error((await apiErrorDetail(e)) || '创建失败')
  }
}

async function resetPw(row: UserRow): Promise<void> {
  try {
    const { value } = await ElMessageBox.prompt('输入新密码（≥8 位）', `重置 ${row.username} 密码`, {
      inputType: 'password',
      inputValidator: (v: string) => (v && v.length >= 8) || '至少 8 位',
    })
    await api.resetPassword(row.id, value)
    ElMessage.success('已重置')
  } catch (e) {
    if (e !== 'cancel' && e !== 'close') ElMessage.error((await apiErrorDetail(e)) || '重置失败')
  }
}

async function toggleStatus(row: UserRow): Promise<void> {
  const next = row.status === 'active' ? 'disabled' : 'active'
  try {
    await api.updateUser(row.id, { status: next })
    await load()
  } catch (e) {
    ElMessage.error((await apiErrorDetail(e)) || '操作失败')
  }
}

async function toggleRole(row: UserRow): Promise<void> {
  const next: SiteRole = row.site_role === 'admin' ? 'member' : 'admin'
  try {
    await api.updateUser(row.id, { site_role: next })
    await load()
  } catch (e) {
    ElMessage.error((await apiErrorDetail(e)) || '操作失败')
  }
}

onMounted(load)
defineExpose({ load, createUser, users })
</script>

<style scoped>
.um__bar {
  margin-bottom: 12px;
  display: flex;
  justify-content: flex-end;
}
</style>
