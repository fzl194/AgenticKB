import type { FrozenMiningWorkflowSummary } from '@/types/miningWorkflow'

export interface DomainInfo {
  domain_id: string
  display_name: string
  enabled: boolean
  default_channel: string
  scenario_pack_ref: string
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
  active_release?: string
}

// ─── Mining Run ───

export interface MiningRun {
  id: string
  status: 'pending' | 'queued' | 'running' | 'completed' | 'failed' | 'cancelled' | 'interrupted' | 'awaiting_review'
  subloop_stage?: string | null
  ontology_version_id?: string | null
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
  error_message?: string
  config?: Record<string, unknown>
  execution_engine?: 'legacy' | 'workflow'
  workflow_id?: string | null
  workflow_version?: number | null
  workflow_graph_hash?: string | null
  workflow?: FrozenMiningWorkflowSummary | null
}

export type MiningSubmissionEngine = 'legacy' | 'workflow'

interface CreateMiningRunBase {
  domain: string
  max_workers?: number
  phase1_only?: boolean
  publish_on_partial_failure?: boolean
  workflow_id?: string
  workflow_version?: number
  preflight_id?: string
  document_decisions?: MiningPreflightDecision[]
}

export type MiningPreflightAction =
  | 'NEW' | 'REUSED' | 'RESTORED' | 'REMINED' | 'KEPT_CURRENT' | 'JOINED_EXISTING'

export interface MiningPreflightDecision {
  relative_path: string
  raw_content_hash: string
  selected_action: MiningPreflightAction
  state_token: string
}

export interface MiningPreflightSnapshot {
  snapshot_id: string
  document_id: string
  document_key: string
  workflow_id: string | null
  workflow_version: number | null
  workflow_version_id?: string | null
  workflow_graph_hash: string | null
  is_active?: boolean
  artifacts_complete?: boolean
}

export interface MiningPreflightItem extends MiningPreflightDecision {
  file_name: string
  file_size: number
  classification: string
  default_action: MiningPreflightAction
  allowed_actions: MiningPreflightAction[]
  current_snapshot: MiningPreflightSnapshot | null
  matched_snapshot: MiningPreflightSnapshot | null
  existing_run_id?: string | null
}

export interface MiningPreflightResult {
  preflight_id: string
  domain: string
  workflow: { id: string; version: number; version_id: string; graph_hash: string }
  summary: Record<string, number>
  items: MiningPreflightItem[]
}

export type CreateMiningRunRequest =
  | (CreateMiningRunBase & { upload_batch_id: string; input_path?: never })
  | (CreateMiningRunBase & { input_path: string; upload_batch_id?: never })

export interface CreateMiningRunResponse {
  run_id: string
  status: string
  current_stage: string
  started_at?: string | null
  execution_engine: MiningSubmissionEngine
  workflow_id: string | null
  workflow_version: number | null
  workflow_graph_hash: string | null
}

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
  action: 'new' | 'updated' | 'unchanged'
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

export interface KnowledgeRelation {
  id: string
  document_snapshot_id: string
  source_segment_id: string
  target_segment_id: string
  relation_type: string
  weight: number
  confidence: number
  distance: number
  source_text?: string
  target_text?: string
}

// ─── Search / Serving ───

export interface SearchResult {
  items: SearchContextItem[]
  relations: SearchContextRelation[]
  sources: SearchSourceRef[]
  evidence_groups?: SearchEvidenceGroup[]
  issues?: SearchIssue[]
  suggestions?: string[]
  debug?: SearchDebug
}

export interface SearchContextItem {
  id: string
  kind: string
  role: 'seed' | 'context' | 'support'
  text: string
  score: number
  title: string
  blockType: string
  semanticRole: string
  sourceId: string | null
  relationToSeed?: string | null
  routeSources?: string[]
  scoreChain?: Record<string, unknown>
  evidenceRole: string
  citation?: {
    raw_segment_ids?: string[]
    section?: string
    document_snapshot_id?: string
  }
  metadata?: Record<string, unknown>
}

export interface SearchContextRelation {
  id: string
  fromId: string
  toId: string
  relationType: string
  distance?: number
}

export interface SearchSourceRef {
  id: string
  documentKey: string
  title: string
  relativePath?: string
  /** 所属知识库；legacy 文档（走 /api/runs 进来的）不属于任何 KB，为 null。 */
  kbId?: string | null
  metadata?: Record<string, unknown>
}

// ── 原文下钻（POST /api/v1/segments/fulltext）──
//
// 检索返回的 text 是按上下文预算压缩过的：命中项硬截断、其余抽取式摘要。要拿存储
// 的原文必须再查一次，这组类型就是那次查询的形状。

export interface FullTextRef {
  /** 取自条目的 kind：命中项 retrieval_unit，上下文/支撑项 raw_segment。 */
  type: 'retrieval_unit' | 'raw_segment'
  id: string
}

export interface FullTextSegment {
  id: string
  role: 'target' | 'before' | 'after'
  segmentIndex: number | null
  text: string
  blockType: string | null
  semanticRole: string | null
  sectionPath: string[]
  sectionTitle: string | null
  tokenCount: number | null
  documentSnapshotId: string | null
  documentId: string | null
  documentKey: string | null
  documentName: string | null
  kbId: string | null
  /** 该文档是否还有可下载的原件。legacy 文档从来没有过原件。 */
  hasRawFile: boolean
}

export interface FullTextItem {
  ref: FullTextRef
  found: boolean
  /** 仅 found=false 时有值。不存在、越权、被移出当前 build 共用 out_of_scope。 */
  reason?: string | null
  unit?: { id: string; unitType: string | null; title: string | null; text: string } | null
  segments: FullTextSegment[]
}

export interface FullTextResult {
  scope: { releaseId: string | null; buildId: string | null; snapshotCount: number }
  items: FullTextItem[]
}

export interface SearchEvidenceGroup {
  documentSnapshotId: string
  itemIds: string[]
  relationIds: string[]
}

export interface SearchIssue {
  severity: string
  message: string
}

export interface SearchDebug {
  understanding?: {
    original_query: string
    intent: string
    source: string
    keywords: string[]
    entities_count: number
  }
  route_plan?: {
    routes_count: number
    fusion_method: string
    rerank_method: string
  }
  /**
   * 后端的 debug 键名是 domain_context（SearchService.domainContextToMap）。
   * release_id 在按知识库收窄时不是真实 release id，而是合成的 "kb:a,b" —— 它同时
   * 是语义缓存的分区键，所以每种知识库组合各占一个缓存桶。
   */
  domain_context?: {
    domain: string
    channel: string
    database?: string
    release_id: string
    build_id: string | null
    snapshot_count: number
    kb_ids?: string[]
  }
  trace?: {
    request_id: string
    total_duration_ms: number
    stages: SearchDebugStage[]
  }
  candidate_count?: number
  fusion_method?: string
  query_embedding_dim?: number
}

export interface SearchDebugStage {
  name: string
  duration_ms: number
  summary?: string
  input?: string
  output?: string
  error?: string | null
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

// ─── Upload Config ───

export interface UploadConfig {
  max_file_size: number
  max_archive_size: number
  max_files_per_request: number
  max_file_size_mb: number
  max_archive_size_mb: number
  accepted_extensions: string[]
  archive_extensions: string[]
  mining_run_submission_engine: MiningSubmissionEngine
}

export interface UploadResult {
  upload_batch_id: string
  domain: string
  file_count: number
  files: string[]
  storage_path: string
  extracted_archives: Array<{
    archive: string
    error: string | null
    file_count: number
    files: string[]
  }>
}

// ─── 本体 / 知识图谱（B7）───

export interface RunTrace {
  run_id: string
  domain: string
  status: string
  current_stage?: string | null
  subloop_stage?: string | null
  ontology_version_id?: string | null
  awaiting_review: boolean
  active_gate?: string | null
  counts: {
    total_documents: number
    committed: number
    new: number
    updated: number
    failed: number
    skipped: number
  }
  ontology_proposed_candidates: number
  entity_pending_mentions: number
  entity_count: number
  relation_count: number
  escape_hatch_candidates?: number
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
  asset_counts: { entities: number; relations: number }
  build_id?: string | null
}

export interface OntologyVersion {
  id: string
  domain_id: string
  version_no: number
  status: 'draft' | 'active' | 'superseded'
  source?: string
  created_by?: string | null
  note?: string | null
  created_at?: string
}

export interface OntologyNodeType {
  // id 在读取服务端本体时存在；前端编辑草稿（saveOntologyDraft）时尚无 id，故可选
  id?: string
  name: string
  layer: string
  is_strong: boolean
  definition?: string | null
  examples_json?: unknown[]
}

export interface OntologyRelationType {
  // 同 OntologyNodeType：草稿编辑期无 id，故可选
  id?: string
  name: string
  layer: string
  is_directed: boolean
  inverse_name?: string | null
  allowed_pairs_json?: Array<{ head: string; tail: string }>
  definition?: string | null
}

export interface ActiveOntology {
  domain: string
  version: OntologyVersion | null
  node_types: OntologyNodeType[]
  relation_types: OntologyRelationType[]
}

// 草稿编辑器：GET /ontology/draft 返回 / PUT 提交载荷，结构与 ActiveOntology 同构
export interface OntologyDraft {
  domain: string
  version: OntologyVersion | null
  node_types: OntologyNodeType[]
  relation_types: OntologyRelationType[]
}

export interface OntologyCandidate {
  id: string
  domain_id: string
  kind: 'node_type' | 'relation_type'
  layer: string
  proposed_name: string
  payload_json?: Record<string, unknown>
  source?: string
  evidence_json?: unknown[]
  score?: number | null
  status: 'proposed' | 'accepted' | 'rejected'
  review_note?: string | null
  created_at?: string
  duplicate_of?: string | null
}

export interface PendingMention {
  id: string
  document_snapshot_id: string
  segment_id: string
  node_type: string
  mention_text: string
  canonical_name?: string | null
  resolved_entity_id?: string | null
  resolve_status: string
  confidence: number
  metadata_json?: Record<string, unknown>
  segment_text?: string | null      // §14.2：所在段原文
  segment_section?: string | null   // §14.2：所在段标题
}

export interface GraphEntity {
  id: string
  domain_id: string
  canonical_name: string
  node_type: string
  layer: string
  aliases_json?: unknown[]
  attributes_json?: Record<string, unknown>
  mention_count: number
  document_count: number
}

export interface EntityNeighbors {
  center_id: string
  hops: number
  nodes: Array<{ id: string; canonical_name: string; node_type: string; mention_count: number }>
  edges: Array<{ id: string; head_entity_id: string; tail_entity_id: string; relation_type: string; confidence?: number | null }>
}

export interface EntityMutationResult {
  recomputed_edges?: number
  affected?: string[]
  neighbors?: EntityNeighbors
  primary_id?: string
}

export interface GraphEvidence {
  id: string
  domain_id: string
  document_snapshot_id: string
  segment_id: string
  quote?: string | null
  target_kind: string
  target_id: string
  segment_text?: string | null
  segment_section?: string | null
  created_at?: string
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
