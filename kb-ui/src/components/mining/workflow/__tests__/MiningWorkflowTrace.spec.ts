import { describe, expect, it } from 'vitest'
import { shallowMount } from '@vue/test-utils'
import MiningWorkflowTrace from '../MiningWorkflowTrace.vue'
import type { RunTrace } from '@/types'

function frozenTrace(): RunTrace {
  return {
    run_id: 'run-1', domain: 'plant-a', status: 'failed', awaiting_review: false,
    counts: { total_documents: 1, committed: 0, new: 0, updated: 0, failed: 1, skipped: 0 },
    execution_engine: 'workflow', active_node_id: null, active_operator_type: null, pause_step: null,
    workflow: {
      id: 'workflow-a', version: 4, graph_hash: 'hash-a',
      graph: {
        schemaVersion: '1.0',
        nodes: [
          { nodeId: 'asset-persist', operatorType: 'asset_persist', params: {}, ui: { x: 0, y: 0 } },
          { nodeId: 'graph-write', operatorType: 'graph_write', params: {}, ui: { x: 240, y: 0 } },
        ],
        edges: [{ fromNode: 'asset-persist', fromSlot: 'finalizeInput', toNode: 'graph-write', toSlot: 'finalizeInput' }],
        output: { nodeId: 'graph-write', slot: 'finalizeInput' },
      },
    },
    node_events: [{
      id: 'event-1', run_id: 'run-1', run_document_id: null,
      node_id: 'graph-write', operator_type: 'graph_write', operator_version: '1',
      attempt_no: 2, status: 'failed', started_at: '2026-07-24T00:00:00Z',
      finished_at: '2026-07-24T00:00:01Z', duration_ms: 1000,
      error_code: 'transaction_failed', error_message: 'edge transaction failed',
      metadata_json: { warnings: [{ code: 'fallback', message: 'used fallback writer' }] },
    }],
    warnings: [{ node_id: 'graph-write', attempt_no: 2, code: 'fallback', message: 'used fallback writer' }],
    stage_events: [], documents: [], build_id: null,
  }
}

describe('frozen mining Workflow trace', () => {
  it('renders frozen nodes with the latest attempt, failure, and warning', () => {
    const wrapper = shallowMount(MiningWorkflowTrace, { props: { trace: frozenTrace() } })
    const failed = wrapper.get('[data-node-id="graph-write"]')

    expect(failed.classes()).toContain('is-failed')
    expect(failed.text()).toContain('attempt 2')
    expect(failed.text()).toContain('edge transaction failed')
    expect(failed.text()).toContain('used fallback writer')
    expect(wrapper.text()).toContain('workflow-a')
    expect(wrapper.text()).toContain('v4')
  })

  it('marks active and paused nodes and shows final release information', () => {
    const trace = frozenTrace()
    trace.status = 'awaiting_review'
    trace.active_node_id = 'graph-write'
    trace.pause_step = 'ontology_review_gate'
    trace.build_id = 'build-9'
    const wrapper = shallowMount(MiningWorkflowTrace, { props: { trace } })

    expect(wrapper.get('[data-node-id="graph-write"]').classes()).toContain('is-active')
    expect(wrapper.text()).toContain('ontology_review_gate')
    expect(wrapper.text()).toContain('build-9')
  })

  it('renders a legacy fallback without inventing a Workflow graph', () => {
    const trace = frozenTrace()
    trace.execution_engine = 'legacy'
    trace.workflow = null
    trace.node_events = []
    const wrapper = shallowMount(MiningWorkflowTrace, { props: { trace } })

    expect(wrapper.text()).toContain('Legacy Pipeline')
    expect(wrapper.find('.trace-node').exists()).toBe(false)
  })
})

