import { describe, expect, it } from 'vitest'
import {
  canDeleteNode, canDeleteNodeInGraph, canDisableNode, canReconnectNode,
  effectiveEditReason, effectiveEditState,
  fromVueFlowElements, stableGraphJson, toVueFlowElements, validateLocalGraph,
} from '@/utils/miningWorkflowGraph'
import type {
  MiningOperatorDef, MiningWorkflowGraph, MiningWorkflowNode,
} from '@/types/miningWorkflow'

function operator(
  type: string,
  editPolicy: MiningOperatorDef['editPolicy'] = 'editable',
  inputType = 'DOCUMENT_BATCH',
  outputType = 'DOCUMENT_BATCH',
): MiningOperatorDef {
  return {
    type, version: '1', displayName: type, description: '', category: 'document', zone: 'document',
    editPolicy, requires: [], provides: [], errorPolicy: 'FAIL_FAST', unique: true,
    inputSlots: type === 'source' ? [] : [{ name: 'in', type: inputType, required: true, variadic: false, description: '' }],
    outputSlots: type === 'sink' ? [] : [{ name: 'out', type: outputType, required: true, variadic: false, description: '' }],
    paramSchemaJson: type === 'middle' ? {
      type: 'object', required: ['limit'], additionalProperties: false,
      properties: { limit: { type: 'integer', minimum: 1, maximum: 10 }, mode: { type: 'string', enum: ['safe', 'fast'] } },
    } : { type: 'object', properties: {} },
  }
}

const catalog = [operator('source', 'fixed'), operator('middle'), operator('sink', 'protected')]

function node(nodeId: string, operatorType = nodeId, params: Record<string, unknown> = {}): MiningWorkflowNode {
  return { nodeId, operatorType, operatorVersion: '1', params, ui: { x: 0, y: 0 } }
}

function validGraph(): MiningWorkflowGraph {
  return {
    schemaVersion: '2.0',
    nodes: [node('source'), node('middle', 'middle', { limit: 2, mode: 'safe' }), node('sink')],
    edges: [
      { fromNode: 'source', fromSlot: 'out', toNode: 'middle', toSlot: 'in' },
      { fromNode: 'middle', fromSlot: 'out', toNode: 'sink', toSlot: 'in' },
    ],
    output: { nodeId: 'sink', slot: 'result' },
  }
}

describe('mining workflow graph rules', () => {
  it('derives fixed, currently required, and optional states from the active graph', () => {
    const fixed = operator('input_ingest', 'fixed')
    const graphWrite = operator('graph_write', 'protected')
    const entityReview = operator('entity_review_gate', 'protected')
    const ontologyReview = operator('ontology_review_gate', 'protected')
    const ontologyInduction = operator('ontology_induction')
    const entityNodes = [node('entity', 'entity_extract')]
    const inductionNodes = [node('induction', 'ontology_induction')]

    expect(effectiveEditState(fixed, [])).toBe('fixed')
    expect(effectiveEditState(graphWrite, [])).toBe('optional')
    expect(effectiveEditState(graphWrite, entityNodes)).toBe('required')
    expect(effectiveEditState(entityReview, entityNodes)).toBe('required')
    expect(effectiveEditState(ontologyReview, entityNodes)).toBe('optional')
    expect(effectiveEditState(ontologyReview, inductionNodes)).toBe('required')
    expect(effectiveEditState(ontologyInduction, inductionNodes)).toBe('optional')
    expect(canDeleteNodeInGraph(graphWrite, entityNodes)).toBe(false)
    expect(canDeleteNodeInGraph(graphWrite, [])).toBe(true)
    expect(effectiveEditReason(ontologyReview, inductionNodes)).toContain('本体归纳')
  })

  it('maps fixed, protected, and editable policies to editor capabilities', () => {
    const fixed = catalog[0]
    const editable = catalog[1]
    const protectedDef = catalog[2]

    // canDelete/canDisable/canReconnect —— editPolicy 只管结构，不管参数编辑。
    // （节点位置拖动已与 editPolicy 解耦——2026-08-31 修复，canMoveNode 已删。）
    expect([canDeleteNode(fixed), canDisableNode(fixed), canReconnectNode(fixed)])
      .toEqual([false, false, false])
    expect([canDeleteNode(protectedDef), canDisableNode(protectedDef), canReconnectNode(protectedDef)])
      .toEqual([false, false, true])
    expect([canDeleteNode(editable), canDisableNode(editable), canReconnectNode(editable)])
      .toEqual([true, true, true])
  })

  it('rejects cycles, incompatible slots, duplicate non-variadic inputs, and missing inputs', () => {
    const graph = validGraph()
    graph.edges.push({ fromNode: 'sink', fromSlot: 'missing', toNode: 'source', toSlot: 'missing' })
    graph.edges.push({ fromNode: 'source', fromSlot: 'out', toNode: 'sink', toSlot: 'in' })
    graph.nodes.push(node('raw', 'raw'))
    graph.edges.push({ fromNode: 'raw', fromSlot: 'out', toNode: 'middle', toSlot: 'in' })
    const mismatchCatalog = [...catalog, operator('raw', 'editable', 'DOCUMENT_BATCH', 'RAW_FILE_BATCH')]

    const codes = validateLocalGraph(graph, mismatchCatalog).map(issue => issue.code)
    expect(codes).toContain('workflow_cycle')
    expect(codes).toContain('missing_slot')
    expect(codes).toContain('incompatible_slot')
    expect(codes).toContain('multiple_input_edges')

    graph.edges = []
    expect(validateLocalGraph(graph, catalog).map(issue => issue.code)).toContain('missing_required_input')
  })

  it('rejects duplicate unique operators, disconnected nodes, and basic JSON Schema violations', () => {
    const graph = validGraph()
    graph.nodes[1].params = { limit: 0, mode: 'unknown', extra: true }
    graph.nodes.push(node('middle-copy', 'middle', { limit: 'two' }))

    const issues = validateLocalGraph(graph, catalog)
    const codes = issues.map(issue => issue.code)
    expect(codes).toContain('duplicate_operator')
    expect(codes).toContain('disconnected_node')
    expect(codes.filter(code => code === 'invalid_params').length).toBeGreaterThanOrEqual(3)
  })

  it('serializes nodes, edges, params, and object keys deterministically without mutating the graph', () => {
    const graph = validGraph()
    graph.nodes[1].params = { z: { b: 2, a: 1 }, a: true }
    graph.nodes.reverse()
    graph.edges.reverse()
    const before = JSON.stringify(graph)

    const first = stableGraphJson(graph)
    const second = stableGraphJson({ ...graph, nodes: [...graph.nodes].reverse(), edges: [...graph.edges].reverse() })

    expect(first).toBe(second)
    expect(JSON.stringify(graph)).toBe(before)
    expect(first.indexOf('"a":true')).toBeLessThan(first.indexOf('"z"'))
  })

  it('maps graph contracts to Vue Flow data and back without leaking canvas fields', () => {
    const graph = validGraph()
    const elements = toVueFlowElements(graph, catalog)

    expect(elements.nodes[1]).toMatchObject({
      id: 'middle', position: { x: 0, y: 0 },
      data: { operatorType: 'middle', definition: catalog[1], params: { limit: 2, mode: 'safe' } },
    })
    expect(elements.edges[0]).toMatchObject({ source: 'source', sourceHandle: 'out', target: 'middle', targetHandle: 'in' })
    expect(fromVueFlowElements(elements.nodes, elements.edges, graph.output)).toEqual(graph)
  })
})
