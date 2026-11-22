<template>
  <el-dialog
    v-if="canWrite"
    :model-value="true"
    :title="t('kbManage.replace.title')"
    width="520px"
    :close-on-click-modal="false"
    :close-on-press-escape="!uploading"
    :show-close="!uploading"
    @update:model-value="close"
  >
    <p>{{ t('kbManage.replace.intro', { name: document.document_name }) }}</p>
    <p>{{ t('kbManage.replace.note') }}</p>
    <label class="replace-file__label">
      {{ t('kbManage.replace.pickFile') }}
      <input type="file" :disabled="uploading || blocked" @change="selectFile" />
    </label>
    <p v-if="!hasRevision" role="alert">{{ t('kbManage.replace.noRevision') }}</p>
    <p v-if="blocked" role="alert">{{ t('kbManage.replace.blocked') }}</p>
    <template #footer>
      <el-button data-testid="replace-cancel" :disabled="uploading" @click="close">{{ t('common.cancel') }}</el-button>
      <el-button
        data-testid="replace-confirm" type="primary" :loading="uploading"
        :disabled="!file || !hasRevision || blocked || uploading" @click="replace"
      >{{ t('kbManage.replace.confirm') }}</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { useKbApi } from '@/api/kb'
import { apiErrorDetail } from '@/api/proxyClient'
import type { KbDocument } from '@/types/kb'
import { t } from '@/locales/t'

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
    // 57号：替换后自动排队挖掘——挖完前旧知识继续可检索（不再要求手动重挖）
    ElMessage.success(result.auto_mined
      ? t('kbManage.replace.doneAutoMined')
      : t('kbManage.replace.doneManual'))
    emit('replaced', result)
  } catch (error) {
    const status = (error as { response?: { status?: number } })?.response?.status
    const errors: Record<number, string> = {
      409: t('kbManage.replace.err409'),
      403: t('kbManage.replace.err403'),
      404: t('kbManage.replace.err404'),
      413: t('kbManage.replace.err413'),
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
