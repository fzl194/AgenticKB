import { describe, expect, it } from 'vitest'
import {
  isActiveRunStatus,
  processedDocumentCount,
  runStatusLabel,
  runStatusTagType,
} from '@/utils/runStatus'

describe('runStatus', () => {
  it('uses the database run status vocabulary', () => {
    expect(runStatusLabel('completed')).toBe('已完成')
    expect(runStatusTagType('completed')).toBe('success')
    expect(runStatusTagType('failed')).toBe('danger')
  })

  it('keeps queued runs polling', () => {
    expect(isActiveRunStatus('queued')).toBe(true)
    expect(isActiveRunStatus('running')).toBe(true)
    expect(isActiveRunStatus('completed')).toBe(false)
  })

  it('counts skipped documents as processed', () => {
    expect(processedDocumentCount({
      committed_count: 2,
      failed_count: 1,
      skipped_count: 7,
    })).toBe(10)
  })
})
