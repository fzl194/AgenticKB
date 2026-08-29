/**
 * 知识库管理（KB）前端类型。
 *
 * 与后端 knowledge_mining/mining/kb/routes 的响应形状对齐：
 * - KbSummary 来自 list_visible（含派生 my_role + document_count）
 * - KbDetail 来自 get_kb
 * - KbMember 来自 list_members
 * - KbDocument 来自 documents 各端点（字段按端点部分返回，故多数可选）
 */

export type KbVisibility = 'private' | 'public'
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
  /** 阶段 A：库级默认检索范式（serving operator_paradigm id）。null = 跟随领域默认。 */
  default_paradigm_id?: string | null
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
  /** 批次4 readiness 四档（后端纯查询派生；环境异常时为 null，不阻塞详情）。 */
  readiness?: KbReadiness | null
}

/** 检索就绪度（empty→parsed→segmented→lexical_ready→vector_ready）。 */
export type KbReadinessLevel = 'empty' | 'parsed' | 'segmented' | 'lexical_ready' | 'vector_ready'

export interface KbReadiness {
  level: KbReadinessLevel
  documents: number
  segments: number
  retrieval_units: number
  embeddings: number
  /** true = 嵌入服务不可用已留痕（语义检索缺失但可见，最高 lexical_ready）。 */
  embedding_fallback: boolean
}

export interface KbMember {
  kb_id: string
  user_id: string
  role: KbMemberRole
  added_at: string
  username: string
  display_name: string | null
}

/** 候选用户(可加入 KB 的用户,排除 owner 与已成员;GET /api/kb/{kbId}/members/candidates)。 */
export interface KbUserCandidate {
  id: string
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
  /** 阶段 A：库级默认检索范式。null = 清除（跟随领域默认）。 */
  default_paradigm_id?: string | null
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

// ── 概览页聚合（GET /api/kb/overview）──────────────────────────────────────

/**
 * 每库状态摘要。**只有首页卡片真正渲染的三个数** —— 后端刻意不回六态齐全：
 * published/withdrawn 要多挂两条 release⋈snapshot 的 EXISTS，而这是登录落地页。
 */
export interface KbStatusCounts {
  /** 文档总数（即卡片上的「N 篇」；后端不另发 document_count，两个数迟早会不一致）。 */
  total: number
  mining: number
  failed: number
}

export interface KbOverviewItem {
  id: string
  name: string
  my_role: KbMyRole
  /** 由 my_role 推导。待处理区块只列有写权限的库。 */
  can_write: boolean
  status_counts: KbStatusCounts
  /** 最近一次**成功**挖掘的完成时间；没成功过为 null。已按它排序（NULLS LAST）。 */
  last_mined_at: string | null
  /** 有值 = 该库有 run 卡在人工审核，待处理区块据此列一条。 */
  awaiting_review_run_id: string | null
}

/** 最近挖掘记录（跨库）。kb_id 必带——前端要用它拼 /kb/{kbId}/run/{runId}。 */
export interface KbOverviewRun {
  id: string
  kb_id: string
  kb_name: string
  status: string
  total_documents: number | null
  new_count: number | null
  updated_count: number | null
  started_at: string | null
  finished_at: string | null
}

export interface KbOverview {
  /**
   * 该域有无域级 active release。
   * 纯 KB 部署恒 false（KB 挖掘 publish=false，永不产 release）——此时检索范围选择器
   * 不呈现「域级发布」项，否则用户选中后必撞 no_active_release。
   */
  has_active_release: boolean
  kbs: KbOverviewItem[]
  recent_runs: KbOverviewRun[]
}

// ── 概览页统计（GET /api/kb/stats）────────────────────────────────────────────

/** 文档六态。与后端 _STATUS_CASE_SQL 的 CASE 分支一一对应。 */
export type KbDocStatusKey =
  | 'uploaded'
  | 'mining'
  | 'mined'
  | 'published'
  | 'withdrawn'
  | 'failed'

/**
 * 「当前知识」的资产量。口径是每个文档最近一次进入 validated/published build 的快照，
 * 不是 asset_* 表的累计行数——重挖会产生新快照，累计计数会把挖了 3 遍的库虚报 3 倍。
 */
export interface KbAssetCounts {
  snapshots: number
  segments: number
  retrieval_units: number
  entity_mentions: number
  relations: number
}

/** 挖掘趋势的一天。后端已补齐空天（补零），前端直接照数组画，不必自己填缺口。 */
export interface KbMiningTrendPoint {
  /** YYYY-MM-DD（UTC）。 */
  date: string
  runs: number
  completed: number
  /** 当天入库（committed）的文档数。 */
  documents: number
}

export interface KbStats {
  /** 统计口径覆盖的知识库数 = 当前用户在本域可见的全部库。 */
  kb_count: number
  /**
   * false 时 document_status 的 published / withdrawn 恒为 0——那是「口径不适用」，
   * 不是「一篇都没发布」。图表据此把这两档从图例里摘掉，而不是画两个恒零扇区。
   */
  has_active_release: boolean
  /** 趋势窗口天数（后端 TREND_DAYS），用于图表标题，避免前后端各写一个 30。 */
  trend_days: number
  document_status: Record<KbDocStatusKey, number>
  assets: KbAssetCounts
  /** 只含非零类型；前端按拿到的键渲染，不假定 unit_type 全集。 */
  retrieval_unit_types: Record<string, number>
  mining_trend: KbMiningTrendPoint[]
}


/** 阶段 A：用户级 MCP 接入（一人一钥 + 开放库清单）。 */
export interface McpAccessStatus {
  configured: boolean
  key_prefix?: string
  status?: string
  created_at?: string
  last_used_at?: string | null
  rotated_at?: string
  open_kb_ids: string[]
  /** null = 全部工具开放；非空 = 仅这些工具可用 */
  open_tools?: string[] | null
  instructions?: string | null
  tool_descriptions?: Record<string, string> | null
}

export interface McpAccessRotateResult {
  /** 明文密钥——仅轮换响应出现一次，界面展示后不再可取。 */
  key: string
  key_prefix: string
  rotated_at: string
}
