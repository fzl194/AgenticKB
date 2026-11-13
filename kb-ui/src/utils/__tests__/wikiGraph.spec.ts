import { describe, expect, it } from 'vitest'
import { buildWikiNeighborhood } from '@/utils/wikiGraph'
import type { WikiLink, WikiPageMeta } from '@/types/artifacts'

function page(key: string, title = `页${key}`): WikiPageMeta {
  return {
    page_key: key, title, page_type: 'topic', origin: 'manual',
    ordinal: 1, flags: null, aliases: [],
    updated_at: '2026-11-01T08:00:00+00:00', updated_by: 'u1',
  }
}

function link(from: string, to: string, status: 'valid' | 'broken' = 'valid'): WikiLink {
  return { from_page: from, to_page: to, anchor_text: '', status }
}

describe('buildWikiNeighborhood 纯函数（邻域自我中心图模型）', () => {
  const pagesFixture: WikiPageMeta[] = [
    {
      page_key: 'home', title: '首页', page_type: 'topic', origin: 'manual',
      ordinal: 1, flags: null, aliases: [],
      updated_at: '2026-11-01T08:00:00+00:00', updated_by: 'u1',
    },
    {
      page_key: 'solo', title: '独立页', page_type: 'glossary', origin: 'machine',
      ordinal: 2, flags: null, aliases: [],
      updated_at: '2026-11-01T08:10:00+00:00', updated_by: 'u2',
    },
    {
      page_key: 'res-pool', title: '资源池管理特性', page_type: 'topic',
      origin: 'machine', ordinal: 1001, flags: null, aliases: [],
      updated_at: '2026-11-01T08:20:00+00:00', updated_by: 'u2',
    },
  ]

  const linksFixture: WikiLink[] = [
    { from_page: 'home', to_page: 'res-pool', anchor_text: '资源池', status: 'valid' },
    { from_page: 'home', to_page: 'solo', anchor_text: '术语', status: 'valid' },
    { from_page: 'res-pool', to_page: 'home', anchor_text: '回到首页', status: 'valid' },
    { from_page: 'solo', to_page: 'res-pool', anchor_text: '相关特性', status: 'valid' },
    { from_page: 'home', to_page: 'res-pool', anchor_text: '再看资源池', status: 'valid' },
    { from_page: 'res-pool', to_page: 'solo', anchor_text: '术语表', status: 'valid' },
  ]

  it('中心=首页缺省；一阶输入/输出角色正确；平行边去重但 refCount 保留', () => {
    const model = buildWikiNeighborhood(pagesFixture, linksFixture)
    expect(model.centerKey).toBe('home')
    const roles = new Map(model.nodes.map(n => [n.id, n.role]))
    expect(roles.get('home')).toBe('center')
    expect(roles.get('res-pool')).toBe('input1')          // res-pool→home（既是输入又含出链回中心）
    expect(roles.get('solo')).toBe('output1')             // home→solo
    const parallel = model.edges.find(e => e.source === 'home' && e.target === 'res-pool')
    expect(parallel?.refCount).toBe(2)
    expect(model.totals).toEqual({ input1: 1, output1: 2, broken: 0 })
    const center = model.nodes.find(n => n.id === 'home')
    expect(center?.outCount).toBe(3)
    expect(center?.inCount).toBe(1)
  })

  it('中心键无效 → 回落首页；空 wiki → 空模型', () => {
    const fallback = buildWikiNeighborhood(pagesFixture, linksFixture, '不存在')
    expect(fallback.centerKey).toBe('home')
    const empty = buildWikiNeighborhood([], [])
    expect(empty.nodes).toEqual([])
    expect(empty.centerKey).toBe('')
  })

  it('二阶折叠计数与展开（链 a→b→c→d，中心 b）', () => {
    const chainPages = ['a', 'b', 'c', 'd'].map(key => page(key))
    const chainLinks = [link('a', 'b'), link('b', 'c'), link('c', 'd')]

    const collapsed = buildWikiNeighborhood(chainPages, chainLinks, 'b')
    expect(collapsed.nodes.map(n => n.id).sort()).toEqual(['a', 'b', 'c'])
    expect(collapsed.edges.map(e => `${e.source}->${e.target}`).sort()).toEqual(['a->b', 'b->c'])
    expect(collapsed.nodes.find(n => n.id === 'a')?.folded).toEqual({ dir: 'in', count: 0 })
    expect(collapsed.nodes.find(n => n.id === 'c')?.folded).toEqual({ dir: 'out', count: 1 })

    const expanded = buildWikiNeighborhood(chainPages, chainLinks, 'b', new Set(['c']))
    expect(expanded.nodes.map(n => n.id).sort()).toEqual(['a', 'b', 'c', 'd'])
    expect(expanded.nodes.find(n => n.id === 'd')?.role).toBe('output2')
    expect(expanded.edges.map(e => `${e.source}->${e.target}`)).toContain('c->d')
  })

  it('方向纯净：输入节点展开只加上游，不加旁枝下游', () => {
    const pages = [page('x'), page('a'), page('y'), page('c'), page('b')]
    const links = [link('x', 'a'), link('a', 'c'), link('a', 'y'), link('c', 'b')]

    const collapsed = buildWikiNeighborhood(pages, links, 'c')
    expect(collapsed.nodes.map(n => n.id).sort()).toEqual(['a', 'b', 'c'])
    const a = collapsed.nodes.find(n => n.id === 'a')
    expect(a?.role).toBe('input1')
    expect(a?.folded).toEqual({ dir: 'in', count: 1 })   // 只数上游 x，不数旁枝 y

    const expanded = buildWikiNeighborhood(pages, links, 'c', new Set(['a']))
    expect(expanded.nodes.map(n => n.id).sort()).toEqual(['a', 'b', 'c', 'x'])
    expect(expanded.edges.map(e => `${e.source}->${e.target}`)).not.toContain('a->y')
  })

  it('断链：输出侧伪节点（不可打开）；中心直连断链与二阶断链都成节点', () => {
    const pages = [page('home'), page('res-pool')]
    const links = [
      link('home', 'res-pool'),
      link('home', '幽灵页', 'broken'),        // 中心直连断链 → output1 伪节点
      link('res-pool', '二级幽灵', 'broken'),  // 输出节点下游断链 → 展开后 output2 伪节点
    ]
    const collapsed = buildWikiNeighborhood(pages, links, 'home')
    expect(collapsed.totals.broken).toBe(1)
    const ghost = collapsed.nodes.find(n => n.id === '幽灵页')
    expect(ghost?.isBroken).toBe(true)
    expect(ghost?.role).toBe('output1')
    expect(ghost?.inCount).toBe(1)
    expect(collapsed.nodes.find(n => n.id === 'res-pool')?.folded).toEqual({ dir: 'out', count: 1 })

    const expanded = buildWikiNeighborhood(pages, links, 'home', new Set(['res-pool']))
    const ghost2 = expanded.nodes.find(n => n.id === '二级幽灵')
    expect(ghost2?.isBroken).toBe(true)
    expect(ghost2?.role).toBe('output2')
    expect(expanded.totals.broken).toBe(2)
  })
})
