/**
 * 概览页的派生逻辑（设计文档 §4.1）。
 *
 * 抽成纯函数是因为这两条规则最容易在改版里被悄悄改掉：
 * - 卡片角标的**优先级**（失败 > 挖掘中 > 就绪）——反过来的话，一个正在挖的库会把
 *   已经失败的文档盖住；
 * - 待处理只列**有写权限**的库——对只读的库列待办没有意义，他也处理不了。
 */
import type { KbOverviewItem } from '@/types/kb'

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
  /** 直接可用的路由路径。run 详情走 /kb/{kbId}/run/{runId}（修 D1 的那条死链形状）。 */
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

/**
 * 搜索框跳转目标。范围显式带上，让 /search 复现同一次范围而不是自己再默认一遍。
 * kbIds 为空（「域级发布」范围）时不带该参数——空串会被 scopeFromQuery 当成"没传"。
 */
export function searchTarget(query: string, kbIds: string[]) {
  const q = query.trim()
  return {
    path: '/search',
    query: kbIds.length > 0 ? { q, kbIds: kbIds.join(',') } : { q },
  }
}
