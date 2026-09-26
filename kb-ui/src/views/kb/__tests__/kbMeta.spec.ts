import { describe, expect, it } from 'vitest'

import {
  canManageKbLifecycle,
  canWriteKb,
  roleLabel,
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
