<template>
  <el-dialog
    v-if="canWrite"
    :model-value="true"
    title="替换当前文件"
    width="520px"
    :close-on-click-modal="false"
    :close-on-press-escape="!uploading"
    :show-close="!uploading"
    @update:model-value="close"
  >
    <p>将替换「{{ document.document_name }}」的原文件，文件名和所在目录保持不变。</p>
    <p>替换后需要重新挖掘；已有的当前可搜索知识继续可用，直到新内容挖掘成功。</p>
    <label class="replace-file__label">
      选择新文件
      <input type="file" :disabled="uploading || blocked" @change="selectFile" />
    </label>
    <p v-if="!hasRevision" role="alert">缺少文件版本信息，请关闭窗口并刷新文件列表后重试。</p>
    <p v-if="blocked" role="alert">文件已被更新或不可操作，请关闭窗口并刷新后重新选择。</p>
    <template #footer>
      <el-button data-testid="replace-cancel" :disabled="uploading" @click="close">取消</el-button>
      <el-button
        data-testid="replace-confirm" type="primary" :loading="uploading"
        :disabled="!file || !hasRevision || blocked || uploading" @click="replace"
      >确认替换</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { useKbApi } from '@/api/kb'
import { apiErrorDetail } from '@/api/proxyClient'
import type { KbDocument } from '@/types/kb'

const props = defineProps<{ kbId: string; document: KbDocument; canWrite: boolean }>()
const emit = defineEmits<{
  close: []
  replaced: [document: KbDocument]
  refresh: []
}>()
const api = useKbApi()
const file = ref<File | null>(null)
const uploading = ref(false)
const blocked = ref(false)
const hasRevision = computed(() => Number.isSafeInteger(props.document.content_revision)
  && (props.document.content_revision ?? -1) >= 0)

function selectFile(event: Event) {
  file.value = (event.target as HTMLInputElement).files?.[0] ?? null
}

function close() {
  if (!uploading.value) emit('close')
}

async function replace() {
  if (!props.canWrite || !file.value || !hasRevision.value || blocked.value || uploading.value) return
  uploading.value = true
  try {
    const result = await api.replaceDocumentContent(
      props.kbId, props.document.id, file.value, props.document.content_revision!,
    )
    ElMessage.success('原文件已替换，请重新挖掘以更新当前可搜索知识')
    emit('replaced', result)
  } catch (error) {
    const status = (error as { response?: { status?: number } })?.response?.status
    const errors: Record<number, string> = {
      409: '文件已被更新或当前不可替换，请刷新后重新选择文件',
      403: '没有替换此文件的权限，请刷新后确认访问权限',
      404: '文件不存在或已不可访问，请刷新文件列表',
      413: '文件超过上传大小限制，请选择较小的文件',
    }
    if (status === 409 || status === 403 || status === 404) {
      blocked.value = true
      emit('refresh')
    }
    ElMessage.error((status && errors[status]) || await apiErrorDetail(error))
  } finally {
    uploading.value = false
  }
}
</script>

<style scoped>
p { line-height: 1.6; }
.replace-file__label { display: flex; flex-direction: column; gap: 10px; margin-top: 18px; }
</style>
