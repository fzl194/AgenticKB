/**
 * RetrievalRecordsPanel 的 EN 渲染/导出断言（四区英化 Task 6 / 规则 R11、R13）。
 *
 * mock 与挂载代码复制自同目录 RetrievalRecordsPanel.spec.ts，区别只在语言：
 * 全局语言键必须在 pinia 创建**之前**写入——locale store 在实例化时读它。
 * 装进插件的 pinia 与活动 pinia 是同一个实例（否则 t() 读不到切换）。
 *
 * 夹具数据一律 ASCII：EN 态下整块界面的文本里出现任何 CJK 字符都算泄漏。
 * 导出用例守 R13：CSV 表头与文件名在**导出时**求值，跟随界面语言。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { enableAutoUnmount, flushPromises, shallowMount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import type { Pinia } from 'pinia'

const api = vi.hoisted(() => ({ getSummary: vi.fn(), list: vi.fn(), getOne: vi.fn(), exportAll: vi.fn() }))
const domain = vi.hoisted(() => ({ currentDomain: 'domain-a' }))
const kbApi = vi.hoisted(() => ({ listKbs: vi.fn() }))
const operatorApi = vi.hoisted(() => ({ listParadigms: vi.fn() }))
const saveBlob = vi.hoisted(() => vi.fn())

vi.mock('@/api/retrievalRecords', () => ({ useRetrievalRecordsApi: () => api }))
vi.mock('@/stores/domain', () => ({ useDomainStore: () => domain }))
vi.mock('@/api/kb', () => ({ useKbApi: () => kbApi }))
vi.mock('@/api/operator', () => ({ useOperatorApi: () => operatorApi }))
vi.mock('@/utils/download', () => ({ saveBlob }))

import RetrievalRecordsPanel from '@/components/retrieval/RetrievalRecordsPanel.vue'
import { useRetrievalNames } from '@/components/retrieval/useRetrievalNames'
import { useLocaleStore } from '@/stores/locale'

enableAutoUnmount(afterEach)

/** 任何 CJK 字符都算泄漏：EN 态下整块界面不该残留中文。 */
const CJK = /[一-龥]/

const record = {
  id: 'call-1', occurred_at: '2026-09-28T01:02:03Z', completed_at: '2026-09-28T01:02:04Z',
  domain: 'domain-a', actor_user_id: 'u-1', actor_username: 'alice', source: 'mcp',
  operation: 'search', tool_name: 'search_knowledge', mcp_key_id: 'key-1', kb_ids: ['kb-1'],
  query_text: 'core-network-alarm', paradigm_id: 'p-1', paradigm_version: 2, status: 'success',
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
      query: 'core-network-alarm', top_k: 2,
      within: { document_refs: ['doc-1'] },
    },
    effective_context_json: {
      domain: 'domain-a', kb_ids: ['kb-1'], paradigm_id: 'p-1',
    },
    response_mode: 'snapshot',
    response_json: {
      query: 'core-network-alarm',
      evidence: [
        {
          ref: 'ev-1', type: 'prose', content: 'first evidence',
          source: { document_ref: 'doc-1', file_name: 'manual-a.pdf', section: 'Alarm Handling' },
          truncated: false,
        },
        {
          ref: 'ev-2', type: 'table_row', content: '<img src=x onerror=alert(1)>second evidence',
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

let pinia: Pinia

// 顺序铁律：全局键先于 pinia 写入——store 在创建时读它
beforeEach(() => {
  localStorage.setItem('kb-ui-lang', 'en')
  pinia = createPinia()
  setActivePinia(pinia)
  vi.clearAllMocks()
  useRetrievalNames().reset()
  domain.currentDomain = 'domain-a'
  api.getSummary.mockResolvedValue({
    days: 7,
    available: true,
    summary: { total_calls: 1, calls: 1, no_result: 0, no_result_rate: 0, failed: 0, failure_rate: 0, p95_duration_ms: 120, avg_duration_ms: 100, active_paradigms: 1 },
    trend: [], paradigms: [], tools: [], sources: {}, top_queries: [],
  })
  api.list.mockResolvedValue({ items: [record], next_cursor: null, has_more: false, page_size: 25 })
  api.getOne.mockResolvedValue(record)
  api.exportAll.mockResolvedValue({ items: [record, readRecord], truncated: false, row_limit: 10000 })
  kbApi.listKbs.mockResolvedValue([{ id: 'kb-1', name: 'cloud-core-manual' }, { id: 'kb-2', name: 'transport-manual' }])
  operatorApi.listParadigms.mockResolvedValue([{ id: 'p-1', name: 'alarm-paradigm' }, { id: 'p-2', name: 'config-paradigm' }])
})
afterEach(() => localStorage.removeItem('kb-ui-lang'))

async function mountPanel() {
  const wrapper = shallowMount(RetrievalRecordsPanel, { global: { plugins: [pinia] } })
  await flushPromises()
  return wrapper
}

function optionTexts(wrapper: Awaited<ReturnType<typeof mountPanel>>, selector: string): string[] {
  return wrapper.find(selector).findAll('option').map(option => option.text())
}

async function csvLines(blob: Blob): Promise<string[]> {
  // text() 按 spec 会剥 BOM；兜底再剥一次，保证首行就是表头
  return (await blob.text()).replace(/^\\uFEFF/, '').split('\r\n')
}

describe('retrieval records panel (EN)', () => {
  it('labels the summary cards, filters, buttons and table headers in English', async () => {
    const wrapper = await mountPanel()

    const labels = wrapper.findAllComponents({ name: 'StatsCard' }).map(card => card.props('label'))
    expect(labels).toEqual(['Calls', 'No-result Rate', 'Failure Rate', 'P95 Latency'])

    expect(wrapper.findAll('th').map(th => th.text())).toEqual([
      'Time', 'User', 'Source', 'Tool / Operation', 'Query or Action', 'Target Knowledge Base',
      'Paradigm', 'Status', 'Results', 'Duration', '',
    ])
    // 分页条只要有记录就渲染（翻页按钮在无游标时禁用）
    expect(wrapper.findAll('button').map(button => button.text()))
      .toEqual(['Filter', 'Reset', 'Export', 'Details', 'Previous Page', 'Next Page'])
    // 日期输入只在"自定义"档位渲染（默认 7 天档不出现）
    expect(wrapper.find('[aria-label="Start Date"]').exists()).toBe(false)
    for (const label of ['Time Range', 'Source', 'Status', 'Operation', 'Tool Name', 'Knowledge Base', 'User ID', 'Paradigm']) {
      expect(wrapper.find(`[aria-label="${label}"]`).exists(), label).toBe(true)
    }
    expect(wrapper.find('[placeholder="Tool Name"]').exists()).toBe(true)
    expect(wrapper.find('[placeholder="User ID"]').exists()).toBe(true)
    // 服务端名称原样透出，其余文案走界面语言
    expect(wrapper.find('.records-panel__table tbody').text()).toContain('cloud-core-manual')
    expect(wrapper.find('.records-panel__table tbody').text()).toContain('alarm-paradigm v2')
    expect(wrapper.text()).not.toMatch(CJK)
  })

  it('lists the filter options in English, server names untouched', async () => {
    const wrapper = await mountPanel()

    expect(optionTexts(wrapper, 'select[aria-label="Time Range"]')).toEqual(['Last 7 days', 'Last 30 days', 'Last 90 days', 'Custom'])
    expect(optionTexts(wrapper, 'select[aria-label="Source"]')).toEqual(['All Sources', 'Web', 'MCP', 'API'])
    expect(optionTexts(wrapper, 'select[aria-label="Status"]'))
      .toEqual(['All Statuses', 'Processing', 'Success', 'No Result', 'Access Denied', 'Invalid Parameters', 'Timeout', 'Failed'])
    expect(optionTexts(wrapper, 'select[aria-label="Operation"]')).toEqual(['All Operations', 'Search', 'Read', 'Upload', 'Replace'])
    expect(optionTexts(wrapper, 'select[aria-label="Knowledge Base"]')).toEqual(['All Knowledge Bases', 'cloud-core-manual', 'transport-manual'])
    expect(optionTexts(wrapper, 'select[aria-label="Paradigm"]')).toEqual(['All Paradigms', 'alarm-paradigm', 'config-paradigm'])
    // 切到自定义后出现日期输入与分隔文案
    await wrapper.find('select[aria-label="Time Range"]').setValue('custom')
    expect(wrapper.find('.records-panel__range-sep').text()).toBe('to')
    expect(wrapper.find('input[aria-label="Start Date"]').exists()).toBe(true)
    expect(wrapper.find('input[aria-label="End Date"]').exists()).toBe(true)
  })

  it('translates the status and source cells of a row and the pager', async () => {
    api.list.mockResolvedValue({
      // tool_name 为空 → 回落到操作文案（有 tool_name 时原样透出服务端名称）
      items: [{ ...record, source: 'web', status: 'failed', tool_name: null }],
      next_cursor: 'cursor-2', has_more: true, page_size: 25,
    })
    const wrapper = await mountPanel()

    const cells = wrapper.findAll('.records-panel__table tbody td').map(cell => cell.text())
    expect(cells[2]).toBe('Web')
    expect(cells[3]).toBe('Search')
    expect(wrapper.find('.records-panel__status').text()).toBe('Failed')
    expect(wrapper.find('.records-panel__pager span').text()).toBe('Records on this page: 1')
    expect(wrapper.findAll('.records-panel__pager button').map(button => button.text())).toEqual(['Previous Page', 'Next Page'])
  })

  it('validates custom ranges in English without hitting the API', async () => {
    const wrapper = await mountPanel()

    await wrapper.find('select[aria-label="Time Range"]').setValue('custom')
    await wrapper.find('input[aria-label="Start Date"]').setValue('2026-10-01')
    await wrapper.find('input[aria-label="End Date"]').setValue('')
    await wrapper.find('form').trigger('submit')
    expect(wrapper.find('.records-panel__filter-error').text())
      .toBe('Select both a start date and an end date for the custom range')

    await wrapper.find('input[aria-label="End Date"]').setValue('2026-09-30')
    await wrapper.find('form').trigger('submit')
    expect(wrapper.find('.records-panel__filter-error').text())
      .toBe('The start date cannot be later than the end date')
  })

  it('exports a CSV whose headers, cells and file name follow the UI language (R13)', async () => {
    api.exportAll.mockResolvedValue({
      items: [
        record,
        { ...record, id: 'call-2', source: 'web', status: 'failed', kb_ids: ['kb-1', 'kb-2'], result_count: null, duration_ms: null, error_code: 'E_TIMEOUT' },
      ],
      truncated: false, row_limit: 10000,
    })
    const wrapper = await mountPanel()

    await wrapper.findAll('button').find(b => b.text() === 'Export')!.trigger('click')
    await flushPromises()

    expect(api.exportAll).toHaveBeenCalledWith('domain-a', expect.objectContaining({ days: 7 }))
    expect(saveBlob).toHaveBeenCalledTimes(1)
    const [blob, filename] = saveBlob.mock.calls[0]
    expect(filename).toMatch(/^retrieval-records_\d{8}-\d{6}\.csv$/)
    // BOM 让 Excel 以 UTF-8 打开（从原始字节断言）
    const head = new Uint8Array(await blob.arrayBuffer()).slice(0, 3)
    expect([...head]).toEqual([0xEF, 0xBB, 0xBF])
    expect(await csvLines(blob)).toEqual([
      'Time,User,Source,Tool / Operation,Query or Action,Target Knowledge Base,Paradigm,Status,Result Count,Duration (ms),Error Code,Call ID',
      '2026-09-28 01:02:03,alice,MCP,search_knowledge,core-network-alarm,cloud-core-manual,alarm-paradigm v2,Success,3,120,,call-1',
      // 多库分隔符随语言（"、→", "），含逗号的单元格按 CSV 规则加引号
      '2026-09-28 01:02:03,alice,Web,search_knowledge,core-network-alarm,"cloud-core-manual, transport-manual",alarm-paradigm v2,Failed,,,E_TIMEOUT,call-2',
    ])
    // 未截断时无提示
    expect(wrapper.find('.records-panel__filter-notice').exists()).toBe(false)
  })

  it('re-evaluates the CSV headers and file name after a runtime language switch (R13)', async () => {
    const wrapper = await mountPanel()

    await wrapper.findAll('button').find(b => b.text() === 'Export')!.trigger('click')
    await flushPromises()
    const [enBlob, enFilename] = saveBlob.mock.calls[0]
    expect(enFilename).toMatch(/^retrieval-records_/)
    expect((await csvLines(enBlob))[0]).toBe('Time,User,Source,Tool / Operation,Query or Action,Target Knowledge Base,Paradigm,Status,Result Count,Duration (ms),Error Code,Call ID')

    useLocaleStore().setLang('zh')
    await nextTick()
    expect(wrapper.findAll('th').map(th => th.text())).toEqual([
      '时间', '用户', '来源', 'Tool / 操作', '查询或动作', '目标知识库', '范式', '状态', '结果', '耗时', '',
    ])
    expect(wrapper.find('.records-panel__status').text()).toBe('成功')

    await wrapper.findAll('button').find(b => b.text() === '导出')!.trigger('click')
    await flushPromises()
    const [zhBlob, zhFilename] = saveBlob.mock.calls[1]
    expect(zhFilename).toMatch(/^检索记录_\d{8}-\d{6}\.csv$/)
    expect((await csvLines(zhBlob))[0])
      .toBe('时间,用户,来源,Tool / 操作,查询或动作,目标知识库,范式,状态,结果数,耗时(ms),错误码,调用 ID')
  })

  it('renders the export notices in English', async () => {
    const wrapper = await mountPanel()

    api.exportAll.mockResolvedValue({ items: [], truncated: false, row_limit: 10000 })
    await wrapper.findAll('button').find(b => b.text() === 'Export')!.trigger('click')
    await flushPromises()
    expect(saveBlob).not.toHaveBeenCalled()
    expect(wrapper.find('.records-panel__filter-notice').text()).toBe('No records to export under the current filters')

    api.exportAll.mockResolvedValue({ items: [record], truncated: true, row_limit: 10000 })
    await wrapper.findAll('button').find(b => b.text() === 'Export')!.trigger('click')
    await flushPromises()
    expect(wrapper.find('.records-panel__filter-notice').text())
      .toBe('Matching rows exceed the limit of 10000; the export was truncated to the limit')
  })

  it('shows the loading, error and empty states in English', async () => {
    api.list.mockReturnValue(new Promise(() => {}))
    const loading = shallowMount(RetrievalRecordsPanel, { global: { plugins: [pinia] } })
    // onMounted 置 loading 后 DOM 要等一个 tick
    await nextTick()
    expect(loading.find('.records-panel__notice').text()).toBe('Loading retrieval records…')
    expect(loading.text()).not.toMatch(CJK)

    api.list.mockRejectedValue(new Error('list downstream down'))
    const failed = await mountPanel()
    const notice = failed.find('.records-panel__notice--error')
    expect(notice.text()).toContain('list downstream down')
    expect(notice.find('button').text()).toBe('Retry')

    api.list.mockResolvedValue({ items: [], next_cursor: null, has_more: false, page_size: 25 })
    const empty = await mountPanel()
    expect(empty.find('.records-panel__empty strong').text()).toBe('No retrieval records yet')
    expect(empty.find('.records-panel__empty span').text())
      .toBe('Unified records are kept starting from this release; legacy Java logs are not migrated here.')
    expect(empty.text()).not.toMatch(CJK)
  })

  it('renders the detail drawer fields, payload sections and truncation sentence in English', async () => {
    api.getOne.mockResolvedValue(snapshotRecord)
    const wrapper = await mountPanel()

    await wrapper.find('[data-test="record-detail-call-1"]').trigger('click')
    await flushPromises()

    const drawer = wrapper.find('.records-panel__drawer')
    expect(drawer.attributes('aria-label')).toBe('Call Details')
    expect(drawer.find('h3').text()).toBe('Call Details')
    expect(drawer.find('button[aria-label="Close"]').exists()).toBe(true)
    expect(drawer.findAll('dt').map(dt => dt.text())).toEqual([
      'Call ID', 'Occurred At', 'User', 'Source', 'Tool / Operation', 'Query', 'Target Knowledge Base',
      'Paradigm', 'Status', 'Result Count', 'Total Duration', 'Error Code',
    ])
    expect(drawer.findAll('summary').map(summary => summary.text()))
      .toEqual(['Call Parameters', 'Effective Execution Parameters', 'Response Content', 'Raw JSON'])
    // 被标签切碎的句子按片段拼接：片段各自的标点随语言
    expect(drawer.find('.records-panel__payload-warning').text()).toBe('The response snapshot was truncated, omitting 3 items.')
    expect(drawer.findAll('.records-panel__evidence strong').map(strong => strong.text())).toEqual(['Evidence 1', 'Evidence 2'])
    // 来源 = 文件名 · 章节 · 页码 · 文档引用（语言中性的 " · " 分隔）
    expect(drawer.find('.records-panel__evidence-source').text()).toBe('Source: manual-a.pdf · Alarm Handling · doc-1')
    expect(drawer.find('.records-panel__evidence-truncated').text()).toBe('This evidence content has been truncated')
    expect(wrapper.text()).not.toMatch(CJK)
  })

  it('closes the truncation sentence with the English full stop when nothing was omitted', async () => {
    api.getOne.mockResolvedValue({
      ...snapshotRecord,
      payload: { ...snapshotRecord.payload, response_omitted_count: 0 },
    })
    const wrapper = await mountPanel()

    await wrapper.find('[data-test="record-detail-call-1"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('.records-panel__payload-warning').text()).toBe('The response snapshot was truncated.')
  })

  it('labels the whitelisted MCP detail fields in English', async () => {
    api.list.mockResolvedValue({ items: [readRecord], next_cursor: null, has_more: false, page_size: 25 })
    api.getOne.mockResolvedValue(readRecord)
    const wrapper = await mountPanel()

    await wrapper.find('[data-test="record-detail-call-read-1"]').trigger('click')
    await flushPromises()

    const fields = wrapper.findAll('.records-panel__details dt').map(dt => dt.text())
    expect(fields.slice(-5)).toEqual(['Action', 'Reference Type', 'File Count', 'Uploaded Count', 'Failed Count'])
    expect(wrapper.text()).not.toContain('must-not-render')
  })

  it('renders the reference note, the no-payload note and the detail failure in English', async () => {
    api.getOne.mockResolvedValue(referenceRecord)
    const wrapper = await mountPanel()
    await wrapper.find('[data-test="record-detail-call-1"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('.records-panel__reference-note').text())
      .toBe('This operation reads an existing document or section, so the body is not saved again; only traceable references are kept below.')

    api.getOne.mockResolvedValue({ ...record, payload: null })
    const bare = await mountPanel()
    await bare.find('[data-test="record-detail-call-1"]').trigger('click')
    await flushPromises()
    expect(bare.find('.records-panel__payload-empty').text()).toBe('This record retains no call parameters or response snapshot.')

    api.getOne.mockRejectedValue(new Error('detail unavailable'))
    const failed = await mountPanel()
    await failed.find('[data-test="record-detail-call-1"]').trigger('click')
    await flushPromises()
    const notice = failed.find('.records-panel__drawer .records-panel__notice--error')
    expect(notice.text()).toBe('Failed to load details: detail unavailable')
  })
})
