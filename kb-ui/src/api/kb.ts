/**
 * 知识库管理 API —— 全部经 main_control_service 反向代理转发到 mining 服务。
 *
 * 走 createProxyClient('mining')：baseURL 在每次请求时按当前域重算
 * （/api/control-plane/api/v1/proxy/{domain}/mining），切域即生效。
 * 当前用户身份由 proxyClient 的请求拦截器注入 X-KB-User 头（见 proxyClient.ts）。
 */
import { createProxyClient, extractItems, extractOne } from '@/api/proxyClient'
import type { ParseResult } from '@/api/mining'
import type {
  KbCreateBody, KbDetail, KbDocument, KbFolder, KbMember, KbMemberRole, KbMineResult,
  KbRunRecord, KbSummary, KbUpdateBody, KbUserCandidate, DocumentKnowledge,
} from '@/types/kb'

export function useKbApi() {
  const client = createProxyClient('mining')

  return {
    // ── KB CRUD ──
    async listKbs(domain: string): Promise<KbSummary[]> {
      const { data } = await client.get('/api/kb', { params: { domain } })
      return extractItems<KbSummary>(data)
    },

    async createKb(body: KbCreateBody): Promise<KbDetail> {
      const { data } = await client.post('/api/kb', body)
      return extractOne<KbDetail>(data)
    },

    async getKb(kbId: string): Promise<KbDetail> {
      const { data } = await client.get(`/api/kb/${kbId}`)
      return extractOne<KbDetail>(data)
    },

    async updateKb(kbId: string, body: KbUpdateBody): Promise<KbDetail> {
      const { data } = await client.patch(`/api/kb/${kbId}`, body)
      return extractOne<KbDetail>(data)
    },

    async deleteKb(kbId: string): Promise<void> {
      await client.delete(`/api/kb/${kbId}`)
    },

    // ── 成员 ──
    async addMember(kbId: string, body: { username: string; role: KbMemberRole }): Promise<KbMember> {
      const { data } = await client.post(`/api/kb/${kbId}/members`, body)
      return extractOne<KbMember>(data)
    },

    async listMembers(kbId: string): Promise<KbMember[]> {
      const { data } = await client.get(`/api/kb/${kbId}/members`)
      return extractItems<KbMember>(data)
    },

    async removeMember(kbId: string, userId: string): Promise<void> {
      await client.delete(`/api/kb/${kbId}/members/${userId}`)
    },

    /** 可加入该 KB 的候选用户(成员面板选择器用;排除 owner 与已成员,最小字段集)。 */
    async listMemberCandidates(kbId: string): Promise<KbUserCandidate[]> {
      const { data } = await client.get(`/api/kb/${kbId}/members/candidates`)
      return extractItems<KbUserCandidate>(data)
    },

    // ── 文件夹（G2/G3）──
    async listFolders(kbId: string): Promise<KbFolder[]> {
      const { data } = await client.get(`/api/kb/${kbId}/folders`)
      return extractItems<KbFolder>(data)
    },

    async createFolder(
      kbId: string,
      body: { parent_id: string | null; name: string },
    ): Promise<KbFolder> {
      const { data } = await client.post(`/api/kb/${kbId}/folders`, body)
      return extractOne<KbFolder>(data)
    },

    async renameFolder(kbId: string, folderId: string, name: string): Promise<KbFolder> {
      const { data } = await client.patch(`/api/kb/${kbId}/folders/${folderId}`, { name })
      return extractOne<KbFolder>(data)
    },

    async moveFolder(
      kbId: string,
      folderId: string,
      targetParentId: string | null,
    ): Promise<KbFolder> {
      const { data } = await client.post(`/api/kb/${kbId}/folders/${folderId}/move`, {
        target_parent_id: targetParentId,
      })
      return extractOne<KbFolder>(data)
    },

    async deleteFolder(kbId: string, folderId: string): Promise<void> {
      await client.delete(`/api/kb/${kbId}/folders/${folderId}`)
    },

    async moveDocument(
      kbId: string,
      docId: string,
      targetFolderId: string | null,
    ): Promise<KbDocument> {
      const { data } = await client.post(`/api/kb/${kbId}/documents/${docId}/move`, {
        target_folder_id: targetFolderId,
      })
      return extractOne<KbDocument>(data)
    },

    // ── 文档 ──
    /**
     * 上传单文件。zip 由后端自动解压（走同端点，后端按扩展名分支），
     * 解压结果用 uploadZip 拿数组；此方法假定非 zip，返回单个文档。
     */
    async uploadDocument(
      kbId: string,
      file: File,
      opts?: { directory?: string; documentType?: string },
    ): Promise<KbDocument> {
      const form = new FormData()
      form.append('file', file)
      if (opts?.directory) form.append('directory', opts.directory)
      if (opts?.documentType) form.append('document_type', opts.documentType)
      const { data } = await client.post(`/api/kb/${kbId}/documents`, form)
      return extractOne<KbDocument>(data)
    },

    /** 上传 zip（后端解压），返回解压出的文档数组。 */
    async uploadZip(kbId: string, file: File): Promise<KbDocument[]> {
      const form = new FormData()
      form.append('file', file)
      const { data } = await client.post(`/api/kb/${kbId}/documents`, form)
      const envelope = data as { documents?: KbDocument[] }
      if (Array.isArray(envelope.documents)) return envelope.documents
      // 后端若按单文件处理（非 zip 扩展名），兜底成单元素数组
      return [extractOne<KbDocument>(data)]
    },

    async listDocuments(kbId: string, directory?: string): Promise<KbDocument[]> {
      const { data } = await client.get(`/api/kb/${kbId}/documents`, {
        params: directory !== undefined ? { directory } : undefined,
      })
      return extractItems<KbDocument>(data)
    },

    async getDocument(kbId: string, docId: string): Promise<KbDocument> {
      const { data } = await client.get(`/api/kb/${kbId}/documents/${docId}`)
      return extractOne<KbDocument>(data)
    },

    async patchDocument(
      kbId: string,
      docId: string,
      body: { document_name?: string; document_type?: string },
    ): Promise<KbDocument> {
      const { data } = await client.patch(`/api/kb/${kbId}/documents/${docId}`, body)
      return extractOne<KbDocument>(data)
    },

    async downloadDocument(kbId: string, docId: string): Promise<Blob> {
      const { data } = await client.get(`/api/kb/${kbId}/documents/${docId}/download`, {
        responseType: 'blob',
      })
      return data
    },

    /**
     * 大文件预览直连地址（短时效预签名）。浏览器 iframe/img 直接指向
     * 对象存储，自带 Range 分页按需加载——避免全量下载后才首屏。
     */
    async getDocumentPreviewUrl(kbId: string, docId: string): Promise<string> {
      const { data } = await client.get(`/api/kb/${kbId}/documents/${docId}/preview-url`)
      return (data as { url: string }).url
    },

    async deleteDocument(kbId: string, docId: string): Promise<void> {
      await client.delete(`/api/kb/${kbId}/documents/${docId}`)
    },

    // ── 挖掘 ──
    /**
     * 触发挖掘。documentIds 非空 → 仅挖所选文档子集（选择性挖掘）；
     * 省略/空 → 整库增量。
     */
    async mineKb(
      kbId: string,
      documentIds?: string[],
      forceRedo?: boolean,
    ): Promise<KbMineResult> {
      const body: Record<string, unknown> = {}
      if (documentIds && documentIds.length) body.document_ids = documentIds
      if (forceRedo) body.force_redo = true
      const { data } = await client.post(
        `/api/kb/${kbId}/mine`,
        Object.keys(body).length ? body : undefined,
      )
      return data
    },

    /** 本 KB 的挖掘记录（最新在前）。 */
    async getKbRuns(kbId: string): Promise<KbRunRecord[]> {
      const { data } = await client.get(`/api/kb/${kbId}/runs`)
      return extractItems<KbRunRecord>(data)
    },

    /** 单文档已挖掘知识（切片分段 / 检索单元 / 实体提及）。 */
    async getDocumentKnowledge(kbId: string, docId: string): Promise<DocumentKnowledge> {
      const { data } = await client.get(`/api/kb/${kbId}/documents/${docId}/knowledge`)
      return extractOne<DocumentKnowledge>(data)
    },

    async getDocumentParseResult(kbId: string, docId: string): Promise<ParseResult> {
      const { data } = await client.get(`/api/kb/${kbId}/documents/${docId}/parse-result`)
      return data as ParseResult
    },
  }
}
