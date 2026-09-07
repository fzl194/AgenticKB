/**
 * A1（37/38 号）：检索面板——来源成为结构入口 + 位置徽标。
 *
 * 要钉的行为：
 * - locator 徽标按 kind 渲染（页码/行区间/Sheet!Cell/native description）；
 * - 文件名/章节点击 → 服务端解析端点 → 路由到文档页（document 模式无锚、
 *   section 模式带大纲锚、table 模式带表格锚）；
 * - locator 缺省（旧快照）不渲染徽标且不出「查看表格」。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

const kbApi = vi.hoisted(() => ({ updateKb: vi.fn() }))
const operatorApi = vi.hoisted(() => ({ listParadigms: vi.fn() }))
const servingApi = vi.hoisted(() => ({
  resolveParadigm: vi.fn(),
  runParadigmSearch: vi.fn(),
  getEvidenceFull: vi.fn(),
  getEvidenceSource: vi.fn(),
}))
const routerPush = vi.hoisted(() => vi.fn())

vi.mock('@/api/kb', () => ({ useKbApi: () => kbApi }))
vi.mock('@/api/operator', () => ({ useOperatorApi: () => operatorApi }))
vi.mock('@/api/serving', () => ({ useServingApi: () => servingApi }))
vi.mock('@/api/proxyClient', () => ({ apiErrorDetail: async () => '网络错误' }))
vi.mock('@/stores/domain', () => ({
  useDomainStore: () => ({ currentDomain: 'generic' }),
}))
vi.mock('vue-router', () => ({ useRouter: () => ({ push: routerPush }) }))

import KbSearchPanel from '@/components/kb/KbSearchPanel.vue'
import type { EvidenceItem } from '@/types/operator'

const KB = {
  id: 'kb-1', name: '规范库', visibility: 'private', created_at: '2026-09-01',
} as never

// 全局 ElInput 桩（test/setup.ts）不透传 v-model/key 事件——本 spec 需要
// 真实输入触发检索，挂载级覆盖为可透传桩。
const ElInputStub = {
  props: ['modelValue'],
  emits: ['update:modelValue'],
  template: `<input
    :value="modelValue"
    @input="$emit('update:modelValue', $event.target.value)"
    @keyup="$emit('keyup', $event)"
  />`,
}

async function mountPanel() {
  const wrapper = mount(KbSearchPanel, {
    props: { kb: KB, canWrite: true },
    global: { stubs: { ElInput: ElInputStub } },
  })
  await flushPromises()
  return wrapper
}

async function search(wrapper: Awaited<ReturnType<typeof mountPanel>>, evidence: EvidenceItem[]) {
  servingApi.runParadigmSearch.mockResolvedValue({
    evidenceResponse: { evidence, has_more: false },
  })
  await wrapper.find('input').setValue('告警 A101')
  await wrapper.find('input').trigger('keyup.enter')
  await flushPromises()
  expect(servingApi.runParadigmSearch).toHaveBeenCalled()
  return wrapper
}

describe('A1 检索面板来源入口', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    operatorApi.listParadigms.mockResolvedValue([])
    servingApi.resolveParadigm.mockResolvedValue({
      bound: true, paradigmId: 'p1', name: '混合检索', version: 1, source: 'official',
    })
    servingApi.getEvidenceSource.mockResolvedValue({
      document_id: 'doc-uuid-1',
      kb_id: 'kb-1',
      file_name: 'spec.xlsx',
      section_element_id: 'h-2',
      section_path: '第二章 > 告警表',
      table_ref: 't1',
      row_index: 3,
      locator: { kind: 'sheet_cell', sheet: '告警表', cell: 'B7' },
    })
  })

  it.each([
    [{ kind: 'page', page: 7 }, '第 7 页'],
    [{ kind: 'line_range', line_start: 11, line_end: 20 }, '第 12–20 行'],
    [{ kind: 'line_range', line_start: 4, line_end: 5 }, '第 5 行'],
    [{ kind: 'sheet_cell', sheet: '告警表', cell: 'B7' }, '告警表!B7'],
    [{ kind: 'native', description: '第 12 段' }, '第 12 段'],
  ])('locator 徽标：%j → %s', async (locator, label) => {
    const wrapper = await mountPanel()
    await search(wrapper, [{
      ref: 'ev_1', type: 'prose', content: '命中文本',
      source: { file_name: 'spec.md', section: '第一章', locator },
    }])
    expect(wrapper.find('[data-testid="loc-ev_1"]').text()).toBe(label)
  })

  it('无 locator（旧快照）不渲染徽标与「查看表格」', async () => {
    const wrapper = await mountPanel()
    await search(wrapper, [{
      ref: 'ev_2', type: 'prose', content: '命中文本',
      source: { file_name: 'spec.md' },
    }])
    expect(wrapper.find('[data-testid="loc-ev_2"]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('查看表格')
  })

  it('文件名点击 → 解析端点 → 跳文档页原始预览（无锚）', async () => {
    const wrapper = await mountPanel()
    await search(wrapper, [{
      ref: 'ev_3', type: 'prose', content: '命中文本',
      source: { file_name: 'spec.xlsx', section: '第二章 > 告警表' },
    }])
    const links = wrapper.findAll('.kb-search__src-link')
    await links[0].trigger('click')  // 文件名
    await flushPromises()
    expect(servingApi.getEvidenceSource).toHaveBeenCalledWith('ev_3', 'generic', 'kb-1')
    expect(routerPush).toHaveBeenCalledWith({
      name: 'kb-doc-preview',
      params: { kbId: 'kb-1', docId: 'doc-uuid-1' },
      query: {},
    })
  })

  it('章节点击 → 跳文档页并带大纲锚', async () => {
    const wrapper = await mountPanel()
    await search(wrapper, [{
      ref: 'ev_4', type: 'prose', content: '命中文本',
      source: { file_name: 'spec.xlsx', section: '第二章 > 告警表' },
    }])
    const links = wrapper.findAll('.kb-search__src-link')
    await links[1].trigger('click')  // 章节路径
    await flushPromises()
    expect(routerPush).toHaveBeenCalledWith({
      name: 'kb-doc-preview',
      params: { kbId: 'kb-1', docId: 'doc-uuid-1' },
      query: { anchor: 'h-2' },
    })
  })

  it('表格证据「查看表格」→ 跳文档页并带表格锚+行号+章节锚', async () => {
    const wrapper = await mountPanel()
    await search(wrapper, [{
      ref: 'ev_5', type: 'table_row', content: '告警码=A101；原因=风扇停转',
      source: {
        file_name: 'spec.xlsx', section: '第二章 > 告警表',
        locator: { kind: 'sheet_cell', sheet: '告警表', cell: 'B7', table_ref: 't1', row_index: 3 },
      },
    }])
    const tableBtn = wrapper.findAll('.kb-search__src-link')
      .find(b => b.text() === '查看表格')
    expect(tableBtn).toBeTruthy()
    await tableBtn!.trigger('click')
    await flushPromises()
    expect(routerPush).toHaveBeenCalledWith({
      name: 'kb-doc-preview',
      params: { kbId: 'kb-1', docId: 'doc-uuid-1' },
      query: { table: 't1', row: '3', anchor: 'h-2' },
    })
  })

  it('非 ev_ ref 不可定位（doc_/st_ 直接证据走结构工具，不在此跳转）', async () => {
    const wrapper = await mountPanel()
    await search(wrapper, [{
      ref: 'doc_9', type: 'document', content: '整文',
      source: { file_name: 'spec.md' },
    }])
    const links = wrapper.findAll('.kb-search__src-link')
    expect(links.length).toBe(0)
  })
})
