import type { OnenetTocFile, OnenetTocNode } from '@/api/onenet'

/**
 * 章节树勾选 → 导入子树路径集合（2026-09-16 事故修复）。
 *
 * 事故：getCheckedNodes(false, true) 的 includeHalfChecked 会把「半选祖先」
 * 一并返回——勾深层节点时祖先链一路半选到包名根（beta-2 后树根=包名，即
 * 全文档公共前缀），selection 退化为整本导入。规则：
 * 1. 调用方只传「全选」节点（getCheckedNodes(false, false)）；
 * 2. 本函数再做最小化：父路径已在集合内则子路径冗余（勾父必自动勾全子树）。
 */
export function minimalSubtreePaths(paths: string[]): string[] {
  const set = new Set(paths.map((p) => p.trim()).filter(Boolean))
  return [...set].filter((p) => {
    const parent = p.includes(' > ') ? p.slice(0, p.lastIndexOf(' > ')) : ''
    return !parent || !set.has(parent)
  })
}

/** 路径按 '>' 切段：trim 并丢弃空段（分隔语义，与标题内含 '>' 无关） */
export function splitSegments(path: string): string[] {
  return path.split('>').map((p) => p.trim()).filter(Boolean)
}

function isPrefixSegments(subtree: string[], filePath: string): boolean {
  const segs = splitSegments(filePath)
  if (subtree.length > segs.length) return false
  return subtree.every((seg, i) => segs[i] === seg)
}

/**
 * 53 号 §六：勾选联动过滤——与后端导入语义精确等价的两规则。
 * 规则1 段前缀：勾选子树 p 按段是 file_path 的前缀（含相等）→ 文件在勾选
 * 章节之下（方向与后端 Selection.matches 一致）。
 * 规则2 父文件：p 节点 direct_slice_count>0 → 直属切片归树结构父节点的文件
 * （父路径取树结构 Map，规避 ' > ' 字符串歧义）。
 * 空 selection = 整包全显。
 */
export function filterFilesBySelection(
  files: OnenetTocFile[],
  subtreePaths: string[],
  nodesByPath: Map<string, OnenetTocNode>,
  parentByPath: Map<string, string>,
): OnenetTocFile[] {
  if (!subtreePaths.length) return files
  const subtrees = subtreePaths.map(splitSegments)
  const shown = new Set<string>()
  for (const f of files) {
    if (subtrees.some((p) => isPrefixSegments(p, f.file_path))) shown.add(f.file_path)
  }
  for (const p of subtreePaths) {
    if ((nodesByPath.get(p)?.direct_slice_count ?? 0) > 0) {
      const parent = parentByPath.get(p)
      if (parent) shown.add(parent)
    }
  }
  return files.filter((f) => shown.has(f.file_path))
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
