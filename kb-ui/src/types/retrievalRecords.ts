export type RetrievalSource = 'web' | 'mcp' | 'api'
export type RetrievalOperation = 'search' | 'read' | 'upload' | 'replace'
export type RetrievalStatus =
  | 'pending' | 'success' | 'no_result' | 'denied' | 'invalid' | 'timeout' | 'failed'

/** JSON-compatible API value. Kept opaque to avoid recursive Vue template inference. */
export type RetrievalJson = unknown

export interface RetrievalRecordPayload {
  request_json: RetrievalJson | null
  effective_context_json: RetrievalJson | null
  response_mode: 'snapshot' | 'reference' | 'summary'
  response_json: RetrievalJson | null
  response_refs_json: RetrievalJson[]
  request_bytes: number | null
  response_bytes: number | null
  response_truncated: boolean
  response_original_bytes: number | null
  response_omitted_count: number
  response_sha256: string | null
  redactions_json: RetrievalJson[]
  payload_schema_version: number
}

export interface RetrievalRecord {
  id: string
  occurred_at: string
  completed_at: string | null
  domain: string
  actor_user_id: string | null
  actor_username: string | null
  source: RetrievalSource
  operation: RetrievalOperation
  tool_name: string | null
  mcp_key_id: string | null
  kb_ids: string[]
  query_text: string | null
  paradigm_id: string | null
  paradigm_version: number | null
  status: RetrievalStatus
  result_count: number | null
  duration_ms: number | null
  error_code: string | null
  details_json: Record<string, unknown>
  payload?: RetrievalRecordPayload | null
}

export interface RetrievalRecordsPage {
  items: RetrievalRecord[]
  next_cursor: string | null
  has_more: boolean
  page_size: number
}

export interface RetrievalSummaryCounts {
  total_calls: number
  calls: number
  no_result: number
  no_result_rate: number
  failed: number
  failure_rate: number
  p95_duration_ms: number
  avg_duration_ms: number
  active_paradigms: number
}

export interface RetrievalBreakdownItem {
  key: string
  count: number
}

export interface RetrievalQuerySummary {
  query_text: string
  count: number
  no_result?: number
  last_at?: string | null
}

export interface RetrievalRecordsSummary {
  available: boolean
  days: number
  summary: RetrievalSummaryCounts
  trend: Array<{ date: string; total_calls: number; calls: number; no_result: number; failed: number }>
  paradigms: Array<{ paradigm_id: string; calls: number; no_result: number }>
  tools: Array<{ tool_name: string; calls: number; no_result: number }>
  sources: Record<string, number>
  top_queries?: RetrievalQuerySummary[]
}

export interface RetrievalRecordFilters {
  cursor?: string
  pageSize?: number
  days?: number
  source?: RetrievalSource | ''
  toolName?: string
  operation?: RetrievalOperation | ''
  kbId?: string
  actorUserId?: string
  mcpKeyId?: string
  status?: RetrievalStatus | ''
  paradigmId?: string
}

export type RetrievalSummaryFilters = Omit<RetrievalRecordFilters, 'cursor' | 'pageSize'>
