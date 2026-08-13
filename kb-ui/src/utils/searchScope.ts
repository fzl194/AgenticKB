/**
 * 检索范围的选择语义（设计文档 §5.3 / 缺陷 D8）。
 *
 * 背景：`selectedKbIds` 原本默认 `[]`，而 `serving.ts` 对空数组会**省掉 kbIds 键**，
 * 后端于是走域级 active release 分支。KB 挖掘 `publish=false` 永不产 release，所以
 * 纯 KB 部署下"开箱即搜"必然撞 `no_active_release`——搜索前置的前提就塌了。
 *
 * 修法的关键不只是"默认全选"，而是**把"全域"从隐式变成显式**：
 * - 默认 = 全部可见知识库，显式传 kbIds；
 * - 「域级发布」只在该域真有 active release 时作为一个**具名选项**出现，选中它才发空
 *   kbIds（那是它唯一正确的语义）；
 * - 什么都不选 ≠ 全域，而是不能检索——否则用户点一下 clearable 的叉就原地复现 D8。
 *
 * 这些规则做成纯函数，好让它们能被单测钉住，而不是散在组件的 watch 里。
 */

/** 「域级生效发布」在多选框里的哨兵值。真实 kb_id 是 uuid，不会与它相撞。 */
export const DOMAIN_RELEASE_SCOPE = '__domain_release__'

/**
 * 用户改动选择后，强制「域级发布」与具体知识库互斥。
 *
 * 二者语义上不可叠加：域级发布检索的是 active release 快照，知识库检索的是各库自己的
 * build，后端是两条互斥的分支（`resolveActiveScope` 按 kbIds 是否为空分派）。
 * 需要 prev 才能判断"这一次是哪边新加进来的"——同时出现时谁让位是有方向的。
 */
export function reconcileScopeSelection(prev: string[], next: string[]): string[] {
  const nextHasRelease = next.includes(DOMAIN_RELEASE_SCOPE)
  if (!nextHasRelease) return next

  // 刚勾上「域级发布」→ 它独占
  if (!prev.includes(DOMAIN_RELEASE_SCOPE)) return [DOMAIN_RELEASE_SCOPE]

  // 「域级发布」本来就在，又勾了具体知识库 → 让位给知识库
  const kbOnly = next.filter(id => id !== DOMAIN_RELEASE_SCOPE)
  return kbOnly.length > 0 ? kbOnly : next
}

/**
 * 本次检索真正发给后端的 kbIds。
 *
 * 「域级发布」→ 空数组：`serving.ts` 见空数组会省掉该键，正是域级 release 分支。
 * 这是那条路径**唯一**的入口——清空选择不再走到这里（见 canSearchWithScope）。
 */
export function resolveRequestKbIds(selected: string[]): string[] {
  if (selected.includes(DOMAIN_RELEASE_SCOPE)) return []
  return selected.filter(id => !!id)
}

/** 能否发起检索。什么都没选 = 不能——这正是 D8 的隐式路径，堵死它。 */
export function canSearchWithScope(selected: string[]): boolean {
  return selected.length > 0
}

/**
 * 默认选择：**全部可见知识库**，两种部署下一致。
 *
 * 有 active release 的域也不默认选「域级发布」——多数用户要搜的是自己的库，
 * 那个选项只是给混合部署留一条回到 legacy 语料的路。
 */
export function defaultScopeSelection(kbs: Array<{ id: string }>): string[] {
  return kbs.map(kb => kb.id)
}

/**
 * 把 URL 上的 `?kbIds=` 还原成选择。
 *
 * 首页跳转过来时带的是本次要搜的范围；空串或全部无效 id → 回落默认全选，
 * 而不是留空（留空 = 不能检索，对着一个带 q 的链接却搜不了很费解）。
 */
export function scopeFromQuery(
  raw: string | string[] | null | undefined,
  kbs: Array<{ id: string }>,
): string[] {
  const parts = (Array.isArray(raw) ? raw : [raw ?? ''])
    .flatMap(v => String(v ?? '').split(','))
    .map(v => v.trim())
    .filter(Boolean)
  if (parts.length === 0) return defaultScopeSelection(kbs)

  const known = new Set(kbs.map(kb => kb.id))
  // 只保留当前域仍可见的 id：切域或改权限后旧链接会带上已失效的 id，
  // 原样发出去会让整单 404 kb_not_found（后端刻意不做静默子集）。
  const valid = parts.filter(id => known.has(id))
  return valid.length > 0 ? valid : defaultScopeSelection(kbs)
}
