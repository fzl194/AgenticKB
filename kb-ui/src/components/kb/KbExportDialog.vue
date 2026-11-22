<template>
  <el-dialog
    :model-value="modelValue"
    :title="t('kbManage.export.title')"
    width="720px"
    @update:model-value="emit('update:modelValue', $event)"
    @open="refresh"
  >
    <el-alert type="info" :closable="false" class="kb-export__notice">
      <p>{{ t('kbManage.export.intro', { name: kb?.name ?? '' }) }}</p>
      <p>{{ t('kbManage.export.notice') }}</p>
    </el-alert>

    <div class="kb-export__toolbar">
      <el-button type="primary" size="small" :loading="creating" @click="create">
        <el-icon class="el-icon--left"><Download /></el-icon>{{ t('kbManage.export.newExport') }}
      </el-button>
      <span class="kb-export__hint">{{ t('kbManage.export.oneAtATime') }}</span>
    </div>

    <el-table v-loading="loading" :data="tasks" size="small" :empty-text="t('kbManage.export.empty')">
      <el-table-column :label="t('common.status')" width="92">
        <template #default="{ row }">
          <el-tag :type="statusTagType(row.status)" size="small" disable-transitions>
            {{ statusLabel(row.status) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column :label="t('kbManage.mining.progress')" min-width="240">
        <template #default="{ row }">
          <template v-if="isPacking(row)">
            <el-progress
              :percentage="packPercent(row)" :stroke-width="8"
              :format="() => t('kbManage.export.docsProgress', { packed: row.progress.packed_docs ?? 0, total: row.progress.total_docs ?? '?' })"
            />
            <div class="kb-export__meta">
              {{ t('kbManage.export.packed') }} {{ formatBytes(row.progress.packed_bytes ?? 0) }}
              <el-tooltip v-if="row.progress.warnings_count" placement="top">
                <template #content>
                  {{ t('kbManage.export.warningsDetail', { n: row.progress.warnings_count }) }}
                </template>
                <el-link type="warning" :underline="false">{{ t('kbManage.export.skippedCount', { n: row.progress.warnings_count }) }}</el-link>
              </el-tooltip>
            </div>
          </template>
          <span v-else class="kb-export__meta">{{ phaseLabel(row) }}</span>
        </template>
      </el-table-column>
      <el-table-column :label="t('kbManage.export.artifact')" width="150">
        <template #default="{ row }">
          <template v-if="row.status === 'succeeded'">
            <div>{{ t('kbManage.export.docCount', { n: row.doc_count ?? 0 }) }} · {{ formatBytes(row.artifact_size ?? 0) }}</div>
            <el-tooltip :content="t('kbManage.export.shaTip', { sha: row.artifact_sha256 ?? '' })" placement="top">
              <el-link type="primary" :underline="false" @click="copySha(row)">
                {{ t('kbManage.export.copySha') }}
              </el-link>
            </el-tooltip>
          </template>
          <el-tooltip v-else-if="row.error" :content="row.error" placement="top">
            <span class="kb-export__error">{{ t('kbManage.export.failureReason') }}</span>
          </el-tooltip>
        </template>
      </el-table-column>
      <el-table-column :label="t('kbManage.export.startedAt')" width="150">
        <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
      </el-table-column>
      <el-table-column :label="t('common.actions')" width="150" fixed="right">
        <template #default="{ row }">
          <el-button
            v-if="row.status === 'succeeded'" size="small" type="primary" plain
            :loading="downloadingId === row.id" @click="download(row)"
          >{{ t('kbManage.export.download') }}</el-button>
          <el-button
            v-if="row.status === 'failed' || row.status === 'cancelled' || row.status === 'expired'"
            size="small" plain @click="create"
          >{{ t('kbManage.export.reexport') }}</el-button>
          <el-button
            v-if="row.status === 'queued' || row.status === 'running'"
            size="small" type="warning" plain @click="cancel(row)"
          >{{ t('common.cancel') }}</el-button>
          <el-button
            v-if="row.status !== 'queued' && row.status !== 'running'"
            size="small" type="danger" text @click="remove(row)"
          >{{ t('common.delete') }}</el-button>
        </template>
      </el-table-column>
    </el-table>

    <div v-if="expiredHint" class="kb-export__expired">{{ expiredHint }}</div>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Download } from '@element-plus/icons-vue'
import { useKbApi } from '@/api/kb'
import { apiErrorDetail } from '@/api/proxyClient'
import { usePolling } from '@/composables/usePolling'
import type { KbExportTask, KbSummary } from '@/types/kb'
import { t } from '@/locales/t'

const props = defineProps<{ modelValue: boolean; kb: KbSummary | null }>()
const emit = defineEmits<{ 'update:modelValue': [boolean] }>()

const kbApi = useKbApi()
const tasks = ref<KbExportTask[]>([])
const loading = ref(false)
const creating = ref(false)
const downloadingId = ref<string | null>(null)

const hasActive = computed(() =>
  tasks.value.some((t) => t.status === 'queued' || t.status === 'running' || t.status === 'cancelling'))

const expiredHint = computed(() => {
  const done = tasks.value.filter((t) => t.status === 'succeeded' && t.expires_at)
  if (!done.length) return ''
  const soonest = done
    .map((t) => Date.parse(t.expires_at as string))
    .filter((ts) => !Number.isNaN(ts) && ts > Date.now())
    .sort((a, b) => a - b)[0]
  if (!soonest) return ''
  const hours = Math.max(1, Math.round((soonest - Date.now()) / 3600_000))
  return t('kbManage.export.retention', { hours })
})

async function refresh() {
  if (!props.kb) return
  loading.value = true
  try {
    tasks.value = await kbApi.listExportTasks(props.kb.id)
  } catch (e) {
    ElMessage.error(await apiErrorDetail(e))
  } finally {
    loading.value = false
  }
}

// 有活跃任务才轮询（5s）；对话框关闭即停
usePolling(refresh, 5000, {
  shouldRun: () => props.modelValue && hasActive.value,
})

async function create() {
  if (!props.kb) return
  try {
    await ElMessageBox.confirm(
      t('kbManage.export.confirmPrompt', { name: props.kb.name }),
      t('kbManage.export.confirmTitle'),
      { confirmButtonText: t('kbManage.export.start'), cancelButtonText: t('common.cancel'), type: 'info' },
    )
  } catch { return }
  creating.value = true
  try {
    const task = await kbApi.createExportTask(props.kb.id)
    if (task.already_started) ElMessage.info(t('kbManage.export.alreadyRunning'))
    else ElMessage.success(t('kbManage.export.created'))
    await refresh()
  } catch (e) {
    ElMessage.error(await apiErrorDetail(e))
  } finally {
    creating.value = false
  }
}

async function download(task: KbExportTask) {
  downloadingId.value = task.id
  try {
    const access = await kbApi.getExportDownloadUrl(task.id)
    // presign 直连对象存储（绝不经代理走 blob：大包会撞网关读超时）；
    // attachment disposition 已带中文保存名，天然支持断点续传。
    window.open(access.url, '_blank')
    ElMessage.success(t('kbManage.export.downloadStarted', { filename: access.filename }))
  } catch (e) {
    ElMessage.error(await apiErrorDetail(e))
  } finally {
    downloadingId.value = null
  }
}

async function cancel(task: KbExportTask) {
  try {
    await ElMessageBox.confirm(t('kbManage.export.cancelPrompt'), t('kbManage.export.cancelTitle'), {
      confirmButtonText: t('kbManage.export.cancelTask'), cancelButtonText: t('kbManage.export.keepWaiting'), type: 'warning',
    })
  } catch { return }
  try {
    await kbApi.deleteExportTask(task.id)
    ElMessage.success(t('kbManage.export.cancelRequested'))
    await refresh()
  } catch (e) {
    ElMessage.error(await apiErrorDetail(e))
  }
}

async function remove(task: KbExportTask) {
  try {
    await ElMessageBox.confirm(t('kbManage.export.deletePrompt'), t('kbManage.export.deleteTitle'), {
      confirmButtonText: t('common.delete'), cancelButtonText: t('common.cancel'), type: 'warning',
    })
  } catch { return }
  try {
    await kbApi.deleteExportTask(task.id)
    ElMessage.success(t('kbManage.export.deleted'))
    await refresh()
  } catch (e) {
    ElMessage.error(await apiErrorDetail(e))
  }
}

async function copySha(task: KbExportTask) {
  const sha = task.artifact_sha256 ?? ''
  if (!sha) return
  try {
    await navigator.clipboard.writeText(sha)
    ElMessage.success(t('kbManage.export.shaCopied'))
  } catch {
    ElMessage.warning(t('kbManage.export.copyFailed', { sha }))
  }
}

// ── 展示辅助 ──

function isPacking(task: KbExportTask): boolean {
  return task.status === 'running' && task.phase === 'packaging'
}

function packPercent(task: KbExportTask): number {
  const total = task.progress.total_docs ?? 0
  if (!total) return 0
  return Math.min(100, Math.round(((task.progress.packed_docs ?? 0) / total) * 100))
}

const STATUS_LABEL_KEYS: Record<string, string> = {
  queued: 'common.runStatus.queued', running: 'kbManage.export.status.running',
  cancelling: 'kbManage.export.status.cancelling', succeeded: 'common.runStatus.completed',
  failed: 'common.runStatus.failed', cancelled: 'common.runStatus.cancelled', expired: 'kbManage.export.status.expired',
}

function statusLabel(status: KbExportTask['status']): string {
  const key = STATUS_LABEL_KEYS[status]
  return key ? t(key) : status
}

function statusTagType(status: KbExportTask['status']) {
  return ({
    queued: 'info', running: 'primary', cancelling: 'warning', succeeded: 'success',
    failed: 'danger', cancelled: 'info', expired: 'info',
  } as const)[status] ?? 'info'
}

const PHASE_LABEL_KEYS: Record<string, string> = {
  queued: 'kbManage.export.phase.queued', prepare: 'kbManage.export.phase.prepare',
  packaging: 'kbManage.export.phase.packaging', uploading: 'kbManage.export.phase.uploading',
  finalize: 'kbManage.export.phase.finalize',
}

function phaseLabel(task: KbExportTask): string {
  if (task.status === 'failed') return t('kbManage.export.failedPhase')
  if (task.status === 'cancelled') return t('common.runStatus.cancelled')
  if (task.status === 'expired') return t('kbManage.export.expiredPhase')
  if (task.status === 'succeeded') return t('kbManage.export.donePhase')
  const key = PHASE_LABEL_KEYS[task.phase]
  return key ? t(key) : task.phase
}

function formatBytes(n: number): string {
  if (!n) return '0 MB'
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`
}

function formatTime(iso: string): string {
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString('zh-CN', { hour12: false })
}
</script>

<style scoped>
.kb-export__notice {
  margin-bottom: 12px;
}
.kb-export__notice p {
  margin: 0 0 4px;
  font-size: 12.5px;
  line-height: 1.6;
}
.kb-export__notice p:last-child {
  margin-bottom: 0;
}
.kb-export__toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
}
.kb-export__hint {
  font-size: 12px;
  color: var(--kb-text-tertiary, #909399);
}
.kb-export__meta {
  margin-top: 2px;
  font-size: 12px;
  color: var(--kb-text-tertiary, #909399);
}
.kb-export__error {
  color: var(--el-color-danger);
  cursor: help;
  border-bottom: 1px dashed currentColor;
}
.kb-export__expired {
  margin-top: 8px;
  font-size: 12px;
  color: var(--kb-text-tertiary, #909399);
}
</style>
