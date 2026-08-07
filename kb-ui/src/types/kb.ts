/**
 * 知识库管理（KB）前端类型。
 *
 * 与后端 knowledge_mining/mining/kb/routes 的响应形状对齐：
 * - KbSummary 来自 list_visible（含派生 my_role + document_count）
 * - KbDetail 来自 get_kb
 * - KbMember 来自 list_members
 * - KbDocument 来自 documents 各端点（字段按端点部分返回，故多数可选）
 */

export type KbVisibility = 'private' | 'shared' | 'public'
export type KbStatus = 'active' | 'deleted'
export type KbMemberRole = 'viewer' | 'editor'
/** 当前用户在该 KB 的有效访问级别（列表页展示用）。admin = site admin 全通。 */
export type KbMyRole = 'owner' | 'editor' | 'viewer' | 'admin'
/** 文档派生状态（后端 derive_document_status 实时计算，不存列）。 */
export type KbDocStatus =
  | 'uploaded'
  | 'mining'
  | 'mined'
  | 'published'
  | 'withdrawn'
  | 'failed'
  | 'unknown'

export interface KbSummary {
  id: string
  domain: string
  name: string
  description: string | null
  owner_id: string
  visibility: KbVisibility
  /** 选定的挖掘范式（workflow id）。后端 list_visible 已下发。 */
  mining_workflow_id: string | null
  created_at: string
  my_role: KbMyRole
  document_count: number
}

export interface KbDetail {
  id: string
  domain: string
  name: string
  description: string | null
  owner_id: string
  visibility: KbVisibility
  status: KbStatus
  /** 选定的挖掘范式（workflow id）。null 表示未选范式，触发整库挖掘会被后端 400。 */
  mining_workflow_id: string | null
  deleted_at: string | null
  created_at: string
  updated_at: string
}

export interface KbMember {
  kb_id: string
  user_id: string
  role: KbMemberRole
  added_at: string
  username: string
  display_name: string | null
}

export interface KbFolder {
  id: string
  kb_id: string
  parent_id: string | null
  name: string
  path: string
  created_at: string
  created_by: string | null
}

export interface KbDocument {
  id: string
  domain?: string
  kb_id?: string
  document_key?: string
  document_name: string
  document_type?: string | null
  storage_path?: string
  directory_path?: string | null
  owner_id?: string | null
  created_at?: string
  file_size?: number | null
  modified_at?: string | null
  status?: KbDocStatus
}

export interface KbCreateBody {
  domain: string
  name: string
  visibility: KbVisibility
  description?: string | null
}

export interface KbUpdateBody {
  name?: string
  description?: string | null
  visibility?: KbVisibility
  /** 选/换挖掘范式。传 null 清除绑定。 */
  mining_workflow_id?: string | null
}

export interface KbMineResult {
  run_id: string
  kb_id: string
  status: string
  started_at: string
  execution_engine: string
  workflow_id: string
  workflow_version: number
  workflow_graph_hash: string
  /** 本次是否强制重挖（用户显式或签名变更自动触发）。 */
  force_redo?: boolean
  /** 是否因范式签名变更自动触发 force_redo。 */
  auto_force_redo?: boolean
}

/** 整库挖掘状态码（与 mining_runs.status 对齐）。 */
export type KbRunStatus =
  | 'queued'
  | 'running'
  | 'succeeded'
  | 'completed_with_errors'
  | 'failed'
  | 'cancelled'
  | string

/** 本 KB 的挖掘记录（GET /api/kb/{kbId}/runs 单条，最新在前）。 */
export interface KbRunRecord {
  id: string
  status: KbRunStatus
  current_stage: string | null
  execution_engine: string | null
  workflow_id: string | null
  workflow_version: number | null
  started_at: string
  finished_at: string | null
  error_summary: string | null
  total_documents: number | null
  new_count: number | null
  updated_count: number | null
  skipped_count: number | null
  failed_count: number | null
  /** 已落库文档数（任务列表双色进度条用，避免 N+1 轮询 getRunProgress）。 */
  committed_count: number | null
}

/** 文档已挖掘知识的切片分段。 */
export interface KbDocSegment {
  segment_index: number
  block_type: string | null
  semantic_role: string | null
  section_title: string | null
  raw_text: string | null
  normalized_text: string | null
}

/** 文档已挖掘知识的检索单元。 */
export interface KbDocRetrievalUnit {
  unit_key: string
  unit_type: string | null
  title: string | null
  text: string | null
  block_type: string | null
  semantic_role: string | null
}

/** 文档已挖掘知识的实体提及。 */
export interface KbDocEntityMention {
  node_type: string | null
  mention_text: string | null
  canonical_name: string | null
  resolve_status: string | null
}

/** 文档已挖掘知识的切片间关系（asset_raw_segment_relations）。 */
export interface KbDocRelation {
  relation_type: string | null
  weight: number | null
  confidence: number | null
  distance: number | null
  source_segment_text: string | null
  target_segment_text: string | null
}

/** 文档已挖掘知识（GET /api/kb/{kbId}/documents/{docId}/knowledge）。 */
export interface DocumentKnowledge {
  mined: boolean
  build_id: string | null
  document_snapshot_id: string | null
  segments?: KbDocSegment[]
  retrieval_units?: KbDocRetrievalUnit[]
  entity_mentions?: KbDocEntityMention[]
  relations?: KbDocRelation[]
}
