/**
 * KB 展示层映射：可见性 / 角色 / 文档派生状态 → 中文文案 + Element Plus tag 类型。
 * 列表页与详情页共享，避免在多个组件里重复硬编码。
 */
import type { KbDocStatus, KbMemberRole, KbMyRole, KbVisibility } from '@/types/kb'

// ── 可见性（visibility 已收口为 private / public 两档）──
export function visibilityLabel(v: KbVisibility): string {
  return v === 'public' ? '公开' : '私有'
}

export function visibilityTagType(v: KbVisibility): 'warning' | 'success' {
  return v === 'public' ? 'success' : 'warning'
}

// ── 我的角色（列表 my_role / 成员表 role 都用）──
export function roleLabel(r: KbMyRole | KbMemberRole): string {
  return ({ owner: '拥有者', editor: '编辑者', viewer: '只读', admin: '管理员' } as Record<string, string>)[r]
}

export function roleTagType(r: KbMyRole | KbMemberRole): 'primary' | 'success' | 'info' | 'danger' {
  return ({ owner: 'primary', editor: 'success', viewer: 'info', admin: 'danger' } as Record<string, 'primary' | 'success' | 'info' | 'danger'>)[r]
}

// ── 文档派生状态 ──
// 36号 §九：mined 的文案是「已入库」（进入 KB Build 且可检索）；
// unchanged（最近一次增量判定 SKIP，rd_action='SKIP'）显示「未变化」。
export function docStatusLabel(s: KbDocStatus, unchanged = false): string {
  if (s === 'mined') return unchanged ? '已入库（未变化）' : '已入库'
  return {
    uploaded: '未挖掘',
    mining: '处理中',
    update_failed: '更新失败，仍用上一版本',
    published: '已发布',
    withdrawn: '已撤回',
    failed: '挖掘失败，等待重试',
    unknown: '未知',
  }[s] ?? '未知'
}

export function docStatusTagType(
  s: KbDocStatus,
): 'success' | 'warning' | 'info' | 'danger' {
  switch (s) {
    case 'published': return 'success'
    case 'mined': return 'success'
    case 'mining': return 'warning'
    case 'failed': return 'danger'
    case 'update_failed': return 'warning'
    case 'uploaded':
    case 'withdrawn':
    case 'unknown':
    default:
      return 'info'
  }
}
