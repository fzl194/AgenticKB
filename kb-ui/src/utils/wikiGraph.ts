/**
 * Wiki 图谱视图模型——邻域自我中心图（ego-graph）纯函数。
 *
 * 铁律：节点=本制品页面，箭头=正文引用方向（from→to）；引用解析以服务端
 * links 为准（别名歧义/去重已在服务端算好，前端只消费）；缺失引用目标=
 * 断链伪节点（isBroken 区分，不可打开）。
 *
 * 视图语义（按 2026-11-12 拍板）：不做全量全景——中心=当前阅读/编辑页，
 * 左侧=输入（谁引用了中心，一阶；展开后含二阶上游），右侧=输出（中心
 * 引用了谁，一阶；展开后含二阶下游）。方向纯净：输入侧只向上游走、
 * 输出侧只向下游走，不画侧向旁枝。二阶默认折叠为一阶节点上的计数，
 * 由组件按需展开（expandedKeys）。断链目标只可能出现在输出侧（指向
 * 中心/存在页的链接按定义不会是 broken）。
 *
 * 布局坐标由本模块给出**确定性初值**（左侧输入/右侧输出、角色分列、
 * 组内纵向排布），组件交给 echarts force 布局弛豫——初值保证语义方向，
 * 物理模拟保证不重叠。
 */
import type { WikiLink, WikiPageMeta } from '@/types/artifacts'

/** 四页型中文标记（63号 §4.3）——与 Editor/Reader 面板同表独立维护（纯展示映射）。 */
export const WIKI_PAGE_TYPE_LABELS: Record<string, string> = {
  topic: '主题',
  glossary: '术语',
  contradiction: '矛盾',
  open_question: '开放问题',
}

/** 节点在邻域中的角色（决定初始列位与展开语义）。 */
export type WikiGraphRole = 'center' | 'input1' | 'output1' | 'input2' | 'output2'

export interface WikiNeighborhoodNode {
  /** page_key（断链伪节点=断链目标键）。 */
  id: string
  label: string
  pageType: string
  typeLabel: string
  origin: string
  /** 断链伪节点：引用目标页不存在（不可打开、弱化渲染）。 */
  isBroken: boolean
  role: WikiGraphRole
  /** 确定性初始坐标（x 按角色分列：输入负/输出正；y 组内纵向居中排布）。 */
  position: { x: number; y: number }
  /** 全图出链数（含平行引用，上下文面板展示口径）。 */
  outCount: number
  /** 全图被引用数。 */
  inCount: number
  /** 一阶节点可折叠的二阶计数（输入节点=上游来源数；输出节点=下游去向数；其余 null）。 */
  folded: { dir: 'in' | 'out'; count: number } | null
}

export interface WikiNeighborhoodEdge {
  /** 去重键 `${source}->${target}`。 */
  id: string
  source: string
  target: string
  /** 断链边（目标缺失）——组件渲染琥珀虚线。 */
  isBroken: boolean
  /** 平行引用数（同 from→to 多个锚文本）——tooltip 口径。 */
  refCount: number
}

export interface WikiNeighborhood {
  /** 实际采用的中心键（centerKey 无效时回落首页；空 wiki=''）。 */
  centerKey: string
  nodes: WikiNeighborhoodNode[]
  edges: WikiNeighborhoodEdge[]
  totals: { input1: number; output1: number; broken: number }
}

/** 角色初始 x 列位（输入负半轴、输出正半轴、二阶更远）。 */
const ROLE_X: Record<WikiGraphRole, number> = {
  center: 0,
  input1: -300,
  output1: 300,
  input2: -620,
  output2: 620,
}
/** 组内纵向间距。 */
const ROW_GAP = 120

/** 同 from→to 平行引用去重（refCount 累计；断链标记取真）。 */
function dedupeLinks(links: readonly WikiLink[]): WikiLink[] {
  const byPair = new Map<string, WikiLink>()
  for (const link of links) {
    const key = `${link.from_page}->${link.to_page}`
    const existing = byPair.get(key)
    if (!existing || (existing.status !== 'broken' && link.status === 'broken')) {
      byPair.set(key, link)
    }
  }
  return [...byPair.values()]
}

/**
 * pages/links 信封 → 中心页邻域模型。
 *
 * @param centerKey 中心页键；无效/缺省回落第一页（无页则空模型）。
 * @param expandedKeys 已展开二阶的一阶节点键集合（输入节点展开上游、输出节点展开下游）。
 */
export function buildWikiNeighborhood(
  pages: readonly WikiPageMeta[],
  links: readonly WikiLink[],
  centerKey?: string,
  expandedKeys: ReadonlySet<string> = new Set(),
): WikiNeighborhood {
  const pageByKey = new Map(pages.map(page => [page.page_key, page]))
  const unique = dedupeLinks(links)

  const outCounts = new Map<string, number>()
  const inCounts = new Map<string, number>()
  for (const link of links) {
    outCounts.set(link.from_page, (outCounts.get(link.from_page) ?? 0) + 1)
    inCounts.set(link.to_page, (inCounts.get(link.to_page) ?? 0) + 1)
  }

  const upstream = new Map<string, string[]>()   // 目标 → 来源键（去重）
  const downstream = new Map<string, string[]>() // 来源 → 目标键（去重）
  const pushUnique = (map: Map<string, string[]>, key: string, value: string) => {
    const items = map.get(key) ?? []
    if (!items.includes(value)) map.set(key, [...items, value])
  }
  for (const link of unique) {
    pushUnique(upstream, link.to_page, link.from_page)
    pushUnique(downstream, link.from_page, link.to_page)
  }

  const resolvedCenter = centerKey && pageByKey.has(centerKey)
    ? centerKey
    : pages[0]?.page_key ?? ''

  if (!resolvedCenter) {
    return { centerKey: '', nodes: [], edges: [], totals: { input1: 0, output1: 0, broken: 0 } }
  }

  // ── 一阶：输入=中心的上游来源；输出=中心的下游去向（含断链伪目标）──
  const input1 = (upstream.get(resolvedCenter) ?? []).filter(key => key !== resolvedCenter)
  const output1 = (downstream.get(resolvedCenter) ?? []).filter(key => key !== resolvedCenter)

  // ── 二阶（按展开集合收集，方向纯净）──
  const input2 = new Set<string>()
  const output2 = new Set<string>()
  for (const key of expandedKeys) {
    if (input1.includes(key)) {
      for (const source of upstream.get(key) ?? []) {
        if (source !== resolvedCenter) input2.add(source)
      }
    }
    if (output1.includes(key)) {
      for (const target of downstream.get(key) ?? []) {
        if (target !== resolvedCenter) output2.add(target)
      }
    }
  }

  const roleOf = (key: string): WikiGraphRole => {
    if (key === resolvedCenter) return 'center'
    if (input1.includes(key)) return 'input1'
    if (output1.includes(key)) return 'output1'
    if (input2.has(key)) return 'input2'
    if (output2.has(key)) return 'output2'
    return 'input2'   // 防御：既非一阶也非二阶（不应发生）——归输入侧不悬挂
  }

  const makeNode = (key: string, role: WikiGraphRole): WikiNeighborhoodNode => {
    const page = pageByKey.get(key)
    const isBroken = !page
    return {
      id: key,
      label: page?.title || key,
      pageType: page?.page_type ?? '',
      typeLabel: isBroken ? '断链' : WIKI_PAGE_TYPE_LABELS[page!.page_type] ?? page!.page_type,
      origin: page?.origin ?? '',
      isBroken,
      role,
      position: { x: 0, y: 0 },
      outCount: isBroken ? 0 : outCounts.get(key) ?? 0,
      inCount: inCounts.get(key) ?? 0,
      folded: null,
    }
  }

  // 一阶集合（key 集内排重：同一键可能同时是输入与输出的场景保一阶角色）
  const firstOrder = [...new Set([...input1, ...output1])]
  const secondOrder = [...input2, ...output2].filter(key => !firstOrder.includes(key))
  const allKeys = [resolvedCenter, ...firstOrder, ...secondOrder]

  // 一阶折叠计数（方向纯净：输入节点数上游、输出节点数下游；同为输入+输出
  // 的键按两侧合并计数，展开时两侧一并展开）
  const foldedOf = (key: string): WikiNeighborhoodNode['folded'] => {
    if (key === resolvedCenter) return null
    let dir: 'in' | 'out' | null = null
    let count = 0
    if (input1.includes(key)) {
      dir = 'in'
      count += (upstream.get(key) ?? []).filter(k => k !== resolvedCenter).length
    }
    if (output1.includes(key)) {
      dir = 'out'
      count += (downstream.get(key) ?? []).filter(k => k !== resolvedCenter).length
    }
    return dir ? { dir, count } : null
  }

  const nodes = allKeys.map(key => {
    const node = makeNode(key, roleOf(key))
    node.folded = foldedOf(key)
    return node
  })

  // 组内纵向居中排布（确定性初值；force 弛豫）
  const byRole = new Map<WikiGraphRole, WikiNeighborhoodNode[]>()
  for (const node of nodes) {
    byRole.set(node.role, [...(byRole.get(node.role) ?? []), node])
  }
  for (const group of byRole.values()) {
    group.forEach((node, index) => {
      node.position = {
        x: ROLE_X[node.role],
        y: (index - (group.length - 1) / 2) * ROW_GAP,
      }
    })
  }

  // 边：中心↔一阶 + 展开的一阶↔二阶（两端都在节点集内的去重边）
  const nodeIds = new Set(allKeys)
  const edges = unique
    .filter(link => nodeIds.has(link.from_page) && nodeIds.has(link.to_page))
    .filter(link => {
      // 只保留邻域语义边：端点含中心，或两端分别是某展开键与其二阶邻居
      const touchesCenter = link.from_page === resolvedCenter || link.to_page === resolvedCenter
      if (touchesCenter) return true
      const fromExpandedIn = expandedKeys.has(link.from_page) && input1.includes(link.from_page)
        && (upstream.get(link.from_page) ?? []).includes(link.to_page)
      const fromExpandedOut = expandedKeys.has(link.from_page) && output1.includes(link.from_page)
        && (downstream.get(link.from_page) ?? []).includes(link.to_page)
      return fromExpandedIn || fromExpandedOut
    })
    .map(link => ({
      id: `${link.from_page}->${link.to_page}`,
      source: link.from_page,
      target: link.to_page,
      isBroken: link.status === 'broken',
      refCount: links.filter(l => l.from_page === link.from_page && l.to_page === link.to_page).length,
    }))

  return {
    centerKey: resolvedCenter,
    nodes,
    edges,
    totals: {
      input1: input1.length,
      output1: output1.length,
      broken: nodes.filter(node => node.isBroken).length,
    },
  }
}

/** 初始角色 x 列位（组件画布取值用）。 */
export const WIKI_GRAPH_ROLE_X: Readonly<Record<WikiGraphRole, number>> = ROLE_X
