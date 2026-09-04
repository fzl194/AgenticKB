/**
 * A0-1 / A0-5 / A0-6（34 号）：文档结构化页——版本可信 + v2 检索单元 + 文案清理。
 *
 * 要钉的行为：
 * - A0-1：默认请求 current_serving；versioning 不一致时显示「尚未进入搜索」；
 *   in_sync 显示版本一致；无 serving 显示最新解析 + 明确标记；可切换视图；
 * - A0-5：检索单元展示公开类型与章节上下文；alias 是搜索辅助（进高级信息折叠区，
 *   不冒充原文知识）；
 * - A0-6：表格提示不再出现已删除的 query_structured_asset 工具名。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

const kbApi = vi.hoisted(() => ({
  getDocument: vi.fn(),
  getDocumentKnowledge: vi.fn(),
  getDocumentParseResult: vi.fn(),
  getDocumentPreviewUrl: vi.fn(),
  downloadDocument: vi.fn(),
}))
const routerPush = vi.hoisted(() => vi.fn())

vi.mock('@/api/kb', () => ({ useKbApi: () => kbApi }))
vi.mock('vue-router', () => ({ useRouter: () => ({ push: routerPush }) }))
vi.mock('@/api/proxyClient', () => ({
  apiErrorDetail: async () => '网络错误',
}))
vi.mock('@/components/kb/DocumentStructureGraph.vue', () => ({
  default: {
    props: ['documentTitle', 'result'],
    template: '<div data-testid="structure-graph-stub">{{ documentTitle }} · {{ result.outline.length }}</div>',
  },
}))

import KbDocPreviewView from '@/views/kb/KbDocPreviewView.vue'

const DOC = {
  id: 'doc-1', document_name: 'manual.md', status: 'mined', file_size: 10,
}

function parseResult(
  overrides: Record<string, unknown> = {},
  versioningOverrides: Record<string, unknown> = {},
) {
  return {
    view: 'current_serving',
    versioning: {
      view: 'current_serving',
      serving: { document_snapshot_id: 's1', build_id: 'b1', source_content_revision: 1 },
      latest: { document_snapshot_id: 's2', source_content_revision: 2 },
      in_sync: false,
      latest_state: 'not_in_search',
      ...versioningOverrides,
    },
    snapshot: {
      id: 's1', title: 'manual', mime_type: 'text/markdown',
      quality_status: 'PASS', lifecycle_status: 'READY',
      parser_fingerprint: 'p@1', compiler_fingerprint: 'c@1',
      snapshot_fingerprint: 'f', created_by_run_id: 'r1',
      created_at: '2026-09-03T00:00:00Z',
      source_storage_object_id: 'so1', source_content_revision: 1,
    },
    outline: [{ element_id: 'e0', level: 1, title: '第一章' }],
    elements: { count: 0, items: [] },
    tables: [],
    segments: { count: 0, items: [] },
    diagnostics: { warnings: [], containers: 0, relations: 0 },
    ...overrides,
  }
}

function knowledge(units: unknown[] = [], assist: unknown[] = []) {
  return {
    mined: units.length > 0,
    build_id: 'b1',
    document_snapshot_id: 's1',
    segments: [],
    units_source: 'asset_retrieval_units_v2',
    retrieval_units: units,
    search_assist_units: assist,
  }
}

async function mountView() {
  const wrapper = mount(KbDocPreviewView, {
    props: { kbId: 'kb-1', docId: 'doc-1' },
  })
  await flushPromises()
  return wrapper
}

describe('A0-1/A0-5/A0-6 文档结构化页', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    kbApi.getDocument.mockResolvedValue(DOC)
    kbApi.getDocumentKnowledge.mockResolvedValue(knowledge([
      { representation_id: 'r1', unit_type: 'prose', text: '正文', structural_context: '第一章' },
      { representation_id: 'r2', unit_type: 'code', text: 'ls -l', structural_context: '第一章' },
    ], [
      { representation_id: 'a1', unit_type: 'query_alias', text: '如何配置', structural_context: '' },
    ]))
    kbApi.getDocumentParseResult.mockResolvedValue(parseResult())
    kbApi.getDocumentPreviewUrl.mockRejectedValue(new Error('no'))
    kbApi.downloadDocument.mockRejectedValue(new Error('no'))
  })

  it('A0-1: 默认请求 current_serving（不带 view 查询参数）', async () => {
    await mountView()
    expect(kbApi.getDocumentParseResult).toHaveBeenCalledWith('kb-1', 'doc-1')
  })

  it('A0-1: serving 与 latest 不一致 → 显示「尚未进入搜索」且展示当前可搜索版本号', async () => {
    kbApi.getDocumentParseResult.mockResolvedValue(parseResult())
    const wrapper = await mountView()
    const banner = wrapper.find('[data-testid="doc-version-banner"]')
    expect(banner.exists()).toBe(true)
    expect(banner.text()).toContain('尚未进入搜索')
    expect(banner.text()).toContain('当前可搜索')
  })

  it('A0-1: in_sync → 显示版本一致，无警示', async () => {
    kbApi.getDocumentParseResult.mockResolvedValue(parseResult({}, {
      view: 'current_serving',
      serving: { document_snapshot_id: 's2', build_id: 'b1', source_content_revision: 2 },
      latest: { document_snapshot_id: 's2', source_content_revision: 2 },
      in_sync: true,
      latest_state: 'in_search',
    }))
    const wrapper = await mountView()
    const banner = wrapper.find('[data-testid="doc-version-banner"]')
    expect(banner.exists()).toBe(true)
    expect(banner.text()).not.toContain('尚未进入搜索')
    expect(banner.text()).toContain('一致')
  })

  it('A0-1: 无 current_serving → 显示最新解析并明确「尚未进入搜索」', async () => {
    kbApi.getDocumentParseResult.mockResolvedValue(parseResult({}, {
      view: 'current_serving', serving: null,
      latest: { document_snapshot_id: 's2', source_content_revision: 2 },
      in_sync: false, latest_state: 'not_in_search',
    }))
    const wrapper = await mountView()
    const banner = wrapper.find('[data-testid="doc-version-banner"]')
    expect(banner.text()).toContain('尚未进入搜索')
    expect(banner.text()).toContain('最新')
  })

  it('A0-1: 切换「查看最新解析」→ 以 view=latest_revision 重新请求', async () => {
    const wrapper = await mountView()
    const toggle = wrapper.find('[data-testid="doc-version-toggle"]')
    expect(toggle.exists()).toBe(true)
    await toggle.trigger('click')
    await flushPromises()
    // 用「任意一次匹配」而非 lastCalledWith：VTU stub 环境下 click 可能双派发
    //（真实 el-button 单 listener 单执行）——钉住的是切换确实发出了 latest_revision 请求
    expect(kbApi.getDocumentParseResult).toHaveBeenCalledWith('kb-1', 'doc-1', 'latest_revision')
  })

  it('A0-5: 检索单元展示公开类型与章节上下文；alias 在搜索辅助区不冒充原文', async () => {
    const wrapper = await mountView()
    const unitsText = wrapper.find('[data-testid="doc-retrieval-units"]').text()
    expect(unitsText).toContain('prose')
    expect(unitsText).toContain('code')
    expect(unitsText).toContain('第一章')

    const assist = wrapper.find('[data-testid="doc-search-assist"]')
    expect(assist.exists()).toBe(true)
    expect(assist.text()).toContain('query_alias')
    expect(assist.text()).toContain('如何配置')
    // alias 不出现在默认检索单元清单里
    expect(unitsText).not.toContain('如何配置')
  })

  it('A0-6: 表格提示使用当前能力名称，不再出现 query_structured_asset', async () => {
    kbApi.getDocumentParseResult.mockResolvedValue(parseResult({
      tables: [{
        table_id: 't1', rows: 60, columns: 2,
        header: ['告警码', '原因'], preview: [['A101', '风扇停转']],
      }],
    }))
    const wrapper = await mountView()
    const text = wrapper.text()
    expect(text).not.toContain('query_structured_asset')
    expect(text).toContain('get_knowledge')
  })

  it('在结构化数据页直接暴露当前版本的文档结构图', async () => {
    const wrapper = await mountView()
    expect(wrapper.find('[data-testid="structure-graph-stub"]').exists()).toBe(false)

    ;(wrapper.vm as unknown as { activeTab: string }).activeTab = 'structured'
    await wrapper.vm.$nextTick()
    const graph = wrapper.get('[data-testid="structure-graph-stub"]')

    expect(wrapper.find('el-collapse-item[title="文档结构图"]').exists()).toBe(true)
    expect(graph.text()).toContain('manual.md')
    expect(graph.text()).toContain('1')
  })
})
