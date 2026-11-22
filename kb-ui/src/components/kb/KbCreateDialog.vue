<template>
  <el-dialog
    :model-value="modelValue"
    :title="t('dashboard.action.newKb')"
    width="480px"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <el-form :model="form" label-width="72px" @submit.prevent>
      <el-form-item :label="t('kbManage.create.domain')">
        <el-input :model-value="domain" disabled />
      </el-form-item>
      <el-form-item :label="t('common.name')" required>
        <el-input
          v-model="form.name"
          :placeholder="t('kbManage.create.namePlaceholder')"
          maxlength="80"
          show-word-limit
        />
      </el-form-item>
      <el-form-item :label="t('kbManage.create.publicRead')">
        <el-switch v-model="isPublic" :active-text="t('kbManage.create.publicText')" :inactive-text="t('kbManage.create.privateText')" />
        <div class="kb-create__hint">{{ isPublic ? t('kbManage.create.publicHint') : t('kbManage.create.privateHint') }}</div>
      </el-form-item>
      <el-form-item :label="t('common.description')">
        <el-input
          v-model="form.description"
          type="textarea"
          :rows="3"
          :placeholder="t('kbManage.create.descPlaceholder')"
          maxlength="300"
          show-word-limit
        />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="close">{{ t('common.cancel') }}</el-button>
      <el-button type="primary" :loading="submitting" @click="submit">{{ t('common.create') }}</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { useKbApi } from '@/api/kb'
import { apiErrorDetail } from '@/api/proxyClient'
import type { KbVisibility } from '@/types/kb'
import { t } from '@/locales/t'

const props = defineProps<{ modelValue: boolean; domain: string }>()
const emit = defineEmits<{
  'update:modelValue': [boolean]
  created: []
}>()

const kbApi = useKbApi()
const submitting = ref(false)

const form = reactive<{ name: string; visibility: KbVisibility; description: string }>({
  name: '',
  visibility: 'private',
  description: '',
})

/** 「公开」开关：on=public，off=private（shared 已并入 private）。 */
const isPublic = computed<boolean>({
  get: () => form.visibility === 'public',
  set: (v: boolean) => { form.visibility = v ? 'public' : 'private' },
})

// 每次打开重置表单
watch(
  () => props.modelValue,
  (open) => {
    if (open) {
      form.name = ''
      form.visibility = 'private'
      form.description = ''
    }
  },
)

function close() {
  emit('update:modelValue', false)
}

async function submit() {
  const name = form.name.trim()
  if (!name) {
    ElMessage.warning(t('kbManage.create.nameRequired'))
    return
  }
  submitting.value = true
  try {
    await kbApi.createKb({
      domain: props.domain,
      name,
      visibility: form.visibility,
      description: form.description.trim() || null,
    })
    ElMessage.success(t('kbManage.create.created'))
    emit('update:modelValue', false)
    emit('created')
  } catch (e) {
    ElMessage.error(await apiErrorDetail(e))
  } finally {
    submitting.value = false
  }
}
</script>

<style scoped>
.kb-create__hint {
  margin-top: 4px;
  font-size: 11.5px;
  color: var(--kb-text-tertiary);
  line-height: 1.5;
}
</style>
