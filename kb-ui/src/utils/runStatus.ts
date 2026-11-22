/**
 * 挖掘 run 状态的文案（key 经 t() 按当前语言翻译）。
 *
 * 权威取值来自 DB 的 CHECK 约束（`002_mining_runtime_postgresql.sql:12`），共 **7 个**：
 *   queued / running / completed / interrupted / failed / cancelled / awaiting_review
 *
 * 原先散在各页面的映射有两个毛病：缺 `queued`、`awaiting_review`、`interrupted`
 * （KB 挖掘的常见态，于是界面上直接显示英文），却又多出一个 `pending`——那个值
 * **不在 CHECK 里**，是从 `mining_run_documents.status` 串过来的（文档级才有 pending）。
 *
 * `StatusBadge.vue` 的配色本来就覆盖全部 7 个，缺的只是文案。
 */
import { t } from '@/locales/t'

const RUN_STATUS_LABEL_KEYS: Record<string, string> = {
  queued: 'common.runStatus.queued',
  running: 'common.runStatus.running',
  completed: 'common.runStatus.completed',
  interrupted: 'common.runStatus.interrupted',
  failed: 'common.runStatus.failed',
  cancelled: 'common.runStatus.cancelled',
  awaiting_review: 'common.runStatus.awaiting_review',
}

export function runStatusLabel(status: string): string {
  const key = RUN_STATUS_LABEL_KEYS[status]
  return key ? t(key) : status
}

export function runStatusTagType(
  status: string,
): 'success' | 'warning' | 'info' | 'danger' | 'primary' {
  if (status === 'completed') return 'success'
  if (status === 'failed') return 'danger'
  if (status === 'running') return 'primary'
  if (status === 'queued' || status === 'awaiting_review') return 'warning'
  return 'info'
}

export function isActiveRunStatus(status: string | null | undefined): boolean {
  return status === 'queued' || status === 'running'
}

export function processedDocumentCount(counts: {
  committed_count?: number | null
  failed_count?: number | null
  skipped_count?: number | null
}): number {
  return (counts.committed_count ?? 0)
    + (counts.failed_count ?? 0)
    + (counts.skipped_count ?? 0)
}

/** 供测试与类型收敛用：DB CHECK 里的全部 run 状态。 */
export const RUN_STATUSES = Object.keys(RUN_STATUS_LABEL_KEYS)
