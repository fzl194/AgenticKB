<template>
  <div class="kb-settings">
    <div class="kb-settings__form">
      <el-form :model="form" label-width="72px" @submit.prevent>
        <el-form-item :label="t('common.name')">
          <el-input v-model="form.name" :disabled="!canWrite" maxlength="80" show-word-limit />
        </el-form-item>
        <el-form-item :label="t('kbManage.create.publicRead')">
          <el-switch
            v-model="isPublic"
            :disabled="!canWrite"
            :active-text="t('kbManage.create.publicText')"
            :inactive-text="t('kbManage.create.privateText')"
          />
        </el-form-item>
        <el-form-item :label="t('common.description')">
          <el-input
            v-model="form.description"
            type="textarea"
            :rows="3"
            :disabled="!canWrite"
            maxlength="300"
            show-word-limit
          />
        </el-form-item>
        <el-form-item v-if="canWrite">
          <el-button type="primary" :loading="saving" @click="save">{{ t('kbManage.settings.saveChanges') }}</el-button>
          <el-button @click="reset">{{ t('kbManage.settings.reset') }}</el-button>
        </el-form-item>
      </el-form>
    </div>

    <div v-if="canManageLifecycle" class="kb-settings__owner">
      <div>
        <div class="kb-settings__owner-title">{{ t('kbManage.settings.ownerTitle') }}</div>
        <div class="kb-settings__hint">{{ t('kbManage.settings.ownerHint') }}</div>
      </div>
      <el-button plain @click="openOwnerTransfer">{{ t('kbManage.settings.transferOwner') }}</el-button>
    </div>

    <div v-if="canManageLifecycle" class="kb-settings__danger">
      <div class="kb-settings__danger-text">
        <div class="kb-settings__danger-title">{{ t('kbManage.settings.deleteTitle') }}</div>
        <div class="kb-settings__danger-desc">
          {{ t('kbManage.settings.deleteDesc1') }}
          {{ t('kbManage.settings.deleteDesc2') }}
        </div>
      </div>
      <el-button type="danger" plain :loading="deleting" @click="confirmDelete">
        {{ t('kbManage.settings.deleteKb') }}
      </el-button>
    </div>

    <el-dialog v-model="ownerDialogVisible" :title="t('kbManage.settings.transferTitle')" width="460px">
      <el-form label-width="90px">
        <el-form-item :label="t('kbManage.settings.newOwner')">
          <el-select
            v-model="newOwnerId"
            filterable
            remote
            :remote-method="loadOwnerCandidates"
            :loading="ownerCandidatesLoading"
            :placeholder="t('kbManage.settings.searchUser')"
            style="width: 100%"
          >
            <el-option
              v-for="candidate in ownerCandidates"
              :key="candidate.id"
              :value="candidate.id"
              :label="candidate.display_name ? `${candidate.display_name} (${candidate.username})` : candidate.username"
            />
          </el-select>
        </el-form-item>
        <el-form-item :label="t('kbManage.settings.oldOwner')">
          <el-checkbox v-model="keepOldAsEditor">{{ t('kbManage.settings.keepAsEditor') }}</el-checkbox>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="ownerDialogVisible = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="ownerTransferring" :disabled="!newOwnerId" @click="confirmOwnerTransfer">
          {{ t('kbManage.settings.confirmTransfer') }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useKbApi } from '@/api/kb'
import { managementErrorMessage } from '@/utils/managementError'
import type { KbSummary, KbUserCandidate, KbVisibility } from '@/types/kb'
import { canManageKbLifecycle as roleCanManageLifecycle } from '@/views/kb/kbMeta'
import { t } from '@/locales/t'

const props = defineProps<{ kb: KbSummary; canWrite: boolean }>()
const emit = defineEmits<{ updated: []; deleted: [] }>()

const kbApi = useKbApi()
const saving = ref(false)
const deleting = ref(false)
const ownerDialogVisible = ref(false)
const ownerTransferring = ref(false)
const ownerCandidatesLoading = ref(false)
const ownerCandidates = ref<KbUserCandidate[]>([])
const newOwnerId = ref('')
const keepOldAsEditor = ref(false)
const canManageLifecycle = computed(
  () => roleCanManageLifecycle(props.kb.my_role),
)

const form = reactive<{
  name: string
  visibility: KbVisibility
  description: string
}>({
  name: '',
  visibility: 'private',
  description: '',
})

/** 「公开」开关：on=public，off=private（shared 已并入 private）。 */
const isPublic = computed<boolean>({
  get: () => form.visibility === 'public',
  set: (v: boolean) => { form.visibility = v ? 'public' : 'private' },
})

function reset() {
  form.name = props.kb.name
  form.visibility = props.kb.visibility
  form.description = props.kb.description ?? ''
}

watch(() => props.kb, reset, { immediate: true })

async function save() {
  const name = form.name.trim()
  if (!name) {
    ElMessage.warning(t('kbManage.settings.nameRequired'))
    return
  }
  saving.value = true
  try {
    await kbApi.updateKb(props.kb.id, {
      name,
      visibility: form.visibility,
      description: form.description.trim() || null,
    })
    ElMessage.success(t('kbManage.settings.saved'))
    emit('updated')
  } catch (e) {
    ElMessage.error(await managementErrorMessage(e))
  } finally {
    saving.value = false
  }
}

async function loadOwnerCandidates(query = '') {
  ownerCandidatesLoading.value = true
  try {
    ownerCandidates.value = await kbApi.listOwnerCandidates(props.kb.id, query.trim())
  } catch (e) {
    ElMessage.error(await managementErrorMessage(e))
  } finally {
    ownerCandidatesLoading.value = false
  }
}

async function openOwnerTransfer() {
  newOwnerId.value = ''
  keepOldAsEditor.value = false
  ownerDialogVisible.value = true
  await loadOwnerCandidates()
}

async function confirmOwnerTransfer() {
  if (!newOwnerId.value) return
  ownerTransferring.value = true
  try {
    await kbApi.transferOwner(props.kb.id, {
      new_owner_id: newOwnerId.value,
      keep_old_as_editor: keepOldAsEditor.value,
    })
    ownerDialogVisible.value = false
    ElMessage.success(t('kbManage.settings.ownerTransferred'))
    emit('updated')
  } catch (e) {
    ElMessage.error(await managementErrorMessage(e))
  } finally {
    ownerTransferring.value = false
  }
}

async function confirmDelete() {
  // 硬删确认：输入库全名（2026-09-16 删除体系——永久删除，不可恢复）
  let name = ''
  try {
    const { value } = await ElMessageBox.prompt(
      t('kbManage.settings.deletePrompt', { name: props.kb.name }),
      t('kbManage.settings.deleteConfirmTitle'),
      {
        type: 'warning', confirmButtonText: t('common.deletePermanently'), cancelButtonText: t('common.cancel'),
        inputValidator: (v) => v?.trim() === props.kb.name || t('kbManage.settings.nameMismatch'),
      },
    )
    name = value.trim()
  } catch {
    return // 用户取消
  }
  deleting.value = true
  try {
    await kbApi.deleteKb(props.kb.id, name)
    ElMessage.success(t('kbManage.settings.deleteStarted'))
    emit('deleted')
  } catch (e) {
    ElMessage.error(await managementErrorMessage(e))
  } finally {
    deleting.value = false
  }
}
</script>

<style scoped>
.kb-settings {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.kb-settings__form {
  background: var(--kb-bg-card);
  border: 1px solid var(--kb-border-light);
  border-radius: var(--kb-radius);
  padding: 20px 24px;
}

.kb-settings__hint {
  width: 100%;
  margin-top: 4px;
  font-size: 12px;
  line-height: 1.5;
  color: var(--kb-text-tertiary);
}

.kb-settings__owner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 16px 20px;
  border: 1px solid var(--kb-border-light);
  border-radius: var(--kb-radius);
  background: var(--kb-bg-card);
}

.kb-settings__owner-title {
  font-size: 13.5px;
  font-weight: 600;
  color: var(--kb-text-primary);
}

.kb-settings__danger {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  background: var(--kb-danger-soft);
  border: 1px solid var(--kb-danger);
  border-radius: var(--kb-radius);
  padding: 16px 20px;
}

.kb-settings__danger-title {
  font-size: 13.5px;
  font-weight: 600;
  color: var(--kb-danger);
}

.kb-settings__danger-desc {
  margin-top: 4px;
  font-size: 12px;
  color: var(--kb-text-secondary);
  line-height: 1.5;
  max-width: 520px;
}
</style>
