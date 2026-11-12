import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, shallowMount } from '@vue/test-utils'
import type { VueWrapper } from '@vue/test-utils'

const state = vi.hoisted(() => ({
  api: {
    listWikiPages: vi.fn(),
    listWikiLinks: vi.fn(),
  },
}))

vi.mock('@/api/artifacts', () => ({ useArtifactsApi: () => state.api }))
vi.mock('@/api/proxyClient', () => ({ apiErrorDetail: async () => 'mock-error' }))
vi.mock('element-plus', () => ({ ElMessage: { info: vi.fn() } }))

import WikiGraphPanel from '../WikiGraphPanel.vue'
import WikiGraphCanvas from '../WikiGraphCanvas.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import { buildWikiNeighborhood } from '@/utils/wikiGraph'
import type {
  WikiLink,
  WikiLinksEnvelope,
  WikiPageMeta,
  WikiPagesEnvelope,
} from '@/types/artifacts'

interface GraphVm {
  searchQuery: string
  reload: () => Promise<void>
  locateSearch: () => void
}

/** 三页样例：home/solo/res-pool。 */
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

/** 六条全 valid 边（home→res-pool 两条平行边=不同锚文本）。 */
const linksFixture: WikiLink[] = [
  { from_page: 'home', to_page: 'res-pool', anchor_text: '资源池', status: 'valid' },
  { from_page: 'home', to_page: 'solo', anchor_text: '术语', status: 'valid' },
  { from_page: 'res-pool', to_page: 'home', anchor_text: '回到首页', status: 'valid' },
  { from_page: 'solo', to_page: 'res-pool', anchor_text: '相关特性', status: 'valid' },
  { from_page: 'home', to_page: 'res-pool', anchor_text: '再看资源池', status: 'valid' },
  { from_page: 'res-pool', to_page: 'solo', anchor_text: '术语表', status: 'valid' },
]

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

function mountGraph(draft = false, centerPageKey?: string): VueWrapper {
  return shallowMount(WikiGraphPanel, {
    props: { artifactId: 'art-1', draft, ...(centerPageKey ? { centerPageKey } : {}) },
  })
}

interface CanvasNode {
  key: string
  pageTitle?: string
  fixed?: boolean
  isBroken?: boolean
}
interface CanvasEdge { source: string; target: string; isBroken?: boolean; refCount?: number }

/** 画布桩上的节点/边（浅渲染：echarts 画布不打 DOM，断言传入 option）。 */
function graphData(wrapper: VueWrapper): { nodes: CanvasNode[]; edges: CanvasEdge[] } {
  const option = wrapper.getComponent(WikiGraphCanvas).props('option') as {
    series: { data: CanvasNode[]; links: CanvasEdge[] }[]
  }
  return { nodes: option.series[0].data, edges: option.series[0].links }
}

const nodeByKey = (nodes: CanvasNode[], key: string) => nodes.find(node => node.key === key)

/** 画布节点点击/双击（桩组件 emit 转译层）。 */
function canvasEmit(wrapper: VueWrapper, event: 'node-click' | 'node-dblclick', key: string): void {
  wrapper.getComponent(WikiGraphCanvas).vm.$emit(event, key)
}

beforeEach(() => {
  state.api.listWikiPages.mockReset().mockResolvedValue(
    { revision: 3, pages: pagesFixture })
  state.api.listWikiLinks.mockReset().mockResolvedValue(
    { revision: 3, links: linksFixture })
})

// ── 纯函数：邻域模型（角色/折叠计数/断链/方向纯净）──

describe('buildWikiNeighborhood 纯函数', () => {
  it('中心=首页缺省；一阶输入/输出角色正确；平行边去重但 refCount 保留', () => {
    const model = buildWikiNeighborhood(pagesFixture, linksFixture)
    expect(model.centerKey).toBe('home')
    const roles = new Map(model.nodes.map(n => [n.id, n.role]))
    expect(roles.get('home')).toBe('center')
    expect(roles.get('res-pool')).toBe('input1')          // res-pool→home（既是输入又含出链回中心）
    expect(roles.get('solo')).toBe('output1')             // home→solo
    // 边去重：home->res-pool 平行两条合并 refCount=2；只保留触中心或已展开的边
    const parallel = model.edges.find(e => e.source === 'home' && e.target === 'res-pool')
    expect(parallel?.refCount).toBe(2)
    expect(model.totals).toEqual({ input1: 1, output1: 2, broken: 0 })
    // 全图计数（上下文面板口径，平行引用计入）
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
    // a 无上游（count 0 无徽章）；c 下游 1 个（d）→ 折叠计数 1
    expect(collapsed.nodes.find(n => n.id === 'a')?.folded).toEqual({ dir: 'in', count: 0 })
    expect(collapsed.nodes.find(n => n.id === 'c')?.folded).toEqual({ dir: 'out', count: 1 })

    const expanded = buildWikiNeighborhood(chainPages, chainLinks, 'b', new Set(['c']))
    expect(expanded.nodes.map(n => n.id).sort()).toEqual(['a', 'b', 'c', 'd'])
    const d = expanded.nodes.find(n => n.id === 'd')
    expect(d?.role).toBe('output2')
    expect(expanded.edges.map(e => `${e.source}->${e.target}`)).toContain('c->d')
  })

  it('方向纯净：输入节点展开只加上游，不加旁枝下游', () => {
    // x→a→c（输入链）+ a→y（a 的旁枝出链）+ c→b
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
    // res-pool 折叠计数含下游断链目标
    expect(collapsed.nodes.find(n => n.id === 'res-pool')?.folded).toEqual({ dir: 'out', count: 1 })

    const expanded = buildWikiNeighborhood(pages, links, 'home', new Set(['res-pool']))
    const ghost2 = expanded.nodes.find(n => n.id === '二级幽灵')
    expect(ghost2?.isBroken).toBe(true)
    expect(ghost2?.role).toBe('output2')
    expect(expanded.totals.broken).toBe(2)
  })
})

// ── 组件：加载/渲染/交互（echarts 画布以桩断言 option）──

describe('WikiGraphPanel', () => {
  it('渲染：邻域模型进画布 option；运行态缺省不带 draft 读正式版', async () => {
    const wrapper = mountGraph()
    await flushPromises()

    expect(state.api.listWikiPages).toHaveBeenCalledWith('art-1', { draft: false })
    expect(state.api.listWikiLinks).toHaveBeenCalledWith('art-1', { draft: false })

    const { nodes } = graphData(wrapper)
    expect(nodes.map(n => n.key).sort()).toEqual(['home', 'res-pool', 'solo'])
    // 中心节点固定（fixed）供力导向围绕
    expect(nodeByKey(nodes, 'home')?.fixed).toBe(true)
  })

  it('设计态模式：两请求都带 draft=true（页面守卫在入口，组件不重复鉴权）', async () => {
    const wrapper = mountGraph(true)
    await flushPromises()

    expect(state.api.listWikiPages).toHaveBeenCalledWith('art-1', { draft: true })
    expect(state.api.listWikiLinks).toHaveBeenCalledWith('art-1', { draft: true })
    expect(graphData(wrapper).nodes).toHaveLength(3)
  })

  it('锚定页 prop：centerPageKey 有效 → 初始即以该页为中心（统计随新邻域）', async () => {
    const wrapper = mountGraph(false, 'res-pool')
    await flushPromises()

    expect(graphData(wrapper).nodes.map(n => n.key).sort()).toEqual(['home', 'res-pool', 'solo'])
    // res-pool 为中心：上游来源 {home, solo} → 输入 2；下游去向 {home, solo} → 输出 2
    const stats = wrapper.find('[data-test="wiki-graph-stats"]').text()
    expect(stats).toContain('一阶输入 2')
    expect(stats).toContain('一阶输出 2')
  })

  it('断链：中心直连 broken 目标渲染伪节点 + 点击不选中不可打开', async () => {
    state.api.listWikiLinks.mockResolvedValue({
      revision: 3,
      links: [
        { from_page: 'home', to_page: 'res-pool', anchor_text: 'a', status: 'valid' },
        { from_page: 'home', to_page: '幽灵页', anchor_text: '断链', status: 'broken' },
      ],
    })
    const wrapper = mountGraph()
    await flushPromises()

    const { nodes } = graphData(wrapper)
    const ghost = nodeByKey(nodes, '幽灵页')
    expect(ghost).toBeDefined()
    expect(ghost?.isBroken).toBe(true)

    canvasEmit(wrapper, 'node-click', '幽灵页')
    await flushPromises()
    const detailAfterBrokenClick = wrapper.find('[data-test="wiki-graph-detail"]')
    expect(detailAfterBrokenClick.text()).toContain('点击节点查看')
    expect(detailAfterBrokenClick.find('[data-test="wiki-graph-open"]').exists()).toBe(false)
    expect(wrapper.emitted('open-page')).toBeUndefined()
  })

  it('点击节点 → 详情区（标题/页型/origin/出链数/被引用数）+ 打开页面 emit', async () => {
    const wrapper = mountGraph()
    await flushPromises()

    canvasEmit(wrapper, 'node-click', 'home')
    await flushPromises()

    const detail = wrapper.find('[data-test="wiki-graph-detail"]')
    expect(detail.exists()).toBe(true)
    const text = detail.text()
    expect(text).toContain('首页')
    expect(text).toContain('主题')     // 页型中文标记
    expect(text).toContain('手工')     // origin 标记（manual 中文化）
    expect(text).toContain('3')        // 出链数
    expect(text).toContain('1')        // 被引用数

    await wrapper.find('[data-test="wiki-graph-open"]').trigger('click')
    expect(wrapper.emitted('open-page')).toEqual([['home']])
  })

  it('二阶展开/收起：面板按钮加节点加边，收起还原；双击节点重定中心', async () => {
    state.api.listWikiPages.mockResolvedValue({
      revision: 3, pages: ['a', 'b', 'c', 'd'].map(key => page(key)),
    })
    state.api.listWikiLinks.mockResolvedValue({
      revision: 3, links: [link('a', 'b'), link('b', 'c'), link('c', 'd')],
    })
    const wrapper = mountGraph(false, 'b')
    await flushPromises()
    expect(graphData(wrapper).nodes).toHaveLength(3)   // b 中心 + a + c

    // 选中一阶输出 c → 展开二阶
    canvasEmit(wrapper, 'node-click', 'c')
    await flushPromises()
    await wrapper.find('[data-test="wiki-graph-expand"]').trigger('click')
    await flushPromises()
    expect(graphData(wrapper).nodes.map(n => n.key).sort()).toEqual(['a', 'b', 'c', 'd'])
    expect(graphData(wrapper).edges.map(e => `${e.source}->${e.target}`)).toContain('c->d')

    await wrapper.find('[data-test="wiki-graph-collapse"]').trigger('click')
    await flushPromises()
    expect(graphData(wrapper).nodes).toHaveLength(3)

    // 双击 a → 重定中心（新邻域 a+b，二阶全收起）
    canvasEmit(wrapper, 'node-dblclick', 'a')
    await flushPromises()
    expect(graphData(wrapper).nodes.map(n => n.key).sort()).toEqual(['a', 'b'])
  })

  it('定位搜索：标题匹配 → 重定中心并选中；无匹配不动视图', async () => {
    const wrapper = mountGraph()
    await flushPromises()
    const vm = wrapper.vm as unknown as GraphVm

    vm.searchQuery = '资源池'
    vm.locateSearch()
    await flushPromises()
    expect(wrapper.find('[data-test="wiki-graph-stats"]').text()).toContain('一阶输入 2')
    expect(wrapper.find('[data-test="wiki-graph-detail"]').text()).toContain('资源池管理特性')

    vm.searchQuery = '不存在页面'
    vm.locateSearch()
    await flushPromises()
    // 无匹配 → 不动当前视图
    expect(wrapper.find('[data-test="wiki-graph-detail"]').text()).toContain('资源池管理特性')
  })

  it('混版防护：两响应 revision 不一致 → 自动重拉一次对齐（不报错）', async () => {
    state.api.listWikiPages.mockReset()
      .mockResolvedValueOnce({ revision: 2, pages: pagesFixture })
      .mockResolvedValue({ revision: 3, pages: pagesFixture })
    state.api.listWikiLinks.mockReset()
      .mockResolvedValueOnce({ revision: 1, links: linksFixture })   // 首对不一致（请求间发生发布）
      .mockResolvedValue({ revision: 3, links: linksFixture })       // 重拉对齐
    const wrapper = mountGraph()
    await flushPromises()

    expect(state.api.listWikiPages).toHaveBeenCalledTimes(2)
    expect(state.api.listWikiLinks).toHaveBeenCalledTimes(2)
    expect(graphData(wrapper).nodes).toHaveLength(3)
    expect(wrapper.text()).not.toContain('版本切换中')
  })

  it('混版防护：重拉后仍不一致 → 错误态「版本切换中，请重试」，不再第三次请求', async () => {
    state.api.listWikiPages.mockResolvedValue({ revision: 2, pages: pagesFixture })
    state.api.listWikiLinks.mockResolvedValue({ revision: 3, links: linksFixture })
    const wrapper = mountGraph()
    await flushPromises()

    expect(state.api.listWikiPages).toHaveBeenCalledTimes(2)   // 首拉+重拉一次，封顶
    // 错误态非空白：EmptyState 文案 + 无画布
    expect(wrapper.findComponent(EmptyState).props('text'))
      .toContain('版本切换中，请重试')
    expect(wrapper.findComponent(WikiGraphCanvas).exists()).toBe(false)
  })

  it('并发守卫（审查 #2）：artifactId 快切时旧响应晚到被丢，不串台', async () => {
    const newPages: WikiPageMeta[] = [page('new-page', '新制品页')]
    let resolveOldPages!: (value: WikiPagesEnvelope) => void
    let resolveOldLinks!: (value: WikiLinksEnvelope) => void
    state.api.listWikiPages.mockReset()
      .mockImplementationOnce(() => new Promise<WikiPagesEnvelope>(resolve => {
        resolveOldPages = resolve
      }))   // 旧制品请求挂起（慢）
      .mockResolvedValue({ revision: 5, pages: newPages })
    state.api.listWikiLinks.mockReset()
      .mockImplementationOnce(() => new Promise<WikiLinksEnvelope>(resolve => {
        resolveOldLinks = resolve
      }))
      .mockResolvedValue({ revision: 5, links: [] })

    const wrapper = mountGraph()
    await flushPromises()   // 首次请求在飞（未落地）

    await wrapper.setProps({ artifactId: 'art-2' })   // 快切 → watch 触发第二次 reload
    await flushPromises()
    expect(graphData(wrapper).nodes.map(n => n.key)).toEqual(['new-page'])

    resolveOldPages({ revision: 1, pages: pagesFixture })   // 旧制品慢响应迟到
    resolveOldLinks({ revision: 1, links: linksFixture })
    await flushPromises()

    expect(graphData(wrapper).nodes.map(n => n.key)).toEqual(['new-page'])   // 旧数据不落地
    expect(state.api.listWikiPages).toHaveBeenCalledTimes(2)
  })

  it('边界：空 Wiki（0 页）→ 空态提示，不渲染画布', async () => {
    state.api.listWikiPages.mockResolvedValue({ revision: 0, pages: [] })
    state.api.listWikiLinks.mockResolvedValue({ revision: 0, links: [] })
    const wrapper = mountGraph()
    await flushPromises()

    const empty = wrapper.findComponent(EmptyState)
    expect(empty.exists()).toBe(true)
    expect(empty.props('text')).toContain('暂无页面')
    expect(wrapper.findComponent(WikiGraphCanvas).exists()).toBe(false)
  })

  it('边界：无权/加载失败 → 错误态非空白 + 重试入口重拉', async () => {
    state.api.listWikiPages
      .mockRejectedValueOnce({ response: { status: 403 } })
      .mockResolvedValue({ revision: 3, pages: pagesFixture })
    const wrapper = mountGraph()
    await flushPromises()

    expect(wrapper.findComponent(EmptyState).props('text')).toContain('加载失败')
    expect(wrapper.findComponent(WikiGraphCanvas).exists()).toBe(false)
    const retry = wrapper.find('[data-test="wiki-graph-retry"]')
    expect(retry.exists()).toBe(true)

    await retry.trigger('click')
    await flushPromises()
    expect(state.api.listWikiPages).toHaveBeenCalledTimes(2)
    expect(graphData(wrapper).nodes).toHaveLength(3)
  })

  it('只读：无任何写操作入口（无新建/保存/删除/发布按钮）', async () => {
    const wrapper = mountGraph(true)
    await flushPromises()

    const buttonLabels = wrapper.findAll('button').map(b => b.text()).join('|')
    for (const banned of ['新建', '保存', '删除', '发布']) {
      expect(buttonLabels).not.toContain(banned)
    }
  })
})
