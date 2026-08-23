export type MiningSlotType =
  | 'INPUT_SPEC' | 'RAW_FILE_BATCH' | 'DOCUMENT_BATCH'
  | 'FINALIZE_INPUT' | 'FINALIZE_RESULT'

export type MiningExecutionZone = 'input' | 'document' | 'global'
export type MiningEditPolicy = 'fixed' | 'protected' | 'editable'
export type MiningWorkflowStatus = 'active' | 'archived'
export type MiningTemplateKey =
  | 'minimal'
  | 'fast_retrieval'
  | 'discourse_only'
  | 'entity_graph'
  | 'hybrid_knowledge'
  | 'ontology_only'
  | 'full'

export interface MiningSlotDecl {
  name: string
  type: MiningSlotType | string
  required: boolean
  variadic: boolean
  description: string
}

export interface MiningJsonSchemaProperty {
  type?: string | string[]
  title?: string
  description?: string
  default?: unknown
  enum?: unknown[]
  minimum?: number
  maximum?: number
  minLength?: number
  maxLength?: number
  minItems?: number
  maxItems?: number
  items?: MiningJsonSchemaProperty
  properties?: Record<string, MiningJsonSchemaProperty>
  additionalProperties?: boolean | MiningJsonSchemaProperty
  /**
   * 渲染提示，非 JSON Schema 关键字（校验器忽略未知关键字）。后端算子用它声明「这个
   * 字段该用哪种控件」，由调用方通过 optionSources 提供候选项；没提供就退化成默认控件。
   */
  'x-widget'?: string
}

export interface MiningJsonSchema extends MiningJsonSchemaProperty {
  required?: string[]
  properties?: Record<string, MiningJsonSchemaProperty>
}

export interface MiningOperatorDef {
  type: string
  version: string
  displayName: string
  description: string
  category: string
  zone: MiningExecutionZone
  editPolicy: MiningEditPolicy
  inputSlots: MiningSlotDecl[]
  outputSlots: MiningSlotDecl[]
  requires: string[]
  provides: string[]
  paramSchemaJson: MiningJsonSchema
  errorPolicy: string
  unique: boolean
}

export interface MiningOperatorCatalog {
  catalog_version: string
  items: MiningOperatorDef[]
}

export interface MiningWorkflowNode {
  nodeId: string
  operatorType: string
  operatorVersion?: string
  params: Record<string, unknown>
  ui?: { x: number; y: number; [key: string]: unknown }
  disabled?: boolean
}

export interface MiningWorkflowEdge {
  fromNode: string
  fromSlot: string
  toNode: string
  toSlot: string
}

export interface MiningWorkflowGraph {
  schemaVersion?: string
  nodes: MiningWorkflowNode[]
  edges: MiningWorkflowEdge[]
  output: { nodeId: string; slot: string }
}

export interface MiningWorkflow {
  id: string
  name: string
  description: string | null
  status: MiningWorkflowStatus
  draft_graph_json: MiningWorkflowGraph
  draft_revision: number
  current_version: number | null
  is_system: boolean
  is_system_default: boolean
  created_by: string | null
  updated_by: string | null
  metadata_json: Record<string, unknown>
  created_at?: string | null
  updated_at?: string | null
}

export interface MiningWorkflowVersion {
  id: string
  workflow_id: string
  version: number
  graph_json: MiningWorkflowGraph
  compiled_manifest_json?: Record<string, unknown>
  graph_hash: string
  schema_version: string
  operator_catalog_version: string
  release_notes: string | null
  created_by: string | null
  metadata_json?: Record<string, unknown>
  created_at?: string | null
}

export interface MiningWorkflowOption {
  id: string
  name: string
  description: string | null
  current_version: number
  is_system_default: boolean
}

export interface WorkflowValidationIssue {
  code: string
  message: string
  nodeId?: string
  slot?: string
  edge?: string
  rule?: string
  severity: 'error' | 'warning'
}

export interface MiningWorkflowValidationResult {
  valid: boolean
  errors: Array<{
    kind: string
    message: string
    nodeId?: string
    slot?: string
    edge?: string
    rule?: string
  }>
  executionPlan?: {
    inputOrder: string[]
    documentOrder: string[]
    globalOrder: string[]
  } | null
}

export interface CreateMiningWorkflowRequest {
  name: string
  description?: string
  template_key?: MiningTemplateKey
  /** 唯一解析链路骨架：解析/切片分离（质量门控+知识快照）。 */
  schema_version?: '2.0'
  graph?: MiningWorkflowGraph
  created_by?: string
}

export interface SaveMiningWorkflowDraftRequest {
  graph: MiningWorkflowGraph
  expected_revision: number
  updated_by?: string
}

export interface PublishMiningWorkflowRequest {
  expected_revision: number
  release_notes?: string
  created_by?: string
}

export interface RestoreMiningWorkflowDraftRequest {
  expected_revision: number
  updated_by?: string
}

export interface CloneMiningWorkflowRequest {
  name: string
  description?: string
  source_version?: number
  created_by?: string
}

export interface FrozenMiningWorkflowSummary {
  id: string
  name?: string | null
  version: number
  version_id?: string | null
  graph_hash: string
  schema_version?: string
  catalog_version?: string
  graph?: MiningWorkflowGraph
  nodes?: Array<Record<string, unknown>>
  edges?: MiningWorkflowEdge[]
  required_completion?: string[]
}

export interface MiningWorkflowNodeEvent {
  id: string
  run_id: string
  run_document_id: string | null
  node_id: string
  operator_type: string
  operator_version: string
  attempt_no: number
  status: string
  started_at: string | null
  finished_at: string | null
  duration_ms?: number | null
  input_summary?: Record<string, unknown> | null
  output_summary?: Record<string, unknown> | null
  error_code?: string | null
  error_message?: string | null
  metadata?: Record<string, unknown> | null
  input_summary_json?: Record<string, unknown> | null
  output_summary_json?: Record<string, unknown> | null
  metadata_json?: Record<string, unknown> | null
}
