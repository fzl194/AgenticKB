import { describe, it, expect } from 'vitest'
import {
  DASHBOARD_KB_LIMIT,
  kbCardStatus,
  pendingTasks,
  searchTarget,
  visibleKbCards,
} from '@/utils/dashboard'
import { runStatusLabel } from '@/utils/runStatus'
import type { KbOverviewItem } from '@/types/kb'

function kb(over: Partial<KbOverviewItem> & { id: string }): KbOverviewItem {
  return {
    name: over.id.toUpperCase(),
    my_role: 'owner',
    can_write: true,
    status_counts: { total: 10, mining: 0, failed: 0 },
    last_mined_at: null,
    awaiting_review_run_id: null,
    ...over,
  } as KbOverviewItem
}

describe('卡片状态角标', () => {
  it('失败优先于挖掘中', () => {
    // 一个库可能边挖新文档、边留着上一轮的失败——该提醒的是需要人动手的那件事
    const both = kb({ id: 'kb-a', status_counts: { total: 10, mining: 3, failed: 2 } })
    expect(kbCardStatus(both)).toBe('failed')
  })

  it('只有挖掘中 → mining；都没有 → ready', () => {
    expect(kbCardStatus(kb({ id: 'a', status_counts: { total: 5, mining: 1, failed: 0 } })))
      .toBe('mining')
    expect(kbCardStatus(kb({ id: 'b' }))).toBe('ready')
  })
})

describe('待处理清单', () => {
  it('只列有写权限的库——只读的列了他也处理不了', () => {
    const tasks = pendingTasks([
      kb({ id: 'ro', can_write: false, my_role: 'viewer', awaiting_review_run_id: 'run-1' }),
      kb({ id: 'ro2', can_write: false, status_counts: { total: 3, mining: 0, failed: 2 } }),
    ])
    expect(tasks).toEqual([])
  })

  it('待人审排在解析失败之前——它卡住的是整条流水线', () => {
    const tasks = pendingTasks([
      kb({ id: 'kb-fail', status_counts: { total: 9, mining: 0, failed: 3 } }),
      kb({ id: 'kb-review', awaiting_review_run_id: 'run-9' }),
    ])
    expect(tasks.map(t => t.type)).toEqual(['review', 'failed'])
  })

  it('待人审链接指向 /kb/{kbId}/run/{runId}（修 D1 的死链形状）', () => {
    const [task] = pendingTasks([kb({ id: 'kb-a', awaiting_review_run_id: 'run-9' })])
    expect(task.to).toBe('/kb/kb-a/run/run-9')
  })

  it('失败任务给出具体篇数，链接进 KB 文件页', () => {
    const [task] = pendingTasks([
      kb({ id: 'kb-a', status_counts: { total: 9, mining: 0, failed: 3 } }),
    ])
    expect(task.text).toBe('3 篇文档解析失败')
    expect(task.to).toBe('/kb/kb-a')
  })

  it('同一个库两件事都有时产出两条，key 不撞', () => {
    const tasks = pendingTasks([
      kb({
        id: 'kb-a',
        awaiting_review_run_id: 'run-9',
        status_counts: { total: 9, mining: 0, failed: 1 },
      }),
    ])
    expect(tasks).toHaveLength(2)
    expect(new Set(tasks.map(t => t.key)).size).toBe(2)
  })

  it('无事可做时为空 —— 区块整块不渲染', () => {
    expect(pendingTasks([kb({ id: 'kb-a' })])).toEqual([])
  })
})

describe('卡片截断', () => {
  it('首页只渲染前 6 张，但传入的是全集（搜索范围要用）', () => {
    const many = Array.from({ length: 9 }, (_, i) => kb({ id: `kb-${i}` }))
    expect(visibleKbCards(many)).toHaveLength(DASHBOARD_KB_LIMIT)
    expect(many).toHaveLength(9)
  })
})

describe('搜索跳转', () => {
  it('显式带上范围，让 /search 复现同一次范围', () => {
    expect(searchTarget(' SMF ', ['kb-a', 'kb-b'])).toEqual({
      path: '/search',
      query: { q: 'SMF', kbIds: 'kb-a,kb-b' },
    })
  })

  it('范围为空时不带 kbIds —— 空串会被当成"没传"', () => {
    expect(searchTarget('x', [])).toEqual({ path: '/search', query: { q: 'x' } })
  })
})

describe('run 状态文案（D5）', () => {
  it('覆盖 DB CHECK 里的全部 7 个状态，不再露出英文', () => {
    for (const s of [
      'queued', 'running', 'completed', 'interrupted', 'failed', 'cancelled',
      'awaiting_review',
    ]) {
      expect(runStatusLabel(s)).not.toBe(s)
    }
  })

  it('DB 里不存在的 pending 不再有专门文案', () => {
    // 它是从 mining_run_documents.status 串过来的，run 级没有这个值
    expect(runStatusLabel('pending')).toBe('pending')
  })

  it('未知状态原样回显，不掩盖', () => {
    expect(runStatusLabel('some_new_state')).toBe('some_new_state')
  })
})
