
import { afterEach, describe, expect, it, vi } from 'vitest'

import { apiErrorDetail } from '@/api/proxyClient'
import { filenameFromDisposition, saveBlob } from '@/utils/download'

describe('download helpers', () => {
  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it('parses_utf8_filename_star', () => {
    const header = "attachment; filename=report.pdf; FILENAME*=UTF-8''%E6%8A%A5%E5%91%8A.pdf"

    expect(filenameFromDisposition(header, 'fallback.pdf')).toBe('报告.pdf')
  })

  it('falls_back_to_quoted_filename', () => {
    const header = 'attachment; filename="quarterly report.pdf"'

    expect(filenameFromDisposition(header, 'fallback.pdf')).toBe('quarterly report.pdf')
  })

  it('falls_back_to_filename_when_filename_star_is_malformed', () => {
    const header = "attachment; filename=report.pdf; filename*=UTF-8''%E6%ZZ"

    expect(filenameFromDisposition(header, 'fallback.pdf')).toBe('report.pdf')
  })

  it('falls_back_to_document_name', () => {
    expect(filenameFromDisposition(null, 'source document.pdf')).toBe('source document.pdf')
  })

  it('uses_cross_platform_basename_and_removes_control_and_windows_invalid_characters', () => {
    const header = 'attachment; filename="C:\\incoming\\report\r\n:\u0000?*.pdf"'

    expect(filenameFromDisposition(header, 'fallback.pdf')).toBe('report.pdf')
  })

  it('guarantees_a_non_empty_filename', () => {
    expect(filenameFromDisposition('attachment; filename="<>:\\|?*"', '\r\n')).toBe('download')
  })

  it('revokes_object_url_after_saving_blob', () => {
    const createObjectURL = vi.fn(() => 'blob:test-url')
    const revokeObjectURL = vi.fn()
    vi.stubGlobal('URL', { createObjectURL, revokeObjectURL })
    const appendChild = vi.spyOn(document.body, 'appendChild')
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})

    saveBlob(new Blob(['contents']), 'report.pdf')

    expect(createObjectURL).toHaveBeenCalledOnce()
    expect(appendChild).toHaveBeenCalledOnce()
    expect(click).toHaveBeenCalledOnce()
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:test-url')
    expect(document.querySelector('a[download="report.pdf"]')).toBeNull()
  })

  it('removes_anchor_and_revokes_object_url_when_click_throws', () => {
    const revokeObjectURL = vi.fn()
    vi.stubGlobal('URL', {
      createObjectURL: vi.fn(() => 'blob:test-url'),
      revokeObjectURL,
    })
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {
      throw new Error('click failed')
    })

    expect(() => saveBlob(new Blob(['contents']), 'report.pdf')).toThrow('click failed')
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:test-url')
    expect(document.querySelector('a[download="report.pdf"]')).toBeNull()
  })
})

describe('apiErrorDetail', () => {
  it('extracts_fastapi_detail_from_object_error', async () => {
    const error = { response: { data: { detail: '文档已下架' } } }

    await expect(apiErrorDetail(error)).resolves.toBe('文档已下架')
  })

  it('extracts_fastapi_detail_from_json_string_error', async () => {
    const error = { response: { data: JSON.stringify({ detail: '批次已下架' }) } }

    await expect(apiErrorDetail(error)).resolves.toBe('批次已下架')
  })

  it('returns_raw_string_error', async () => {
    const error = { response: { data: '网关错误' } }

    await expect(apiErrorDetail(error)).resolves.toBe('网关错误')
  })

  it('extracts_fastapi_detail_from_blob_error', async () => {
    const error = {
      response: {
        data: new Blob([JSON.stringify({ detail: '文档已下架' })], { type: 'application/json' }),
      },
    }

    await expect(apiErrorDetail(error)).resolves.toBe('文档已下架')
  })

  it('uses_an_ordinary_error_message', async () => {
    await expect(apiErrorDetail(new Error('network failed'))).resolves.toBe('network failed')
  })

  it('uses_a_stable_fallback', async () => {
    await expect(apiErrorDetail({})).resolves.toBe('请求失败')
  })

  it('extracts_fastapi_typed_detail_objects', async () => {
    const error = {
      response: {
        data: {
          detail: {
            code: 'workflow_not_found',
            message: '挖掘范式不存在',
          },
        },
      },
    }
    await expect(apiErrorDetail(error)).resolves.toBe('挖掘范式不存在')
  })
})
