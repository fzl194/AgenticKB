import type {
  MiningRun, MiningRunStage, MiningRunDocument, KnowledgeStats, HealthStatus,
  KnowledgeDocument, KnowledgeSegment, KnowledgeUnit,
  MiningBatchSummary, LifecycleRemovalResult,
  RunTrace,
} from '@/types'
import type { PaginatedResponse } from '@/types'
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

    async getDocumentRawContent(docId: string): Promise<{ content: string; format: string }> {
      const { data, headers } = await client.get(`/api/knowledge/documents/${docId}/raw-content`, {
        responseType: 'text',
      })
      const format = headers['x-content-format'] || 'plain'
      return { content: data, format }
    },




    // Knowledge assets
    // domain 通常由 proxyClient 拦截器自动注入；下架/下载等破坏性操作可显式传入
    // domain 以「钉住」发起时的领域，避免请求在途中领域切换导致误操作（拦截器会
    // 尊重显式传入的 domain，优先于默认注入值）。
    async getDocuments(params?: {
      domain?: string; limit?: number; offset?: number; source_batch_id?: string; unclassified?: boolean
    }): Promise<PaginatedResponse<KnowledgeDocument>> {
      const { data } = await client.get('/api/knowledge/documents', { params })
      return data
    },

    async getBatches(domain?: string): Promise<{ items: MiningBatchSummary[] }> {
      const { data } = await client.get('/api/knowledge/batches', {
        params: domain ? { domain } : undefined,
      })
      return data
    },

    async downloadDocument(documentId: string, domain?: string): Promise<{
      blob: Blob; contentDisposition: string | null
    }> {
      const response = await client.get(`/api/knowledge/documents/${documentId}/download`, {
        params: domain ? { domain } : undefined,
        responseType: 'blob',
      })
      const contentDisposition = response.headers['content-disposition']
      return {
        blob: response.data,
        contentDisposition: typeof contentDisposition === 'string' ? contentDisposition : null,
      }
    },

    async removeDocument(documentId: string, domain?: string): Promise<LifecycleRemovalResult> {
      const { data } = await client.delete(`/api/knowledge/documents/${documentId}`, {
        params: domain ? { domain } : undefined,
      })
      return extractOne<LifecycleRemovalResult>(data)
    },

    async removeBatch(sourceBatchId: string, domain?: string): Promise<LifecycleRemovalResult> {
      const { data } = await client.delete(`/api/knowledge/batches/${sourceBatchId}`, {
        params: domain ? { domain } : undefined,
      })
      return extractOne<LifecycleRemovalResult>(data)
    },

    async getDocument(docId: string): Promise<KnowledgeDocument> {
      const { data } = await client.get(`/api/knowledge/documents/${docId}`)
      return extractOne<KnowledgeDocument>(data)
    },

    async getDocumentSegments(docId: string, params?: {
      limit?: number; offset?: number
    }): Promise<{ document_id: string; snapshot_id: string; total: number; items: KnowledgeSegment[] }> {
      const { data } = await client.get(`/api/knowledge/documents/${docId}/segments`, { params })
      return data
    },

    async getDocumentUnits(docId: string, params?: {
      unit_type?: string; limit?: number; offset?: number
    }): Promise<{ document_id: string; snapshot_id: string; total: number; items: KnowledgeUnit[] }> {
      const { data } = await client.get(`/api/knowledge/documents/${docId}/units`, { params })
      return data
    },

    // 挖掘过程透视 — 冻结 Workflow、节点事件和文档执行状态
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
}

export interface ParseResultSegment {
  segment_index: number
  block_type: string
  heading_chain: { level: number; title: string }[]
  text: string
  element_ids: string[]
  /** 语义角色（segment-compiler v2 起标注；旧快照可能为空） */
  semantic_role?: string | null
  /** token 计数（字符近似，CJK 1 字 ≈ 1 token） */
  token_count?: number | null
}

export interface ParseResult {
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
  diagnostics: { warnings: string[]; containers: number; relations: number }
}
