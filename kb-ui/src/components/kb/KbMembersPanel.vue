<template>
  <div class="kb-members">
    <div v-if="canWrite" class="kb-members__add">
      <el-select
        v-model="selectedUsername"
        filterable
        :placeholder="t('kbManage.members.pickUser')"
        size="small"
        class="kb-members__user-select"
        :loading="loadingCandidates"
        :no-data-text="t('kbManage.members.noUsers')"
      >
        <el-option
          v-for="u in candidates"
          :key="u.id"
          :value="u.username"
          :label="u.display_name ? `${u.display_name}（${u.username}）` : u.username"
        />
      </el-select>
      <el-select v-model="newRole" size="small" class="kb-members__role">
        <el-option v-if="visibility !== 'public'" :label="t('kbView.role.viewer')" value="viewer" />
        <el-option :label="t('kbView.role.editor')" value="editor" />
      </el-select>
      <el-button type="primary" size="small" :loading="adding" @click="addOne">
        {{ t('kbManage.members.add') }}
      </el-button>
    </div>
    <p v-else class="kb-members__hint">{{ t('kbManage.members.writeOnlyHint') }}</p>
    <p v-if="canWrite && visibility === 'public'" class="kb-members__hint">
      {{ t('kbManage.members.publicHint') }}
    </p>

    <div class="kb-members__table-wrap">
      <el-table
        :data="members"
        v-loading="loading"
        class="kb-table"
        :header-cell-style="{ background: 'transparent' }"
      >
        <el-table-column :label="t('kbManage.members.username')" min-width="180">
          <template #default="{ row }">
            <span class="kb-member-name">{{ row.username }}</span>
            <span v-if="row.display_name" class="kb-member-dn">{{ row.display_name }}</span>
          </template>
        </el-table-column>
        <el-table-column :label="t('kbManage.members.role')" width="120">
          <template #default="{ row }">
            <el-tag :type="roleTagType(row.role)" size="small" effect="plain">
              {{ roleLabel(row.role) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column :label="t('kbManage.members.joinedAt')" min-width="160">
          <template #default="{ row }">{{ formatTime(row.added_at) }}</template>
        </el-table-column>
        <el-table-column v-if="canWrite" :label="t('common.actions')" width="90" fixed="right">
          <template #default="{ row }">
            <el-button size="small" text type="danger" @click="removeOne(row)">
              {{ t('kbManage.members.remove') }}
            </el-button>
          </template>
        </el-table-column>
        <template #empty>
          <EmptyState :text="t('kbManage.members.empty')" />
        </template>
      </el-table>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useKbApi } from '@/api/kb'
import { apiErrorDetail } from '@/api/proxyClient'
import EmptyState from '@/components/common/EmptyState.vue'
import { roleLabel, roleTagType } from '@/views/kb/kbMeta'
import type { KbMember, KbMemberRole, KbUserCandidate, KbVisibility } from '@/types/kb'
import { t } from '@/locales/t'

const props = defineProps<{ kbId: string; canWrite: boolean; visibility: KbVisibility }>()
const kbApi = useKbApi()

const members = ref<KbMember[]>([])
const candidates = ref<KbUserCandidate[]>([])
const loading = ref(false)
const loadingCandidates = ref(false)
const adding = ref(false)
const selectedUsername = ref('')
const newRole = ref<KbMemberRole>(props.visibility === 'public' ? 'editor' : 'viewer')

// public 库下 viewer 选项被隐藏;切到 public 时若当前停在 viewer,归正为 editor。
watch(() => props.visibility, (v) => {
  if (v === 'public' && newRole.value === 'viewer') newRole.value = 'editor'
})

async function loadMembers(): Promise<void> {
  loading.value = true
  try {
    members.value = await kbApi.listMembers(props.kbId)
  } catch (e) {
    members.value = []
    ElMessage.error(await apiErrorDetail(e))
  } finally {
    loading.value = false
  }
}

async function loadCandidates(): Promise<void> {
  loadingCandidates.value = true
  try {
    candidates.value = await kbApi.listMemberCandidates(props.kbId)
  } catch (e) {
    // 候选拉取失败(如无写权限)静默:选择器显示空,不阻塞成员列表。
    candidates.value = []
    void e
  } finally {
    loadingCandidates.value = false
  }
}

async function addOne(): Promise<void> {
  const username = selectedUsername.value
  if (!username) {
    ElMessage.warning(t('kbManage.members.pickRequired'))
    return
  }
  adding.value = true
  try {
    await kbApi.addMember(props.kbId, { username, role: newRole.value })
    ElMessage.success(t('kbManage.members.added', { username }))
    selectedUsername.value = ''
    await Promise.all([loadMembers(), loadCandidates()])
  } catch (e) {
    ElMessage.error(await apiErrorDetail(e))
  } finally {
    adding.value = false
  }
}

async function removeOne(row: KbMember): Promise<void> {
  try {
    await ElMessageBox.confirm(
      t('kbManage.members.removeConfirm', { username: row.username }),
      t('kbManage.members.removeTitle'),
      { type: 'warning' },
    )
  } catch {
    return // 用户取消
  }
  try {
    await kbApi.removeMember(props.kbId, row.user_id)
    ElMessage.success(t('kbManage.members.removed', { username: row.username }))
    await Promise.all([loadMembers(), loadCandidates()])
  } catch (e) {
    ElMessage.error(await apiErrorDetail(e))
  }
}

function formatTime(value: string): string {
  if (!value) return '-'
  return new Date(value).toLocaleString('zh-CN')
}

onMounted(() => {
  loadMembers()
  if (props.canWrite) loadCandidates()
})
</script>

<style scoped>
.kb-members {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.kb-members__add {
  display: flex;
  gap: 8px;
  align-items: center;
}

.kb-members__user-select {
  flex: 1;
  max-width: 320px;
}

.kb-members__role {
  width: 120px;
}

.kb-members__hint {
  font-size: 12.5px;
  color: var(--kb-text-tertiary);
  margin: 0;
}

.kb-members__table-wrap {
  background: var(--kb-bg-card);
  border-radius: var(--kb-radius);
  border: 1px solid var(--kb-border-light);
  overflow: hidden;
}

.kb-member-name {
  font-weight: 500;
  font-size: 13px;
  color: var(--kb-text-primary);
}

.kb-member-dn {
  margin-left: 8px;
  font-size: 12px;
  color: var(--kb-text-tertiary);
}
</style>
