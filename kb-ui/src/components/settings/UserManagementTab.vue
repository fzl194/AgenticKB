<template>
  <div class="um">
    <div class="um__bar">
      <template v-if="isGlobal">
        <el-checkbox v-model="showDeleted" @change="load">显示已删除用户</el-checkbox>
        <el-button size="small" @click="importDialog?.open()">CSV/XLSX 导入</el-button>
        <el-button size="small" @click="openCreate">新建用户</el-button>
      </template>
      <template v-else>
        <el-button size="small" @click="importDialog?.open()">批量添加</el-button>
        <el-button size="small" type="primary" @click="openAddDomainUser">添加本域成员</el-button>
      </template>
    </div>

    <el-table :data="users" size="small">
      <el-table-column prop="username" label="用户名" />
      <el-table-column prop="display_name" label="显示名" />
      <el-table-column :label="isGlobal ? '系统角色' : '域角色'" width="110">
        <template #default="{ row }">
          {{ isGlobal ? siteRoleLabel(row.site_role) : domainRoleLabel(row.domain_role) }}
        </template>
      </el-table-column>
      <el-table-column v-if="isGlobal" label="状态" width="90">
        <template #default="{ row }">
          {{ row.deleted_at ? '已删除' : row.status === 'active' ? '启用' : '禁用' }}
        </template>
      </el-table-column>
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
      <el-table-column label="操作" :width="isGlobal ? 410 : 150">
        <template #default="{ row }">
          <template v-if="isGlobal">
            <template v-if="row.deleted_at">
              <el-button size="small" link type="primary" @click="restoreDeletedUser(row)">
                恢复账号
              </el-button>
            </template>
            <template v-else>
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
              <el-button v-if="!isSelf(row)" size="small" link type="danger" @click="removeUser(row)">
                删除
              </el-button>
              <span v-if="isSelf(row)" class="um__self-mark">（你）</span>
            </template>
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
      <el-form v-if="domainAdminNeedsInitialPassword" label-width="110" class="um__initial-password">
        <el-form-item label="初始密码">
          <el-input
            v-model="initialDomainPassword"
            type="password"
            show-password
            placeholder="该用户尚无密码，请设置至少 8 位"
          />
        </el-form-item>
      </el-form>
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
          <el-select
            v-model="addUsername"
            filterable
            remote
            clearable
            :remote-method="searchDomainCandidates"
            :loading="candidateLoading"
            placeholder="搜索用户名或显示名"
            style="width: 100%"
          >
            <el-option
              v-for="candidate in domainCandidates"
              :key="candidate.id"
              :label="candidate.display_name
                ? `${candidate.display_name}（${candidate.username}）`
                : candidate.username"
              :value="candidate.username"
              :disabled="candidate.already_in_domain"
            >
              <span>{{ candidate.display_name || candidate.username }}</span>
              <span class="um__candidate-meta">
                {{ candidate.username }}{{ candidate.already_in_domain ? ' · 已在本域' : '' }}
              </span>
            </el-option>
          </el-select>
        </el-form-item>
      </el-form>
      <div class="um__hint">只能选择已有、已启用的普通用户；不会展示其密码或其他域权限。</div>
      <template #footer>
        <el-button @click="addVisible = false">取消</el-button>
        <el-button type="primary" :loading="memberSaving" @click="confirmAddDomainUser">添加</el-button>
      </template>
    </el-dialog>
    <UserImportDialog ref="importDialog" :domain-id="props.domainId" @imported="load" />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useAuthApi } from '@/api/auth'
import { useAuthStore } from '@/stores/auth'
import { useDomainStore } from '@/stores/domain'
import { managementErrorMessage } from '@/utils/managementError'
import UserImportDialog from './UserImportDialog.vue'
import type {
  DomainRole,
  DomainUser,
  DomainUserCandidate,
  ManagedUser,
  SiteRole,
  UserDomainGrant,
} from '@/types/auth'

defineOptions({ name: 'UserManagementTab' })

const props = defineProps<{ domainId?: string }>()

interface UserRow extends ManagedUser {
  domain_grants: UserDomainGrant[]
  domain_role?: DomainRole
}

const api = useAuthApi()
const auth = useAuthStore()
const domainStore = useDomainStore()
const isGlobal = computed(() => !props.domainId)
const users = ref<UserRow[]>([])
const userDomains = ref<Record<string, UserDomainGrant[]>>({})
const showDeleted = ref(false)
const importDialog = ref<InstanceType<typeof UserImportDialog> | null>(null)
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
let domainsSaveVersion = 0
const domainsForm = ref<{
  id: string
  username: string
  has_password: boolean
  grants: UserDomainGrant[]
}>({
  id: '', username: '', has_password: false, grants: [],
})
const initialDomainPassword = ref('')
const domainAdminNeedsInitialPassword = computed(
  () => !domainsForm.value.has_password
    && domainsForm.value.grants.some(grant => grant.domain_role === 'admin'),
)

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
  domainsSaveVersion += 1
  domainsSaving.value = false
  domainsForm.value = {
    id: row.id,
    username: row.username,
    has_password: Boolean(row.has_password),
    grants: [],
  }
  initialDomainPassword.value = ''
  domainsVisible.value = true
  domainsLoading.value = true
  try {
    const grants = await api.getUserDomainGrants(row.id)
    if (loadVersion !== domainsLoadVersion) return
    userDomains.value = { ...userDomains.value, [row.id]: grants }
    users.value = users.value.map(user =>
      user.id === row.id
        ? { ...user, domains: grants.map(grant => grant.domain), domain_grants: grants }
        : user
    )
    domainsForm.value = { ...domainsForm.value, grants: grants.map(grant => ({ ...grant })) }
  } catch (error) {
    if (loadVersion === domainsLoadVersion) {
      ElMessage.error(await managementErrorMessage(error, '加载用户域权限失败'))
    }
  } finally {
    if (loadVersion === domainsLoadVersion) domainsLoading.value = false
  }
}

async function confirmDomains(): Promise<void> {
  if (
    domainAdminNeedsInitialPassword.value
    && initialDomainPassword.value.length < 8
  ) {
    ElMessage.warning('域管理员初始密码至少 8 位')
    return
  }
  const targetId = domainsForm.value.id
  const submittedGrants = domainsForm.value.grants.map(grant => ({ ...grant }))
  const dialogVersion = domainsLoadVersion
  const saveVersion = ++domainsSaveVersion
  try {
    domainsSaving.value = true
    const establishedPassword = domainAdminNeedsInitialPassword.value
    const saved = await api.setUserDomainGrants(
      targetId,
      submittedGrants,
      establishedPassword ? initialDomainPassword.value : undefined,
    )
    userDomains.value = { ...userDomains.value, [targetId]: saved }
    users.value = users.value.map(user =>
      user.id === targetId
        ? {
            ...user,
            has_password: establishedPassword ? true : user.has_password,
            domains: saved.map(grant => grant.domain),
            domain_grants: saved,
          }
        : user
    )
    if (
      establishedPassword
      && dialogVersion === domainsLoadVersion
      && domainsForm.value.id === targetId
    ) {
      domainsForm.value = { ...domainsForm.value, has_password: true }
    }
    if (dialogVersion === domainsLoadVersion && domainsForm.value.id === targetId) {
      initialDomainPassword.value = ''
      domainsVisible.value = false
      ElMessage.success('已保存')
    }
  } catch (error) {
    if (dialogVersion === domainsLoadVersion && domainsForm.value.id === targetId) {
      ElMessage.error(await managementErrorMessage(error, '保存失败'))
    }
  } finally {
    if (saveVersion === domainsSaveVersion) domainsSaving.value = false
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
    ElMessage.error(await managementErrorMessage(error, '更新失败'))
  }
}

function normaliseGlobalUser(user: ManagedUser): UserRow {
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

let usersLoadVersion = 0

async function load(): Promise<void> {
  const loadVersion = ++usersLoadVersion
  const requestedDomain = props.domainId
  const includeDeleted = showDeleted.value
  try {
    if (requestedDomain) {
      const listed = await api.listDomainUsers(requestedDomain)
      if (loadVersion !== usersLoadVersion) return
      users.value = listed.map(user => normaliseDomainUser(user, requestedDomain))
      userDomains.value = {}
      return
    }
    const listed = await api.listUsers(includeDeleted)
    if (loadVersion !== usersLoadVersion) return
    users.value = listed.map(normaliseGlobalUser)
    userDomains.value = Object.fromEntries(
      users.value
        .filter(user => user.site_role !== 'admin')
        .map(user => [user.id, user.domain_grants.map(grant => ({ ...grant }))]),
    )
  } catch (error) {
    if (loadVersion !== usersLoadVersion) return
    users.value = []
    ElMessage.error(await managementErrorMessage(error, '加载失败'))
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
    ElMessage.error(await managementErrorMessage(error, '创建失败'))
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
      ElMessage.error(await managementErrorMessage(error, '重置失败'))
    }
  }
}

async function toggleStatus(row: UserRow): Promise<void> {
  try {
    await api.updateUser(row.id, { status: row.status === 'active' ? 'disabled' : 'active' })
    await load()
  } catch (error) {
    ElMessage.error(await managementErrorMessage(error, '操作失败'))
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
      ElMessage.error(await managementErrorMessage(error, '操作失败'))
    }
  }
}

const addVisible = ref(false)
const addUsername = ref('')
const memberSaving = ref(false)
const candidateLoading = ref(false)
const domainCandidates = ref<DomainUserCandidate[]>([])
let candidateLoadVersion = 0

function openAddDomainUser(): void {
  addUsername.value = ''
  domainCandidates.value = []
  addVisible.value = true
  void searchDomainCandidates('')
}

async function searchDomainCandidates(query: string): Promise<void> {
  if (!props.domainId) return
  const version = ++candidateLoadVersion
  candidateLoading.value = true
  try {
    const candidates = await api.listDomainUserCandidates(props.domainId, query)
    if (version === candidateLoadVersion) {
      domainCandidates.value = candidates
    }
  } catch (error) {
    if (version === candidateLoadVersion) {
      domainCandidates.value = []
      ElMessage.error(await managementErrorMessage(error, '搜索候选用户失败'))
    }
  } finally {
    if (version === candidateLoadVersion) candidateLoading.value = false
  }
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
    domainCandidates.value = []
    await load()
    ElMessage.success('已添加本域成员')
  } catch (error) {
    ElMessage.error(await managementErrorMessage(error, '添加失败'))
  } finally {
    memberSaving.value = false
  }
}

async function removeUser(row: UserRow): Promise<void> {
  try {
    const preview = await api.previewUserDeletion(row.id)
    if (preview.owned_knowledge_bases.length) {
      const names = preview.owned_knowledge_bases
        .slice(0, 5)
        .map(kb => `${kb.name}（${kb.domain}）`)
        .join('、')
      ElMessage.error(`该用户仍拥有知识库：${names}。请先转移所有者。`)
      return
    }
    const impact = `将清退 ${preview.domain_count ?? 0} 个域绑定、`
      + `${preview.kb_member_count ?? 0} 个知识库成员关系、`
      + `${preview.mcp_key_count ?? 0} 把 MCP 钥匙。`
    const { value } = await ElMessageBox.prompt(
      `${impact}请输入用户名 ${row.username} 确认：`,
      '删除用户',
      {
        type: 'warning',
        confirmButtonText: '删除',
        cancelButtonText: '取消',
        inputValidator: (input: string) => input === row.username || '用户名不一致',
      },
    )
    await api.deleteUser(row.id, value)
    await load()
    ElMessage.success('用户已删除')
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') {
      ElMessage.error(await managementErrorMessage(error, '删除用户失败'))
    }
  }
}

async function restoreDeletedUser(row: UserRow): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `恢复 ${row.username}？账号将保持禁用，且不会恢复历史域权限、KB 成员关系或 MCP 钥匙。`,
      '恢复用户',
      { type: 'warning', confirmButtonText: '恢复', cancelButtonText: '取消' },
    )
    await api.restoreUser(row.id)
    await load()
    ElMessage.success('用户已恢复为禁用账号，请重新分配域后再启用')
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') {
      ElMessage.error(await managementErrorMessage(error, '恢复用户失败'))
    }
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
      ElMessage.error(await managementErrorMessage(error, '移除失败'))
    }
  }
}

onMounted(load)
watch(() => props.domainId, (next, previous) => {
  if (next !== previous) {
    candidateLoadVersion += 1
    domainCandidates.value = []
    addUsername.value = ''
    void load()
  }
})

defineExpose({
  load,
  createUser,
  openDomains,
  users,
  userDomains,
  domainsLoading,
  isGlobal,
  showDeleted,
  domainsForm,
  initialDomainPassword,
  domainAdminNeedsInitialPassword,
  domainCandidates,
  searchDomainCandidates,
  confirmDomains,
  addUsername,
  confirmAddDomainUser,
  removeUser,
  restoreDeletedUser,
})
</script>

<style scoped>
.um__bar {
  margin-bottom: 12px;
  display: flex;
  align-items: center;
  gap: 12px;
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

.um__initial-password {
  margin-top: 16px;
}

.um__candidate-meta {
  float: right;
  margin-left: 12px;
  color: var(--kb-text-tertiary);
  font-size: 12px;
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
