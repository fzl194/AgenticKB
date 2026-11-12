import { beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick, reactive } from 'vue'
import { flushPromises, shallowMount } from '@vue/test-utils'
import type { VueWrapper } from '@vue/test-utils'

const state = vi.hoisted(() => ({
  domain: {
    currentDomain: 'plant-a',
    enabledDomains: [
      { domain_id: 'plant-a', enabled: true },
      { domain_id: 'plant-b', enabled: true },
    ],
  },
  router: { push: vi.fn() },
  api: {
    list: vi.fn(), create: vi.fn(),
  },
  ui: { confirm: vi.fn(), success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}))

vi.mock('@/api/artifacts', () => ({ useArtifactsApi: () => state.api }))
vi.mock('@/stores/domain', () => ({ useDomainStore: () => state.domain }))
vi.mock('vue-router', () => ({ useRouter: () => state.router }))
vi.mock('element-plus', () => ({
  ElMessageBox: { confirm: state.ui.confirm },
  ElMessage: { success: state.ui.success, error: state.ui.error, warning: state.ui.warning },
}))

import ArtifactsListView from '../ArtifactsListView.vue'

state.domain = reactive(state.domain)

interface ListVm {
  artifacts: unknown[]
  filteredArtifacts: unknown[]
  filterKind: string
  filterStatus: string
  filterTitle: string
  emptyText: string
  form: { title: string; description: string; domainId: string; kind: string }
  createVisible: boolean
  openCreate: () => void
  createArtifact: () => Promise<void>
  statusTagType: (status: string) => 'info' | 'success' | 'danger'
  statusLabel: (status: string) => string
}

// vitest 链路无 unplugin-vue-components：模板里的 el-radio-group / el-select /
// el-input 是未解析的原生元素，v-model 事件无法经组件通道驱动——注册最小具名桩
// 让 resolveComponent 命中（shallowMount 再包自动桩，data-test 随 attrs 透传到
// 桩根元素）。
const ElRadioGroupStub = {
  name: 'ElRadioGroup',
  props: { modelValue: { type: String, default: '' } },
  emits: ['update:modelValue'],
  template: '<div class="el-radio-group"><slot /></div>',
}

const ElSelectStub = {
  name: 'ElSelect',
  props: { modelValue: { type: [String, Number, Boolean], default: '' } },
  emits: ['update:modelValue'],
  template: '<div class="el-select"><slot /></div>',
}

const ElInputStub = {
  name: 'ElInput',
  props: { modelValue: { type: [String, Number], default: '' } },
  emits: ['update:modelValue'],
  template: '<input class="el-input-stub" />',
}

function mountList(): VueWrapper {
  return shallowMount(ArtifactsListView, {
    global: {
      components: { ElRadioGroup: ElRadioGroupStub, ElSelect: ElSelectStub, ElInput: ElInputStub },
    },
  })
}

const artifacts = [
  {
    artifact_id: 'art-draft', domain_id: 'plant-a', kind: 'table',
    title: '割接 MOP 表', description: '', usage_note: '',
    owner_user_id: 'u1', status: 'draft', current_revision: 0, draft_open: false,
    created_at: '2026-10-16T08:00:00+00:00', updated_at: '2026-10-16T08:00:00+00:00',
    my_role: 'owner',
  },
  {
    artifact_id: 'art-pub', domain_id: 'plant-a', kind: 'table',
    title: '责任域表', description: 'd', usage_note: '查责任域',
    owner_user_id: 'u2', status: 'published', current_revision: 3, draft_open: true,
    created_at: '2026-10-15T08:00:00+00:00', updated_at: '2026-10-16T09:30:00+00:00',
    my_role: 'viewer',
  },
  {
    artifact_id: 'art-dep', domain_id: 'plant-a', kind: 'table',
    title: '旧表', description: '', usage_note: '',
    owner_user_id: 'u1', status: 'deprecated', current_revision: 2, draft_open: false,
    created_at: '2026-10-14T08:00:00+00:00', updated_at: '2026-10-14T08:00:00+00:00',
    my_role: 'editor',
  },
  {
    // L01 筛选用 wiki 样本：published 的 Wiki 制品（与表格样本错开三筛选维度）
    artifact_id: 'art-wiki', domain_id: 'plant-a', kind: 'wiki',
    title: '电源模块规格 Wiki', description: '', usage_note: '',
    owner_user_id: 'u2', status: 'published', current_revision: 1, draft_open: false,
    created_at: '2026-10-13T08:00:00+00:00', updated_at: '2026-10-13T08:00:00+00:00',
    my_role: 'viewer',
  },
]

describe('artifacts list view', () => {
  beforeEach(() => {
    state.domain.currentDomain = 'plant-a'
    state.router.push.mockReset()
    state.api.list.mockReset().mockResolvedValue(artifacts)
    state.api.create.mockReset().mockResolvedValue({ ...artifacts[0], artifact_id: 'art_new' })
    state.ui.success.mockReset()
    state.ui.error.mockReset()
    state.ui.warning.mockReset()
  })

  it('loads artifacts on mount and renders rows', async () => {
    const wrapper = mountList()
    await flushPromises()

    expect(state.api.list).toHaveBeenCalledTimes(1)
    expect(state.api.list).toHaveBeenCalledWith({ domain_id: 'plant-a' })
    // el-table 在 shallowMount 下被 stub，行数据断言走 vm（渲染链路由状态驱动）
    expect(wrapper.text()).toContain('我参与的')
    const vm = wrapper.vm as unknown as ListVm
    expect(vm.artifacts).toHaveLength(4)
    expect(vm.artifacts.map(a => (a as { title: string }).title)).toEqual([
      '割接 MOP 表', '责任域表', '旧表', '电源模块规格 Wiki',
    ])
    // 默认筛选=全部：过滤视图与原始数据同集
    expect(vm.filteredArtifacts).toHaveLength(4)
  })

  it('reloads only the selected domain and ignores an older response after switching', async () => {
    let resolveOld!: (value: typeof artifacts) => void
    state.api.list.mockImplementation(({ domain_id }: { domain_id: string }) =>
      domain_id === 'plant-a'
        ? new Promise(resolve => { resolveOld = resolve })
        : Promise.resolve([{ ...artifacts[0], artifact_id: 'art-b', domain_id: 'plant-b', title: 'B 域制品' }]))
    const wrapper = mountList()
    await nextTick()

    state.domain.currentDomain = 'plant-b'
    await flushPromises()
    const vm = wrapper.vm as unknown as ListVm
    expect(state.api.list).toHaveBeenCalledWith({ domain_id: 'plant-b' })
    expect(vm.filteredArtifacts.map(a => (a as { title: string }).title)).toEqual(['B 域制品'])

    resolveOld(artifacts)
    await flushPromises()
    expect(vm.filteredArtifacts.map(a => (a as { title: string }).title)).toEqual(['B 域制品'])
  })

  it('maps artifact status to grey/green/red tags with labels', async () => {
    const wrapper = mountList()
    await flushPromises()
    const vm = wrapper.vm as unknown as ListVm

    expect(vm.statusTagType('draft')).toBe('info')
    expect(vm.statusTagType('published')).toBe('success')
    expect(vm.statusTagType('deprecated')).toBe('danger')
    expect(vm.statusLabel('draft')).toBe('草稿')
    expect(vm.statusLabel('published')).toBe('已发布')
    expect(vm.statusLabel('deprecated')).toBe('已停用')
  })

  it('creates an artifact with the current domain and routes into its detail page', async () => {
    const wrapper = mountList()
    await flushPromises()
    const vm = wrapper.vm as unknown as ListVm

    vm.form = { title: '  新制品  ', description: ' 说明 ', domainId: 'plant-a', kind: 'table' }
    await vm.createArtifact()

    // codex二审功能项③：create payload 显式带当前域（不再依赖后端绑定域兜底）；
    // 类型默认 table（不选 radio 时的缺省请求体口径）
    expect(state.api.create).toHaveBeenCalledWith({
      title: '新制品', description: '说明', domain_id: 'plant-a', kind: 'table',
    })
    expect(state.router.push).toHaveBeenCalledWith({
      name: 'artifact-detail', params: { artifactId: 'art_new' },
    })
  })

  it('sends kind=wiki when the Wiki type is picked in the create dialog', async () => {
    const wrapper = mountList()
    await flushPromises()
    const vm = wrapper.vm as unknown as ListVm

    // 打开对话框：openCreate 重置表单（kind 回到默认 table）
    vm.openCreate()
    await nextTick()
    expect(vm.createVisible).toBe(true)
    expect(vm.form.kind).toBe('table')

    // shallowMount 下 el-radio-group 为桩（data-test 透传）——经桩 emit
    // update:modelValue 模拟选择 wiki（v-model 回写 form.kind）
    const radioGroup = wrapper.findComponent('[data-test="create-artifact-kind"]')
    expect(radioGroup.exists()).toBe(true)
    // CSS 选择器重载的返回类型是 WrapperLike（可能命中原生元素）——此处已由
    // mountList 注册具名桩，断言存在后收窄为 VueWrapper 走组件事件通道
    ;(radioGroup as unknown as VueWrapper).vm.$emit('update:modelValue', 'wiki')
    await nextTick()
    expect(vm.form.kind).toBe('wiki')

    vm.form.title = '割接 Wiki 手册'
    await vm.createArtifact()

    expect(state.api.create).toHaveBeenCalledWith(expect.objectContaining({
      title: '割接 Wiki 手册', domain_id: 'plant-a', kind: 'wiki',
    }))
  })

  it('blocks creation when the target domain is unknown', async () => {
    state.domain.currentDomain = ''
    const wrapper = mountList()
    await flushPromises()
    const vm = wrapper.vm as unknown as ListVm

    vm.form = { title: '无域制品', description: '', domainId: '', kind: 'table' }
    await vm.createArtifact()

    expect(state.ui.warning).toHaveBeenCalledWith(expect.stringContaining('归属知识域'))
    expect(state.api.create).not.toHaveBeenCalled()
    expect(state.router.push).not.toHaveBeenCalled()
  })

  it('blocks empty title and surfaces api errors without routing', async () => {
    const wrapper = mountList()
    await flushPromises()
    const vm = wrapper.vm as unknown as ListVm

    vm.form = { title: '   ', description: '', domainId: 'plant-a', kind: 'table' }
    await vm.createArtifact()
    expect(state.ui.warning).toHaveBeenCalled()
    expect(state.api.create).not.toHaveBeenCalled()

    state.api.create.mockRejectedValueOnce({
      response: { status: 422, data: { detail: 'domain_id_required: x' } },
    })
    vm.form = { title: '无域制品', description: '', domainId: 'plant-a', kind: 'table' }
    await vm.createArtifact()
    expect(state.router.push).not.toHaveBeenCalled()
    expect(state.ui.error).toHaveBeenCalled()
  })
})

describe('artifacts list view — L01 筛选组（66号 §4：类型/状态/标题，本地过滤）', () => {
  beforeEach(() => {
    state.router.push.mockReset()
    state.api.list.mockReset().mockResolvedValue(artifacts)
  })

  const titlesOf = (rows: unknown[]): string[] => rows.map(a => (a as { title: string }).title)

  it('renders the filter group: kind select + status select + title keyword input', async () => {
    const wrapper = mountList()
    await flushPromises()

    expect(wrapper.findComponent('[data-test="filter-kind"]').exists()).toBe(true)
    expect(wrapper.findComponent('[data-test="filter-status"]').exists()).toBe(true)
    expect(wrapper.findComponent('[data-test="filter-title"]').exists()).toBe(true)
  })

  it('filters by kind locally without refetching from the api', async () => {
    const wrapper = mountList()
    await flushPromises()
    const vm = wrapper.vm as unknown as ListVm

    vm.filterKind = 'wiki'
    await nextTick()
    expect(titlesOf(vm.filteredArtifacts)).toEqual(['电源模块规格 Wiki'])

    vm.filterKind = 'table'
    await nextTick()
    expect(titlesOf(vm.filteredArtifacts)).toEqual(['割接 MOP 表', '责任域表', '旧表'])

    // 前端本地过滤铁律：筛选变更不触发任何后端重取（列表量级 <1000）
    expect(state.api.list).toHaveBeenCalledTimes(1)
  })

  it('filters by status across kinds', async () => {
    const wrapper = mountList()
    await flushPromises()
    const vm = wrapper.vm as unknown as ListVm

    vm.filterStatus = 'published'
    await nextTick()
    expect(titlesOf(vm.filteredArtifacts)).toEqual(['责任域表', '电源模块规格 Wiki'])

    vm.filterStatus = 'draft'
    await nextTick()
    expect(titlesOf(vm.filteredArtifacts)).toEqual(['割接 MOP 表'])
  })

  it('filters by title keyword case-insensitively with surrounding spaces tolerated', async () => {
    const wrapper = mountList()
    await flushPromises()
    const vm = wrapper.vm as unknown as ListVm

    vm.filterTitle = '  mop '
    await nextTick()
    expect(titlesOf(vm.filteredArtifacts)).toEqual(['割接 MOP 表'])
  })

  it('combines kind + status + keyword filters as AND', async () => {
    const wrapper = mountList()
    await flushPromises()
    const vm = wrapper.vm as unknown as ListVm

    vm.filterKind = 'wiki'
    vm.filterStatus = 'published'
    vm.filterTitle = '规格'
    await nextTick()
    expect(titlesOf(vm.filteredArtifacts)).toEqual(['电源模块规格 Wiki'])

    // 同 kind 下状态不命中 → 空
    vm.filterStatus = 'draft'
    await nextTick()
    expect(vm.filteredArtifacts).toHaveLength(0)
  })

  it('switches the empty text between pristine and no-match states', async () => {
    const wrapper = mountList()
    await flushPromises()
    const vm = wrapper.vm as unknown as ListVm

    expect(vm.emptyText).toContain('还没有知识制品')

    vm.filterKind = 'wiki'
    vm.filterStatus = 'draft'
    await nextTick()
    expect(vm.filteredArtifacts).toHaveLength(0)
    expect(vm.emptyText).toContain('筛选')

    // 清空筛选恢复原始空态文案口径
    vm.filterKind = 'all'
    vm.filterStatus = 'all'
    await nextTick()
    expect(vm.emptyText).toContain('还没有知识制品')
  })

  it('wires the kind select and title input through v-model stubs', async () => {
    const wrapper = mountList()
    await flushPromises()
    const vm = wrapper.vm as unknown as ListVm

    const kindSelect = wrapper.findComponent('[data-test="filter-kind"]') as unknown as VueWrapper
    kindSelect.vm.$emit('update:modelValue', 'wiki')
    await nextTick()
    expect(vm.filterKind).toBe('wiki')

    const titleInput = wrapper.findComponent('[data-test="filter-title"]') as unknown as VueWrapper
    titleInput.vm.$emit('update:modelValue', '电源')
    await nextTick()
    expect(vm.filterTitle).toBe('电源')
    expect(titlesOf(vm.filteredArtifacts)).toEqual(['电源模块规格 Wiki'])
  })
})
