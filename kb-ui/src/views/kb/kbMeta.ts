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
export function docStatusLabel(s: KbDocStatus): string {
  return {
    uploaded: '已上传',
    mining: '挖掘中',
    mined: '已挖掘',
    published: '已发布',
    withdrawn: '已撤回',
    failed: '失败',
    unknown: '未知',
  }[s]
}

export function docStatusTagType(
  s: KbDocStatus,
): 'success' | 'warning' | 'info' | 'danger' {
  switch (s) {
    case 'published': return 'success'
    case 'mined': return 'success'
    case 'mining': return 'warning'
    case 'failed': return 'danger'
    case 'uploaded':
    case 'withdrawn':
    case 'unknown':
    default:
      return 'info'
  }
}
