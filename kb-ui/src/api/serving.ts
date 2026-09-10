import type { HealthStatus } from '@/types'
import type { EvidenceItem, EvidenceLocator, EvidenceResponse } from '@/types/operator'
import { createProxyClient } from '@/api/proxyClient'

export type { EvidenceItem, EvidenceResponse }

export interface ParadigmResolveResult {
  domain: string
  bound: boolean
  paradigmId?: string
  name?: string
  description?: string
  version?: number
  url?: string
  source?: 'library' | 'official' | string
  degraded?: boolean
  degradedFrom?: string
}

export interface ParadigmSearchResult {
  evidenceResponse?: EvidenceResponse
  [k: string]: unknown
}

/**
 * 1.0.2 修复：检索错误三分型。kb_not_found（无权限或不存在，服务端防探测同码）
 * 与 no_active_kb_build（库在但无可检索 Build）必须给用户不同指引——前者让用户
 * 找库主/管理员加权限，后者让用户先完成挖掘。未识别的错误原样上抛（不吞错）；
 * paradigm 路由自身的 404（范式不存在）不误报。
 */
export function localizeSearchError(err: unknown): unknown {
  const e = err as { response?: { status?: number; data?: { message?: string; error?: string } }; message?: string }
  const status = e?.response?.status
  const bodyMsg = e?.response?.data?.message || e?.response?.data?.error || ''
  const text = String(bodyMsg)
  if (status === 404) {
    const isNoActiveBuild = /no_active_kb_build|no mined content/i.test(text)
    if (isNoActiveBuild) {
      return new Error('所选知识库暂无可检索内容：尚未完成挖掘，或全部文档挖掘失败。请先完成一次成功的挖掘（部分文档成功即可检索）。')
    }
    const isKbNotFound = /kb_not_found|knowledge bases were not found/i.test(text)
    if (isKbNotFound) {
      return new Error('无法访问所选知识库：库不存在，或当前账号无读取权限（需库主/成员/public/站点管理员）。')
    }
  }
  if (status === 401 || status === 403) {
    return new Error('没有访问所选知识库的权限，请确认知识库可见性或联系管理员。')
  }
  return err
}

/** A2：结构导航结果（/api/v1/structure/{ref}/navigate）。 */
export interface StructureNavigateNode {
  ref: string
  node_type: string
  title?: string | null
  level?: number | null
  ordinal?: number | null
  block_type?: string | null
  relations?: string[]
}

export interface StructureNavigateResult {
  structure_ref: string
  relation: string
  depth: number
  limit: number
  nodes: StructureNavigateNode[]
  cursor?: string | null
  has_more: boolean
  source?: Record<string, unknown>
}

/** A1（37/38 号）：ev_ ref 的服务端导航解析结果（/api/v1/evidence/{ref}/source）。 */
export interface EvidenceSourceNavigation {
  document_id: string
  kb_id?: string | null
  file_name?: string | null
  relative_path?: string | null
  section_element_id?: string | null
  section_path?: string | null
  table_ref?: string | null
  row_index?: number | null
  locator?: EvidenceLocator | null
}

/** A3：结构化查询结果（/api/v1/structure/{ref}/query）。 */
export interface TableFieldSchema {
  name: string
  value_type: 'number' | 'text' | 'date' | string
  sortable: boolean
  can_aggregate: boolean
  operations: string[]
}

export interface TableQueryResult {
  asset_ref: string
  table_name: string
  columns: TableFieldSchema[]
  rows: Array<Record<string, unknown> & { _row?: number }>
  cursor?: string | null
  has_more: boolean
  aggregate?: { op: string; field?: string | null; value?: unknown; row_count?: number } | null
}

/** A3：查询 DSL（与 MCP get_knowledge(ref, query=…) 同一形状）。 */
export interface TableQuerySpec {
  select?: string[]
  where?: Array<{ field: string; op: string; value?: unknown }>
  order_by?: Array<{ field: string; direction: 'asc' | 'desc' }>
  limit?: number
  cursor?: string
  aggregate?: { op: string; field?: string }
}

export function useServingApi() {
  const client = createProxyClient('serving')

  return {
    async getHealth(): Promise<HealthStatus> {
      const { data } = await client.get('/actuator/health')
      return data
    },

    /**
     * ev_ ref -> 完整原文（2026-09-01）：检索面板把截断证据展开为完整内容。
     * 与 MCP get_content(ev_) 同源（ref 反查带授权），经平台前端通道。
     */
    async getEvidenceFull(
      ref: string, domain: string, kbId?: string, mode?: string,
    ): Promise<EvidenceItem> {
      const params: Record<string, unknown> = { domain }
      if (kbId) params.kbId = kbId
      if (mode) params.mode = mode
      const { data } = await client.get(`/api/v1/evidence/${ref}`, { params })
      return data
    },

    /**
     * A1 来源导航解析（37 号 P0-6）：ev_ ref -> 服务端在当前权限下解析的跳转锚
     * （document_id / 大纲锚 / 表格锚 / locator）。前端据此路由到文档页对应位置，
     * 不持有、不拼接任何内部编号。
     */
    async getEvidenceSource(
      ref: string, domain: string, kbId?: string,
    ): Promise<EvidenceSourceNavigation> {
      const params: Record<string, unknown> = { domain }
      if (kbId) params.kbId = kbId
      const { data } = await client.get(`/api/v1/evidence/${ref}/source`, { params })
      return data
    },

    /**
     * 三层解析：这个库组合该走哪条检索范式（库级 > 官方默认）。
     * 批次6「知识库检索 tab」与 MCP 路由共用同一判定。
     */
    async resolveParadigm(domain: string, kbIds?: string[]): Promise<ParadigmResolveResult> {
      const params: Record<string, unknown> = { domain }
      if (kbIds?.length) params.kbIds = kbIds.join(',')
      const { data } = await client.get('/api/v1/paradigm/resolve', { params })
      return data
    },

    /**
     * 按范式执行检索（批次6：检索唯一入口）。kbIds 只对图内 scope 留空的范式生效
     * ——写死范围的专属范式优先按图执行。身份由 proxyClient 注入的 X-KB-User 决定。
     *
     * 36号 §九：检索失败的用户可见文案在此中文化——「one or more knowledge bases
     * were not found」只在全库尚无任何可检索 Build（或不可见）时出现，用户需要
     * 的是可行动的中文原因，不是英文内部话术。
     */
    async runParadigmSearch(
      paradigmId: string,
      query: string,
      options?: {
        domain?: string
        kbIds?: string[]
        debug?: boolean
        /** A2 章节范围（39 号 §2.2）：within.section_refs + section_scope */
        within?: { document_refs?: string[]; section_refs?: string[]; section_scope?: 'exact' | 'descendants' }
      },
    ): Promise<ParadigmSearchResult> {
      const payload: Record<string, unknown> = {
        query,
        domain: options?.domain,
        debug: options?.debug ?? false,
      }
      if (options?.kbIds?.length) payload.kbIds = options.kbIds
      if (options?.within) payload.within = options.within
      try {
        const { data } = await client.post(`/api/v1/paradigm/${paradigmId}/search`, payload)
        return data
      } catch (err: unknown) {
        throw localizeSearchError(err)
      }
    },

    /**
     * A3 结构化查询（39 号 §3.3）：表格资产 ref（st_ 或内部 "{doc}#table:{t}"）
     * + schema-bound DSL。与 MCP get_knowledge 的 query 分支同一 service——
     * 网页与 Agent 对同一查询同结果。
     *
     * P1-6 契约：POST /api/v1/structure/query，ref/domain/kbId 全在 body——
     * 内部 ref 含 # 与 /，不进 URL path（fragment 截断 + path 编码陷阱）。
     */
    async queryStructure(
      ref: string, query: TableQuerySpec,
      opts?: { domain?: string; kbId?: string },
    ): Promise<TableQueryResult> {
      const { data } = await client.post('/api/v1/structure/query', {
        ref,
        query,
        domain: opts?.domain,
        ...(opts?.kbId ? { kbId: opts.kbId } : {}),
      })
      return data
    },

    /**
     * A2 结构导航（39 号 §2.3）：ref + 白名单关系（parent/children/
     * previous/next/ancestors/descendants…）。与 MCP get_knowledge 的
     * navigate 分支同一 service，网页与 Agent 同源。
     *
     * P1-6 契约：ref 走 query 参数（内部 ref 含 # / 不能进 path）。
     */
    async navigateStructure(
      ref: string, relation: string,
      opts?: { domain?: string; kbId?: string; depth?: number; limit?: number; cursor?: string },
    ): Promise<StructureNavigateResult> {
      const params: Record<string, unknown> = {
        ref, relation, domain: opts?.domain,
      }
      if (opts?.kbId) params.kbId = opts.kbId
      if (opts?.depth != null) params.depth = opts.depth
      if (opts?.limit != null) params.limit = opts.limit
      if (opts?.cursor) params.cursor = opts.cursor
      const { data } = await client.get('/api/v1/structure/navigate', { params })
      return data
    },
  }
}
