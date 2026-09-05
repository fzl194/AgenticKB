/**
 * 概览页的派生逻辑。
 *
 * 抽成纯函数是因为这两条规则最容易在改版里被悄悄改掉：
 * - 卡片角标的**优先级**（失败 > 挖掘中 > 就绪）——反过来的话，一个正在挖的库会把
 *   已经失败的文档盖住；
 * - 待处理只列**有写权限**的库——对只读的库列待办没有意义，他也处理不了。
 */
import type { KbDocStatusKey, KbOverviewItem, KbStats } from '@/types/kb'

export type KbCardStatus = 'failed' | 'mining' | 'ready'

/**
 * 卡片右上角状态角标。
 *
 * 失败优先于挖掘中：一个库可能正在挖新文档、同时还有上一轮失败的没处理，
 * 这时该提醒的是失败那件事——它需要人动手，挖掘中只需要等。
 */
export function kbCardStatus(kb: KbOverviewItem): KbCardStatus {
  if (kb.status_counts.failed > 0) return 'failed'
  if (kb.status_counts.mining > 0) return 'mining'
  return 'ready'
}

export interface PendingTask {
  /** 同一个库可能同时产出两条（待人审 + 有失败文档），用它做 :key。 */
  key: string
  type: 'review' | 'failed'
  kbId: string
  kbName: string
  text: string
  actionLabel: string
  /**
   * 直接可用的路由路径。run 详情走 /kb/{kbId}/run/{runId} —— 旧的 /mining/{runId}
   * 路由已删除，拼成那个形状会点进空白页。
   */
  to: string
}

/**
 * 待处理清单。**任务条而不是计数**——计数没法直接处理，列表可以：
 * 用户看到「3 个失败」还得自己去找是哪三个，等于把工作推回去。
 *
 * 待人审排在解析失败之前：它卡住的是整条挖掘流水线，失败文档只影响自己。
 */
export function pendingTasks(kbs: KbOverviewItem[]): PendingTask[] {
  const writable = kbs.filter(kb => kb.can_write)

  const reviews: PendingTask[] = writable
    .filter(kb => !!kb.awaiting_review_run_id)
    .map(kb => ({
      key: `review:${kb.id}`,
      type: 'review' as const,
      kbId: kb.id,
      kbName: kb.name,
      text: '挖掘已暂停，等待人工审核',
      actionLabel: '去处理',
      to: `/kb/${kb.id}/run/${kb.awaiting_review_run_id}`,
    }))

  const failures: PendingTask[] = writable
    .filter(kb => kb.status_counts.failed > 0)
    .map(kb => ({
      key: `failed:${kb.id}`,
      type: 'failed' as const,
      kbId: kb.id,
      kbName: kb.name,
      text: `${kb.status_counts.failed} 篇文档解析失败`,
      actionLabel: '查看',
      to: `/kb/${kb.id}`,
    }))

  return [...reviews, ...failures]
}

/** 首页卡片区只渲染前 N 张；「查看全部 →」进 /kb。 */
export const DASHBOARD_KB_LIMIT = 6

export function visibleKbCards(kbs: KbOverviewItem[]): KbOverviewItem[] {
  return kbs.slice(0, DASHBOARD_KB_LIMIT)
}

// 注：原先这里还有个 searchTarget()（概览页搜索框跳 /search 用）。概览页改成统计仪表盘
// 后它没有调用方了，随本次改版一并删除——检索范围的默认与还原逻辑本来就在 SearchView
// 自己手里（utils/searchScope.ts），这个函数只是首页那个入口的附属品。

// ── 统计图表的数据派生 ───────────────────────────────────────────────────────
// 同样抽成纯函数：图表最容易出的错不在渲染，在喂给它的数组——排序方向反了、
// 不适用的口径被当成 0 画出来、横向柱的上下颠倒。这些用单测钉死比看图靠谱。

export interface ChartDatum {
  name: string
  value: number
  color?: string
}

/**
 * 横向柱状图的高度：每根柱 28px + 上下留白，最少 140px。
 *
 * 不能用固定高度——2 根柱子的图和 8 根的一样高时，前者会被拉成几条又粗又空的横条。
 * 概览页与运维面板各有横柱图，共用这一份，免得两边的柱子疏密不一。
 */
export function barChartHeight(count: number): string {
  return `${Math.max(140, count * 28 + 40)}px`
}

/**
 * 文档状态的呈现顺序与配色。
 *
 * 这是**状态色**不是分类色：绿=已挖掘、黄=进行中、红=失败、灰=还没开始，语义固定，
 * 不可挪作第 N 个系列用。配色跑过色觉校验（deutan/protan 相邻对 ΔE ≥ 8）——因为擦着
 * 下限过，色环必须同时给图例和数值表，绝不能只靠颜色区分（见 DashboardView 模板）。
 * 「待挖掘」用中性灰是刻意的：它是"尚未发生"，不该和三个真状态抢注意力。
 */
export const DOC_STATUS_META: { key: KbDocStatusKey; label: string; color: string }[] = [
  { key: 'mined', label: '已入库', color: '#10b981' },
  { key: 'mining', label: '处理中', color: '#f59e0b' },
  { key: 'uploaded', label: '待挖掘', color: '#64748b' },
  { key: 'failed', label: '失败', color: '#ef4444' },
  // 36号 §九：已入库但最近一次更新失败（检索仍用上一版本）——橙红系与
  // failed 区分：旧版本仍可检索，严重度低于彻底失败。
  { key: 'update_failed', label: '更新失败', color: '#f97316' },
  { key: 'published', label: '已发布', color: '#0891b2' },
  { key: 'withdrawn', label: '已撤回', color: '#8b5cf6' },
]

/** 无 active release 时不适用的两档——它们恒为 0，画出来会被读成「一篇都没发布」。 */
const RELEASE_ONLY_STATUSES: KbDocStatusKey[] = ['published', 'withdrawn']

/**
 * 文档状态分布。**保留计数为 0 的档**（「失败 0」是有用的信息），但域里没有
 * active release 时整个摘掉 published/withdrawn —— 那是口径不适用，不是数值为零。
 */
export function documentStatusSlices(stats: KbStats): ChartDatum[] {
  return DOC_STATUS_META
    .filter(m => stats.has_active_release || !RELEASE_ONLY_STATUSES.includes(m.key))
    .map(m => ({ name: m.label, value: stats.document_status?.[m.key] ?? 0, color: m.color }))
}

/** 概览页横向柱状图取前几名。再多的话每根柱子矮到读不出差异，剩下的去 /kb 看。 */
export const TOP_KB_BAR_LIMIT = 8

/**
 * 各知识库文档数（横向柱）。
 *
 * ⚠️ 末尾的 reverse 是必须的：ECharts 的 category 轴自下而上排列，不反转的话「最多的
 * 库」会落在图表最底下，与人从上往下读的顺序相反。
 * 文档数为 0 的库不进图——空柱只占位不传达信息，它们在下面的卡片区里照样看得到。
 */
export function kbDocumentBars(
  kbs: KbOverviewItem[],
  limit: number = TOP_KB_BAR_LIMIT,
): ChartDatum[] {
  return kbs
    .filter(kb => (kb.status_counts?.total ?? 0) > 0)
    .map(kb => ({ name: kb.name, value: kb.status_counts.total }))
    .sort((a, b) => b.value - a.value)
    .slice(0, limit)
    .reverse()
}

/** 检索单元类型的中文名。后端只回 DB 里出现过的键，没收录的原样显示。 */
const UNIT_TYPE_LABELS: Record<string, string> = {
  raw_text: '原始文本',
  contextual_text: '上下文文本',
  summary: '摘要',
  generated_question: '生成问题',
  entity_card: '实体卡片',
  table_row: '表格行',
  other: '其他',
}

/** 检索单元类型分布（横向柱）。reverse 的理由同 kbDocumentBars。 */
export function unitTypeBars(byType: Record<string, number> | undefined): ChartDatum[] {
  return Object.entries(byType ?? {})
    .filter(([, v]) => v > 0)
    .map(([k, v]) => ({ name: UNIT_TYPE_LABELS[k] ?? k, value: v }))
    .sort((a, b) => b.value - a.value)
    .reverse()
}

/**
 * 挖掘趋势的折线数据。
 *
 * **只出一条线（入库文档数）**：曾想过把「运行次数」并进来，但两者量级差一到两个数量级
 * （一天几次 run vs 几百篇文档），共用一根 y 轴时 run 那条会被压成贴地的直线；而给它单开
 * 一根右轴就是双轴图——同一张图两套刻度，读者无从判断两条线的交叉有没有意义。
 * 后端已补零，这里不再填缺口。
 */
export function trendSeries(stats: KbStats | null): { labels: string[]; data: number[] } {
  const points = stats?.mining_trend ?? []
  return {
    // 只留 MM-DD：30 个刻度写全 YYYY-MM-DD 必然互相压盖
    labels: points.map(p => p.date.slice(5)),
    data: points.map(p => p.documents),
  }
}

/** 趋势窗口内的合计，给折线图标题当副文案（一条没有数字的线很难判断量级）。 */
export function trendTotals(stats: KbStats | null): { runs: number; documents: number } {
  const points = stats?.mining_trend ?? []
  return {
    runs: points.reduce((s, p) => s + p.runs, 0),
    documents: points.reduce((s, p) => s + p.documents, 0),
  }
}
