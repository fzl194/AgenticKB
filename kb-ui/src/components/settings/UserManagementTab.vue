<template>
  <div class="um">
    <div class="um__bar">
      <el-button v-if="isGlobal" size="small" @click="openCreate">新建用户</el-button>
      <el-button v-else size="small" type="primary" @click="openAddDomainUser">
        添加本域成员
      </el-button>
    </div>

    <el-table :data="users" size="small">
      <el-table-column prop="username" label="用户名" />
      <el-table-column prop="display_name" label="显示名" />
      <el-table-column :label="isGlobal ? '系统角色' : '域角色'" width="110">
        <template #default="{ row }">
          {{ isGlobal ? siteRoleLabel(row.site_role) : domainRoleLabel(row.domain_role) }}
        </template>
      </el-table-column>
      <el-table-column v-if="isGlobal" prop="status" label="状态" width="90" />
      <el-table-column v-if="isGlobal" label="知识域" min-width="210">
        <template #default="{ row }">
          <el-tag v-if="row.site_role === 'admin'" size="small" type="info">全域通行</el-tag>
          <template v-else-if="userDomains[row.id]">
            <el-tag
              v-for="grant in userDomains[row.id]!.slice(0, 3)"
              :key="grant.domain"
              size="small"
              :type="grant.domain_role === 'admin' ? 'danger' : undefined"
              class="um__domain-tag"
            >
              {{ domainLabel(grant.domain) }} · {{ domainRoleLabel(grant.domain_role) }}
            </el-tag>
            <span v-if="userDomains[row.id]!.length === 0" class="um__hint">未分配</span>
          </template>
          <span v-else class="um__hint">—</span>
        </template>
      </el-table-column>
      <el-table-column label="操作" :width="isGlobal ? 340 : 150">
        <template #default="{ row }">
          <template v-if="isGlobal">
            <el-button size="small" link @click="openEdit(row)">编辑</el-button>
            <el-tooltip v-if="row.site_role === 'admin'" content="系统管理员默认全域通行" placement="top">
              <span class="um__disabled"><el-button size="small" link disabled>分配域</el-button></span>
            </el-tooltip>
            <el-button v-else size="small" link @click="openDomains(row)">分配域权限</el-button>
            <el-button size="small" link @click="resetPw(row)">重置密码</el-button>
            <el-button v-if="!isSelf(row)" size="small" link @click="toggleStatus(row)">
              {{ row.status === 'active' ? '禁用' : '启用' }}
            </el-button>
            <el-button v-if="!isSelf(row)" size="small" link @click="toggleRole(row)">
              设为{{ row.site_role === 'admin' ? '用户' : '管理员' }}
            </el-button>
            <span v-if="isSelf(row)" class="um__self-mark">（你）</span>
          </template>
          <template v-else>
            <el-button
              v-if="row.domain_role === 'member'"
              size="small"
              link
              type="danger"
              @click="removeFromDomain(row)"
            >从本域移除</el-button>
            <span v-else class="um__hint">域管理员由系统管理员维护</span>
          </template>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-if="isGlobal" v-model="createVisible" title="新建用户" width="420">
      <el-form label-width="80">
        <el-form-item label="用户名"><el-input v-model="form.username" /></el-form-item>
        <el-form-item label="显示名"><el-input v-model="form.display_name" /></el-form-item>
        <el-form-item label="角色">
          <el-select v-model="form.site_role">
            <el-option label="用户（工号，无密码）" value="member" />
            <el-option label="系统管理员（需密码）" value="admin" />
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

    <el-dialog v-if="isGlobal" v-model="editVisible" title="编辑用户" width="420">
      <el-form label-width="80">
        <el-form-item label="用户名">
          <el-input :model-value="editForm.username" disabled />
          <div class="um__hint">登录名不可改（身份键，用于登录与权限）。</div>
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

    <el-dialog v-if="isGlobal" v-model="domainsVisible" :title="`分配域权限：${domainsForm.username}`" width="560">
      <div v-for="option in domainOptions" :key="option.value" class="um__grant-row">
        <el-checkbox
          :model-value="!!grantFor(option.value)"
          @change="toggleDomainGrant(option.value, Boolean($event))"
        >{{ option.label }}</el-checkbox>
        <el-select
          v-if="grantFor(option.value)"
          :model-value="grantFor(option.value)?.domain_role"
          size="small"
          class="um__grant-role"
          @change="setDomainRole(option.value, $event)"
        >
          <el-option label="普通用户" value="member" />
          <el-option label="域管理员" value="admin" />
        </el-select>
      </div>
      <div class="um__hint">域管理员可管理该域用户和该域全部知识库；系统管理员始终全域通行。</div>
      <template #footer>
        <el-button @click="domainsVisible = false">取消</el-button>
        <el-button type="primary" :loading="domainsSaving" :disabled="domainsLoading" @click="confirmDomains">
          保存
        </el-button>
      </template>
    </el-dialog>

    <el-dialog v-if="!isGlobal" v-model="addVisible" title="添加本域成员" width="420">
      <el-form label-width="80">
        <el-form-item label="用户名">
          <el-input v-model="addUsername" placeholder="输入已有普通用户的准确用户名" />
        </el-form-item>
      </el-form>
      <div class="um__hint">域管理员只能添加已有且已启用的普通用户。</div>
      <template #footer>
        <el-button @click="addVisible = false">取消</el-button>
        <el-button type="primary" :loading="memberSaving" @click="confirmAddDomainUser">添加</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useAuthApi } from '@/api/auth'
import { useAuthStore } from '@/stores/auth'
import { useDomainStore } from '@/stores/domain'
import { apiErrorDetail } from '@/api/proxyClient'
import type {
  AuthUser,
  DomainRole,
  DomainUser,
  SiteRole,
  UserDomainGrant,
} from '@/types/auth'

defineOptions({ name: 'UserManagementTab' })

const props = defineProps<{ domainId?: string }>()

interface UserRow extends AuthUser {
  id: string
  status: string
  has_password?: boolean
  domains: string[]
  domain_grants: UserDomainGrant[]
  domain_role?: DomainRole
}

const api = useAuthApi()
const auth = useAuthStore()
const domainStore = useDomainStore()
const isGlobal = computed(() => !props.domainId)
const users = ref<UserRow[]>([])
const userDomains = ref<Record<string, UserDomainGrant[]>>({})
const domainOptions = computed(() =>
  domainStore.domains.map(domain => ({
    value: domain.domain_id,
    label: domain.display_name || domain.domain_id,
  })),
)

function siteRoleLabel(role: SiteRole): string {
  return role === 'admin' ? '系统管理员' : '用户'
}

function domainRoleLabel(role: DomainRole | undefined): string {
  return role === 'admin' ? '域管理员' : '普通用户'
}

function domainLabel(domainId: string): string {
  return domainStore.domains.find(domain => domain.domain_id === domainId)?.display_name || domainId
}

const domainsVisible = ref(false)
const domainsLoading = ref(false)
const domainsSaving = ref(false)
let domainsLoadVersion = 0
const domainsForm = ref<{ id: string; username: string; grants: UserDomainGrant[] }>({
  id: '', username: '', grants: [],
})

function grantFor(domain: string): UserDomainGrant | undefined {
  return domainsForm.value.grants.find(grant => grant.domain === domain)
}

function toggleDomainGrant(domain: string, selected: boolean): void {
  const retained = domainsForm.value.grants.filter(grant => grant.domain !== domain)
  domainsForm.value = {
    ...domainsForm.value,
    grants: selected ? [...retained, { domain, domain_role: 'member' }] : retained,
  }
}

function setDomainRole(domain: string, domainRole: DomainRole): void {
  domainsForm.value = {
    ...domainsForm.value,
    grants: domainsForm.value.grants.map(grant =>
      grant.domain === domain ? { ...grant, domain_role: domainRole } : grant
    ),
  }
}

async function openDomains(row: UserRow): Promise<void> {
  const loadVersion = ++domainsLoadVersion
  domainsForm.value = { id: row.id, username: row.username, grants: [] }
  domainsVisible.value = true
  domainsLoading.value = true
  try {
    const grants = await api.getUserDomainGrants(row.id)
    userDomains.value = { ...userDomains.value, [row.id]: grants }
    users.value = users.value.map(user =>
      user.id === row.id
        ? { ...user, domains: grants.map(grant => grant.domain), domain_grants: grants }
        : user
    )
    if (domainsForm.value.id === row.id) {
      domainsForm.value = { ...domainsForm.value, grants: grants.map(grant => ({ ...grant })) }
    }
  } catch (error) {
    ElMessage.error((await apiErrorDetail(error)) || '加载用户域权限失败')
  } finally {
    if (loadVersion === domainsLoadVersion) domainsLoading.value = false
  }
}

async function confirmDomains(): Promise<void> {
  try {
    domainsSaving.value = true
    const saved = await api.setUserDomainGrants(domainsForm.value.id, domainsForm.value.grants)
    userDomains.value = { ...userDomains.value, [domainsForm.value.id]: saved }
    users.value = users.value.map(user =>
      user.id === domainsForm.value.id
        ? { ...user, domains: saved.map(grant => grant.domain), domain_grants: saved }
        : user
    )
    domainsVisible.value = false
    ElMessage.success('已保存')
  } catch (error) {
    ElMessage.error((await apiErrorDetail(error)) || '保存失败')
  } finally {
    domainsSaving.value = false
  }
}

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
  } catch (error) {
    ElMessage.error((await apiErrorDetail(error)) || '更新失败')
  }
}

function normaliseGlobalUser(user: AuthUser & {
  id: string
  status: string
  has_password?: boolean
  domains: string[]
  domain_grants?: UserDomainGrant[]
}): UserRow {
  const grants = user.domain_grants
    ?? (user.domains ?? []).map(domain => ({ domain, domain_role: 'member' as const }))
  return { ...user, domains: grants.map(grant => grant.domain), domain_grants: grants }
}

function normaliseDomainUser(user: DomainUser, domain: string): UserRow {
  const grant = { domain, domain_role: user.domain_role }
  return {
    ...user,
    site_role: 'member',
    status: 'active',
    domains: [domain],
    domain_grants: [grant],
  }
}

async function load(): Promise<void> {
  try {
    if (!isGlobal.value && props.domainId) {
      const listed = await api.listDomainUsers(props.domainId)
      users.value = listed.map(user => normaliseDomainUser(user, props.domainId!))
      userDomains.value = {}
      return
    }
    const listed = await api.listUsers()
    users.value = listed.map(normaliseGlobalUser)
    userDomains.value = Object.fromEntries(
      users.value
        .filter(user => user.site_role !== 'admin')
        .map(user => [user.id, user.domain_grants.map(grant => ({ ...grant }))]),
    )
  } catch (error) {
    users.value = []
    ElMessage.error((await apiErrorDetail(error)) || '加载失败')
  }
}

function openCreate(): void {
  form.value = { username: '', display_name: '', password: '', site_role: 'member' }
  createVisible.value = true
}

async function createUser(body: {
  username: string
  password?: string
  site_role: SiteRole
  display_name?: string
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
  } catch (error) {
    ElMessage.error((await apiErrorDetail(error)) || '创建失败')
  }
}

async function resetPw(row: UserRow): Promise<void> {
  try {
    const { value } = await ElMessageBox.prompt('输入新密码（≥8 位）', `重置 ${row.username} 密码`, {
      inputType: 'password',
      inputValidator: (input: string) => (input && input.length >= 8) || '至少 8 位',
    })
    await api.resetPassword(row.id, value)
    ElMessage.success('已重置')
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') {
      ElMessage.error((await apiErrorDetail(error)) || '重置失败')
    }
  }
}

async function toggleStatus(row: UserRow): Promise<void> {
  try {
    await api.updateUser(row.id, { status: row.status === 'active' ? 'disabled' : 'active' })
    await load()
  } catch (error) {
    ElMessage.error((await apiErrorDetail(error)) || '操作失败')
  }
}

async function toggleRole(row: UserRow): Promise<void> {
  try {
    if (row.site_role === 'member') {
      const { value } = await ElMessageBox.prompt(
        '设为系统管理员需先设置密码（≥8 位）',
        `提升 ${row.username}`,
        {
          inputType: 'password',
          inputValidator: (input: string) => (input && input.length >= 8) || '至少 8 位',
        },
      )
      await api.resetPassword(row.id, value)
      await api.updateUser(row.id, { site_role: 'admin' })
    } else {
      await api.updateUser(row.id, { site_role: 'member' })
    }
    await load()
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') {
      ElMessage.error((await apiErrorDetail(error)) || '操作失败')
    }
  }
}

const addVisible = ref(false)
const addUsername = ref('')
const memberSaving = ref(false)

function openAddDomainUser(): void {
  addUsername.value = ''
  addVisible.value = true
}

async function confirmAddDomainUser(): Promise<void> {
  const username = addUsername.value.trim()
  if (!props.domainId || !username) {
    ElMessage.warning('请输入用户名')
    return
  }
  try {
    memberSaving.value = true
    await api.addDomainUser(props.domainId, username)
    addVisible.value = false
    await load()
    ElMessage.success('已添加本域成员')
  } catch (error) {
    ElMessage.error((await apiErrorDetail(error)) || '添加失败')
  } finally {
    memberSaving.value = false
  }
}

async function removeFromDomain(row: UserRow): Promise<void> {
  if (!props.domainId) return
  try {
    await ElMessageBox.confirm(
      `确认将 ${row.username} 从当前域移除？若其仍拥有本域知识库，后端会拒绝并要求先转移所有者。`,
      '移除本域成员',
      { type: 'warning' },
    )
    await api.removeDomainUser(props.domainId, row.id)
    await load()
    ElMessage.success('已移除')
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') {
      ElMessage.error((await apiErrorDetail(error)) || '移除失败')
    }
  }
}

onMounted(load)
watch(() => props.domainId, (next, previous) => {
  if (next !== previous) void load()
})

defineExpose({
  load,
  createUser,
  openDomains,
  users,
  userDomains,
  domainsLoading,
  isGlobal,
})
</script>

<style scoped>
.um__bar {
  margin-bottom: 12px;
  display: flex;
  justify-content: flex-end;
}

.um__domain-tag {
  margin: 0 4px 4px 0;
}

.um__grant-row {
  display: flex;
  min-height: 42px;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid var(--kb-border);
}

.um__grant-role {
  width: 130px;
}

.um__hint {
  color: var(--kb-text-tertiary);
  font-size: 12px;
  line-height: 1.6;
}

.um__self-mark {
  color: var(--kb-text-tertiary);
  font-size: 12px;
}

.um__disabled {
  display: inline-flex;
}
</style>
