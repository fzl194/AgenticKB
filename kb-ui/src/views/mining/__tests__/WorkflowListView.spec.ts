import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, shallowMount } from '@vue/test-utils'

const state = vi.hoisted(() => ({
  domain: { currentDomain: 'plant-a' },
  router: { push: vi.fn() },
  api: {
    list: vi.fn(), create: vi.fn(), clone: vi.fn(), archive: vi.fn(),
  },
  ui: { confirm: vi.fn(), success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}))

vi.mock('@/api/miningWorkflow', () => ({ useMiningWorkflowApi: () => state.api }))
vi.mock('@/stores/domain', () => ({ useDomainStore: () => state.domain }))
vi.mock('vue-router', () => ({ useRouter: () => state.router }))
vi.mock('element-plus', () => ({
  ElMessageBox: { confirm: state.ui.confirm },
  ElMessage: { success: state.ui.success, error: state.ui.error, warning: state.ui.warning },
}))

import WorkflowListView from '../WorkflowListView.vue'

const workflows = [
  {
    id: 'system-full-baseline', name: 'FULL', description: 'default', status: 'active',
    draft_graph_json: { nodes: [], edges: [], output: { nodeId: '', slot: '' } },
    draft_revision: 0, current_version: 1, is_system: true, is_system_default: true,
    created_by: null, updated_by: null, metadata_json: {},
  },
  {
    id: 'custom', name: 'Custom', description: null, status: 'active',
    draft_graph_json: { nodes: [], edges: [], output: { nodeId: '', slot: '' } },
    draft_revision: 2, current_version: null, is_system: false, is_system_default: false,
    created_by: null, updated_by: null, metadata_json: {},
  },
]

describe('global mining Workflow list', () => {
  beforeEach(() => {
    state.domain.currentDomain = 'plant-a'
    state.router.push.mockReset()
    state.api.list.mockReset().mockResolvedValue(workflows)
    state.api.create.mockReset().mockResolvedValue({ ...workflows[1], id: 'new' })
    state.api.clone.mockReset().mockResolvedValue({ ...workflows[1], id: 'copy' })
    state.api.archive.mockReset().mockResolvedValue({ ...workflows[1], status: 'archived' })
    state.ui.confirm.mockReset().mockResolvedValue('confirm')
    state.ui.success.mockReset()
    state.ui.error.mockReset()
    state.ui.warning.mockReset()
  })

  it('loads once globally and does not reload or filter when Domain changes', async () => {
    const wrapper = shallowMount(WorkflowListView)
    await flushPromises()

    state.domain.currentDomain = 'plant-b'
    await flushPromises()

    expect(state.api.list).toHaveBeenCalledTimes(1)
    expect(wrapper.text()).toContain('全局共享')
    expect((wrapper.vm as unknown as { workflows: unknown[] }).workflows).toHaveLength(2)
  })

  it('shows and creates all four official presets and preserves a conflicting name', async () => {
    const wrapper = shallowMount(WorkflowListView)
    await flushPromises()
    const vm = wrapper.vm as unknown as {
      form: { name: string; description: string; template_key: string }
      templates: Array<{ key: string; label: string }>
      createWorkflow: () => Promise<void>
    }

    // 批次8 M6：官方 4 套预置（旧 7 类模板退役）
    const templateKeys = [
      'hybrid_assets', 'lexical_assets', 'query_alias_assets', 'longdoc_assets',
    ]
    expect(vm.templates.map(template => template.key)).toEqual(templateKeys)
    expect(vm.templates.map(template => template.label)).toEqual([
      '标准混合资产（推荐）', '轻量关键词资产',
      '问题别名增强资产（实验）', '长文档全局增强资产（实验）',
    ])

    for (const template_key of templateKeys) {
      vm.form = { name: `workflow-${template_key}`, description: '', template_key }
      await vm.createWorkflow()
    }
    expect(state.api.create.mock.calls.map(call => call[0].template_key)).toEqual(templateKeys)

    state.api.create.mockRejectedValueOnce({ response: { status: 409, data: { detail: { code: 'workflow_name_conflict' } } } })
    vm.form = { name: 'keep-this-name', description: '', template_key: 'hybrid_assets' }
    await vm.createWorkflow()
    expect(vm.form.name).toBe('keep-this-name')
  })

  it('copies ordinary workflows and never offers archive for the system default', async () => {
    const wrapper = shallowMount(WorkflowListView)
    await flushPromises()
    const vm = wrapper.vm as unknown as {
      copyWorkflow: (workflow: typeof workflows[number], name: string) => Promise<void>
      archiveWorkflow: (workflow: typeof workflows[number]) => Promise<void>
      canArchive: (workflow: typeof workflows[number]) => boolean
    }

    expect(vm.canArchive(workflows[0])).toBe(false)
    expect(vm.canArchive(workflows[1])).toBe(true)
    await vm.copyWorkflow(workflows[1], 'Custom copy')
    await vm.archiveWorkflow(workflows[0])

    expect(state.api.clone).toHaveBeenCalledWith('custom', expect.objectContaining({ name: 'Custom copy' }))
    expect(state.api.archive).not.toHaveBeenCalled()
  })
})

