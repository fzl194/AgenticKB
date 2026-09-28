import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { enableAutoUnmount, flushPromises, shallowMount } from '@vue/test-utils'

const api = vi.hoisted(() => ({ getSummary: vi.fn(), list: vi.fn(), getOne: vi.fn() }))
const domain = vi.hoisted(() => ({ currentDomain: 'domain-a' }))

vi.mock('@/api/retrievalRecords', () => ({ useRetrievalRecordsApi: () => api }))
vi.mock('@/stores/domain', () => ({ useDomainStore: () => domain }))

import RetrievalRecordsPanel from '@/components/retrieval/RetrievalRecordsPanel.vue'

enableAutoUnmount(afterEach)

const record = {
  id: 'call-1', occurred_at: '2026-09-28T01:02:03Z', completed_at: '2026-09-28T01:02:04Z',
  domain: 'domain-a', actor_user_id: 'u-1', actor_username: 'alice', source: 'mcp',
  operation: 'search', tool_name: 'search_knowledge', mcp_key_id: 'key-1', kb_ids: ['kb-1'],
  query_text: '核心网告警', paradigm_id: 'p-1', paradigm_version: 2, status: 'success',
  result_count: 3, duration_ms: 120, error_code: null, details_json: {},
}

const readRecord = {
  ...record,
  id: 'call-read-1',
  operation: 'read',
  tool_name: 'get_knowledge',
  query_text: null,
  paradigm_id: null,
  paradigm_version: null,
  details_json: {
    action: 'structured_query', ref_type: 'st_', file_count: 4,
    uploaded_count: 2, failed_count: 1, arbitrary_payload: 'must-not-render',
  },
}

const snapshotRecord = {
  ...record,
  payload: {
    request_json: {
      query: '核心网告警', top_k: 2,
      within: { document_refs: ['doc-1'] },
    },
    effective_context_json: {
      domain: 'domain-a', kb_ids: ['kb-1'], paradigm_id: 'p-1',
    },
    response_mode: 'snapshot',
    response_json: {
      query: '核心网告警',
      evidence: [
        {
          ref: 'ev-1', type: 'prose', content: '第一条证据',
          source: { document_ref: 'doc-1', file_name: 'manual-a.pdf', section: '告警处理' },
          truncated: false,
        },
        {
          ref: 'ev-2', type: 'table_row', content: '<img src=x onerror=alert(1)>第二条证据',
          source: { document_ref: 'doc-2', file_name: 'manual-b.pdf' },
          truncated: true,
        },
      ],
      has_more: true,
    },
    response_refs_json: [],
    request_bytes: 88,
    response_bytes: 512,
    response_truncated: true,
    response_original_bytes: 1024,
    response_omitted_count: 3,
    response_sha256: 'a'.repeat(64),
    redactions_json: ['authorization'],
    payload_schema_version: 1,
  },
}

const referenceRecord = {
  ...readRecord,
  payload: {
    request_json: { ref: 'doc-1', mode: 'window', limit: 100 },
    effective_context_json: { domain: 'domain-a', kb_ids: ['kb-1'] },
    response_mode: 'reference',
    response_json: null,
    response_refs_json: [{
      view: 'document_content', ref: 'doc-1', next_cursor: 'next-1',
      segments: [{ ref: 'st-1', source: { document_ref: 'doc-1' } }],
    }],
    request_bytes: 42,
    response_bytes: 96,
    response_truncated: false,
    response_original_bytes: 96,
    response_omitted_count: 0,
    response_sha256: 'b'.repeat(64),
    redactions_json: [],
    payload_schema_version: 1,
  },
}

describe('RetrievalRecordsPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    domain.currentDomain = 'domain-a'
    api.getSummary.mockResolvedValue({
      days: 7,
      available: true,
      summary: { total_calls: 1, calls: 1, no_result: 0, no_result_rate: 0, failed: 0, failure_rate: 0, p95_duration_ms: 120, avg_duration_ms: 100, active_paradigms: 1 },
      trend: [], paradigms: [], tools: [], sources: {}, no_result_queries: [], top_queries: [],
    })
    api.list.mockResolvedValue({ items: [record], next_cursor: null, has_more: false, page_size: 25 })
    api.getOne.mockResolvedValue(record)
  })

  it('loads summary and the first page with a fixed knowledge base scope', async () => {
    const wrapper = shallowMount(RetrievalRecordsPanel, { props: { kbId: 'kb-1' } })
    await flushPromises()

    expect(api.getSummary).toHaveBeenCalledWith('domain-a', expect.objectContaining({ kbId: 'kb-1' }))
    expect(api.list).toHaveBeenCalledWith('domain-a', expect.objectContaining({ kbId: 'kb-1' }))
    expect(wrapper.text()).toContain('核心网告警')
    expect(wrapper.text()).toContain('search_knowledge')
    expect(wrapper.text()).toContain('p-1 v2')
  })

  it('applies one time window and the same complete filter set to list and summary', async () => {
    const wrapper = shallowMount(RetrievalRecordsPanel, { props: { mcpKeyId: 'key-1' } })
    await flushPromises()
    vi.clearAllMocks()
    api.getSummary.mockResolvedValue({ days: 30, available: true, summary: {}, trend: [], paradigms: [], tools: [], sources: {} })
    api.list.mockResolvedValue({ items: [], next_cursor: null, has_more: false, page_size: 25 })

    await wrapper.find('[aria-label="时间范围"]').setValue('30')
    await wrapper.find('[aria-label="来源"]').setValue('mcp')
    await wrapper.find('[aria-label="状态"]').setValue('failed')
    await wrapper.find('[aria-label="操作"]').setValue('read')
    await wrapper.find('[aria-label="Tool 名称"]').setValue('get_knowledge')
    await wrapper.find('[aria-label="知识库 ID"]').setValue('kb-1')
    await wrapper.find('[aria-label="用户 ID"]').setValue('u-2')
    await wrapper.find('[aria-label="范式 ID"]').setValue('p-2')
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    const expected = {
      days: 30, source: 'mcp', status: 'failed', operation: 'read',
      toolName: 'get_knowledge', kbId: 'kb-1', actorUserId: 'u-2',
      paradigmId: 'p-2', mcpKeyId: 'key-1',
    }
    expect(api.getSummary).toHaveBeenCalledWith('domain-a', expected)
    expect(api.list).toHaveBeenCalledWith('domain-a', expect.objectContaining(expected))
  })

  it('opens a safe detail drawer without displaying secrets or response bodies', async () => {
    const wrapper = shallowMount(RetrievalRecordsPanel)
    await flushPromises()
    await wrapper.find('[data-test="record-detail-call-1"]').trigger('click')
    await flushPromises()

    expect(api.getOne).toHaveBeenCalledWith('domain-a', 'call-1')
    expect(wrapper.text()).toContain('调用详情')
    expect(wrapper.text()).toContain('call-1')
    expect(wrapper.text()).not.toContain('Authorization')
  })

  it('shows the MCP action summary and only whitelisted detail fields', async () => {
    api.list.mockResolvedValue({ items: [readRecord], next_cursor: null, has_more: false, page_size: 25 })
    api.getOne.mockResolvedValue(readRecord)
    const wrapper = shallowMount(RetrievalRecordsPanel)
    await flushPromises()

    expect(wrapper.text()).toContain('structured_query')
    expect(wrapper.text()).toContain('st_')
    await wrapper.find('[data-test="record-detail-call-read-1"]').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('动作')
    expect(wrapper.text()).toContain('引用类型')
    expect(wrapper.text()).toContain('文件数')
    expect(wrapper.text()).toContain('上传成功数')
    expect(wrapper.text()).toContain('失败数')
    expect(wrapper.text()).not.toContain('must-not-render')
    expect(wrapper.text()).not.toContain('arbitrary_payload')
  })

  it('uses the new-version empty-state copy', async () => {
    api.list.mockResolvedValue({ items: [], next_cursor: null, has_more: false, page_size: 25 })
    const wrapper = shallowMount(RetrievalRecordsPanel)
    await flushPromises()

    expect(wrapper.text()).toContain('本版本上线后开始记录')
  })

  it('shows complete request context and ordered search evidence with safe JSON text', async () => {
    api.getOne.mockResolvedValue(snapshotRecord)
    const wrapper = shallowMount(RetrievalRecordsPanel)
    await flushPromises()

    await wrapper.find('[data-test="record-detail-call-1"]').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('调用参数')
    expect(wrapper.text()).toContain('有效执行参数')
    expect(wrapper.text()).toContain('返回内容')
    expect(wrapper.text()).toContain('top_k')
    expect(wrapper.text()).toContain('第一条证据')
    expect(wrapper.text()).toContain('第二条证据')
    expect(wrapper.text().indexOf('第一条证据')).toBeLessThan(wrapper.text().indexOf('第二条证据'))
    expect(wrapper.text()).toContain('manual-a.pdf')
    expect(wrapper.text()).toContain('原始 JSON')
    expect(wrapper.html()).not.toContain('<img src="x"')
  })

  it('renders Java evidenceResponse snapshots with the same evidence view', async () => {
    api.getOne.mockResolvedValue({
      ...snapshotRecord,
      payload: {
        ...snapshotRecord.payload,
        response_json: {
          evidenceResponse: {
            query: '核心网告警',
            evidence: [{ ref: 'ev-java', type: 'prose', content: 'Java 证据快照', source: { file_name: 'java.pdf' } }],
          },
        },
      },
    })
    const wrapper = shallowMount(RetrievalRecordsPanel)
    await flushPromises()

    await wrapper.find('[data-test="record-detail-call-1"]').trigger('click')
    await flushPromises()

    expect(wrapper.findAll('.records-panel__evidence')).toHaveLength(1)
    expect(wrapper.find('.records-panel__evidence').text()).toContain('Java 证据快照')
    expect(wrapper.find('.records-panel__evidence').text()).toContain('java.pdf')
  })
  it('explains reference-only responses without duplicating document bodies', async () => {
    api.getOne.mockResolvedValue(referenceRecord)
    const wrapper = shallowMount(RetrievalRecordsPanel)
    await flushPromises()

    await wrapper.find('[data-test="record-detail-call-1"]').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('正文未重复保存')
    expect(wrapper.text()).toContain('doc-1')
    expect(wrapper.text()).toContain('st-1')
  })

  it('makes response truncation explicit in the detail drawer', async () => {
    api.getOne.mockResolvedValue(snapshotRecord)
    const wrapper = shallowMount(RetrievalRecordsPanel)
    await flushPromises()

    await wrapper.find('[data-test="record-detail-call-1"]').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('返回快照已截断')
    expect(wrapper.text()).toContain('省略 3 项')
  })

  it('handles detail request failures and renders a friendly fallback', async () => {
    api.getOne.mockRejectedValue(new Error('detail unavailable'))
    const wrapper = shallowMount(RetrievalRecordsPanel)
    await flushPromises()

    await wrapper.find('[data-test="record-detail-call-1"]').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('详情加载失败')
    expect(wrapper.text()).toContain('detail unavailable')
  })
})
