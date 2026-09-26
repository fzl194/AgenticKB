<template>
  <div class="kb-settings">
    <div class="kb-settings__form">
      <el-form :model="form" label-width="72px" @submit.prevent>
        <el-form-item label="名称">
          <el-input v-model="form.name" :disabled="!canWrite" maxlength="80" show-word-limit />
        </el-form-item>
        <el-form-item label="公开读">
          <el-switch
            v-model="isPublic"
            :disabled="!canWrite"
            active-text="公开（全员可读）"
            inactive-text="私有（仅成员）"
          />
        </el-form-item>
        <el-form-item label="描述">
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
          <el-button type="primary" :loading="saving" @click="save">保存修改</el-button>
          <el-button @click="reset">重置</el-button>
        </el-form-item>
      </el-form>
    </div>

    <div v-if="canManageLifecycle" class="kb-settings__danger">
      <div class="kb-settings__danger-text">
        <div class="kb-settings__danger-title">删除知识库</div>
        <div class="kb-settings__danger-desc">
          软删除：知识库对所有人不可见；库内已上传文档与已挖掘知识保留，不会物理删除。
          删除后原名称可重新使用；历史数据仍保留。
        </div>
      </div>
      <el-button type="danger" plain :loading="deleting" @click="confirmDelete">
        删除知识库
      </el-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useKbApi } from '@/api/kb'
import { apiErrorDetail } from '@/api/proxyClient'
import type { KbSummary, KbVisibility } from '@/types/kb'
import { canManageKbLifecycle as roleCanManageLifecycle } from '@/views/kb/kbMeta'

const props = defineProps<{ kb: KbSummary; canWrite: boolean }>()
const emit = defineEmits<{ updated: []; deleted: [] }>()

const kbApi = useKbApi()
const saving = ref(false)
const deleting = ref(false)
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
    ElMessage.warning('名称不能为空')
    return
  }
  saving.value = true
  try {
    await kbApi.updateKb(props.kb.id, {
      name,
      visibility: form.visibility,
      description: form.description.trim() || null,
    })
    ElMessage.success('已保存')
    emit('updated')
  } catch (e) {
    ElMessage.error(await apiErrorDetail(e))
  } finally {
    saving.value = false
  }
}

async function confirmDelete() {
  // 硬删确认：输入库全名（2026-09-16 删除体系——永久删除，不可恢复）
  let name = ''
  try {
    const { value } = await ElMessageBox.prompt(
      `此操作将永久删除知识库「${props.kb.name}」及其全部文档、挖掘知识与历史记录，不可恢复。请输入库全名确认：`,
      '永久删除知识库',
      {
        type: 'warning', confirmButtonText: '永久删除', cancelButtonText: '取消',
        inputValidator: (v) => v?.trim() === props.kb.name || '名称不一致',
      },
    )
    name = value.trim()
  } catch {
    return // 用户取消
  }
  deleting.value = true
  try {
    await kbApi.deleteKb(props.kb.id, name)
    ElMessage.success('已开始后台删除：库即刻停用，可在知识库列表查看进度')
    emit('deleted')
  } catch (e) {
    ElMessage.error(await apiErrorDetail(e))
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
