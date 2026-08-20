/**
 * 挖掘 run 状态的中文文案。
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
const RUN_STATUS_LABELS: Record<string, string> = {
  queued: '排队中',
  running: '运行中',
  completed: '已完成',
  interrupted: '已中断',
  failed: '失败',
  cancelled: '已取消',
  awaiting_review: '待人审',
}

export function runStatusLabel(status: string): string {
  return RUN_STATUS_LABELS[status] || status
}

/** 供测试与类型收敛用：DB CHECK 里的全部 run 状态。 */
export const RUN_STATUSES = Object.keys(RUN_STATUS_LABELS)
