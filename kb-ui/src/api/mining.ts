import type {
  MiningRun, MiningRunStage, MiningRunDocument, KnowledgeStats, HealthStatus,
  KnowledgeSegment, KnowledgeUnit,
  RunTrace,
} from '@/types'
import { createProxyClient, extractItems, extractOne } from '@/api/proxyClient'

export function useMiningApi() {
  const client = createProxyClient('mining')

  return {
    // Health
    async getHealth(): Promise<HealthStatus> {
      const { data } = await client.get('/health')
      return data
    },

    // Stats
    async getStats(): Promise<KnowledgeStats> {
      const { data } = await client.get('/api/knowledge/stats')
      return data
    },

    // Runs
    async getRuns(domain: string, params?: { status?: string; limit?: number }): Promise<MiningRun[]> {
      const { data } = await client.get('/api/runs', { params: { ...params, domain } })
      return extractItems<MiningRun>(data, ['stages'])
    },

    async getRun(runId: string): Promise<MiningRun> {
      const { data } = await client.get(`/api/runs/${runId}`)
      return extractOne<MiningRun>(data)
    },

    async getRunStages(runId: string): Promise<MiningRunStage[]> {
      const { data } = await client.get(`/api/runs/${runId}/stages`)
      return extractItems<MiningRunStage>(data, ['stages'])
    },

    async getRunDocuments(runId: string, params?: {
      status?: string; action?: string; has_error?: boolean; page?: number; page_size?: number
    }): Promise<{ total: number; page: number; page_size: number; documents: MiningRunDocument[] }> {
      const { data } = await client.get(`/api/runs/${runId}/documents`, { params })
      return data
    },

    async getRunProgress(runId: string): Promise<{
      run_id: string; total: number; completed: number; failed: number
      skipped: number; processing: number; progress_percent: number
      current_stage: string | null; stage_summary: Record<string, { done: number; failed: number }>
    }> {
      const { data } = await client.get(`/api/runs/${runId}/progress`)
      return data
    },



    async cancelRun(runId: string): Promise<void> {
      await client.post(`/api/runs/${runId}/cancel`)
    },

    async publishRun(runId: string, domain?: string): Promise<void> {
      await client.post(`/api/runs/${runId}/publish`, domain ? { domain } : undefined)
    },

    // Run document detail
    async getRunDocument(runId: string, docId: string): Promise<MiningRunDocument> {
      const { data } = await client.get(`/api/runs/${runId}/documents/${docId}`)
      return data
    },

    async getRunDocumentStages(runId: string, docId: string): Promise<MiningRunStage[]> {
      const { data } = await client.get(`/api/runs/${runId}/documents/${docId}/stages`)
      return extractItems<MiningRunStage>(data, ['stages'])
    },

    async getRunDocumentArtifacts(runId: string, docId: string): Promise<{
      run_id: string; document_id: string; snapshot_id: string | null
      segment_count: number; unit_count: number; relation_count: number
    }> {
      const { data } = await client.get(`/api/runs/${runId}/documents/${docId}/artifacts`)
      return data
    },

    async getRunDocumentSegments(runId: string, docId: string, params?: {
      limit?: number; offset?: number
    }): Promise<{ run_id: string; document_id: string; snapshot_id: string | null; total: number; items: KnowledgeSegment[] }> {
      const { data } = await client.get(`/api/runs/${runId}/documents/${docId}/segments`, { params })
      return data
    },

    async getRunDocumentUnits(runId: string, docId: string, params?: {
      unit_type?: string; limit?: number; offset?: number
    }): Promise<{ run_id: string; document_id: string; snapshot_id: string | null; total: number; items: KnowledgeUnit[] }> {
      const { data } = await client.get(`/api/runs/${runId}/documents/${docId}/units`, { params })
      return data
    },

    async getRunArtifacts(runId: string): Promise<{
      run_id: string; document_count: number
      segment_count: number; unit_count: number; relation_count: number
    }> {
      const { data } = await client.get(`/api/runs/${runId}/artifacts`)
      return data
    },

    // Raw source content (V5 document viewer)
    async getRunDocumentRawContent(runId: string, docId: string): Promise<{ content: string; format: string }> {
      const { data, headers } = await client.get(`/api/runs/${runId}/documents/${docId}/raw-content`, {
        responseType: 'text',
      })
      const format = headers['x-content-format'] || 'plain'
      return { content: data, format }
    },

    async getRunTrace(runId: string): Promise<RunTrace> {
      const { data } = await client.get(`/api/runs/${runId}/trace`)
      return data
    },

    // 人审后续跑
    async resumeRun(runId: string, domain?: string): Promise<Record<string, unknown>> {
      const { data } = await client.post(`/api/runs/${runId}/resume`, domain ? { domain } : undefined)
      return data
    },

  }
}


/** M5 结构化数据视图（/api/knowledge/documents/{id}/parse-result）. */
export interface ParseResultOutlineNode {
  element_id: string
  level: number
  title: string
  /** 标题元素在 Parse IR 中的稳定阅读顺序；滚动升级期间旧响应可能缺失。 */
  order_index?: number | null
  /** 同一快照内由 Parse IR 层级确定的父章节元素；null 表示文档根。 */
  parent_section_element_id?: string | null
}

export interface ParseResultElement {
  element_id: string
  element_type: string
  text: string
  order_index: number
  containers: string[]
  has_evidence: boolean
}

export interface ParseResultTable {
  table_id: string
  rows: number
  columns: number
  header: string[]
  preview: string[][]
  source_element_id?: string | null
  parent_section_element_id?: string | null
  caption?: string | null
  preview_truncated?: boolean | null
}

export interface ParseResultSegment {
  segment_index: number
  block_type: string
  heading_chain: { level: number; title: string }[]
  text: string
  element_ids: string[]
  /** A0 structure workspace: deterministic ownership/provenance projected by mining. */
  section_element_id?: string | null
  source_order_start?: number | null
  source_order_end?: number | null
  table_ref?: string | null
  table_caption?: string | null
  /** 语义角色（segment-compiler v2 起标注；旧快照可能为空） */
  semantic_role?: string | null
  /** token 计数（字符近似，CJK 1 字 ≈ 1 token） */
  token_count?: number | null
}

/** A0-1 版本对比：当前可搜索版本 vs 最新上传版本的解析。 */
export interface ParseResultVersioning {
  view: 'current_serving' | 'latest_revision'
  serving: {
    document_snapshot_id: string
    build_id: string | null
    source_content_revision: number | null
  } | null
  latest: {
    document_snapshot_id: string
    source_content_revision: number | null
  } | null
  /** 当前搜索与最新解析是否同一快照 */
  in_sync: boolean
  /** latest 解析是否已进入当前搜索：in_search / not_in_search / no_results */
  latest_state: 'in_search' | 'not_in_search' | 'no_results'
}

export interface ParseResult {
  /** 本响应采用的视图（A0-1） */
  view?: 'current_serving' | 'latest_revision'
  /** 版本对比信息（A0-1：serving/latest 快照身份、in_sync、latest_state） */
  versioning?: ParseResultVersioning
  snapshot: {
    id: string
    title: string | null
    mime_type: string
    quality_status: string
    lifecycle_status: string
    parser_fingerprint: string | null
    compiler_fingerprint: string | null
    snapshot_fingerprint: string
    created_by_run_id: string | null
    created_at: string
    source_storage_object_id: string | null
    source_content_revision: number | null
  }
  outline: ParseResultOutlineNode[]
  elements: { count: number; items: ParseResultElement[] }
  tables: ParseResultTable[]
  segments: { count: number; items: ParseResultSegment[] }
  diagnostics: {
    warnings: string[]
    containers: number
    relations: number
    outline_total?: number
    tables_total?: number
    outline_truncated?: boolean
    tables_truncated?: boolean
  }
}
