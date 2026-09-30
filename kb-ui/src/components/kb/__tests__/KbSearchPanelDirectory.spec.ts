/**
 * 57号工作线③：检索面板目录范围（filters.directory_prefix，含子目录递归）。
 * - 有文件夹时出现目录选择器；选中后检索携带 filters.directory_prefix；
 * - 清空（不限目录）时不携带 filters；
 * - 目录选项加载失败不阻断检索（可选增强）。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

const kbApi = vi.hoisted(() => ({
  updateKb: vi.fn(),
  listFolders: vi.fn(),
}))
const operatorApi = vi.hoisted(() => ({ listParadigms: vi.fn() }))
const servingApi = vi.hoisted(() => ({
  resolveParadigm: vi.fn(),
  runParadigmSearch: vi.fn(),
  getEvidenceFull: vi.fn(),
  getEvidenceSource: vi.fn(),
}))

vi.mock('@/api/kb', () => ({ useKbApi: () => kbApi }))
vi.mock('@/api/operator', () => ({ useOperatorApi: () => operatorApi }))
vi.mock('@/api/serving', () => ({ useServingApi: () => servingApi }))
vi.mock('@/api/proxyClient', () => ({ apiErrorDetail: async () => '网络错误' }))
vi.mock('@/stores/domain', () => ({
  useDomainStore: () => ({ currentDomain: 'generic' }),
}))
vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn() }),
  useRoute: () => ({ query: {} as Record<string, unknown>, params: {} }),
}))

import KbSearchPanel from '@/components/kb/KbSearchPanel.vue'

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

const ElSelectStub = {
  name: 'ElSelect',
  props: ['modelValue'],
  emits: ['update:modelValue', 'change'],
  template: `<div class="el-select-stub" data-testid="kb-search-directory">
    <span class="dir-current">{{ modelValue || '不限目录' }}</span>
    <button data-testid="dir-pick" @click="$emit('update:modelValue', '产品文档/手册'); $emit('change', '产品文档/手册')">pick</button>
    <button data-testid="dir-clear" @click="$emit('update:modelValue', ''); $emit('change', '')">clear</button>
  </div>`,
}

async function mountPanel() {
  const wrapper = mount(KbSearchPanel, {
    props: { kb: KB, canWrite: true },
    global: { stubs: { ElInput: ElInputStub, ElSelect: ElSelectStub } },
  })
  await flushPromises()
  return wrapper
}

async function runSearch(wrapper: Awaited<ReturnType<typeof mountPanel>>) {
  servingApi.runParadigmSearch.mockResolvedValue({
    evidenceResponse: { evidence: [], has_more: false },
  })
  await wrapper.find('input').setValue('告警')
  await wrapper.find('input').trigger('keyup.enter')
  await flushPromises()
}

beforeEach(() => {
  vi.clearAllMocks()
  operatorApi.listParadigms.mockResolvedValue([])
  servingApi.resolveParadigm.mockResolvedValue({ bound: true, paradigmId: 'pd-1', source: 'library' })
  kbApi.listFolders.mockResolvedValue([
    { id: 'f1', name: '产品文档', path: '产品文档', parent_id: null },
    { id: 'f2', name: '手册', path: '产品文档/手册', parent_id: 'f1' },
  ])
})

describe('KbSearchPanel 目录范围（57号）', () => {
  it('无文件夹时不渲染目录选择器', async () => {
    kbApi.listFolders.mockResolvedValue([])
    const w = await mountPanel()
    expect(w.find('.kb-search__dir').exists()).toBe(false)
    w.unmount()
  })

  it('选中目录后检索携带 filters.directory_prefix', async () => {
    const w = await mountPanel()
    await w.find('.kb-search__dir [data-testid="dir-pick"]').trigger('click')
    await runSearch(w)
    const options = servingApi.runParadigmSearch.mock.calls[0][2] as { filters?: { directory_prefix?: string } }
    expect(options.filters).toEqual({ directory_prefix: '产品文档/手册' })
    w.unmount()
  })

  it('不限目录时不携带 filters', async () => {
    const w = await mountPanel()
    await runSearch(w)
    const options = servingApi.runParadigmSearch.mock.calls[0][2] as { filters?: unknown }
    expect(options.filters).toBeUndefined()
    w.unmount()
  })

  it('目录选项加载失败不阻断检索', async () => {
    kbApi.listFolders.mockRejectedValue(new Error('boom'))
    const w = await mountPanel()
    expect(w.find('.kb-search__dir').exists()).toBe(false)
    await runSearch(w)
    expect(servingApi.runParadigmSearch).toHaveBeenCalled()
    w.unmount()
  })
})
