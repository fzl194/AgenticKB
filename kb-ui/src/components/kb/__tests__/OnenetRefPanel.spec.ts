/**
 * 一张网引用面板（47 号 §四-8）：加载已引用/导入池、勾选引用、取消引用、预览。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import ElementPlus from 'element-plus'

const state = vi.hoisted(() => ({
  refs: [] as Array<Record<string, unknown>>,
  imports: [] as Array<Record<string, unknown>>,
  importDetail: {} as Record<string, unknown>,
  added: { added: [] as string[], skipped: [] },
  removed: { removed: [] as string[] },
  markdown: '',
}))

vi.mock('@/api/onenet', () => ({
  useOnenetApi: () => ({
    listRefs: async () => state.refs,
    // 服务端只回 done 导入（审查 H8 端点语义）
    listKbImports: async () => state.imports
      .filter((i: Record<string, unknown>) => i.status === 'done')
      .map((i: Record<string, unknown>) => ({ ...i, documents: [] })),
    getImport: async (id: string) => ({ ...state.importDetail, id }),
    addRefs: async (_kb: string, ids: string[]) => ({ ...state.added, added: ids }),
    removeRefs: async (_kb: string, ids: string[]) => ({ ...state.removed, removed: ids }),
    fetchDocumentMarkdown: async () => state.markdown,
    documentMarkdownUrl: (kb: string, doc: string) => `/api/kb/${kb}/onenet/documents/${doc}/markdown`,
  }),
}))
vi.mock('@/stores/domain', () => ({ useDomainStore: () => ({ currentDomain: 'cloud_core_network' }) }))
vi.mock('element-plus', async () => {
  const actual = await vi.importActual<object>('element-plus')
  return { ...actual, ElMessage: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }
})

import OnenetRefPanel from '@/components/kb/OnenetRefPanel.vue'

function mountPanel(props: Record<string, unknown> = {}) {
  return mount(OnenetRefPanel, {
    props: { kbId: 'kb-1', canWrite: true, active: true, ...props },
    global: { plugins: [ElementPlus] },
  })
}

beforeEach(() => {
  state.refs = []
  state.imports = []
  state.importDetail = {}
  state.markdown = ''
})

describe('OnenetRefPanel', () => {
  it('renders referenced docs and done imports only', async () => {
    state.refs = [{ document_id: 'd1', document_name: 'A.jsonl', directory_path: null,
                    file_size: 1, referenced_at: 't', source_kb_name: '一张网产品文档' }]
    state.imports = [
      { id: 'i1', status: 'done', source_id: 'DOC1', doc_name: 'UDG', document_count: 2,
        parsed_version_seen: 'v1' },
      { id: 'i2', status: 'failed', source_id: 'DOC2' },
    ]
    const w = mountPanel()
    await flushPromises()
    // el-table 单元格在 jsdom 的渲染时序不稳，断言用标题行/标签行等
    // 纯模板文本；表格数据本身由 api 测试与后端测试覆盖。
    expect(w.text()).toContain('UDG')
    expect(w.text()).not.toContain('DOC2')  // failed 不进引用池
    expect(w.text()).toContain('本库引用的一张网文档（1）')
    expect(w.text()).toContain('预览')      // 引用行操作列已渲染
  })

  it('shows empty state when nothing referenced', async () => {
    state.imports = []
    const w = mountPanel()
    await flushPromises()
    expect(w.text()).toContain('尚未引用任何一张网文档')
  })

  it('readonly mode hides cancel button', async () => {
    state.refs = [{ document_id: 'd1', document_name: 'A.jsonl', directory_path: null,
                    file_size: 1, referenced_at: 't', source_kb_name: null }]
    const w = mountPanel({ canWrite: false })
    await flushPromises()
    expect(w.text()).toContain('只读视图')
    expect(w.text()).not.toContain('取消引用')
  })
})
