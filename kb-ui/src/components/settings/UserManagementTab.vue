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
      <el-table-column label="知识域" width="140">
        <template #default="{ row }">
          <template v-if="row.site_role === 'admin'">
            <el-tag size="small" type="info">全域通行</el-tag>
          </template>
          <template v-else-if="userDomains[row.id]">
            <el-tag
              v-for="d in userDomains[row.id]!.slice(0, 2)"
              :key="d"
              size="small"
              style="margin-right: 4px"
            >
              {{ domainLabel(d) }}
            </el-tag>
            <el-tag v-if="userDomains[row.id]!.length > 2" size="small" type="info">
              +{{ userDomains[row.id]!.length - 2 }}
            </el-tag>
            <span v-if="userDomains[row.id]!.length === 0" class="um__hint">未分配</span>
          </template>
          <span v-else class="um__hint">—</span>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="340">
        <template #default="{ row }">
          <el-button size="small" link @click="openEdit(row)">编辑</el-button>
          <el-tooltip
            v-if="row.site_role === 'admin'"
            content="管理员默认全域通行"
            placement="top"
          >
            <span class="um__disabled"><el-button size="small" link disabled>分配域</el-button></span>
          </el-tooltip>
          <el-button v-else size="small" link @click="openDomains(row)">分配域</el-button>
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
        <el-form-item label="角色">
          <el-select v-model="form.site_role">
            <el-option label="用户（工号，无密码）" value="member" />
            <el-option label="管理员（需密码）" value="admin" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="form.site_role === 'admin'" label="密码">
          <el-input v-model="form.password" type="password" placeholder="≥8 位" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" @click="confirmCreate">创建</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="editVisible" title="编辑用户" width="420">
      <el-form label-width="80">
        <el-form-item label="用户名">
          <el-input :model-value="editForm.username" disabled />
          <div class="um__hint">登录名不可改(身份键,用于登录与权限)。</div>
        </el-form-item>
        <el-form-item label="显示名">
          <el-input v-model="editForm.display_name" placeholder="留空则不显示" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" @click="confirmEdit">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="domainsVisible" :title="`分配知识域：${domainsForm.username}`" width="480">
      <el-select
        v-model="domainsForm.selected"
        multiple
        collapse-tags
        collapse-tags-tooltip
        placeholder="选择可访问的知识域"
        style="width: 100%"
        :loading="domainsLoading"
      >
        <el-option
          v-for="opt in domainOptions"
          :key="opt.value"
          :label="opt.label"
          :value="opt.value"
        />
      </el-select>
      <div class="um__hint">member 用户只能访问被分配的知识域；管理员默认全域通行。</div>
      <template #footer>
        <el-button @click="domainsVisible = false">取消</el-button>
        <el-button type="primary" :loading="domainsSaving" @click="confirmDomains">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useAuthApi } from '@/api/auth'
import { useAuthStore } from '@/stores/auth'
import { useDomainStore } from '@/stores/domain'
import { apiErrorDetail } from '@/api/proxyClient'
import type { AuthUser, SiteRole } from '@/types/auth'

interface UserRow extends AuthUser {
  id: string
  status: string
  has_password?: boolean
}

const api = useAuthApi()
const auth = useAuthStore()
const domainStore = useDomainStore()
const users = ref<UserRow[]>([])

/** 51号批次1：用户已绑定的知识域（打开分配弹窗时拉取缓存，列表接口不带域数据避免 N 次请求）。 */
const userDomains = ref<Record<string, string[]>>({})
const domainOptions = computed(() =>
  domainStore.domains.map(d => ({ value: d.domain_id, label: d.display_name || d.domain_id }))
)

function domainLabel(domainId: string): string {
  const hit = domainStore.domains.find(d => d.domain_id === domainId)
  return hit?.display_name || domainId
}

const domainsVisible = ref(false)
const domainsLoading = ref(false)
const domainsSaving = ref(false)
const domainsForm = ref<{ id: string; username: string; selected: string[] }>({
  id: '', username: '', selected: [],
})

async function openDomains(row: UserRow): Promise<void> {
  domainsForm.value = { id: row.id, username: row.username, selected: [] }
  domainsVisible.value = true
  if (domainStore.domains.length === 0) await domainStore.fetchDomains()
  domainsLoading.value = true
  try {
    const domains = await api.getUserDomains(row.id)
    userDomains.value = { ...userDomains.value, [row.id]: domains }
    // 仅当仍在该行弹窗时回显，避免竞态覆盖
    if (domainsForm.value.id === row.id) domainsForm.value.selected = [...domains]
  } catch (e) {
    ElMessage.error((await apiErrorDetail(e)) || '加载用户域失败')
  } finally {
    domainsLoading.value = false
  }
}

async function confirmDomains(): Promise<void> {
  try {
    domainsSaving.value = true
    const saved = await api.setUserDomains(domainsForm.value.id, domainsForm.value.selected)
    userDomains.value = { ...userDomains.value, [domainsForm.value.id]: saved }
    domainsVisible.value = false
    ElMessage.success('已保存')
  } catch (e) {
    // 422=空集 / 400=非法域，后端 detail 直接展示
    ElMessage.error((await apiErrorDetail(e)) || '保存失败')
  } finally {
    domainsSaving.value = false
  }
}

/** 不能禁用/降级自己（否则把自己锁死）；后端有同义守卫兜底。 */
function isSelf(row: UserRow): boolean {
  return !!auth.user && row.username === auth.user.username
}
const createVisible = ref(false)
const form = ref<{ username: string; display_name: string; password: string; site_role: SiteRole }>({
  username: '', display_name: '', password: '', site_role: 'member',
})

const editVisible = ref(false)
const editForm = ref<{ id: string; username: string; display_name: string }>({
  id: '', username: '', display_name: '',
})

function openEdit(row: UserRow): void {
  editForm.value = {
    id: row.id,
    username: row.username,
    display_name: row.display_name ?? '',
  }
  editVisible.value = true
}

async function confirmEdit(): Promise<void> {
  try {
    await api.updateUser(editForm.value.id, { display_name: editForm.value.display_name })
    editVisible.value = false
    await load()
    ElMessage.success('已更新')
  } catch (e) {
    ElMessage.error((await apiErrorDetail(e)) || '更新失败')
  }
}

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
  username: string; password?: string; site_role: SiteRole; display_name?: string
}): Promise<void> {
  await api.createUser(body)
}

async function confirmCreate(): Promise<void> {
  if (form.value.site_role === 'admin' && form.value.password.length < 8) {
    ElMessage.warning('管理员密码至少 8 位')
    return
  }
  try {
    await createUser({
      username: form.value.username,
      password: form.value.site_role === 'admin' ? form.value.password : undefined,
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
  if (row.site_role === 'member') {
    // 升 admin：后端要求先设密码 → 先 prompt 密码，reset 后再提升
    try {
      const { value } = await ElMessageBox.prompt('设为管理员需先设置密码（≥8 位）', `提升 ${row.username}`, {
        inputType: 'password',
        inputValidator: (v: string) => (v && v.length >= 8) || '至少 8 位',
      })
      await api.resetPassword(row.id, value)
      await api.updateUser(row.id, { site_role: 'admin' })
      await load()
      ElMessage.success('已设为管理员')
    } catch (e) {
      if (e !== 'cancel' && e !== 'close') ElMessage.error((await apiErrorDetail(e)) || '操作失败')
    }
  } else {
    // 降 admin → member
    try {
      await api.updateUser(row.id, { site_role: 'member' })
      await load()
    } catch (e) {
      ElMessage.error((await apiErrorDetail(e)) || '操作失败')
    }
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
