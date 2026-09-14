/**
 * 知识一张网接入 API（47 号）——经 main_control_service 反向代理转发到 mining。
 *
 * - /api/onenet/*：管理员（查询/摸底/TOC/导入记录/重同步）；
 * - /api/kb/{kbId}/onenet/*：库级（引用建/删/列 + 逻辑文档 markdown 预览）。
 */
import { createProxyClient, extractItems } from '@/api/proxyClient'

/** 查询三元组（V1.2）：字段 × 精确/模糊 × 内容 */
export interface OnenetCondition {
  field: string
  fuzzy: boolean
  content: string
}

export interface OnenetDocHit {
  source_id: string
  doc_name: string | null
  file_name: string | null
  doc_type: string[] | null
  parsed_version: string | null
  publish_time: string | null
  product_line: string[] | null
  language: string | null
  slice_hits: number
  sample_titles: string[]
}

export interface OnenetSearchResult {
  documents: OnenetDocHit[]
  total_documents: number
  page: number
  page_size: number
  slice_total_reported: number | null
  capped: boolean
  slices_pulled: number
  notice?: string
}

export interface OnenetProbe {
  source_id: string
  total_slices: number
  part_id: { min: number | null; max: number | null }
  doc_name: string | null
  file_name: string | null
  doc_type: string[] | null
  parsed_version: string | null
  publish_time: string | null
  product_line: string[] | null
  pbi: string[] | null
}

export interface OnenetTocNode {
  title: string
  path: string
  depth: number
  slice_count: number
  direct_slice_count?: number
  part_min: number | null
  part_max: number | null
  children: OnenetTocNode[]
}

export interface OnenetTocFile {
  file_path: string
  file_title: string
  heading_title: string
  folder_path: string
  slice_count: number
  part_min: number
  part_max: number
}

export interface OnenetToc {
  cached?: boolean
  source_id: string
  parsed_version: string | null
  total_slices: number
  scanned_slices?: number
  nodes?: number
  tree: OnenetTocNode[]
  rule_version?: string
  file_count?: number
  folder_count?: number
  unassigned?: number
  files?: OnenetTocFile[]
}

export type OnenetImportStatus =
  | 'queued' | 'fetching' | 'restoring' | 'importing' | 'mining'
  | 'done' | 'failed'

export interface OnenetImport {
  id: string
  domain: string
  source_id: string
  doc_name: string | null
  parsed_version_seen: string | null
  total_slices: number | null
  fetched_max_part_id: number | null
  selection_json: { subtrees: string[]; max_part_id: number | null }
  status: OnenetImportStatus
  kb_id: string
  document_count: number | null
  error: string | null
  created_at: string
  updated_at: string
  documents?: Array<{
    id: string
    document_name: string
    directory_path: string | null
    status?: string
    file_size?: number | null
  }>
}

export interface OnenetRef {
  document_id: string
  document_name: string | null
  directory_path: string | null
  file_size: number | null
  referenced_at: string
  source_kb_name: string | null
}

export function useOnenetApi() {
  const client = createProxyClient('mining')

  return {
    // ── 管理面 ──
    /** 第一步 · 查询发现（V1.2）：三元组透传 + 文档汇总分页 */
    async search(conditions: OnenetCondition[], page = 1, pageSize = 20): Promise<OnenetSearchResult> {
      const { data } = await client.post('/api/onenet/search', {
        conditions, page, page_size: pageSize,
      })
      return data
    },
    async probe(sourceId: string): Promise<OnenetProbe> {
      const { data } = await client.post('/api/onenet/probe', { source_id: sourceId })
      return data
    },
    async toc(domain: string, sourceId: string, opts: { refresh?: boolean } = {}): Promise<OnenetToc> {
      const { data } = await client.post('/api/onenet/toc', {
        domain, source_id: sourceId, ...(opts.refresh ? { refresh: true } : {}),
      })
      return data
    },
    async listImports(domain: string): Promise<OnenetImport[]> {
      const { data } = await client.get('/api/onenet/imports', { params: { domain } })
      return extractItems<OnenetImport>(data.imports ?? data)
    },
    async getImport(importId: string): Promise<OnenetImport> {
      const { data } = await client.get(`/api/onenet/imports/${importId}`)
      return data
    },
    async startImport(body: {
      domain: string; source_id: string
      selection?: { subtrees?: string[]; max_part_id?: number | null }
      doc_name?: string; parsed_version?: string; total_slices?: number
    }): Promise<OnenetImport> {
      const { data } = await client.post('/api/onenet/imports', body)
      return data
    },
    async updateSelection(importId: string, selection: { subtrees?: string[] }) {
      const { data } = await client.patch(`/api/onenet/imports/${importId}/selection`, { selection })
      return data
    },
    async retryImport(importId: string): Promise<OnenetImport> {
      const { data } = await client.post(`/api/onenet/imports/${importId}/retry`)
      return data
    },
    async resync(importId: string, opts: { force?: boolean } = {}) {
      const suffix = opts.force ? '?force=1' : ''
      const { data } = await client.post(`/api/onenet/imports/${importId}/resync${suffix}`)
      return data as {
        changed: boolean
        diff?: { added: string[]; removed: string[]; changed: string[] } | null
        updated_documents: string[]
        removed_documents: string[]
      }
    },

    // ── 库级引用 ──
    /** 库级导入池（审查 H8：KB 成员走本端点，不打 admin 面）。 */
    async listKbImports(kbId: string): Promise<OnenetImport[]> {
      const { data } = await client.get(`/api/kb/${kbId}/onenet/imports`)
      return extractItems<OnenetImport>(data.imports ?? data)
    },
    async listRefs(kbId: string): Promise<OnenetRef[]> {
      const { data } = await client.get(`/api/kb/${kbId}/onenet/refs`)
      return extractItems<OnenetRef>(data.refs ?? data)
    },
    async addRefs(kbId: string, documentIds: string[]) {
      const { data } = await client.post(`/api/kb/${kbId}/onenet/refs`, { document_ids: documentIds })
      return data as { added: string[]; skipped: Array<{ document_id: string; reason: string }> }
    },
    async removeRefs(kbId: string, documentIds: string[]) {
      const { data } = await client.delete(`/api/kb/${kbId}/onenet/refs`, { data: { document_ids: documentIds } })
      return data as { removed: string[] }
    },
    documentMarkdownUrl(kbId: string, documentId: string): string {
      return `/api/kb/${kbId}/onenet/documents/${documentId}/markdown`
    },
    async fetchDocumentMarkdown(kbId: string, documentId: string): Promise<string> {
      const { data } = await client.get(this.documentMarkdownUrl(kbId, documentId), { responseType: 'text' })
      return typeof data === 'string' ? data : String(data)
    },
  }
}
