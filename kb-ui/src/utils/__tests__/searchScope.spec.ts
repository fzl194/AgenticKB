import { describe, it, expect } from 'vitest'
import {
  DOMAIN_RELEASE_SCOPE,
  canSearchWithScope,
  defaultScopeSelection,
  reconcileScopeSelection,
  resolveRequestKbIds,
  scopeFromQuery,
} from '@/utils/searchScope'

const KBS = [{ id: 'kb-a' }, { id: 'kb-b' }, { id: 'kb-c' }]

describe('默认范围', () => {
  it('默认选中全部可见知识库', () => {
    expect(defaultScopeSelection(KBS)).toEqual(['kb-a', 'kb-b', 'kb-c'])
  })

  it('一个库都没有时是空选择——而不是悄悄变成「全域」', () => {
    expect(defaultScopeSelection([])).toEqual([])
    expect(canSearchWithScope([])).toBe(false)
  })

  it('有 active release 也不默认选「域级发布」', () => {
    // 那个选项只是给混合部署留的退路，不是默认范围
    expect(defaultScopeSelection(KBS)).not.toContain(DOMAIN_RELEASE_SCOPE)
  })
})

describe('发给后端的 kbIds', () => {
  it('选了知识库 → 原样显式传', () => {
    expect(resolveRequestKbIds(['kb-a', 'kb-b'])).toEqual(['kb-a', 'kb-b'])
  })

  it('「域级发布」→ 空数组（serving.ts 会省掉该键 = 域级 release 分支）', () => {
    expect(resolveRequestKbIds([DOMAIN_RELEASE_SCOPE])).toEqual([])
  })

  it('空数组这条路径只能由「域级发布」进入', () => {
    // 清空选择时 canSearchWithScope 为 false，请求根本不会发出——堵死 no_active_release
    expect(canSearchWithScope([])).toBe(false)
    expect(canSearchWithScope([DOMAIN_RELEASE_SCOPE])).toBe(true)
    expect(canSearchWithScope(['kb-a'])).toBe(true)
  })

  it('过滤掉空串，避免拼出 kbIds=""', () => {
    expect(resolveRequestKbIds(['kb-a', ''])).toEqual(['kb-a'])
  })
})

describe('「域级发布」与知识库互斥', () => {
  it('勾上「域级发布」→ 它独占，原有知识库被清掉', () => {
    const next = reconcileScopeSelection(['kb-a', 'kb-b'], ['kb-a', 'kb-b', DOMAIN_RELEASE_SCOPE])
    expect(next).toEqual([DOMAIN_RELEASE_SCOPE])
  })

  it('已选「域级发布」时再勾知识库 → 让位给知识库', () => {
    const next = reconcileScopeSelection([DOMAIN_RELEASE_SCOPE], [DOMAIN_RELEASE_SCOPE, 'kb-a'])
    expect(next).toEqual(['kb-a'])
  })

  it('只动知识库时不干预', () => {
    expect(reconcileScopeSelection(['kb-a'], ['kb-a', 'kb-b'])).toEqual(['kb-a', 'kb-b'])
    expect(reconcileScopeSelection(['kb-a', 'kb-b'], ['kb-b'])).toEqual(['kb-b'])
  })

  it('保持「域级发布」单选不变', () => {
    expect(reconcileScopeSelection([DOMAIN_RELEASE_SCOPE], [DOMAIN_RELEASE_SCOPE]))
      .toEqual([DOMAIN_RELEASE_SCOPE])
  })

  it('清空到只剩「域级发布」也允许（用户逐个取消知识库）', () => {
    const next = reconcileScopeSelection([DOMAIN_RELEASE_SCOPE, 'kb-a'], [DOMAIN_RELEASE_SCOPE])
    expect(next).toEqual([DOMAIN_RELEASE_SCOPE])
  })
})

describe('从 URL 还原范围（首页跳转过来）', () => {
  it('逗号分隔与重复参数两种形状都认', () => {
    expect(scopeFromQuery('kb-a,kb-b', KBS)).toEqual(['kb-a', 'kb-b'])
    expect(scopeFromQuery(['kb-a', 'kb-b'], KBS)).toEqual(['kb-a', 'kb-b'])
  })

  it('没带 kbIds → 回落默认全选', () => {
    expect(scopeFromQuery(undefined, KBS)).toEqual(['kb-a', 'kb-b', 'kb-c'])
    expect(scopeFromQuery('', KBS)).toEqual(['kb-a', 'kb-b', 'kb-c'])
  })

  it('丢弃当前域看不见的 id——旧链接原样发出会让整单 404 kb_not_found', () => {
    expect(scopeFromQuery('kb-a,kb-gone', KBS)).toEqual(['kb-a'])
  })

  it('全部失效 → 回落默认全选，而不是留空让人搜不了', () => {
    expect(scopeFromQuery('kb-gone,kb-also-gone', KBS)).toEqual(['kb-a', 'kb-b', 'kb-c'])
  })
})
