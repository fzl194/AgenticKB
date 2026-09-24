import type {
  OnenetRestoreMode, OnenetRestoreModeChoice, OnenetTocFile, OnenetTocNode,
} from '@/api/onenet'

/**
 * 章节树勾选 → 导入子树路径集合（2026-09-16 事故修复）。
 *
 * 事故：getCheckedNodes(false, true) 的 includeHalfChecked 会把「半选祖先」
 * 一并返回——勾深层节点时祖先链一路半选到包名根（beta-2 后树根=包名，即
 * 全文档公共前缀），selection 退化为整本导入。规则：
 * 1. 调用方只传「全选」节点（getCheckedNodes(false, false)）；
 * 2. 本函数再做最小化：父路径已在集合内则子路径冗余（勾父必自动勾全子树）。
 */
export function minimalSubtreePaths(
  paths: string[],
  parentByPath?: Map<string, string>,
): string[] {
  const set = new Set(paths.map((p) => p.trim()).filter(Boolean))
  return [...set].filter((p) => {
    if (parentByPath) {
      let ancestor = parentByPath.get(p) ?? ''
      while (ancestor) {
        if (set.has(ancestor)) return false
        ancestor = parentByPath.get(ancestor) ?? ''
      }
      return true
    }
    const parent = p.includes(' > ') ? p.slice(0, p.lastIndexOf(' > ')) : ''
    return !parent || !set.has(parent)
  })
}

/** 路径按 '>' 切段：trim 并丢弃空段（分隔语义，与标题内含 '>' 无关） */
export function splitSegments(path: string): string[] {
  return path.split('>').map((p) => p.trim()).filter(Boolean)
}

/** 编辑模式空勾选表示“没有新增”，预览继续采用已保存范围。 */
export function effectivePreviewPaths(
  checkedPaths: string[], existingSubtrees: string[], editing: boolean,
): string[] {
  return editing && !checkedPaths.length ? existingSubtrees : checkedPaths
}

/** “自动推荐”只存在于 UI；落库合同始终写入一个具体还原模式。 */
export function resolveRestoreMode(
  choice: OnenetRestoreModeChoice,
  recommended: OnenetRestoreMode,
): OnenetRestoreMode {
  return choice === 'auto' ? recommended : choice
}

function collectSelectedNodes(
  subtreePaths: string[],
  nodesByPath: Map<string, OnenetTocNode>,
): Map<string, OnenetTocNode> {
  const selectedNodes = new Map<string, OnenetTocNode>()
  const collect = (node: OnenetTocNode) => {
    selectedNodes.set(node.path, node)
    for (const child of node.children) collect(child)
  }
  for (const subtreePath of subtreePaths) {
    const node = nodesByPath.get(subtreePath)
    if (node) collect(node)
  }
  return selectedNodes
}

/**
 * 53 号 §六（beta-4）：按选中子树的直属切片重新投影文件统计。
 * 每个有直属切片的树节点归属其树父节点文件；根节点按 α 规则归自身文件。
 * 这样 slice_count / heading_title / part 范围与后端先过滤再 restore 完全一致。
 */
export function filterFilesBySelection(
  files: OnenetTocFile[],
  subtreePaths: string[],
  nodesByPath: Map<string, OnenetTocNode>,
  parentByPath: Map<string, string>,
  mode: OnenetRestoreMode = 'product_document',
): OnenetTocFile[] {
  if (!subtreePaths.length) return files

  type Aggregate = {
    sliceCount: number
    partMin: number
    partMax: number
    headingTitle: string
  }
  const selectedNodes = collectSelectedNodes(subtreePaths, nodesByPath)

  const filePaths = new Set(files.map((file) => file.file_path))
  const nearestFileAnchor = (nodePath: string): string | null => {
    let candidate = nodePath
    while (candidate) {
      if (filePaths.has(candidate)) return candidate
      candidate = parentByPath.get(candidate) ?? ''
    }
    return null
  }

  const byFile = new Map<string, Aggregate>()
  for (const node of selectedNodes.values()) {
    const directCount = node.direct_slice_count ?? 0
    const directMin = node.direct_part_min
    const directMax = node.direct_part_max
    if (!directCount || directMin == null || directMax == null) continue
    const filePath = mode === 'file_anchor'
      ? nearestFileAnchor(node.path)
      : (parentByPath.get(node.path) || node.path)
    if (!filePath) continue
    const current = byFile.get(filePath)
    if (!current) {
      byFile.set(filePath, {
        sliceCount: directCount,
        partMin: directMin,
        partMax: directMax,
        headingTitle: node.title,
      })
      continue
    }
    byFile.set(filePath, {
      sliceCount: current.sliceCount + directCount,
      partMin: Math.min(current.partMin, directMin),
      partMax: Math.max(current.partMax, directMax),
      headingTitle: directMin < current.partMin ? node.title : current.headingTitle,
    })
  }

  return files
    .flatMap((file) => {
      const aggregate = byFile.get(file.file_path)
      return aggregate ? [{
        ...file,
        slice_count: aggregate.sliceCount,
        heading_title: aggregate.headingTitle,
        part_min: aggregate.partMin,
        part_max: aggregate.partMax,
      }] : []
    })
    .sort((a, b) => a.part_min - b.part_min)
}

/**
 * file_anchor 未归属数：部分勾选只统计所选子树；空勾选沿用后端整本预览值。
 * 已归属数直接取投影文件，避免在前端重复实现文件后缀识别规则。
 */
export function countUnassignedFileAnchorSlices(
  subtreePaths: string[],
  nodesByPath: Map<string, OnenetTocNode>,
  selectedFiles: OnenetTocFile[],
  wholeDocumentUnassigned: number,
): number {
  if (!subtreePaths.length) return wholeDocumentUnassigned
  const selectedTotal = [...collectSelectedNodes(subtreePaths, nodesByPath).values()]
    .reduce((sum, node) => sum + (node.direct_slice_count ?? 0), 0)
  const assigned = selectedFiles.reduce((sum, file) => sum + file.slice_count, 0)
  return Math.max(0, selectedTotal - assigned)
}

/** 树 → childPath → parentPath（父路径以树结构为准，不做字符串推导） */
export function buildParentPathMap(nodes: OnenetTocNode[]): Map<string, string> {
  const map = new Map<string, string>()
  const walk = (children: OnenetTocNode[], parentPath: string) => {
    for (const c of children) {
      map.set(c.path, parentPath)
      walk(c.children, c.path)
    }
  }
  walk(nodes, '')
  return map
}

/** 树 → path → node 索引（查 direct_slice_count 用） */
export function buildNodeIndex(nodes: OnenetTocNode[]): Map<string, OnenetTocNode> {
  const map = new Map<string, OnenetTocNode>()
  const walk = (children: OnenetTocNode[]) => {
    for (const c of children) {
      map.set(c.path, c)
      walk(c.children)
    }
  }
  walk(nodes)
  return map
}

/** 选中 raw path → 树结构中的语义标题段链（子集导入时供后端恢复祖先边界） */
export function buildPathHints(
  subtreePaths: string[],
  nodesByPath: Map<string, OnenetTocNode>,
  parentByPath: Map<string, string>,
): Record<string, string[]> {
  const hints: Record<string, string[]> = {}
  for (const subtreePath of subtreePaths) {
    const segments: string[] = []
    let currentPath = subtreePath
    while (currentPath) {
      const node = nodesByPath.get(currentPath)
      if (!node) break
      segments.unshift(node.title)
      currentPath = parentByPath.get(currentPath) ?? ''
    }
    if (segments.length) hints[subtreePath] = segments
  }
  return hints
}
