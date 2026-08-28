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
        <el-form-item label="检索范式">
          <el-select
            v-model="form.default_paradigm_id"
            :disabled="!canWrite"
            placeholder="跟随领域默认"
            clearable
            style="width: 100%"
          >
            <el-option
              v-for="p in paradigms"
              :key="p.id"
              :value="p.id"
              :label="p.name"
            />
          </el-select>
          <div class="kb-settings__hint">
            MCP/检索搜这个库时走的管线；清除则跟随领域默认 → 官方默认。范式的检索范围
            保持"留空"即可随库组合。
          </div>
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

    <div v-if="canWrite" class="kb-settings__danger">
      <div class="kb-settings__danger-text">
        <div class="kb-settings__danger-title">删除知识库</div>
        <div class="kb-settings__danger-desc">
          软删除：知识库对所有人不可见；库内已上传文档与已挖掘知识保留，不会物理删除。
          此操作不可在 UI 撤销。
        </div>
      </div>
      <el-button type="danger" plain :loading="deleting" @click="confirmDelete">
        删除知识库
      </el-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useKbApi } from '@/api/kb'
import { useOperatorApi } from '@/api/operator'
import { apiErrorDetail } from '@/api/proxyClient'
import type { KbSummary, KbVisibility } from '@/types/kb'
import type { ParadigmView } from '@/types/operator'

const props = defineProps<{ kb: KbSummary; canWrite: boolean }>()
const emit = defineEmits<{ updated: []; deleted: [] }>()

const kbApi = useKbApi()
const operatorApi = useOperatorApi()
const saving = ref(false)
const deleting = ref(false)
const paradigms = ref<ParadigmView[]>([])

const form = reactive<{
  name: string
  visibility: KbVisibility
  description: string
  default_paradigm_id: string | null
}>({
  name: '',
  visibility: 'private',
  description: '',
  default_paradigm_id: null,
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
  form.default_paradigm_id = props.kb.default_paradigm_id ?? null
}

watch(() => props.kb, reset, { immediate: true })

onMounted(async () => {
  // 范式列表仅用于选择器；拉取失败不阻塞设置页其余功能
  try {
    paradigms.value = await operatorApi.listParadigms()
  } catch {
    paradigms.value = []
  }
})

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
      default_paradigm_id: form.default_paradigm_id ?? null,
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
  try {
    await ElMessageBox.confirm(
      `确定删除知识库「${props.kb.name}」？该操作不可在 UI 撤销。`,
      '删除知识库',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return // 用户取消
  }
  deleting.value = true
  try {
    await kbApi.deleteKb(props.kb.id)
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
