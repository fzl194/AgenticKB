/**
 * A2（39 号 §2.4）：检索面板章节范围——
 * - 路由携带 scopeRef/scopeTitle/scopeMode 时吸收为范围状态并渲染徽标；
 * - 检索请求携带 within{section_refs, section_scope}（范围下推到召回）；
 * - 「改回整篇」清除范围后 within 不再携带。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

const servingApi = {
  resolveParadigm: vi.fn(),
  runParadigmSearch: vi.fn(),
  getEvidenceSource: vi.fn(),
}
const operatorApi = { listParadigms: vi.fn() }

let routeQuery: Record<string, unknown> = {}

vi.mock('@/api/serving', () => ({ useServingApi: () => servingApi }))
vi.mock('@/api/operator', () => ({ useOperatorApi: () => operatorApi }))
vi.mock('@/api/proxyClient', () => ({
  apiErrorDetail: async () => '错误',
  createProxyClient: () => ({ get: vi.fn(), post: vi.fn() }),
}))
vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn() }),
  useRoute: () => ({ query: routeQuery, params: {} }),
}))
vi.mock('@/stores/domain', () => ({
  useDomainStore: () => ({ currentDomain: 'default' }),
}))

import KbSearchPanel from '@/components/kb/KbSearchPanel.vue'
import type { EvidenceItem } from '@/types/operator'

const KB = {
  id: 'kb-1', name: '规范库', visibility: 'private', created_at: '2026-09-01',
} as never

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

async function search(wrapper: Awaited<ReturnType<typeof mountPanel>>) {
  servingApi.runParadigmSearch.mockResolvedValue({
    evidenceResponse: { evidence: [] as EvidenceItem[], has_more: false },
  })
  await wrapper.find('input').setValue('回退流程')
  await wrapper.find('input').trigger('keyup.enter')
  await flushPromises()
  expect(servingApi.runParadigmSearch).toHaveBeenCalled()
}

describe('A2 检索面板章节范围', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    routeQuery = {}
    operatorApi.listParadigms.mockResolvedValue([])
    servingApi.resolveParadigm.mockResolvedValue({
      bound: true, paradigmId: 'p1', name: '混合检索', version: 1, source: 'official',
    })
  })

  afterEach(() => { routeQuery = {} })

  it('无范围时检索不携带 within（宽检索，行为不回归）', async () => {
    const wrapper = await mountPanel()
    await search(wrapper)
    const call = servingApi.runParadigmSearch.mock.calls[0]
    expect(call?.[2]?.within).toBeUndefined()
    expect(wrapper.find('[data-testid="kb-search-scope"]').exists()).toBe(false)
  })

  it('路由带入范围 → 徽标 + within{section_refs, section_scope=descendants}', async () => {
    routeQuery = {
      scopeRef: 'spec.md#section:0/2',
      scopeTitle: '3.2 回退流程',
      scopeMode: 'descendants',
    }
    const wrapper = await mountPanel()
    const scopeText = wrapper.find('[data-testid="kb-search-scope"]').text()
    expect(scopeText).toContain('3.2 回退流程')
    expect(scopeText).toContain('本节及子节')
    await search(wrapper)
    const call = servingApi.runParadigmSearch.mock.calls[0]
    expect(call?.[2]?.within).toEqual({
      section_refs: ['spec.md#section:0/2'],
      section_scope: 'descendants',
    })
  })

  it('scopeMode 缺省按 exact（本节）', async () => {
    routeQuery = { scopeRef: 'spec.md#section:0', scopeTitle: '概述' }
    const wrapper = await mountPanel()
    expect(wrapper.find('[data-testid="kb-search-scope"]').text()).toContain('本节')
    await search(wrapper)
    const call = servingApi.runParadigmSearch.mock.calls[0]
    expect(call?.[2]?.within).toEqual({
      section_refs: ['spec.md#section:0'],
      section_scope: 'exact',
    })
  })

  it('「改回整篇」保留文档范围', async () => {
    routeQuery = { scopeRef: 'spec.md#section:0', scopeTitle: '概述', scopeDocumentRef: 'spec.md' }
    const wrapper = await mountPanel()
    const clear = wrapper.findAll('button').find(b => b.text().includes('改回整篇'))
    expect(clear).toBeTruthy()
    await clear!.trigger('click')
    await search(wrapper)
    const call = servingApi.runParadigmSearch.mock.calls[0]
    expect(call?.[2]?.within).toEqual({ document_refs: ['spec.md'] })
    expect(wrapper.text()).toContain('整篇')
  })

  it('无文档ref时明确清除为整库而不声称整篇', async () => {
    routeQuery = { scopeRef: 'spec.md#section:0', scopeTitle: '概述' }
    const wrapper = await mountPanel()
    expect(wrapper.text()).not.toContain('改回整篇')
    const clear = wrapper.findAll('button').find(b => b.text().includes('搜索整个知识库'))
    await clear!.trigger('click')
    await search(wrapper)
    expect(servingApi.runParadigmSearch.mock.calls[0]?.[2]?.within).toBeUndefined()
  })
})
