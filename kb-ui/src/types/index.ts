import type { FrozenMiningWorkflowSummary } from '@/types/miningWorkflow'
import type { DomainCapability, DomainRole } from '@/types/auth'

export interface DomainInfo {
  domain_id: string
  display_name: string
  enabled: boolean
  default_channel: string
  scenario_pack_ref: string
  /** 当前登录用户在这个域中的角色；系统管理员由后端返回 admin。 */
  domain_role?: DomainRole | null
  /** 由后端按域计算的能力，前端只据此显示和导航，安全校验仍在后端。 */
  capabilities?: DomainCapability[]
}


export interface HealthStatus {
  status: string
  message?: string
  timestamp?: string
  version?: string
}

// ─── Knowledge Stats ───

export interface KnowledgeStats {
  documents: number
  snapshots: number
  segments: number
  relations: number
  retrieval_units: number
  embeddings: number
  builds: number
  releases: number
  retrieval_units_by_type?: Record<string, number>
  /**
   * 该域该 channel 下的 active release（后端字段是**复数** `active_releases`，
   * 原先这里写成 `active_release?: string` 是错的，从来取不到值）。
   *
   * 它是区分「口径不适用」与「真的是 0」的唯一依据：全部计数都只统计域级 active
   * release 的范围，纯 KB 部署永远没有 release，一排 0 并不代表没有知识。
   * 后端刻意保留这个区分——撤回最后一个文档会发布一个**空**的 active build，
   * 那种情况下的 0 是真的 0。
   */
  active_releases?: Array<{ id: string; domain: string; channel: string }>
}

// ─── Mining Run ───

export interface MiningRun {
  id: string
  status: 'pending' | 'queued' | 'running' | 'completed' | 'failed' | 'cancelled' | 'interrupted' | 'awaiting_review'
  input_path?: string
  domain?: string
  started_at?: string
  finished_at?: string
  total_documents: number
  committed_count: number
  failed_count: number
  skipped_count: number
  new_count: number
  updated_count: number
  build_id?: string
  error_summary?: string | null
  /** 36号 §九：finalize 写入的 run metadata（部分成功统计 + 拒绝摘要）。 */
  metadata_json?: Record<string, unknown> | null
  config?: Record<string, unknown>
  execution_engine?: 'legacy' | 'workflow'
  workflow_id?: string | null
  workflow_version?: number | null
  workflow_graph_hash?: string | null
  workflow?: FrozenMiningWorkflowSummary | null
}

export type MiningSubmissionEngine = 'legacy' | 'workflow'

export interface MiningRunStage {
  id: string
  stage: string
  status: string
  created_at: string
  duration_ms?: number | null
  output_summary?: string | null
  error_message?: string | null
  run_document_id?: string | null
}

export interface PreprocessWarning {
  code: string
  message: string
  sheet_name?: string | null
  cell_range?: string | null
}

export interface ExcelPreprocessSummary {
  sheet_count?: number
  parsed_sheet_count?: number
  skipped_empty_sheet_count?: number
  table_region_count?: number
  nonempty_cell_count?: number
}

export interface MiningRunDocument {
  id?: string
  document_id: string
  document_name: string
  document_key?: string
  status: 'pending' | 'processing' | 'committed' | 'failed' | 'skipped'
  action: 'NEW' | 'UPDATE' | 'SKIP' | 'REMOVE' | 'new' | 'updated' | 'unchanged'
  error_message?: string
  error_summary?: string
  current_stage?: string | null
  duration_ms?: number | null
  file_size?: number | null
  preprocess_status?: 'success' | 'partial' | 'failed' | null
  error_code?: string | null
  error_detail?: string | null
  warnings?: PreprocessWarning[]
  excel_summary?: ExcelPreprocessSummary | null
  /**
   * 跳过原因码：unchanged | restored | preprocess_failed | parser_failed
   * | unsupported_type | empty_file | no_segments | parse_no_tree
   */
  skip_reason?: string | null
  /** 跳过原因明细（异常文本 / file_type），仅部分原因码有 */
  skip_reason_detail?: string | null
  started_at?: string
  finished_at?: string
  document_snapshot_id?: string | null
  stage?: string
}

// ─── Knowledge Assets ───

export interface KnowledgeDocument {
  id: string
  document_key: string
  document_name: string
  document_type: string
  metadata_json?: Record<string, unknown>
  created_at: string
  source_batch_id?: string | null
  batch_code?: string | null
}

export interface KnowledgeSegment {
  id: string
  segment_key: string
  segment_index: number
  block_type: string
  semantic_role: string
  section_title?: string
  raw_text: string
  token_count: number
}

export interface KnowledgeUnit {
  id: string
  unit_key: string
  unit_type: 'raw_text' | 'contextual_text' | 'summary' | 'generated_question' | 'entity_card'
  target_type: string
  title: string
  text: string
  weight: number
  block_type?: string
  semantic_role?: string
  created_at?: string
}

// ─── LLM Service ───

export interface LlmTaskStats {
  tasks_by_status: Record<string, number>
  tasks_by_type?: Record<string, number>
  succeeded_attempts: number
  total_tokens: number
  avg_latency_ms: number
  services?: string[]
  domains?: string[]
  stages?: string[]
}

export interface LlmTask {
  id: string
  task_type: 'chat' | 'embedding' | 'rerank'
  caller_service?: string
  knowledge_domain?: string
  pipeline_stage?: string
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'dead_letter' | 'cancelled'
  priority: number
  attempt_count: number
  max_attempts: number
  created_at: string
  started_at?: string
  finished_at?: string
  idempotency_key?: string
  error_message?: string
  total_tokens?: number
  latency_ms?: number
  metadata?: Record<string, unknown>
}

export interface LlmTaskDetail extends LlmTask {
  prompt_tokens?: number
  completion_tokens?: number
  total_tokens?: number
  latency_ms?: number
  raw_response?: Record<string, unknown>
  parsed_output?: Record<string, unknown>
}

export interface LlmCleanupCounts {
  tasks: number
  requests: number
  attempts: number
  results: number
  events: number
  model_calls: number
}

export interface LlmCleanupTableSize {
  table_name: string
  label?: string
  approx_rows: number
  total_bytes: number
}

export interface LlmCleanupResult {
  dry_run: boolean
  retention_days: number
  cutoff_at: string
  truncated: boolean
  batches: number
  duration_ms: number
  estimates?: LlmCleanupCounts
  deleted?: LlmCleanupCounts
  remaining?: LlmCleanupCounts
  table_sizes?: LlmCleanupTableSize[]
  notes?: string[]
}

export interface RunTrace {
  run_id: string
  domain: string
  status: string
  current_stage?: string | null
  awaiting_review: boolean
  counts: {
    total_documents: number
    committed: number
    new: number
    updated: number
    failed: number
    skipped: number
  }
  execution_engine?: MiningSubmissionEngine
  workflow: FrozenMiningWorkflowSummary | null
  active_node_id?: string | null
  active_operator_type?: string | null
  pause_step?: string | null
  node_events: import('@/types/miningWorkflow').MiningWorkflowNodeEvent[]
  stage_events: Array<Record<string, unknown>>
  documents: Array<Record<string, unknown>>
  warnings: Array<{
    node_id: string
    attempt_no: number
    code: string
    message: string
  }>
  build_id?: string | null
}

// ─── Paginated Response ───

export interface PaginatedResponse<T> {
  total: number
  limit: number
  offset: number
  items: T[]
}

// ─── 文档生命周期（下载 / 删除 / 批次）───

export interface MiningBatchSummary {
  source_batch_id: string | null
  batch_code: string | null
  mining_run_id: string | null
  active_document_count: number
  created_at: string | null
  deletable: boolean
  unclassified: boolean
}

export interface LifecycleRemovalResult {
  domain: string
  removed_count: number
  build_id: string
  release_id: string
  document_id?: string
  source_batch_id?: string
}
