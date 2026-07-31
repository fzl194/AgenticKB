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
      <el-form-item label="可见性">
        <el-radio-group v-model="form.visibility">
          <el-radio value="private">私有</el-radio>
          <el-radio value="shared">共享</el-radio>
          <el-radio value="public">公开</el-radio>
        </el-radio-group>
        <div class="kb-create__hint">{{ visibilityHint(form.visibility) }}</div>
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
import { reactive, ref, watch } from 'vue'
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

function visibilityHint(v: KbVisibility): string {
  switch (v) {
    case 'private': return '仅你自己可见'
    case 'shared': return '被加入成员的人可见（可编辑/只读）'
    case 'public': return '当前域内所有人可见'
  }
}

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
