<template>
  <div class="kb-members">
    <div v-if="canWrite" class="kb-members__add">
      <el-select
        v-model="selectedUsername"
        filterable
        placeholder="选择用户（登录名）"
        size="small"
        class="kb-members__user-select"
        :loading="loadingCandidates"
        no-data-text="无可添加的用户"
      >
        <el-option
          v-for="u in candidates"
          :key="u.id"
          :value="u.username"
          :label="u.display_name ? `${u.display_name}（${u.username}）` : u.username"
        />
      </el-select>
      <el-select v-model="newRole" size="small" class="kb-members__role">
        <el-option v-if="visibility !== 'public'" label="只读" value="viewer" />
        <el-option label="编辑者" value="editor" />
      </el-select>
      <el-button type="primary" size="small" :loading="adding" @click="addOne">
        添加成员
      </el-button>
    </div>
    <p v-else class="kb-members__hint">仅拥有者或编辑者可管理成员。</p>
    <p v-if="canWrite && visibility === 'public'" class="kb-members__hint">
      公开库全员可读,无需添加只读成员;此处仅可授予「编辑者」写权限。
    </p>

    <div class="kb-members__table-wrap">
      <el-table
        :data="members"
        v-loading="loading"
        class="kb-table"
        :header-cell-style="{ background: 'transparent' }"
      >
        <el-table-column label="用户名" min-width="180">
          <template #default="{ row }">
            <span class="kb-member-name">{{ row.username }}</span>
            <span v-if="row.display_name" class="kb-member-dn">{{ row.display_name }}</span>
          </template>
        </el-table-column>
        <el-table-column label="角色" width="120">
          <template #default="{ row }">
            <el-tag :type="roleTagType(row.role)" size="small" effect="plain">
              {{ roleLabel(row.role) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="加入时间" min-width="160">
          <template #default="{ row }">{{ formatTime(row.added_at) }}</template>
        </el-table-column>
        <el-table-column v-if="canWrite" label="操作" width="90" fixed="right">
          <template #default="{ row }">
            <el-button size="small" text type="danger" @click="removeOne(row)">
              移除
            </el-button>
          </template>
        </el-table-column>
        <template #empty>
          <EmptyState text="还没有成员（拥有者即创建者，不在成员列表中）" />
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
    ElMessage.warning('请选择用户')
    return
  }
  adding.value = true
  try {
    await kbApi.addMember(props.kbId, { username, role: newRole.value })
    ElMessage.success(`已添加 ${username}`)
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
      `确定将 ${row.username} 移出该知识库？`,
      '移除成员',
      { type: 'warning' },
    )
  } catch {
    return // 用户取消
  }
  try {
    await kbApi.removeMember(props.kbId, row.user_id)
    ElMessage.success(`已移除 ${row.username}`)
    await Promise.all([loadMembers(), loadCandidates()])
  } catch (e) {
    ElMessage.error(await apiErrorDetail(e))
  }
}

function formatTime(t: string): string {
  if (!t) return '-'
  return new Date(t).toLocaleString('zh-CN')
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
