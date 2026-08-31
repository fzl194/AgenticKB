import type {
  MiningJsonSchema,
  MiningJsonSchemaProperty,
  MiningOperatorDef,
  MiningWorkflowEdge,
  MiningWorkflowGraph,
  MiningWorkflowNode,
  WorkflowValidationIssue,
} from '@/types/miningWorkflow'

// 2026-08-31 用户反馈修复：节点位置（ui 坐标）是纯视觉属性，拖动不改变图结构，
// 不受 editPolicy 限制（原 canMoveNode 已删）——editPolicy 只管删除/禁用/重连。

export function canDeleteNode(definition: MiningOperatorDef): boolean {
  return definition.editPolicy === 'editable'
}

const ENTITY_BRANCH_TYPES = new Set(['entity_extract', 'entity_resolve', 'entity_relation_extract'])
const MANAGED_PROTECTED_TYPES = new Set(['entity_review_gate', 'ontology_review_gate', 'graph_write'])

export type MiningEffectiveEditState = 'fixed' | 'required' | 'optional'

export function requiredProtectedOperatorTypes(nodes: MiningWorkflowNode[]): Set<string> {
  const enabledTypes = new Set(nodes.filter(node => !node.disabled).map(node => node.operatorType))
  const hasEntityBranch = [...ENTITY_BRANCH_TYPES].some(type => enabledTypes.has(type))
  const hasOntologyInduction = enabledTypes.has('ontology_induction')
  if (hasOntologyInduction) {
    return new Set(['entity_review_gate', 'ontology_review_gate', 'graph_write'])
  }
  if (hasEntityBranch) return new Set(['entity_review_gate', 'graph_write'])
  return new Set()
}

export function canDeleteNodeInGraph(
  definition: MiningOperatorDef,
  nodes: MiningWorkflowNode[],
): boolean {
  return effectiveEditState(definition, nodes) === 'optional'
}

export function effectiveEditState(
  definition: MiningOperatorDef,
  nodes: MiningWorkflowNode[],
): MiningEffectiveEditState {
  if (definition.editPolicy === 'fixed') return 'fixed'
  if (definition.editPolicy !== 'protected') return 'optional'
  if (!MANAGED_PROTECTED_TYPES.has(definition.type)) return 'required'
  return requiredProtectedOperatorTypes(nodes).has(definition.type) ? 'required' : 'optional'
}

export function effectiveEditReason(
  definition: MiningOperatorDef,
  nodes: MiningWorkflowNode[],
): string {
  const state = effectiveEditState(definition, nodes)
  if (state === 'fixed') return '系统固定骨架节点，不能删除或禁用；参数仍可调整'
  if (state === 'optional') return '当前 Workflow 中可选'
  if (definition.type === 'ontology_review_gate') return '当前存在本体归纳，发布前必须完成本体审核'
  if (definition.type === 'entity_review_gate') return '当前存在实体或本体能力线，发布前必须完成实体审核'
  if (definition.type === 'graph_write') return '当前存在实体或本体能力线，发布前必须写入图谱'
  return 'Catalog 将该算子标记为受保护节点'
}

export function canDisableNode(definition: MiningOperatorDef): boolean {
  return definition.editPolicy === 'editable'
}

/**
 * 算子参数的编辑权限与 editPolicy 无关：editPolicy 只管结构（能否删除/移动/禁用），
 * 后端 compiler 对所有算子（含 fixed）一视同仁地校验并接受其参数。因此参数始终可调——
 * 固定解析头中的解析和切片参数仍可调整，调用方无需再判定。
 */

export function canReconnectNode(definition: MiningOperatorDef): boolean {
  return definition.editPolicy !== 'fixed'
}

function issue(code: string, message: string, details: Partial<WorkflowValidationIssue> = {}): WorkflowValidationIssue {
  return { code, message, severity: 'error', ...details }
}

function edgeLabel(edge: MiningWorkflowEdge): string {
  return `${edge.fromNode}.${edge.fromSlot}->${edge.toNode}.${edge.toSlot}`
}

function matchesType(value: unknown, type: string): boolean {
  if (type === 'null') return value === null
  if (type === 'array') return Array.isArray(value)
  if (type === 'object') return value !== null && typeof value === 'object' && !Array.isArray(value)
  if (type === 'integer') return typeof value === 'number' && Number.isInteger(value)
  if (type === 'number') return typeof value === 'number' && Number.isFinite(value)
  if (type === 'boolean') return typeof value === 'boolean'
  if (type === 'string') return typeof value === 'string'
  return true
}

function validateValue(
  value: unknown,
  schema: MiningJsonSchemaProperty,
  nodeId: string,
  path: string,
): WorkflowValidationIssue[] {
  const errors: WorkflowValidationIssue[] = []
  const allowedTypes = Array.isArray(schema.type) ? schema.type : schema.type ? [schema.type] : []
  if (allowedTypes.length && !allowedTypes.some(type => matchesType(value, type))) {
    return [issue('invalid_params', `${path} has an invalid type`, { nodeId, rule: path })]
  }
  if (schema.enum && !schema.enum.some(item => Object.is(item, value))) {
    errors.push(issue('invalid_params', `${path} must be one of the allowed values`, { nodeId, rule: path }))
  }
  if (typeof value === 'number') {
    if (schema.minimum !== undefined && value < schema.minimum) {
      errors.push(issue('invalid_params', `${path} must be at least ${schema.minimum}`, { nodeId, rule: path }))
    }
    if (schema.maximum !== undefined && value > schema.maximum) {
      errors.push(issue('invalid_params', `${path} must be at most ${schema.maximum}`, { nodeId, rule: path }))
    }
  }
  if (typeof value === 'string') {
    if (schema.minLength !== undefined && value.length < schema.minLength) {
      errors.push(issue('invalid_params', `${path} is too short`, { nodeId, rule: path }))
    }
    if (schema.maxLength !== undefined && value.length > schema.maxLength) {
      errors.push(issue('invalid_params', `${path} is too long`, { nodeId, rule: path }))
    }
  }
  if (Array.isArray(value)) {
    if (schema.minItems !== undefined && value.length < schema.minItems) {
      errors.push(issue('invalid_params', `${path} has too few items`, { nodeId, rule: path }))
    }
    if (schema.maxItems !== undefined && value.length > schema.maxItems) {
      errors.push(issue('invalid_params', `${path} has too many items`, { nodeId, rule: path }))
    }
    if (schema.items) value.forEach((item, index) => errors.push(...validateValue(item, schema.items!, nodeId, `${path}.${index}`)))
  }
  if (value !== null && typeof value === 'object' && !Array.isArray(value) && schema.properties) {
    errors.push(...validateParams(value as Record<string, unknown>, schema as MiningJsonSchema, nodeId, path))
  }
  return errors
}

function validateParams(
  params: Record<string, unknown>,
  schema: MiningJsonSchema,
  nodeId: string,
  prefix = '',
): WorkflowValidationIssue[] {
  const errors: WorkflowValidationIssue[] = []
  const properties = schema.properties ?? {}
  for (const required of schema.required ?? []) {
    if (params[required] === undefined) {
      const path = prefix ? `${prefix}.${required}` : required
      errors.push(issue('invalid_params', `${path} is required`, { nodeId, rule: path }))
    }
  }
  for (const [key, value] of Object.entries(params)) {
    const path = prefix ? `${prefix}.${key}` : key
    const property = properties[key]
    if (!property) {
      if (schema.additionalProperties === false) {
        errors.push(issue('invalid_params', `${path} is not allowed`, { nodeId, rule: path }))
      } else if (schema.additionalProperties && typeof schema.additionalProperties === 'object') {
        errors.push(...validateValue(value, schema.additionalProperties, nodeId, path))
      }
      continue
    }
    errors.push(...validateValue(value, property, nodeId, path))
  }
  return errors
}

function cycleExists(nodeIds: Set<string>, edges: MiningWorkflowEdge[]): boolean {
  const outgoing = new Map<string, string[]>()
  const indegree = new Map([...nodeIds].map(id => [id, 0]))
  for (const edge of edges) {
    if (!nodeIds.has(edge.fromNode) || !nodeIds.has(edge.toNode)) continue
    outgoing.set(edge.fromNode, [...(outgoing.get(edge.fromNode) ?? []), edge.toNode])
    indegree.set(edge.toNode, (indegree.get(edge.toNode) ?? 0) + 1)
  }
  const queue = [...indegree].filter(([, degree]) => degree === 0).map(([id]) => id)
  let visited = 0
  while (queue.length) {
    const id = queue.shift()!
    visited += 1
    for (const target of outgoing.get(id) ?? []) {
      const next = (indegree.get(target) ?? 0) - 1
      indegree.set(target, next)
      if (next === 0) queue.push(target)
    }
  }
  return visited !== nodeIds.size
}

function disconnectedNodes(
  nodes: MiningWorkflowNode[],
  definitions: Map<string, MiningOperatorDef>,
  edges: MiningWorkflowEdge[],
  outputId: string,
): string[] {
  const enabled = nodes.filter(node => !node.disabled && definitions.has(node.nodeId))
  const enabledIds = new Set(enabled.map(node => node.nodeId))
  const outgoing = new Map<string, string[]>()
  const incoming = new Map<string, string[]>()
  for (const edge of edges) {
    if (!enabledIds.has(edge.fromNode) || !enabledIds.has(edge.toNode)) continue
    outgoing.set(edge.fromNode, [...(outgoing.get(edge.fromNode) ?? []), edge.toNode])
    incoming.set(edge.toNode, [...(incoming.get(edge.toNode) ?? []), edge.fromNode])
  }
  const roots = enabled.filter(node => {
    const definition = definitions.get(node.nodeId)!
    return definition.zone === 'input' || definition.inputSlots.length === 0
  }).map(node => node.nodeId)
  const walk = (start: string[], adjacency: Map<string, string[]>) => {
    const seen = new Set<string>()
    const queue = [...start]
    while (queue.length) {
      const id = queue.shift()!
      if (seen.has(id)) continue
      seen.add(id)
      queue.push(...(adjacency.get(id) ?? []))
    }
    return seen
  }
  const fromRoot = walk(roots, outgoing)
  const toOutput = walk(enabledIds.has(outputId) ? [outputId] : [], incoming)
  return enabledIds.size <= 1 ? [] : [...enabledIds].filter(id => !fromRoot.has(id) || !toOutput.has(id))
}

export function validateLocalGraph(
  graph: MiningWorkflowGraph,
  catalog: MiningOperatorDef[],
): WorkflowValidationIssue[] {
  const errors: WorkflowValidationIssue[] = []
  const catalogByType = new Map(catalog.map(definition => [definition.type, definition]))
  const nodeCounts = new Map<string, number>()
  const typeCounts = new Map<string, number>()
  for (const node of graph.nodes) {
    nodeCounts.set(node.nodeId, (nodeCounts.get(node.nodeId) ?? 0) + 1)
    typeCounts.set(node.operatorType, (typeCounts.get(node.operatorType) ?? 0) + 1)
  }
  for (const [nodeId, count] of nodeCounts) {
    if (!nodeId || count > 1) errors.push(issue(nodeId ? 'duplicate_node_id' : 'missing_node_id', `Invalid node id '${nodeId}'`, { nodeId: nodeId || undefined }))
  }

  const nodes = new Map(graph.nodes.map(node => [node.nodeId, node]))
  const definitions = new Map<string, MiningOperatorDef>()
  for (const node of graph.nodes) {
    const definition = catalogByType.get(node.operatorType)
    if (!definition) {
      errors.push(issue('unknown_operator', `Unknown operator '${node.operatorType}'`, { nodeId: node.nodeId }))
      continue
    }
    definitions.set(node.nodeId, definition)
    if (definition.unique && (typeCounts.get(node.operatorType) ?? 0) > 1) {
      errors.push(issue('duplicate_operator', `Operator '${node.operatorType}' must be unique`, { nodeId: node.nodeId }))
    }
    if (node.operatorVersion && node.operatorVersion !== definition.version) {
      errors.push(issue('unsupported_operator_version', `Unsupported ${node.operatorType}@${node.operatorVersion}`, { nodeId: node.nodeId }))
    }
    errors.push(...validateParams(node.params ?? {}, definition.paramSchemaJson ?? {}, node.nodeId))
    if (definition.editPolicy === 'fixed' && node.disabled) {
      errors.push(issue('disabled_fixed_operator', `Fixed operator '${node.operatorType}' cannot be disabled`, { nodeId: node.nodeId }))
    }
  }
  for (const definition of catalog.filter(item => item.editPolicy === 'fixed')) {
    if (!(typeCounts.get(definition.type) ?? 0)) {
      errors.push(issue('missing_fixed_operator', `Missing fixed operator '${definition.type}'`))
    }
  }

  const validEdges: MiningWorkflowEdge[] = []
  const seenEdges = new Set<string>()
  const inputCounts = new Map<string, number>()
  for (const edge of graph.edges) {
    const label = edgeLabel(edge)
    if (seenEdges.has(label)) {
      errors.push(issue('duplicate_edge', 'Duplicate edge', { edge: label }))
      continue
    }
    seenEdges.add(label)
    if (!nodes.has(edge.fromNode) || !nodes.has(edge.toNode)) {
      errors.push(issue('missing_node', 'Edge references a missing node', { edge: label }))
      continue
    }
    if (nodes.get(edge.fromNode)?.disabled || nodes.get(edge.toNode)?.disabled) continue
    const source = definitions.get(edge.fromNode)
    const target = definitions.get(edge.toNode)
    if (!source || !target) continue
    const output = source.outputSlots.find(slot => slot.name === edge.fromSlot)
    const input = target.inputSlots.find(slot => slot.name === edge.toSlot)
    if (!output) errors.push(issue('missing_slot', `Unknown output slot '${edge.fromSlot}'`, { nodeId: edge.fromNode, slot: edge.fromSlot, edge: label }))
    if (!input) errors.push(issue('missing_slot', `Unknown input slot '${edge.toSlot}'`, { nodeId: edge.toNode, slot: edge.toSlot, edge: label }))
    if (!output || !input) continue
    if (output.type !== input.type) {
      errors.push(issue('incompatible_slot', `${output.type} cannot feed ${input.type}`, { nodeId: edge.toNode, edge: label }))
      continue
    }
    const inputKey = `${edge.toNode}.${edge.toSlot}`
    inputCounts.set(inputKey, (inputCounts.get(inputKey) ?? 0) + 1)
    validEdges.push(edge)
  }
  for (const [key, count] of inputCounts) {
    const separator = key.indexOf('.')
    const nodeId = key.slice(0, separator)
    const slotName = key.slice(separator + 1)
    const slot = definitions.get(nodeId)?.inputSlots.find(item => item.name === slotName)
    if (slot && !slot.variadic && count > 1) {
      errors.push(issue('multiple_input_edges', `Input ${key} accepts one edge`, { nodeId, slot: slotName }))
    }
  }
  const inbound = new Set(validEdges.map(edge => `${edge.toNode}.${edge.toSlot}`))
  for (const node of graph.nodes.filter(item => !item.disabled)) {
    const definition = definitions.get(node.nodeId)
    if (!definition) continue
    for (const slot of definition.inputSlots) {
      const externallySatisfied = definition.zone === 'input' && slot.type === 'INPUT_SPEC'
      if (slot.required && !externallySatisfied && !inbound.has(`${node.nodeId}.${slot.name}`)) {
        errors.push(issue('missing_required_input', `Required input ${node.nodeId}.${slot.name} is not connected`, { nodeId: node.nodeId, slot: slot.name }))
      }
    }
  }

  const enabledIds = new Set(graph.nodes.filter(node => !node.disabled).map(node => node.nodeId))
  if (cycleExists(enabledIds, graph.edges)) errors.push(issue('workflow_cycle', 'Workflow graph must be acyclic'))
  for (const nodeId of disconnectedNodes(graph.nodes, definitions, validEdges, graph.output.nodeId)) {
    errors.push(issue('disconnected_node', `Node '${nodeId}' is disconnected from the workflow`, { nodeId }))
  }
  return errors
}

function stableValue(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(stableValue)
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>)
        .sort(([left], [right]) => left.localeCompare(right))
        .map(([key, item]) => [key, stableValue(item)]),
    )
  }
  return value
}

export function stableGraphJson(graph: MiningWorkflowGraph): string {
  const normalized: MiningWorkflowGraph = {
    schemaVersion: graph.schemaVersion ?? '2.0',
    nodes: [...graph.nodes]
      .sort((left, right) => left.nodeId.localeCompare(right.nodeId))
      .map(node => stableValue(node) as MiningWorkflowNode),
    edges: [...graph.edges]
      .sort((left, right) => edgeLabel(left).localeCompare(edgeLabel(right)))
      .map(edge => stableValue(edge) as MiningWorkflowEdge),
    output: stableValue(graph.output) as MiningWorkflowGraph['output'],
  }
  return JSON.stringify(stableValue(normalized))
}

export interface MiningVueFlowNode {
  id: string
  type: 'miningOperator'
  position: { x: number; y: number }
  data: {
    operatorType: string
    operatorVersion: string
    definition?: MiningOperatorDef
    params: Record<string, unknown>
    disabled: boolean
    isOutput: boolean
  }
}

export interface MiningVueFlowEdge {
  id: string
  source: string
  sourceHandle: string
  target: string
  targetHandle: string
  selected?: boolean
  selectable?: boolean
  deletable?: boolean
  updatable?: boolean
  interactionWidth?: number
}

/** Explicit boundary mapper: backend graph fields never become implicit Vue Flow node state. */
export function toVueFlowElements(
  graph: MiningWorkflowGraph,
  catalog: MiningOperatorDef[],
): { nodes: MiningVueFlowNode[]; edges: MiningVueFlowEdge[] } {
  const definitions = new Map(catalog.map(definition => [definition.type, definition]))
  return {
    nodes: graph.nodes.map(node => ({
      id: node.nodeId,
      type: 'miningOperator',
      position: { x: node.ui?.x ?? 0, y: node.ui?.y ?? 0 },
      data: {
        operatorType: node.operatorType,
        operatorVersion: node.operatorVersion ?? definitions.get(node.operatorType)?.version ?? '1',
        definition: definitions.get(node.operatorType),
        params: stableValue(node.params ?? {}) as Record<string, unknown>,
        disabled: node.disabled ?? false,
        isOutput: graph.output.nodeId === node.nodeId,
      },
    })),
    edges: graph.edges.map(edge => ({
      id: edgeLabel(edge),
      source: edge.fromNode,
      sourceHandle: edge.fromSlot,
      target: edge.toNode,
      targetHandle: edge.toSlot,
    })),
  }
}

/** Strip Vue Flow-only state before saving a backend Workflow draft. */
export function fromVueFlowElements(
  nodes: MiningVueFlowNode[],
  edges: MiningVueFlowEdge[],
  output: MiningWorkflowGraph['output'],
  /** 画布重建图时保留原骨架版本（v2 = 解析/切片分离）；缺省 1.0 兼容 */
  schemaVersion: MiningWorkflowGraph['schemaVersion'] = '2.0',
): MiningWorkflowGraph {
  return {
    schemaVersion,
    nodes: nodes.map(node => {
      const result: MiningWorkflowNode = {
        nodeId: node.id,
        operatorType: node.data.operatorType,
        operatorVersion: node.data.operatorVersion,
        params: stableValue(node.data.params ?? {}) as Record<string, unknown>,
        ui: { x: node.position.x, y: node.position.y },
      }
      if (node.data.disabled) result.disabled = true
      return result
    }),
    edges: edges.map(edge => ({
      fromNode: edge.source,
      fromSlot: edge.sourceHandle,
      toNode: edge.target,
      toSlot: edge.targetHandle,
    })),
    output: { ...output },
  }
}
