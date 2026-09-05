import type {
  ParseResultOutlineNode,
  ParseResultSegment,
  ParseResultTable,
} from '@/api/mining'

export type DocumentStructureNodeKind = 'document' | 'section' | 'content_group' | 'table'
export type DocumentContentType =
  | 'prose'
  | 'list'
  | 'code'
  | 'formula'
  | 'figure_caption'
  | 'unassigned'

type ReadonlyOutline = Readonly<ParseResultOutlineNode>
type ReadonlySegment = Readonly<Omit<ParseResultSegment, 'heading_chain' | 'element_ids'>> & {
  readonly heading_chain: ReadonlyArray<Readonly<{ level: number; title: string }>>
  readonly element_ids: readonly string[]
}
type ReadonlyTable = Readonly<Omit<ParseResultTable, 'header' | 'preview'>> & {
  readonly header: readonly string[]
  readonly preview: ReadonlyArray<readonly string[]>
}

export interface DocumentStructureNode {
  id: string
  kind: DocumentStructureNodeKind
  label: string
  subtitle: string
  level: number
  parentId: string | null
  orderIndex: number | null
  contentType: DocumentContentType | null
  segments: ReadonlySegment[]
  table: ReadonlyTable | null
}

export interface DocumentStructureEdge {
  id: string
  source: string
  target: string
}

export interface DocumentStructureGraph {
  nodes: DocumentStructureNode[]
  edges: DocumentStructureEdge[]
  diagnostics: string[]
  partial: boolean
  completeness: {
    returnedSegments: number
    totalSegments: number
    returnedElements: number
    totalElements: number
  }
  summary: {
    sections: number
    tables: number
    contentGroups: number
    segments: number
    elements: number
    relations: number
  }
}

interface BuildDocumentStructureGraphInput {
  documentTitle: string
  outline: readonly ReadonlyOutline[]
  tables: readonly ReadonlyTable[]
  segments: readonly ReadonlySegment[]
  segmentCount: number
  elementCount: number
  returnedElementCount?: number
  relationCount: number
  sectionCount?: number
  tableCount?: number
}

export const DOCUMENT_ROOT_ID = 'document:root'
export const UNASSIGNED_GROUP_ID = 'content:unassigned'
const MAX_MODEL_SECTIONS = 500
const MAX_MODEL_TABLES = 100

const CONTENT_LABELS: Record<Exclude<DocumentContentType, 'unassigned'>, string> = {
  prose: '正文',
  list: '列表',
  code: '代码',
  formula: '公式',
  figure_caption: '图片说明',
}

const TYPE_ALIASES: Record<string, Exclude<DocumentContentType, 'unassigned'> | null> = {
  prose: 'prose',
  paragraph: 'prose',
  text: 'prose',
  list: 'list',
  list_item: 'list',
  code: 'code',
  code_block: 'code',
  formula: 'formula',
  figure: 'figure_caption',
  figure_caption: 'figure_caption',
  caption: 'figure_caption',
  table: null,
  table_row: null,
  section: null,
  document: null,
}

function edge(source: string, target: string): DocumentStructureEdge {
  return { id: `contains:${source}:${target}`, source, target }
}

function normalizedContentType(blockType: string): Exclude<DocumentContentType, 'unassigned'> | null {
  const normalized = blockType.trim().toLowerCase()
  if (normalized in TYPE_ALIASES) return TYPE_ALIASES[normalized]
  return normalized ? 'prose' : null
}

function sortSegments(items: readonly ReadonlySegment[]): ReadonlySegment[] {
  return [...items].sort((left, right) => {
    const leftOrder = left.source_order_start ?? Number.MAX_SAFE_INTEGER
    const rightOrder = right.source_order_start ?? Number.MAX_SAFE_INTEGER
    return leftOrder - rightOrder || left.segment_index - right.segment_index
  })
}

function cloneTable(table: ReadonlyTable): ReadonlyTable {
  return {
    ...table,
    header: [...table.header],
    preview: table.preview.map(row => [...row]),
  }
}

/**
 * Build a deterministic, read-only document structure view model.
 *
 * Ownership comes exclusively from section_element_id / parent_section_element_id. Missing or
 * unknown ownership is made visible as an unassigned group; labels and heading text are never
 * used to guess relationships.
 */
export function buildDocumentStructureGraph(
  input: BuildDocumentStructureGraphInput,
): DocumentStructureGraph {
  let nodes: DocumentStructureNode[] = [{
    id: DOCUMENT_ROOT_ID,
    kind: 'document',
    label: input.documentTitle || '未命名文档',
    subtitle: '当前文档',
    level: 0,
    parentId: null,
    orderIndex: null,
    contentType: null,
    segments: [],
    table: null,
  }]
  const edges: DocumentStructureEdge[] = []
  const diagnostics: string[] = []
  const sectionStack: Array<{ id: string; level: number }> = []
  const sectionIdByElement = new Map<string, string>()
  const seenIds = new Map<string, number>()
  const allSectionIds = new Set(input.outline.map(item => item.element_id).filter(Boolean))

  const orderedOutline = input.outline
    .map((item, inputIndex) => ({ item, inputIndex }))
    .sort((left, right) => {
      const leftOrder = left.item.order_index ?? Number.MAX_SAFE_INTEGER
      const rightOrder = right.item.order_index ?? Number.MAX_SAFE_INTEGER
      return leftOrder - rightOrder || left.inputIndex - right.inputIndex
    })
    .slice(0, MAX_MODEL_SECTIONS)
  if (input.outline.length > orderedOutline.length) {
    diagnostics.push(`章节数量较多，当前结构视图仅构建前 ${MAX_MODEL_SECTIONS} 个章节`)
  } else if ((input.sectionCount ?? input.outline.length) > input.outline.length) {
    diagnostics.push('章节列表已由服务端截断，完整结构请查看高级信息')
  }

  orderedOutline.forEach(({ item, inputIndex }) => {
    const elementId = item.element_id || String(inputIndex)
    const rawId = `section:${elementId}`
    const seen = seenIds.get(rawId) ?? 0
    seenIds.set(rawId, seen + 1)
    if (seen > 0) diagnostics.push(`发现重复结构元素编号 ${elementId}，已保留为独立章节`)
    const id = seen === 0 ? rawId : `${rawId}:${seen}`
    const level = Math.max(1, Number.isFinite(item.level) ? item.level : 1)

    while (sectionStack.length && sectionStack[sectionStack.length - 1].level >= level) {
      sectionStack.pop()
    }
    const hasExplicitParent = Object.prototype.hasOwnProperty.call(
      item, 'parent_section_element_id',
    )
    const explicitParentId = item.parent_section_element_id
      ? sectionIdByElement.get(item.parent_section_element_id)
      : undefined
    const parentId = hasExplicitParent
      ? explicitParentId ?? DOCUMENT_ROOT_ID
      : sectionStack.at(-1)?.id ?? DOCUMENT_ROOT_ID
    if (hasExplicitParent && item.parent_section_element_id && !explicitParentId) {
      diagnostics.push(`章节 ${item.title || elementId} 的父章节不可用，已降级到文档根节点`)
    }
    if (!sectionIdByElement.has(elementId)) sectionIdByElement.set(elementId, id)
    nodes.push({
      id,
      kind: 'section',
      label: item.title || '未命名章节',
      subtitle: `${level} 级标题`,
      level,
      parentId,
      orderIndex: item.order_index ?? null,
      contentType: null,
      segments: [],
      table: null,
    })
    edges.push(edge(parentId, id))
    sectionStack.push({ id, level })
  })

  const assignedSegments = new Map<string, ReadonlySegment[]>()
  const contentGroups = new Map<string, ReadonlySegment[]>()
  const unassignedSegments: ReadonlySegment[] = []
  for (const segment of input.segments) {
    const contentType = normalizedContentType(segment.block_type)
    const sectionId = segment.section_element_id
      ? sectionIdByElement.get(segment.section_element_id)
      : undefined
    if (!sectionId) {
      if (segment.section_element_id && allSectionIds.has(segment.section_element_id)) continue
      if (contentType) unassignedSegments.push(segment)
      continue
    }
    if (!contentType) continue
    const sectionSegments = assignedSegments.get(sectionId) ?? []
    assignedSegments.set(sectionId, [...sectionSegments, segment])
    const groupId = `content:${sectionId}:${contentType}`
    const grouped = contentGroups.get(groupId) ?? []
    contentGroups.set(groupId, [...grouped, segment])
  }

  nodes = nodes.map(node => (
    node.kind === 'section'
      ? { ...node, segments: sortSegments(assignedSegments.get(node.id) ?? []) }
      : node
  ))

  for (const [id, grouped] of contentGroups) {
    const lastSeparator = id.lastIndexOf(':')
    const contentType = id.slice(lastSeparator + 1) as Exclude<DocumentContentType, 'unassigned'>
    const parentId = id.slice('content:'.length, lastSeparator)
    const items = sortSegments(grouped)
    nodes.push({
      id,
      kind: 'content_group',
      label: CONTENT_LABELS[contentType],
      subtitle: `${items.length} 个内容片段`,
      level: (nodes.find(node => node.id === parentId)?.level ?? 0) + 1,
      parentId,
      orderIndex: items[0]?.source_order_start ?? null,
      contentType,
      segments: items,
      table: null,
    })
    edges.push(edge(parentId, id))
  }

  const visibleTables = input.tables.slice(0, MAX_MODEL_TABLES)
  if (input.tables.length > visibleTables.length) {
    diagnostics.push(`表格数量较多，当前结构视图仅构建前 ${MAX_MODEL_TABLES} 张表格`)
  } else if ((input.tableCount ?? input.tables.length) > input.tables.length) {
    diagnostics.push('表格列表已由服务端截断，完整结构请查看高级信息')
  }
  const tablesToRender = visibleTables.filter(table => (
    !table.parent_section_element_id
    || sectionIdByElement.has(table.parent_section_element_id)
    || !allSectionIds.has(table.parent_section_element_id)
  ))
  const hasUnassignedTables = tablesToRender.some(table => (
    !table.parent_section_element_id || !sectionIdByElement.has(table.parent_section_element_id)
  ))
  if (unassignedSegments.length || hasUnassignedTables) {
    nodes.push({
      id: UNASSIGNED_GROUP_ID,
      kind: 'content_group',
      label: '未归属内容',
      subtitle: `${unassignedSegments.length} 个内容片段`,
      level: 1,
      parentId: DOCUMENT_ROOT_ID,
      orderIndex: null,
      contentType: 'unassigned',
      segments: sortSegments(unassignedSegments),
      table: null,
    })
    edges.push(edge(DOCUMENT_ROOT_ID, UNASSIGNED_GROUP_ID))
  }

  tablesToRender.forEach((inputTable, index) => {
    const table = cloneTable(inputTable)
    const rawId = `table:${table.table_id || index}`
    const seen = seenIds.get(rawId) ?? 0
    seenIds.set(rawId, seen + 1)
    if (seen > 0) diagnostics.push(`发现重复表格编号 ${table.table_id || index}，已保留为独立表格`)
    const id = seen === 0 ? rawId : `${rawId}:${seen}`
    const knownParent = table.parent_section_element_id
      ? sectionIdByElement.get(table.parent_section_element_id)
      : undefined
    const parentId = knownParent ?? UNASSIGNED_GROUP_ID
    nodes.push({
      id,
      kind: 'table',
      label: table.caption?.trim() || `表格 ${index + 1}`,
      subtitle: `${table.rows} 行 × ${table.columns} 列`,
      level: (nodes.find(node => node.id === parentId)?.level ?? 0) + 1,
      parentId,
      orderIndex: null,
      contentType: null,
      segments: [],
      table,
    })
    edges.push(edge(parentId, id))
  })

  const returnedElementCount = input.returnedElementCount ?? input.elementCount
  const completeness = {
    returnedSegments: input.segments.length,
    totalSegments: input.segmentCount,
    returnedElements: returnedElementCount,
    totalElements: input.elementCount,
  }

  return {
    nodes,
    edges,
    diagnostics,
    partial: (
      completeness.returnedSegments < completeness.totalSegments
      || completeness.returnedElements < completeness.totalElements
      || input.outline.length > orderedOutline.length
      || input.tables.length > visibleTables.length
      || (input.sectionCount ?? input.outline.length) > input.outline.length
      || (input.tableCount ?? input.tables.length) > input.tables.length
    ),
    completeness,
    summary: {
      sections: input.sectionCount ?? input.outline.length,
      tables: input.tableCount ?? input.tables.length,
      contentGroups: nodes.filter(node => node.kind === 'content_group').length,
      segments: input.segmentCount,
      elements: input.elementCount,
      relations: input.relationCount,
    },
  }
}
