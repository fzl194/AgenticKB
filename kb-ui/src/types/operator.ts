// Types for the pluggable retrieval operator system (agent_serving_java operator subsystem).

export type SlotType =
  | 'STRING' | 'INT' | 'DOUBLE' | 'BOOL' | 'VECTOR'
  | 'STRING_LIST' | 'CANDIDATE_LIST' | 'CANDIDATE_LIST_MULTI'
  | 'SCOPE' | 'QUERY_UNDERSTANDING' | 'CONTEXT_PACK'

export interface SlotDecl {
  name: string
  type: SlotType
  required: boolean
  description: string
}

/** A JSON-Schema (draft-07) object, parsed; used to render the param form. */
export interface ParamSchema {
  type?: string
  properties?: Record<string, ParamProperty>
  required?: string[]
}

export interface ParamProperty {
  type: 'string' | 'integer' | 'number' | 'boolean' | 'array' | 'object'
  title?: string
  default?: unknown
  enum?: string[]
  minimum?: number
  maximum?: number
  items?: { type: string }
  additionalProperties?: { type: string }
}

export interface OperatorDef {
  type: string
  category: string
  displayName: string
  description: string
  inputSlots: SlotDecl[]
  outputSlots: SlotDecl[]
  /** Raw JSON-Schema string for node params. */
  paramSchemaJson: string
  errorPolicy: string
}

// ---- paradigm graph (PRD §9) -------------------------------------------------

export interface GraphNode {
  nodeId: string
  operatorType: string
  params?: Record<string, unknown>
  /** Canvas-only display info; ignored by the backend compiler. */
  ui?: { x: number; y: number }
}

export interface GraphEdge {
  fromNode: string
  fromSlot: string
  toNode: string
  toSlot: string
}

export interface ParadigmGraph {
  schemaVersion?: string
  nodes: GraphNode[]
  edges: GraphEdge[]
  output: { nodeId: string; slot: string }
}

// ---- persistence views (backend responses) ----------------------------------

export interface ParadigmView {
  id: string
  name: string
  description: string | null
  status: string
  currentVersion: number
  draftGraph: ParadigmGraph | null
  createdAt: string | null
  updatedAt: string | null
  /** Domain this paradigm serves; null = unbound (still callable by id, excluded from matching). */
  boundDomain: string | null
  /** Whether MCP auto-matches this paradigm for boundDomain. At most one per domain. */
  isDefault: boolean
  boundAt: string | null
}

export interface ParadigmVersionView {
  id: string
  paradigmId: string
  version: number
  schemaVersion: string
  graph: ParadigmGraph | null
  createdAt: string | null
  createdBy: string | null
}

export interface CompileError {
  kind: string
  nodeId: string | null
  edge: string | null
  message: string
}

// ---- execution results -------------------------------------------------------

export interface RunCandidate {
  id: string
  score: number
  source: string
  scoreChain?: unknown
  metadata?: Record<string, unknown>
}

export interface RunTraceStep {
  nodeId: string
  operatorType: string
  durationMs: number
  summary: string
}

export interface RunResult {
  candidates?: RunCandidate[]
  contextPack?: unknown
  result?: unknown
  trace?: RunTraceStep[]
  domain?: string
  channel?: string
  // error shape
  error?: string
  errors?: CompileError[]
}
