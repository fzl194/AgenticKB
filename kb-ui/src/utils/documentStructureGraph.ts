export type DocumentStructureNodeKind = 'document' | 'section' | 'table'

export interface DocumentStructureNode {
  id: string
  kind: DocumentStructureNodeKind
  label: string
  subtitle: string
  level: number
}

export interface DocumentStructureEdge {
  id: string
  source: string
  target: string
}

export interface DocumentStructureGraph {
  nodes: DocumentStructureNode[]
  edges: DocumentStructureEdge[]
  omitted: { sections: number; tables: number }
  diagnostics: string[]
  summary: {
    sections: number
    tables: number
    segments: number
    elements: number
    relations: number
  }
}

interface OutlineInput {
  element_id: string
  level: number
  title: string
}

interface TableInput {
  table_id: string
  rows: number
  columns: number
  header: readonly string[]
  preview: ReadonlyArray<readonly string[]>
}

interface BuildDocumentStructureGraphInput {
  documentTitle: string
  outline: readonly OutlineInput[]
  tables: readonly TableInput[]
  segmentCount: number
  elementCount: number
  relationCount: number
  maxSections?: number
  maxTables?: number
}

const ROOT_ID = 'document:root'

function edge(source: string, target: string): DocumentStructureEdge {
  return { id: `contains:${source}:${target}`, source, target }
}

/**
 * 将 parse-result 中已确认的结构事实转成可视化图。
 *
 * 表格摘要目前没有章节父引用，因此只挂到文档根节点；这里刻意不根据标题或顺序
 * 猜测归属，避免把演示图变成新的事实源。
 */
export function buildDocumentStructureGraph(
  input: BuildDocumentStructureGraphInput,
): DocumentStructureGraph {
  const maxSections = Math.max(0, input.maxSections ?? 120)
  const maxTables = Math.max(0, input.maxTables ?? 20)
  const visibleOutline = input.outline.slice(0, maxSections)
  const visibleTables = input.tables.slice(0, maxTables)
  const nodes: DocumentStructureNode[] = [{
    id: ROOT_ID,
    kind: 'document',
    label: input.documentTitle || '未命名文档',
    subtitle: '当前文档',
    level: 0,
  }]
  const edges: DocumentStructureEdge[] = []
  const diagnostics: string[] = []
  const sectionStack: Array<{ id: string; level: number }> = []
  const seenIds = new Map<string, number>()

  visibleOutline.forEach((item, index) => {
    const rawId = `section:${item.element_id || index}`
    const seen = seenIds.get(rawId) ?? 0
    seenIds.set(rawId, seen + 1)
    if (seen > 0) {
      diagnostics.push(`发现重复结构元素编号 ${item.element_id || index}，画布已保留为独立节点`)
    }
    const id = seen === 0 ? rawId : `${rawId}:${seen}`
    const level = Math.max(1, Number.isFinite(item.level) ? item.level : 1)

    while (sectionStack.length && sectionStack[sectionStack.length - 1].level >= level) {
      sectionStack.pop()
    }
    const parentId = sectionStack.at(-1)?.id ?? ROOT_ID
    nodes.push({
      id,
      kind: 'section',
      label: item.title || '未命名章节',
      subtitle: `${level} 级标题`,
      level,
    })
    edges.push(edge(parentId, id))
    sectionStack.push({ id, level })
  })

  visibleTables.forEach((table, index) => {
    const rawId = `table:${table.table_id || index}`
    const seen = seenIds.get(rawId) ?? 0
    seenIds.set(rawId, seen + 1)
    if (seen > 0) {
      diagnostics.push(`发现重复表格编号 ${table.table_id || index}，画布已保留为独立节点`)
    }
    const id = seen === 0 ? rawId : `${rawId}:${seen}`
    nodes.push({
      id,
      kind: 'table',
      label: `表格 ${index + 1}`,
      subtitle: `${table.rows} 行 × ${table.columns} 列`,
      level: 1,
    })
    edges.push(edge(ROOT_ID, id))
  })

  return {
    nodes,
    edges,
    diagnostics,
    omitted: {
      sections: Math.max(0, input.outline.length - visibleOutline.length),
      tables: Math.max(0, input.tables.length - visibleTables.length),
    },
    summary: {
      sections: input.outline.length,
      tables: input.tables.length,
      segments: input.segmentCount,
      elements: input.elementCount,
      relations: input.relationCount,
    },
  }
}
