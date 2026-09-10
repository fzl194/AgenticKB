import { beforeEach, describe, expect, it, vi } from 'vitest'

const post = vi.hoisted(() => vi.fn())
vi.mock('@/api/proxyClient', () => ({
  createProxyClient: () => ({ post }),
  extractOne: (value: unknown) => value,
  extractItems: (value: unknown) => value,
}))
import { useKbApi } from '@/api/kb'

describe('replace document content', () => {
  beforeEach(() => vi.clearAllMocks())

  it('sends bytes and the observed revision without renaming, moving or mining', async () => {
    const document = { id: 'doc-1', document_name: 'original.md', content_revision: 5 }
    post.mockResolvedValue({ data: document })
    const file = new File(['new contents'], 'selected.md', { type: 'text/markdown' })
    await expect(useKbApi().replaceDocumentContent('kb-1', 'doc-1', file, 4)).resolves.toEqual(document)
    expect(post).toHaveBeenCalledOnce()
    const [path, body] = post.mock.calls[0]!
    expect(path).toBe('/api/kb/kb-1/documents/doc-1/content')
    expect(body.get('file')).toBe(file)
    expect(body.get('expected_revision')).toBe('4')
    expect([...body.keys()]).toEqual(['file', 'expected_revision'])
  })

  it.each([-1, 1.5, Number.NaN])('rejects invalid revision %s without sending bytes', async (revision) => {
    await expect(useKbApi().replaceDocumentContent('kb-1', 'doc-1', new File(['x'], 'x.md'), revision)).rejects.toThrow()
    expect(post).not.toHaveBeenCalled()
  })
})
