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
