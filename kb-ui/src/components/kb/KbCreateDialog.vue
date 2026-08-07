<template>
  <el-dialog
    :model-value="modelValue"
    title="新建知识库"
    width="480px"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <el-form :model="form" label-width="72px" @submit.prevent>
      <el-form-item label="域">
        <el-input :model-value="domain" disabled />
      </el-form-item>
      <el-form-item label="名称" required>
        <el-input
          v-model="form.name"
          placeholder="如：5G 规范库"
          maxlength="80"
          show-word-limit
        />
      </el-form-item>
      <el-form-item label="公开读">
        <el-switch v-model="isPublic" active-text="公开（全员可读）" inactive-text="私有（仅成员）" />
        <div class="kb-create__hint">{{ isPublic ? '任何人可读；写仍需 owner/成员。' : '仅 owner 与成员可见可读。' }}</div>
      </el-form-item>
      <el-form-item label="描述">
        <el-input
          v-model="form.description"
          type="textarea"
          :rows="3"
          placeholder="可选，简要描述这个知识库的用途"
          maxlength="300"
          show-word-limit
        />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="close">取消</el-button>
      <el-button type="primary" :loading="submitting" @click="submit">创建</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { useKbApi } from '@/api/kb'
import { apiErrorDetail } from '@/api/proxyClient'
import type { KbVisibility } from '@/types/kb'

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
    ElMessage.warning('请输入知识库名称')
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
    ElMessage.success('知识库已创建')
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
