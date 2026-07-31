<template>
  <div class="kb-members">
    <div v-if="canWrite" class="kb-members__add">
      <el-input
        v-model="newUsername"
        placeholder="用户名（登录名 / X-KB-User）"
        size="small"
        class="kb-members__username"
        @keyup.enter="addOne"
      />
      <el-select v-model="newRole" size="small" class="kb-members__role">
        <el-option label="编辑者" value="editor" />
        <el-option label="只读" value="viewer" />
      </el-select>
      <el-button type="primary" size="small" :loading="adding" @click="addOne">
        添加成员
      </el-button>
    </div>
    <p v-else class="kb-members__hint">仅拥有者或编辑者可管理成员。</p>

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
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useKbApi } from '@/api/kb'
import { apiErrorDetail } from '@/api/proxyClient'
import EmptyState from '@/components/common/EmptyState.vue'
import { roleLabel, roleTagType } from '@/views/kb/kbMeta'
import type { KbMember, KbMemberRole } from '@/types/kb'

const props = defineProps<{ kbId: string; canWrite: boolean }>()
const kbApi = useKbApi()

const members = ref<KbMember[]>([])
const loading = ref(false)
const adding = ref(false)
const newUsername = ref('')
const newRole = ref<KbMemberRole>('viewer')

async function load() {
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

async function addOne() {
  const username = newUsername.value.trim()
  if (!username) {
    ElMessage.warning('请输入用户名')
    return
  }
  adding.value = true
  try {
    await kbApi.addMember(props.kbId, { username, role: newRole.value })
    ElMessage.success(`已添加 ${username}`)
    newUsername.value = ''
    await load()
  } catch (e) {
    ElMessage.error(await apiErrorDetail(e))
  } finally {
    adding.value = false
  }
}

async function removeOne(row: KbMember) {
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
    await load()
  } catch (e) {
    ElMessage.error(await apiErrorDetail(e))
  }
}

function formatTime(t: string): string {
  if (!t) return '-'
  return new Date(t).toLocaleString('zh-CN')
}

onMounted(load)
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

.kb-members__username {
  max-width: 280px;
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
