/**
 * KB 展示层映射：可见性 / 角色 / 文档派生状态 → 文案（按当前语言翻译） + Element Plus tag 类型。
 * 列表页与详情页共享，避免在多个组件里重复硬编码。
 */
import type { KbDocStatus, KbMemberRole, KbMyRole, KbVisibility } from '@/types/kb'
import { t } from '@/locales/t'

// ── 可见性（visibility 已收口为 private / public 两档）──
export function visibilityLabel(v: KbVisibility): string {
  return v === 'public' ? t('kbView.visibility.public') : t('kbView.visibility.private')
}

export function visibilityTagType(v: KbVisibility): 'warning' | 'success' {
  return v === 'public' ? 'success' : 'warning'
}

// ── 我的角色（列表 my_role / 成员表 role 都用）──
const ROLE_LABEL_KEYS: Record<string, string> = {
  owner: 'kbView.role.owner',
  editor: 'kbView.role.editor',
  viewer: 'kbView.role.viewer',
  admin: 'header.roleAdmin',            // 系统管理员——与壳层同文案同 key（R5）
  domain_admin: 'header.roleDomainAdmin',
}

export function roleLabel(r: KbMyRole | KbMemberRole): string {
  const key = ROLE_LABEL_KEYS[r]
  return key ? t(key) : r
}

export function roleTagType(r: KbMyRole | KbMemberRole): 'primary' | 'success' | 'info' | 'danger' {
  return ({
    owner: 'primary', editor: 'success', viewer: 'info',
    admin: 'danger', domain_admin: 'danger',
  } as Record<string, 'primary' | 'success' | 'info' | 'danger'>)[r]
}

export function canWriteKb(role: KbMyRole): boolean {
  return role === 'owner' || role === 'editor' || role === 'admin' || role === 'domain_admin'
}

export function canManageKbLifecycle(role: KbMyRole): boolean {
  return role === 'owner' || role === 'admin' || role === 'domain_admin'
}

// ── 文档派生状态 ──
// 36号 §九：mined 的文案是「已入库」（进入 KB Build 且可检索）；
// unchanged（最近一次增量判定 SKIP，rd_action='SKIP'）显示「未变化」。
const DOC_STATUS_LABEL_KEYS: Record<string, string> = {
  uploaded: 'kbView.docStatus.uploaded',
  mining: 'common.docStatus.mining',
  update_failed: 'kbView.docStatus.updateFailed',
  published: 'common.docStatus.published',
  withdrawn: 'common.docStatus.withdrawn',
  failed: 'kbView.docStatus.failed',
  unknown: 'kbView.docStatus.unknown',
}

export function docStatusLabel(s: KbDocStatus, unchanged = false): string {
  if (s === 'mined') {
    return t(unchanged ? 'kbView.docStatus.minedUnchanged' : 'common.docStatus.mined')
  }
  return t(DOC_STATUS_LABEL_KEYS[s] ?? 'kbView.docStatus.unknown')
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
