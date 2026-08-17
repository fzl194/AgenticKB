import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, shallowMount } from '@vue/test-utils'

const state = vi.hoisted(() => ({
  router: { push: vi.fn() },
  leaveGuard: undefined as ((to: unknown, from: unknown, next: (value?: unknown) => void) => void) | undefined,
  api: {
    getCatalog: vi.fn(), get: vi.fn(), saveDraft: vi.fn(), validate: vi.fn(), publish: vi.fn(),
    listVersions: vi.fn(), getVersion: vi.fn(), restoreDraft: vi.fn(),
  },
  ui: { confirm: vi.fn(), success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}))

vi.mock('@/api/miningWorkflow', () => ({ useMiningWorkflowApi: () => state.api }))
vi.mock('vue-router', () => ({
  useRouter: () => state.router,
  onBeforeRouteLeave: (guard: typeof state.leaveGuard) => { state.leaveGuard = guard },
}))
vi.mock('element-plus', () => ({
  ElMessageBox: { confirm: state.ui.confirm },
  ElMessage: { success: state.ui.success, error: state.ui.error, warning: state.ui.warning },
}))
vi.mock('@vue-flow/core', () => ({
  VueFlow: { template: '<div><slot name="node-miningOperator" /></div>' },
  useVueFlow: () => ({ screenToFlowCoordinate: ({ x, y }: { x: number; y: number }) => ({ x, y }) }),
  addEdge: vi.fn(),
}))
vi.mock('@vue-flow/background', () => ({ Background: { template: '<div />' } }))
vi.mock('@vue-flow/controls', () => ({ Controls: { template: '<div />' } }))

import WorkflowEditorView from '../WorkflowEditorView.vue'
import MiningOperatorPalette from '@/components/mining/workflow/MiningOperatorPalette.vue'

const catalog = [
  {
    type: 'input_ingest', version: '1', displayName: 'Input', description: '', category: 'input', zone: 'input',
    editPolicy: 'fixed', inputSlots: [], outputSlots: [{ name: 'out', type: 'DOCUMENT_BATCH', required: true, variadic: false, description: '' }],
    requires: [], provides: [], paramSchemaJson: { type: 'object', properties: {} }, errorPolicy: 'FAIL_FAST', unique: true,
  },
  {
    type: 'editable', version: '1', displayName: 'Editable', description: '', category: 'document', zone: 'document',
    editPolicy: 'editable', inputSlots: [{ name: 'in', type: 'DOCUMENT_BATCH', required: true, variadic: false, description: '' }],
    outputSlots: [{ name: 'out', type: 'DOCUMENT_BATCH', required: true, variadic: false, description: '' }],
    requires: [], provides: [], paramSchemaJson: { type: 'object', properties: { limit: { type: 'integer' } } }, errorPolicy: 'FAIL_FAST', unique: true,
  },
  {
    type: 'extra', version: '1', displayName: 'Extra', description: '', category: 'document', zone: 'document',
    editPolicy: 'editable', inputSlots: [{ name: 'in', type: 'DOCUMENT_BATCH', required: true, variadic: false, description: '' }],
    outputSlots: [{ name: 'out', type: 'DOCUMENT_BATCH', required: true, variadic: false, description: '' }],
    requires: [], provides: [], paramSchemaJson: { type: 'object', properties: {} }, errorPolicy: 'FAIL_FAST', unique: true,
  },
]

const graph = {
  schemaVersion: '1.0',
  nodes: [
    { nodeId: 'input', operatorType: 'input_ingest', operatorVersion: '1', params: {}, ui: { x: 0, y: 0 } },
    { nodeId: 'edit', operatorType: 'editable', operatorVersion: '1', params: { limit: 1 }, ui: { x: 200, y: 0 } },
  ],
  edges: [{ fromNode: 'input', fromSlot: 'out', toNode: 'edit', toSlot: 'in' }],
  output: { nodeId: 'edit', slot: 'out' },
}

const workflow = {
  id: 'wf', name: 'Workflow', description: null, status: 'active', draft_graph_json: graph,
  draft_revision: 3, current_version: 1, is_system: false, is_system_default: false,
  created_by: null, updated_by: null, metadata_json: {},
}

describe('mining Workflow editor', () => {
  beforeEach(() => {
    state.leaveGuard = undefined
    state.router.push.mockReset()
    state.api.getCatalog.mockReset().mockResolvedValue({ catalog_version: '1', items: catalog })
    state.api.get.mockReset().mockResolvedValue(workflow)
    state.api.listVersions.mockReset().mockResolvedValue([])
    state.api.saveDraft.mockReset().mockResolvedValue({ ...workflow, draft_revision: 4 })
    state.api.validate.mockReset().mockResolvedValue({ valid: true, errors: [], executionPlan: {} })
    state.api.publish.mockReset().mockResolvedValue({ workflow_id: 'wf', version: 2 })
    state.api.getVersion.mockReset()
    state.api.restoreDraft.mockReset()
    state.ui.confirm.mockReset().mockResolvedValue('confirm')
    state.ui.success.mockReset()
    state.ui.error.mockReset()
    state.ui.warning.mockReset()
  })

  it('passes the active graph to graph-aware operator presentation', async () => {
    const wrapper = shallowMount(WorkflowEditorView, { props: { id: 'wf' } })
    await flushPromises()

    expect(wrapper.getComponent(MiningOperatorPalette).props('nodes')).toEqual(graph.nodes)
  })

  it('keeps deterministic local JSON available after a draft revision conflict', async () => {
    state.api.saveDraft.mockRejectedValueOnce({
      response: { status: 409, data: { detail: { code: 'draft_revision_conflict' } } },
    })
    const wrapper = shallowMount(WorkflowEditorView, { props: { id: 'wf' } })
    await flushPromises()
    const vm = wrapper.vm as unknown as {
      updateNodeParams: (nodeId: string, params: Record<string, unknown>) => void
      saveDraft: () => Promise<void>
    }

    vm.updateNodeParams('edit', { limit: 9 })
    await vm.saveDraft()
    await flushPromises()

    expect(state.api.saveDraft).toHaveBeenCalledWith('wf', expect.objectContaining({ expected_revision: 3 }))
    expect(wrapper.find('[data-test="copy-local-json"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="reload-remote"]').exists()).toBe(true)
    expect(wrapper.get('[data-test="local-json"]').text()).toContain('"limit":9')
  })

  it('requires successful server validation before publish', async () => {
    state.api.validate.mockResolvedValueOnce({ valid: false, errors: [{ kind: 'cycle', message: 'cycle' }] })
    const wrapper = shallowMount(WorkflowEditorView, { props: { id: 'wf' } })
    await flushPromises()
    const vm = wrapper.vm as unknown as { publishWorkflow: () => Promise<void> }

    await vm.publishWorkflow()
    expect(state.api.publish).not.toHaveBeenCalled()

    state.api.validate.mockResolvedValueOnce({ valid: true, errors: [], executionPlan: {} })
    await vm.publishWorkflow()
    expect(state.api.publish).toHaveBeenCalledWith('wf', expect.objectContaining({ expected_revision: 3 }))
  })

  it('saves local changes first and publishes the resulting draft revision', async () => {
    const wrapper = shallowMount(WorkflowEditorView, { props: { id: 'wf' } })
    await flushPromises()
    const vm = wrapper.vm as unknown as {
      updateNodeParams: (nodeId: string, params: Record<string, unknown>) => void
      publishWorkflow: () => Promise<void>
    }

    vm.updateNodeParams('edit', { limit: 7 })
    await vm.publishWorkflow()

    expect(state.api.saveDraft).toHaveBeenCalledWith('wf', expect.objectContaining({ expected_revision: 3 }))
    expect(state.api.publish).toHaveBeenCalledWith('wf', { expected_revision: 4 })
  })

  it('supports undo/redo and protects unsaved navigation', async () => {
    const wrapper = shallowMount(WorkflowEditorView, { props: { id: 'wf' } })
    await flushPromises()
    const vm = wrapper.vm as unknown as {
      updateNodeParams: (nodeId: string, params: Record<string, unknown>) => void
      undo: () => void
      redo: () => void
      graph: { nodes: Array<{ nodeId: string; params: Record<string, unknown> }> }
      dirty: boolean
    }

    vm.updateNodeParams('edit', { limit: 8 })
    expect(vm.graph.nodes[1].params.limit).toBe(8)
    vm.undo()
    expect(vm.graph.nodes[1].params.limit).toBe(1)
    vm.redo()
    expect(vm.graph.nodes[1].params.limit).toBe(8)
    expect(vm.dirty).toBe(true)

    const next = vi.fn()
    state.leaveGuard?.({}, {}, next)
    await flushPromises()
    expect(state.ui.confirm).toHaveBeenCalled()
    expect(next).toHaveBeenCalled()
  })

  it('allows editing parameters of fixed-skeleton nodes (e.g. parse_segment chunk sizes)', async () => {
    const wrapper = shallowMount(WorkflowEditorView, { props: { id: 'wf' } })
    await flushPromises()
    const vm = wrapper.vm as unknown as {
      updateNodeParams: (nodeId: string, params: Record<string, unknown>) => void
      graph: { nodes: Array<{ nodeId: string; params: Record<string, unknown> }> }
    }

    // 'input' 是 input_ingest（editPolicy: 'fixed'）。editPolicy 只锁结构，参数应可调，
    // 否则 parse_segment 的分段 token 上下限等关键旋钮无法调整。
    vm.updateNodeParams('input', { minSegmentTokens: 120 })
    expect(vm.graph.nodes[0].params.minSegmentTokens).toBe(120)
  })

  it('opens immutable history and restores it only as a new draft revision', async () => {
    state.api.getVersion.mockResolvedValueOnce({ workflow_id: 'wf', version: 1, graph_json: graph })
    state.api.restoreDraft.mockResolvedValueOnce({ ...workflow, draft_revision: 4 })
    const wrapper = shallowMount(WorkflowEditorView, { props: { id: 'wf' } })
    await flushPromises()
    const vm = wrapper.vm as unknown as {
      previewVersion: (version: number) => Promise<void>
      restoreVersion: (version: number) => Promise<void>
      readOnly: boolean
    }

    await vm.previewVersion(1)
    expect(vm.readOnly).toBe(true)
    await vm.restoreVersion(1)

    expect(state.api.restoreDraft).toHaveBeenCalledWith('wf', 1, { expected_revision: 3 })
    expect(state.api.publish).not.toHaveBeenCalled()
  })

  it('preserves unsaved draft edits when leaving a read-only version preview', async () => {
    state.api.getVersion.mockResolvedValueOnce({ workflow_id: 'wf', version: 1, graph_json: graph })
    const wrapper = shallowMount(WorkflowEditorView, { props: { id: 'wf' } })
    await flushPromises()
    const vm = wrapper.vm as unknown as {
      updateNodeParams: (nodeId: string, params: Record<string, unknown>) => void
      previewVersion: (version: number) => Promise<void>
      exitPreview: () => void
      graph: typeof graph
    }

    vm.updateNodeParams('edit', { limit: 8 })
    await vm.previewVersion(1)
    vm.exitPreview()

    expect(vm.graph.nodes[1].params.limit).toBe(8)
  })

  it('adds editable operators, replaces single-input edges, and rejects incompatible slots', async () => {
    const wrapper = shallowMount(WorkflowEditorView, { props: { id: 'wf' } })
    await flushPromises()
    const vm = wrapper.vm as unknown as {
      addOperator: (type: string, position?: { x: number; y: number }) => void
      onConnect: (connection: Record<string, string>) => void
      graph: typeof graph
    }

    vm.addOperator('extra', { x: 400, y: 0 })
    expect(vm.graph.nodes.some(node => node.operatorType === 'extra')).toBe(true)
    vm.onConnect({ source: 'edit', sourceHandle: 'out', target: 'extra_1', targetHandle: 'in' })
    expect(vm.graph.edges).toContainEqual({ fromNode: 'edit', fromSlot: 'out', toNode: 'extra_1', toSlot: 'in' })

    vm.onConnect({ source: 'input', sourceHandle: 'out', target: 'extra_1', targetHandle: 'in' })
    expect(vm.graph.edges.filter(edge => edge.toNode === 'extra_1')).toHaveLength(1)
    expect(vm.graph.edges).toContainEqual({ fromNode: 'input', fromSlot: 'out', toNode: 'extra_1', toSlot: 'in' })

    vm.onConnect({ source: 'extra_1', sourceHandle: 'out', target: 'edit', targetHandle: 'in' })
    expect(vm.graph.edges).toContainEqual({ fromNode: 'extra_1', fromSlot: 'out', toNode: 'edit', toSlot: 'in' })
    expect(vm.graph.edges).not.toContainEqual({ fromNode: 'input', fromSlot: 'out', toNode: 'edit', toSlot: 'in' })

    vm.onConnect({ source: 'input', sourceHandle: 'out', target: 'edit', targetHandle: 'missing' })
    expect(vm.graph.edges).toContainEqual({ fromNode: 'extra_1', fromSlot: 'out', toNode: 'edit', toSlot: 'in' })
  })

  it('selects and deletes an edge attached to a fixed node and restores it with undo', async () => {
    const wrapper = shallowMount(WorkflowEditorView, { props: { id: 'wf' } })
    await flushPromises()
    const vm = wrapper.vm as unknown as {
      selectEdge: (event: { edge: { id: string } }) => void
      deleteSelectedEdge: () => void
      undo: () => void
      redo: () => void
      selectedEdgeId: string
      graph: typeof graph
    }

    vm.selectEdge({ edge: { id: 'input.out->edit.in' } })
    await wrapper.vm.$nextTick()
    expect(wrapper.get('[data-test="selected-edge"]').text()).toContain('input.out')
    expect(wrapper.get('[data-test="selected-edge"]').text()).toContain('edit.in')

    vm.deleteSelectedEdge()
    expect(vm.graph.edges).toEqual([])
    expect(vm.selectedEdgeId).toBe('')

    vm.undo()
    expect(vm.graph.edges).toEqual([
      { fromNode: 'input', fromSlot: 'out', toNode: 'edit', toSlot: 'in' },
    ])
    vm.redo()
    expect(vm.graph.edges).toEqual([])
  })

  it('deletes a selected edge with keyboard shortcuts without hijacking text inputs', async () => {
    const wrapper = shallowMount(WorkflowEditorView, { props: { id: 'wf' } })
    await flushPromises()
    const vm = wrapper.vm as unknown as {
      selectEdge: (event: { edge: { id: string } }) => void
      onEditorKeydown: (event: KeyboardEvent) => void
      undo: () => void
      readOnly: boolean
      graph: typeof graph
    }
    const preventDefault = vi.fn()

    vm.selectEdge({ edge: { id: 'input.out->edit.in' } })
    vm.onEditorKeydown({ key: 'Delete', target: document.body, preventDefault } as unknown as KeyboardEvent)
    expect(vm.graph.edges).toEqual([])
    expect(preventDefault).toHaveBeenCalled()

    vm.undo()
    vm.selectEdge({ edge: { id: 'input.out->edit.in' } })
    vm.onEditorKeydown({ key: 'Backspace', target: document.body, preventDefault } as unknown as KeyboardEvent)
    expect(vm.graph.edges).toEqual([])

    vm.undo()
    vm.selectEdge({ edge: { id: 'input.out->edit.in' } })
    const contentEditable = document.createElement('div')
    contentEditable.setAttribute('contenteditable', 'true')
    const editableChild = document.createElement('span')
    contentEditable.appendChild(editableChild)
    vm.onEditorKeydown({ key: 'Delete', target: editableChild, preventDefault } as unknown as KeyboardEvent)
    expect(vm.graph.edges).toHaveLength(1)

    vm.onEditorKeydown({ key: 'Backspace', target: document.createElement('input'), preventDefault } as unknown as KeyboardEvent)
    expect(vm.graph.edges).toHaveLength(1)

    vm.readOnly = true
    vm.onEditorKeydown({ key: 'Delete', target: document.body, preventDefault } as unknown as KeyboardEvent)
    expect(vm.graph.edges).toHaveLength(1)
  })

  it('does not create an undo entry for an unchanged edge update', async () => {
    const wrapper = shallowMount(WorkflowEditorView, { props: { id: 'wf' } })
    await flushPromises()
    const vm = wrapper.vm as unknown as {
      onEdgeUpdate: (event: {
        edge: { id: string; source: string; sourceHandle: string; target: string; targetHandle: string }
        connection: Record<string, string>
      }) => void
      canUndo: boolean
    }
    const oldEdge = {
      id: 'input.out->edit.in', source: 'input', sourceHandle: 'out', target: 'edit', targetHandle: 'in',
    }

    vm.onEdgeUpdate({ edge: oldEdge, connection: { ...oldEdge } })

    expect(vm.canUndo).toBe(false)
  })

  it('reconnects edges attached to fixed nodes and keeps the old edge after an invalid drop', async () => {
    const wrapper = shallowMount(WorkflowEditorView, { props: { id: 'wf' } })
    await flushPromises()
    const vm = wrapper.vm as unknown as {
      addOperator: (type: string, position?: { x: number; y: number }) => void
      onEdgeUpdate: (event: {
        edge: { id: string; source: string; sourceHandle: string; target: string; targetHandle: string }
        connection: Record<string, string>
      }) => void
      undo: () => void
      redo: () => void
      selectedEdgeId: string
      flowEdges: Array<{ id: string; selected?: boolean }>
      graph: typeof graph
    }

    vm.addOperator('extra', { x: 400, y: 0 })
    const oldEdge = {
      id: 'input.out->edit.in', source: 'input', sourceHandle: 'out', target: 'edit', targetHandle: 'in',
    }
    vm.onEdgeUpdate({
      edge: oldEdge,
      connection: { source: 'extra_1', sourceHandle: 'out', target: 'edit', targetHandle: 'in' },
    })
    expect(vm.graph.edges).toContainEqual({ fromNode: 'extra_1', fromSlot: 'out', toNode: 'edit', toSlot: 'in' })
    expect(vm.graph.edges).not.toContainEqual({ fromNode: 'input', fromSlot: 'out', toNode: 'edit', toSlot: 'in' })
    expect(vm.selectedEdgeId).toBe('extra_1.out->edit.in')
    expect(vm.flowEdges.find(edge => edge.id === vm.selectedEdgeId)?.selected).toBe(true)

    vm.undo()
    vm.redo()
    expect(vm.graph.edges).toContainEqual({ fromNode: 'extra_1', fromSlot: 'out', toNode: 'edit', toSlot: 'in' })

    vm.undo()
    vm.onEdgeUpdate({
      edge: oldEdge,
      connection: { source: 'extra_1', sourceHandle: 'out', target: 'edit', targetHandle: 'missing' },
    })
    expect(vm.graph.edges).toContainEqual({ fromNode: 'input', fromSlot: 'out', toNode: 'edit', toSlot: 'in' })
  })

  it('keeps persisted edges connected to fixed nodes valid for Vue Flow initialization', async () => {
    const wrapper = shallowMount(WorkflowEditorView, { props: { id: 'wf' } })
    await flushPromises()
    const vm = wrapper.vm as unknown as {
      isValidConnection: (connection: Record<string, string>) => boolean
      flowNodes: Array<{ id: string; connectable?: boolean }>
    }

    expect(vm.isValidConnection({
      source: 'input', sourceHandle: 'out', target: 'edit', targetHandle: 'in',
    })).toBe(true)
    expect(vm.isValidConnection({ source: 'input', target: 'edit' })).toBe(false)
  })

  it('allows adding a missing compatible edge between fixed skeleton nodes', async () => {
    const fixedSink = {
      type: 'mining_finalize', version: '1', displayName: 'Finalize', description: '', category: 'publish', zone: 'global',
      editPolicy: 'fixed',
      inputSlots: [{ name: 'in', type: 'DOCUMENT_BATCH', required: true, variadic: false, description: '' }],
      outputSlots: [], requires: [], provides: [], paramSchemaJson: { type: 'object', properties: {} }, errorPolicy: 'FAIL_FAST', unique: true,
    }
    state.api.getCatalog.mockResolvedValueOnce({ catalog_version: '1', items: [...catalog, fixedSink] })
    state.api.get.mockResolvedValueOnce({
      ...workflow,
      draft_graph_json: {
        ...graph,
        nodes: [
          graph.nodes[0],
          { nodeId: 'finalize', operatorType: 'mining_finalize', operatorVersion: '1', params: {}, ui: { x: 200, y: 0 } },
        ],
        edges: [],
        output: { nodeId: 'finalize', slot: '' },
      },
    })
    const wrapper = shallowMount(WorkflowEditorView, { props: { id: 'wf' } })
    await flushPromises()
    const vm = wrapper.vm as unknown as {
      onConnect: (connection: Record<string, string>) => void
      flowNodes: Array<{ id: string; connectable?: boolean }>
      graph: typeof graph
    }

    expect(vm.flowNodes.filter(node => node.id === 'input' || node.id === 'finalize')
      .every(node => node.connectable)).toBe(true)
    vm.onConnect({ source: 'input', sourceHandle: 'out', target: 'finalize', targetHandle: 'in' })
    expect(vm.graph.edges).toContainEqual({ fromNode: 'input', fromSlot: 'out', toNode: 'finalize', toSlot: 'in' })
  })

  it('allows deleting an orphaned protected node when no ontology branch is enabled', async () => {
    const protectedDefinition = {
      type: 'graph_write', version: '1', displayName: 'Graph write', description: '', category: 'ontology', zone: 'global',
      editPolicy: 'protected',
      inputSlots: [{ name: 'in', type: 'DOCUMENT_BATCH', required: true, variadic: false, description: '' }],
      outputSlots: [{ name: 'out', type: 'DOCUMENT_BATCH', required: true, variadic: false, description: '' }],
      requires: [], provides: [], paramSchemaJson: { type: 'object', properties: {} }, errorPolicy: 'FAIL_FAST', unique: true,
    }
    state.api.getCatalog.mockResolvedValueOnce({ catalog_version: '1', items: [...catalog, protectedDefinition] })
    state.api.get.mockResolvedValueOnce({
      ...workflow,
      draft_graph_json: {
        ...graph,
        nodes: [
          ...graph.nodes,
          { nodeId: 'graph-write', operatorType: 'graph_write', operatorVersion: '1', params: {}, ui: { x: 400, y: 0 } },
        ],
      },
    })
    const wrapper = shallowMount(WorkflowEditorView, { props: { id: 'wf' } })
    await flushPromises()
    const vm = wrapper.vm as unknown as {
      selectedNodeId: string
      deleteSelected: () => void
      graph: typeof graph
    }

    vm.selectedNodeId = 'graph-write'
    vm.deleteSelected()

    expect(vm.graph.nodes.some(node => node.nodeId === 'graph-write')).toBe(false)
  })
})
