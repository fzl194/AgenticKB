/**
 * 36号 §九：文档状态语义与部分成功展示的展示层契约。
 *
 * - mined 文案改为「已入库」（Build membership），SKIP 携带「未变化」；
 * - update_failed = 已入库但最近一次更新失败（仍用上一版本）；
 * - 检索错误中文化（kb_not_found → 可行动中文提示）。
 */
import { describe, expect, it } from 'vitest'

import { localizeSearchError } from '@/api/serving'
import { docStatusLabel, docStatusTagType } from '@/views/kb/kbMeta'
import { DOC_STATUS_META, documentStatusSlices } from '@/utils/dashboard'
import type { KbStats } from '@/types/kb'

describe('docStatusLabel（36号 §九）', () => {
  it('mined → 已入库；unchanged → 已入库（未变化）', () => {
    expect(docStatusLabel('mined')).toBe('已入库')
    expect(docStatusLabel('mined', true)).toBe('已入库（未变化）')
  })

  it('update_failed → 更新失败，仍用上一版本', () => {
    expect(docStatusLabel('update_failed')).toBe('更新失败，仍用上一版本')
    expect(docStatusTagType('update_failed')).toBe('warning')
  })

  it('uploaded/failed/mining 语义区分', () => {
    expect(docStatusLabel('uploaded')).toBe('未挖掘')
    expect(docStatusLabel('failed')).toBe('挖掘失败，等待重试')
    expect(docStatusLabel('mining')).toBe('处理中')
  })
})

describe('DOC_STATUS_META / documentStatusSlices', () => {
  it('七态齐全且 update_failed 在列', () => {
    const keys = DOC_STATUS_META.map(m => m.key)
    expect(keys).toContain('update_failed')
    expect(keys).toContain('mined')
    expect(DOC_STATUS_META.find(m => m.key === 'mined')?.label).toBe('已入库')
  })

  it('无 release 时摘掉 published/withdrawn，保留 update_failed', () => {
    const stats = {
      has_active_release: false,
      document_status: {
        uploaded: 1, mining: 0, mined: 3, update_failed: 2,
        published: 0, withdrawn: 0, failed: 1,
      },
    } as unknown as KbStats
    const names = documentStatusSlices(stats).map(s => s.name)
    expect(names).not.toContain('已发布')
    expect(names).not.toContain('已撤回')
    expect(names).toContain('更新失败')
    expect(names).toContain('已入库')
  })
})

describe('localizeSearchError（36号 §九 检索行为）', () => {
  it('kb_not_found 404 → 中文可行动提示', () => {
    const err = {
      response: {
        status: 404,
        data: { message: 'One or more knowledge bases were not found' },
      },
    }
    const out = localizeSearchError(err) as Error
    expect(out).toBeInstanceOf(Error)
    expect(out.message).toContain('尚未完成挖掘')
    expect(out.message).not.toContain('knowledge bases')
  })

  it('paradigm 自身的 404（非 kb_not_found）不误报为未挖掘', () => {
    const err = {
      response: { status: 404, data: { message: 'paradigm not found' } },
    }
    // 原样上抛（不吞错、不误导）
    expect(localizeSearchError(err)).toBe(err)
  })

  it('no_active_kb_build 404 → 中文可行动提示', () => {
    const err = {
      response: {
        status: 404,
        data: {
          error: 'no_active_kb_build',
          message: 'The selected knowledge bases have no mined content',
        },
      },
    }
    const out = localizeSearchError(err) as Error
    expect(out.message).toContain('尚未完成挖掘')
  })

  it('403 → 权限提示', () => {
    const err = { response: { status: 403, data: {} } }
    const out = localizeSearchError(err) as Error
    expect(out.message).toContain('权限')
  })

  it('未识别错误原样上抛（不吞错）', () => {
    const err = new Error('network down')
    expect(localizeSearchError(err)).toBe(err)
  })
})
