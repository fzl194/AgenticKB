import { describe, expect, it } from 'vitest'

import {
  canManageKbLifecycle,
  canWriteKb,
  docStatusLabel,
  roleLabel,
  visibilityLabel,
} from '@/views/kb/kbMeta'

describe('domain-admin KB permissions', () => {
  it('treats a domain admin as a writer and lifecycle manager', () => {
    expect(canWriteKb('domain_admin')).toBe(true)
    expect(canManageKbLifecycle('domain_admin')).toBe(true)
    expect(roleLabel('domain_admin')).toBe('域管理员')
  })

  it('keeps editor lifecycle access narrower than write access', () => {
    expect(canWriteKb('editor')).toBe(true)
    expect(canManageKbLifecycle('editor')).toBe(false)
  })
})

describe('kbMeta zh labels (R3：字典值与 i18n 前原文逐字节一致)', () => {
  it('keeps the zh labels byte-identical to the pre-i18n text', () => {
    expect(visibilityLabel('public')).toBe('公开')
    expect(visibilityLabel('private')).toBe('私有')
    expect(roleLabel('owner')).toBe('拥有者')
    expect(roleLabel('editor')).toBe('编辑者')
    expect(roleLabel('viewer')).toBe('只读')
    expect(docStatusLabel('unknown')).toBe('未知')
    expect(docStatusLabel('failed')).toBe('挖掘失败，等待重试')
    expect(docStatusLabel('update_failed')).toBe('更新失败，仍用上一版本')
  })
})
