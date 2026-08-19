import type {
  MiningRun, MiningRunStage, MiningRunDocument, KnowledgeStats, HealthStatus,
  KnowledgeDocument, KnowledgeSegment, KnowledgeUnit, KnowledgeRelation,
  MiningBatchSummary, LifecycleRemovalResult,
  UploadConfig, UploadResult,
  RunTrace, OntologyVersion, OntologyNodeType, OntologyRelationType, ActiveOntology, OntologyDraft, OntologyCandidate,
  PendingMention, GraphEntity, EntityNeighbors, GraphEvidence,
  EntityMutationResult,
    CreateMiningRunRequest, CreateMiningRunResponse, MiningPreflightResult,
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

    // M5 结构化数据（文档最新知识快照的大纲/元素/表格/切片/出生证明）
    async getParseResult(docId: string): Promise<ParseResult> {
      const { data } = await client.get(`/api/knowledge/documents/${docId}/parse-result`)
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

    async createRun(config: CreateMiningRunRequest): Promise<CreateMiningRunResponse> {
      const { data } = await client.post('/api/runs', config)
      return extractOne<CreateMiningRunResponse>(data)
    },

    async preflightRun(config: {
      domain: string
      upload_batch_id: string
      workflow_id: string
      workflow_version: number
    }): Promise<MiningPreflightResult> {
      const { data } = await client.post('/api/runs/preflight', config)
      return extractOne<MiningPreflightResult>(data)
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

    async getRunDocumentRelations(runId: string, docId: string, params?: {
      limit?: number; offset?: number
    }): Promise<{ run_id: string; document_id: string; snapshot_id: string | null; total: number; items: KnowledgeRelation[] }> {
      const { data } = await client.get(`/api/runs/${runId}/documents/${docId}/relations`, { params })
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

    // Upload
    async getUploadConfig(): Promise<UploadConfig> {
      const { data } = await client.get('/api/uploads/config')
      return data
    },

    async uploadFiles(
      domain: string,
      files: File[],
      onUploadProgress?: (progressEvent: { loaded: number; total: number; progress: number }) => void,
    ): Promise<UploadResult> {
      const form = new FormData()
      form.append('domain', domain)
      for (const f of files) {
        form.append('files', f)
      }
      const { data } = await client.post('/api/uploads', form, {
        onUploadProgress(e) {
          if (onUploadProgress && e.total) {
            onUploadProgress({ loaded: e.loaded, total: e.total, progress: Math.round((e.loaded / e.total) * 100) })
          }
        },
      })
      return data
    },

    async listUploads(domain?: string): Promise<{
      items: Array<{
        upload_batch_id: string
        domain: string
        file_count: number
        files: string[]
        storage_path: string
      }>
    }> {
      const { data } = await client.get('/api/uploads', { params: domain ? { domain } : undefined })
      return data
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

    async getDocumentRelations(docId: string, params?: {
      limit?: number; offset?: number
    }): Promise<{ document_id: string; snapshot_id: string; total: number; items: KnowledgeRelation[] }> {
      try {
        const { data } = await client.get(`/api/knowledge/documents/${docId}/relations`, { params })
        return data
      } catch {
        return { document_id: docId, snapshot_id: '', total: 0, items: [] }
      }
    },

    async getSegments(params?: { limit?: number }): Promise<PaginatedResponse<KnowledgeSegment>> {
      const { data } = await client.get('/api/knowledge/segments', { params })
      return data
    },

    async getUnits(params?: { limit?: number }): Promise<PaginatedResponse<KnowledgeUnit>> {
      const { data } = await client.get('/api/knowledge/units', { params })
      return data
    },

    async getRelations(params?: { limit?: number; offset?: number }): Promise<PaginatedResponse<KnowledgeRelation>> {
      const { data } = await client.get('/api/knowledge/relations', { params })
      return data
    },

    // ── 本体 / 知识图谱（B7）──

    // 挖掘过程透视 — run 详情之上叠加本体/图谱视角
    async getRunTrace(runId: string): Promise<RunTrace> {
      const { data } = await client.get(`/api/runs/${runId}/trace`)
      return data
    },

    // 人审后续跑
    async resumeRun(runId: string, domain?: string): Promise<Record<string, unknown>> {
      const { data } = await client.post(`/api/runs/${runId}/resume`, domain ? { domain } : undefined)
      return data
    },

    // 本体版本
    async getOntologyVersions(domain?: string): Promise<{ domain: string; items: OntologyVersion[] }> {
      const { data } = await client.get('/api/ontology/versions', { params: domain ? { domain } : undefined })
      return data
    },

    async getActiveOntology(domain?: string): Promise<ActiveOntology> {
      const { data } = await client.get('/api/ontology/active', { params: domain ? { domain } : undefined })
      return data
    },

    // 上传一份本体 YAML 文本引种 v1
    async bootstrapOntology(yamlText: string, domain?: string, sourceName?: string): Promise<Record<string, unknown>> {
      const { data } = await client.post('/api/ontology/bootstrap', {
        yaml_text: yamlText, domain, source_name: sourceName,
      })
      return data
    },

    // 本体确认评审
    async getOntologyCandidates(params?: { domain?: string; status?: string }): Promise<{ items: OntologyCandidate[] }> {
      const { data } = await client.get('/api/ontology/candidates', { params })
      return data
    },

    async reviewCandidate(candidateId: string, body: { action: string; new_name?: string; note?: string }): Promise<Record<string, unknown>> {
      const { data } = await client.post(`/api/ontology/candidates/${candidateId}/review`, body)
      return data
    },

    async promoteCandidates(domain?: string): Promise<{ new_version_id: string | null; promoted: boolean }> {
      const { data } = await client.post('/api/ontology/promote', domain ? { domain } : {})
      return data
    },

    // 本体图谱编辑器：草稿读 / 整份覆盖式存 / 发布
    async getOntologyDraft(domain?: string): Promise<OntologyDraft> {
      const { data } = await client.get('/api/ontology/draft', { params: domain ? { domain } : undefined })
      return data
    },

    async saveOntologyDraft(
      domain: string,
      payload: { node_types: OntologyNodeType[]; relation_types: OntologyRelationType[] },
    ): Promise<{ domain: string; draft_version_id: string }> {
      const { data } = await client.put('/api/ontology/draft', { domain, ...payload })
      return data
    },

    async publishOntologyDraft(domain: string): Promise<{ domain: string; new_version_id: string }> {
      const { data } = await client.post('/api/ontology/draft/publish', { domain })
      return data
    },

    // 实体确认
    async getPendingMentions(runId?: string): Promise<{ items: PendingMention[] }> {
      const { data } = await client.get('/api/mentions/pending', { params: runId ? { run_id: runId } : undefined })
      return data
    },

    async resolveMention(mentionId: string, body: { action: string; entity_id?: string; domain?: string; canonical_name?: string; node_type?: string }): Promise<Record<string, unknown>> {
      const { data } = await client.post(`/api/mentions/${mentionId}/resolve`, body)
      return data
    },

    async resolveMentionsBatch(body: { mention_ids: string[]; action: string; entity_id?: string; domain?: string; node_type?: string }): Promise<{ action: string; total: number; ok: number; failed: number; results: Array<Record<string, unknown>> }> {
      const { data } = await client.post('/api/mentions/resolve-batch', body)
      return data
    },

    async suggestEntities(params: { mention_text: string; node_type: string; domain?: string; limit?: number }): Promise<{ items: GraphEntity[] }> {
      const { data } = await client.get('/api/mentions/suggest', { params })
      return data
    },

    // 知识图谱浏览
    async getGraphEntities(params?: { domain?: string; type?: string; q?: string; limit?: number; offset?: number }): Promise<{ total: number; items: GraphEntity[] }> {
      const { data } = await client.get('/api/graph/entities', { params })
      return data
    },

    async getEntityNeighbors(entityId: string, hops = 1): Promise<EntityNeighbors> {
      const { data } = await client.get(`/api/graph/entities/${entityId}/neighbors`, { params: { hops } })
      return data
    },

    async mergeEntities(primaryId: string, dropIds: string[], domain?: string): Promise<EntityMutationResult> {
      const { data } = await client.post('/api/graph/entities/merge', {
        primary_id: primaryId, drop_ids: dropIds, domain,
      })
      return data
    },
    async retypeEntity(entityId: string, newType: string, domain?: string): Promise<EntityMutationResult> {
      const { data } = await client.post(`/api/graph/entities/${entityId}/retype`, {
        new_type: newType, domain,
      })
      return data
    },
    async deleteEntity(entityId: string, domain?: string): Promise<{ deleted: boolean }> {
      const { data } = await client.delete(`/api/graph/entities/${entityId}`, {
        params: domain ? { domain } : undefined,
      })
      return data
    },

    async getTargetEvidence(targetId: string): Promise<{ target_id: string; items: GraphEvidence[] }> {
      const { data } = await client.get(`/api/graph/evidence/${targetId}`)
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
