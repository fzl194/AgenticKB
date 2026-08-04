import axios from 'axios'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useServingApi } from '@/api/serving'

type PostCall = { url: string; body: Record<string, unknown> }
type GetCall = { url: string; config: Record<string, unknown> }

function stubClient(): { posts: PostCall[]; gets: GetCall[] } {
  const posts: PostCall[] = []
  const gets: GetCall[] = []
  vi.spyOn(axios, 'create').mockReturnValue({
    interceptors: { request: { use: vi.fn() } },
    post: vi.fn(async (url: string, body: Record<string, unknown>) => {
      posts.push({ url, body })
      return { data: { scope: {}, items: [] } }
    }),
    get: vi.fn(async (url: string, config: Record<string, unknown>) => {
      gets.push({ url, config })
      return {
        data: new Blob(['bytes']),
        headers: { 'content-disposition': "attachment; filename*=UTF-8''spec.pdf" },
      }
    }),
  } as never)
  return { posts, gets }
}

describe('fetchFullText payload', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('posts refs verbatim so type/id come straight off the search result', async () => {
    const { posts } = stubClient()

    await useServingApi().fetchFullText(
      [{ type: 'retrieval_unit', id: 'ru-1' }],
      { domain: 'cloud_core_network' },
    )

    expect(posts).toHaveLength(1)
    expect(posts[0].url).toBe('/api/v1/segments/fulltext')
    expect(posts[0].body).toEqual({
      refs: [{ type: 'retrieval_unit', id: 'ru-1' }],
      domain: 'cloud_core_network',
    })
  })

  it('omits kbIds when the search was domain-wide', async () => {
    const { posts } = stubClient()
    const api = useServingApi()

    await api.fetchFullText([{ type: 'raw_segment', id: 's1' }], { kbIds: [] })
    await api.fetchFullText([{ type: 'raw_segment', id: 's1' }], { kbIds: ['', '  '] })

    expect(posts[0].body).not.toHaveProperty('kbIds')
    expect(posts[1].body).not.toHaveProperty('kbIds')
  })

  it('carries the search scope so ids resolve against the corpus that produced them', async () => {
    // A different scope reports every id as out_of_scope rather than erroring, which is why
    // this has to be the search's kbIds and not whatever the picker currently shows.
    const { posts } = stubClient()

    await useServingApi().fetchFullText(
      [{ type: 'raw_segment', id: 's1' }],
      { kbIds: [' kb1 ', 'kb2'] },
    )

    expect(posts[0].body.kbIds).toEqual(['kb1', 'kb2'])
  })
})

describe('downloadRawFile request', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('goes through axios as a blob rather than a plain link', async () => {
    // The identity header is set by the proxyClient request interceptor; a browser-initiated
    // <a href> navigation skips it entirely and would arrive anonymous, 404-ing private KBs.
    const { gets } = stubClient()

    const { blob, disposition } = await useServingApi()
      .downloadRawFile('doc-7', { domain: 'cloud_core_network' })

    expect(gets[0].url).toBe('/api/v1/documents/doc-7/raw')
    expect(gets[0].config.responseType).toBe('blob')
    expect(blob).toBeInstanceOf(Blob)
    expect(disposition).toContain("filename*=UTF-8''")
  })

  it('serializes kbIds as repeated params, not bracketed ones', async () => {
    // axios defaults to kbIds[]=a, which Spring's @RequestParam List<String> ignores — the
    // request would silently fall back to domain-wide scope and 404 the document.
    const { gets } = stubClient()

    await useServingApi().downloadRawFile('doc-7', { kbIds: ['kb1', 'kb2'] })

    expect(gets[0].config.params).toMatchObject({ kbIds: ['kb1', 'kb2'] })
    expect(gets[0].config.paramsSerializer).toEqual({ indexes: null })
  })

  it('omits kbIds when the search was domain-wide', async () => {
    const { gets } = stubClient()

    await useServingApi().downloadRawFile('doc-7', { domain: 'd1', kbIds: [] })

    expect(gets[0].config.params).not.toHaveProperty('kbIds')
  })
})
