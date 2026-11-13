import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, shallowMount } from '@vue/test-utils'
import type { VueWrapper } from '@vue/test-utils'

const state = vi.hoisted(() => ({
  api: {
    listWikiPages: vi.fn(),
    getWikiPage: vi.fn(),
    listWikiLinks: vi.fn(),
    searchWiki: vi.fn(),
    getWikiToc: vi.fn(),
    getWikiUnitSource: vi.fn(),
    upsertWikiPage: vi.fn(),
    deleteWikiPage: vi.fn(),
    saveWikiToc: vi.fn(),
  },
  ui: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
  uiConfirm: vi.fn(),
  clipboard: { writeText: vi.fn() },
  /** 可覆写的错误文案提取（Q01 弹层「透出后端 message」断言用）。 */
  apiErrorDetail: vi.fn(async (_error?: unknown) => 'mock-error'),
  routerPush: vi.fn(),
}))

vi.mock('@/api/artifacts', () => ({ useArtifactsApi: () => state.api }))
vi.mock('@/api/proxyClient', () => ({
  apiErrorDetail: (error: unknown) => state.apiErrorDetail(error),
}))
vi.mock('vue-router', () => ({ useRouter: () => ({ push: state.routerPush }) }))
vi.mock('element-plus', () => ({
  ElMessage: {
    success: state.ui.success, error: state.ui.error,
    warning: state.ui.warning, info: state.ui.info,
  },
  ElMessageBox: { confirm: state.uiConfirm },
}))

import WikiReaderPanel from '../WikiReaderPanel.vue'
import WikiGraphCanvas from '../WikiGraphCanvas.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import type {
  WikiLink,
  WikiPage,
  WikiPageMeta,
  WikiSearchHit,
  WikiSearchResult,
  WikiUnitSource,
} from '@/types/artifacts'

interface ReaderVm {
  sortedPages: WikiPageMeta[]
  selectedKey: string
  searchQuery: string
  editing: boolean
  editBody: string
  runSearch: () => Promise<void>
  reload: () => Promise<void>
  startEditBody: () => void
  saveBody: () => Promise<void>
  startNewPage: () => void
  removePage: (meta: WikiPageMeta) => Promise<void>
  onDragStart: (key: string) => void
  moveDraggedTo: (sectionIndex: number, insertAt: number) => void
  saveToc: () => Promise<void>
  tocDirty: boolean
}

/** ordinal 乱序给（术语=1 / 首页=2）——目录按 ordinal 排序且自动选中第一页=术语页。 */
const metaPages: WikiPageMeta[] = [
  {
    page_key: 'home', title: '首页', page_type: 'topic', origin: 'manual',
    ordinal: 2, flags: null, aliases: ['主页'],
    updated_at: '2026-10-21T08:00:00+00:00', updated_by: 'u1',
  },
  {
    page_key: 'glossary-mop', title: 'MOP 术语', page_type: 'glossary',
    origin: 'machine', ordinal: 1, flags: null, aliases: [],
    updated_at: '2026-10-21T09:00:00+00:00', updated_by: 'u2',
  },
]

const glossaryPage: WikiPage = {
  page_key: 'glossary-mop', title: 'MOP 术语', page_type: 'glossary',
  origin: 'machine', ordinal: 1, flags: null, aliases: [],
  updated_at: '2026-10-21T09:00:00+00:00', updated_by: 'u2',
  frontmatter: {}, body_md: '术语正文。参见 [[home]]、别名 [[主页]]、断链 [[ghost]]，来源 [[src:unit-9]]',
  provenance: [{ kb_id: 'kb-1', snapshot_id: 'snap-1', unit_ids: ['unit-9', 'unit-10'] }],
}

const homePage: WikiPage = {
  page_key: 'home', title: '首页', page_type: 'topic', origin: 'manual',
  ordinal: 2, flags: null, aliases: ['主页'],
  updated_at: '2026-10-21T08:00:00+00:00', updated_by: 'u1',
  frontmatter: {}, body_md: '首页正文', provenance: [],
}

const outLinks: WikiLink[] = [
  { from_page: 'glossary-mop', to_page: 'home', anchor_text: '首页', status: 'valid' },
  { from_page: 'glossary-mop', to_page: 'gone-page', anchor_text: '已删页', status: 'broken' },
]
const inLinks: WikiLink[] = [
  { from_page: 'home', to_page: 'glossary-mop', anchor_text: '术语', status: 'valid' },
]

function mountReader(canWrite = false): VueWrapper {
  return shallowMount(WikiReaderPanel, {
    props: { artifactId: 'art-1', canWrite },
    global: {
      // el-dialog 桩渲染默认+footer 插槽（Q01 溯源弹层内容可达）
      stubs: {
        ElDialog: {
          // 不硬编码 data-test——组件上的 data-test 经 attrs 透传到各自桩根
          template: '<div class="el-dialog-stub"><slot /><slot name="footer" /></div>',
        },
      },
    },
  })
}

beforeEach(() => {
  // D03.1 信封形态：{revision, pages}（清单只消费 pages）
  state.api.listWikiPages.mockReset().mockResolvedValue({ revision: 3, pages: metaPages })
  state.api.getWikiPage.mockReset().mockImplementation(
    (_id: string, key: string) => Promise.resolve(key === 'home' ? homePage : glossaryPage))
  // D03.1 信封形态：{revision, links}（侧栏只消费 links）
  state.api.listWikiLinks.mockReset().mockImplementation(() =>
    Promise.resolve({ revision: 3, links: [...outLinks, ...inLinks] }))
  state.api.searchWiki.mockReset().mockResolvedValue(
    { revision: 1, degraded_dense: false, results: [] })
  // 运行态目录缺省平铺（无 toc 行/空目录——D03 分节渲染不触发）
  state.api.getWikiToc.mockReset().mockResolvedValue({ sections: [], flat: true, revision: 1 })
  // Q01 溯源回原文：默认拒绝态由各用例覆写
  state.api.getWikiUnitSource.mockReset()
  state.apiErrorDetail.mockReset().mockResolvedValue('mock-error')
  state.api.upsertWikiPage.mockReset().mockResolvedValue(glossaryPage)
  state.api.deleteWikiPage.mockReset().mockResolvedValue(undefined)
  state.api.saveWikiToc.mockReset().mockResolvedValue({ sections: [], updated_at: 'toc-tok-2' })
  state.uiConfirm.mockReset().mockResolvedValue('confirm')
  state.routerPush.mockReset()
  state.ui.success.mockReset()
  state.ui.error.mockReset()
  state.ui.warning.mockReset()
  state.ui.info.mockReset()
  state.clipboard.writeText.mockReset().mockResolvedValue(undefined)
  // clipboard 可用态（jsdom 默认无 navigator.clipboard——逐用例定义/删除）
  Object.defineProperty(navigator, 'clipboard', {
    value: { writeText: state.clipboard.writeText }, configurable: true,
  })
})

describe('WikiReaderPanel', () => {
  it('目录按 ordinal 排序、自动加载第一页并渲染正文（运行态 draft=false）', async () => {
    const wrapper = mountReader()
    await flushPromises()
    const vm = wrapper.vm as unknown as ReaderVm

    // 阅读器读运行态（current_revision 投影，不带 draft）
    expect(state.api.listWikiPages).toHaveBeenCalledWith('art-1', { draft: false })
    expect(vm.sortedPages.map(p => p.page_key)).toEqual(['glossary-mop', 'home'])

    const items = wrapper.findAll('[data-test="wiki-reader-toc-item"]')
    expect(items).toHaveLength(2)
    expect(items[0].text()).toContain('MOP 术语')
    expect(items[0].text()).toContain('术语')       // page_type 徽标
    expect(items[0].classes()).toContain('is-active')   // 第一页自动选中高亮

    expect(state.api.getWikiPage).toHaveBeenCalledWith('art-1', 'glossary-mop', { draft: false })
    const body = wrapper.find('[data-test="wiki-reader-body"]')
    expect(body.exists()).toBe(true)
    // 真实 renderWikiMarkdown 产物（utils/markdown.ts）
    expect(body.find('[data-wiki-page="home"]').exists()).toBe(true)
    expect(body.find('sup.wiki-src[data-unit-id="unit-9"]').exists()).toBe(true)
  })

  it('双链点击切页：page_key 命中 → 加载目标页并高亮', async () => {
    const wrapper = mountReader()
    await flushPromises()

    await wrapper.find('[data-wiki-page="home"]').trigger('click')
    await flushPromises()
    const vm = wrapper.vm as unknown as ReaderVm

    expect(state.api.getWikiPage).toHaveBeenLastCalledWith('art-1', 'home', { draft: false })
    expect(vm.selectedKey).toBe('home')
    expect(wrapper.text()).toContain('首页正文')
  })

  it('双链点击切页：alias 命中（来自目录 metas.aliases）→ 解析到正主页', async () => {
    const wrapper = mountReader()
    await flushPromises()

    // getWikiPage 返回体无顶层 aliases 可信口径——alias 解析只依赖 listWikiPages 的 metas
    await wrapper.find('[data-wiki-page="主页"]').trigger('click')
    await flushPromises()

    expect(state.api.getWikiPage).toHaveBeenLastCalledWith('art-1', 'home', { draft: false })
    expect(state.ui.warning).not.toHaveBeenCalled()
  })

  it('双链点击未命中 → warning 且不发起加载', async () => {
    const wrapper = mountReader()
    await flushPromises()

    await wrapper.find('[data-wiki-page="ghost"]').trigger('click')
    await flushPromises()

    expect(state.ui.warning).toHaveBeenCalledWith('目标页面不存在')
    expect(state.api.getWikiPage).toHaveBeenCalledTimes(1)   // 仍是初始那一次
  })

  it('溯源角标点击复制 unit_id；clipboard 不可用降级 info 展示', async () => {
    const wrapper = mountReader()
    await flushPromises()

    await wrapper.find('sup.wiki-src[data-unit-id="unit-9"]').trigger('click')
    await flushPromises()
    expect(state.clipboard.writeText).toHaveBeenCalledWith('unit-9')
    expect(state.ui.success).toHaveBeenCalledWith('已复制切片 ID')

    // 降级：无 navigator.clipboard（如非安全上下文）
    Object.defineProperty(navigator, 'clipboard', { value: undefined, configurable: true })
    await wrapper.find('sup.wiki-src[data-unit-id="unit-9"]').trigger('click')
    await flushPromises()
    expect(state.ui.info).toHaveBeenCalledWith(expect.stringContaining('unit-9'))
  })

  it('右栏近邻：当前页为中心的力导向图（一阶出入+断链伪节点）+ 溯源 kb/快照/切片计数', async () => {
    const wrapper = mountReader()
    await flushPromises()

    // 图数据：中心 glossary-mop + 输入 home（home→glossary）+ 输出 home/gone-page（glossary→…）
    const graph = wrapper.find('[data-test="wiki-reader-graph"]')
    expect(graph.exists()).toBe(true)
    const canvas = wrapper.getComponent(WikiGraphCanvas)
    const option = canvas.props('option') as { series: { data: { key: string; isBroken?: boolean; fixed?: boolean }[] }[] }
    const nodes = option.series[0].data
    const keys = nodes.map(n => n.key).sort()
    expect(keys).toEqual(['glossary-mop', 'gone-page', 'home'])
    expect(nodes.find(n => n.key === 'glossary-mop')?.fixed).toBe(true)   // 中心固定
    expect(nodes.find(n => n.key === 'gone-page')?.isBroken).toBe(true)   // 断链伪节点
    // 近邻计数（一阶输入 1 · 输出 2）——栏标题口径
    expect(wrapper.find('.wiki-reader__ctx').text()).toContain('一阶出入 3')

    // 溯源（先断言——下步切页后 provenance 随新页变化）
    const prov = wrapper.find('[data-test="wiki-reader-provenance"]')
    expect(prov.exists()).toBe(true)
    expect(prov.text()).toContain('kb-1')
    expect(prov.text()).toContain('snap-1')
    expect(prov.text()).toContain('2')   // unit_ids 计数

    // 图节点点击 → 切页
    canvas.vm.$emit('node-click', 'home')
    await flushPromises()
    expect(state.api.getWikiPage).toHaveBeenLastCalledWith('art-1', 'home', { draft: false })
  })

  it('未发布（listWikiPages 空）→ EmptyState 尚未发布版本', async () => {
    state.api.listWikiPages.mockResolvedValueOnce({ revision: 0, pages: [] })
    const wrapper = mountReader()
    await flushPromises()

    const empty = wrapper.findComponent(EmptyState)
    expect(empty.exists()).toBe(true)
    expect(empty.props('text')).toContain('尚未发布版本')
    expect(wrapper.find('[data-test="wiki-reader-body"]').exists()).toBe(false)
  })

  it('清单加载失败 → EmptyState 错误文案，不白屏', async () => {
    state.api.listWikiPages.mockRejectedValueOnce(new Error('boom'))
    const wrapper = mountReader()
    await flushPromises()

    const empty = wrapper.findComponent(EmptyState)
    expect(empty.exists()).toBe(true)
    expect(empty.props('text')).toContain('加载失败')
  })

  it('reload：外部命令式刷新（发布后父级可调）重拉清单与当前页', async () => {
    const wrapper = mountReader()
    await flushPromises()
    const vm = wrapper.vm as unknown as ReaderVm

    await vm.reload()
    await flushPromises()

    expect(state.api.listWikiPages).toHaveBeenCalledTimes(2)
    // 当前选中页（glossary-mop）在 reload 后被重新加载
    expect(state.api.getWikiPage).toHaveBeenLastCalledWith('art-1', 'glossary-mop', { draft: false })
  })
})

// ── 检索（63号批次4：搜索框 → mining 转发 serving 三通道）──

/** 检索命中样例：page_key 直配已发布页 + 未命中页各一条。 */
const searchHits: WikiSearchHit[] = [
  { page_key: 'home', title: '首页', unit_key: 'u-1',
    snippet: '首页正文的相关片段……', score: 0.91, channels: ['wiki_dense'] },
  { page_key: 'ghost', title: '已删页', unit_key: 'u-2',
    snippet: '第二条片段', score: 0.4, channels: ['wiki_keyword'] },
]

function resultOf(hits: WikiSearchHit[], degraded = false): WikiSearchResult {
  return { view: 'wiki_search', revision: 3, degraded_dense: degraded, results: hits }
}

describe('WikiReaderPanel 检索（批次4）', () => {
  it('回车/按钮提交：默认参数 searchWiki(id, {q}) + 结果渲染（title/snippet，不显示 score）', async () => {
    state.api.searchWiki.mockResolvedValueOnce(resultOf(searchHits))
    const wrapper = mountReader()
    await flushPromises()
    const vm = wrapper.vm as unknown as ReaderVm

    vm.searchQuery = '首页'
    await vm.runSearch()
    await flushPromises()

    // 默认 fusion（后端缺省）——前端只带 q，不带 mode/top_k
    expect(state.api.searchWiki).toHaveBeenCalledWith('art-1', { q: '首页' })
    const hits = wrapper.findAll('[data-test="wiki-reader-search-hit"]')
    expect(hits).toHaveLength(2)
    expect(hits[0].text()).toContain('首页')
    expect(hits[0].text()).toContain('首页正文的相关片段')
    expect(hits[0].text()).not.toContain('0.91')   // score 不展示
    // 零命中提示不出现
    expect(wrapper.find('[data-test="wiki-reader-search-none"]').exists()).toBe(false)
  })

  it('点击结果跳页：命中已发布页 → 加载目标页；未命中页 → warning 提示不加载', async () => {
    state.api.searchWiki.mockResolvedValueOnce(resultOf(searchHits))
    const wrapper = mountReader()
    await flushPromises()
    const vm = wrapper.vm as unknown as ReaderVm
    vm.searchQuery = '首页'
    await vm.runSearch()
    await flushPromises()

    const hits = wrapper.findAll('[data-test="wiki-reader-search-hit"]')
    await hits[0].trigger('click')
    await flushPromises()
    expect(state.api.getWikiPage).toHaveBeenLastCalledWith('art-1', 'home', { draft: false })

    await hits[1].trigger('click')
    await flushPromises()
    expect(state.ui.warning).toHaveBeenCalledWith('目标页面不存在')
    expect(state.api.getWikiPage).toHaveBeenLastCalledWith('art-1', 'home', { draft: false })
  })

  it('请求序号防护：连续提交时迟到旧响应不覆盖新结果', async () => {
    let resolveFirst!: (value: WikiSearchResult) => void
    state.api.searchWiki
      .mockImplementationOnce(() => new Promise<WikiSearchResult>(resolve => {
        resolveFirst = resolve
      }))
      .mockResolvedValueOnce(resultOf([searchHits[1]]))   // 新提交=已删页一条
    const wrapper = mountReader()
    await flushPromises()
    const vm = wrapper.vm as unknown as ReaderVm

    vm.searchQuery = '第一次'
    const first = vm.runSearch()
    vm.searchQuery = '第二次'
    const second = vm.runSearch()
    resolveFirst(resultOf([searchHits[0]]))   // 旧请求迟到返回（首页那条）
    await first
    await second
    await flushPromises()

    const hits = wrapper.findAll('[data-test="wiki-reader-search-hit"]')
    expect(hits).toHaveLength(1)
    expect(hits[0].text()).toContain('已删页')      // 新结果胜出
    expect(hits[0].text()).not.toContain('首页正文') // 旧结果被丢弃
    expect(state.api.searchWiki).toHaveBeenNthCalledWith(1, 'art-1', { q: '第一次' })
    expect(state.api.searchWiki).toHaveBeenNthCalledWith(2, 'art-1', { q: '第二次' })
  })

  it('清空输入 → 作废在飞请求并清空结果与零命中提示', async () => {
    state.api.searchWiki.mockResolvedValueOnce(resultOf(searchHits))
    const wrapper = mountReader()
    await flushPromises()
    const vm = wrapper.vm as unknown as ReaderVm
    vm.searchQuery = '首页'
    await vm.runSearch()
    await flushPromises()
    expect(wrapper.findAll('[data-test="wiki-reader-search-hit"]')).toHaveLength(2)

    vm.searchQuery = ''   // 清空触发 watcher
    await flushPromises()
    expect(wrapper.findAll('[data-test="wiki-reader-search-hit"]')).toHaveLength(0)
    expect(wrapper.find('[data-test="wiki-reader-search-none"]').exists()).toBe(false)
  })

  it('degraded_dense=true → 向量降级提示；零命中 → 无匹配结果提示', async () => {
    state.api.searchWiki.mockResolvedValueOnce(resultOf([], true))
    const wrapper = mountReader()
    await flushPromises()
    const vm = wrapper.vm as unknown as ReaderVm

    vm.searchQuery = '不存在的内容'
    await vm.runSearch()
    await flushPromises()

    expect(state.ui.warning).toHaveBeenCalledWith(expect.stringContaining('向量'))
    expect(wrapper.findAll('[data-test="wiki-reader-search-hit"]')).toHaveLength(0)
    expect(wrapper.find('[data-test="wiki-reader-search-none"]').text()).toContain('无匹配结果')
  })

  it('检索失败 → 错误提示且不渲染结果', async () => {
    state.api.searchWiki.mockRejectedValueOnce(new Error('boom'))
    const wrapper = mountReader()
    await flushPromises()
    const vm = wrapper.vm as unknown as ReaderVm

    vm.searchQuery = '首页'
    await vm.runSearch()
    await flushPromises()

    expect(state.ui.error).toHaveBeenCalledWith(expect.stringContaining('检索失败'))
    expect(wrapper.findAll('[data-test="wiki-reader-search-hit"]')).toHaveLength(0)
    expect(wrapper.find('[data-test="wiki-reader-search-none"]').exists()).toBe(false)
  })
})

// ── 运行态分节目录（66号 D03：目录栏按 runtime toc 分节渲染）──

describe('WikiReaderPanel 分节目录（D03）', () => {
  it('runtime toc 两节 → 节标题分组渲染；节序/节内键序优先于 ordinal；点击页键切页', async () => {
    // home（ordinal 2）排在第一节——目录声明的节序+页键序是唯一顺序口径
    state.api.getWikiToc.mockResolvedValueOnce({
      sections: [
        { section_title: '进阶', page_keys: ['ghost-page', 'home'] },   // ghost 不在已发布页中 → 滤除
        { section_title: '基础', page_keys: ['glossary-mop'] },
      ],
      flat: false, revision: 3,
    })
    const wrapper = mountReader()
    await flushPromises()

    // 运行态缺省口径（不带 draft/revision 参数）
    expect(state.api.getWikiToc).toHaveBeenCalledWith('art-1', { draft: false })

    const titles = wrapper.findAll('[data-test="wiki-reader-toc-section"]')
    expect(titles).toHaveLength(2)
    expect(titles[0].text()).toContain('进阶')
    expect(titles[1].text()).toContain('基础')

    const items = wrapper.findAll('[data-test="wiki-reader-toc-item"]')
    expect(items).toHaveLength(2)
    expect(items[0].text()).toContain('首页')          // 节序优先，非 ordinal 首页在前
    expect(items[1].text()).toContain('MOP 术语')
    expect(items[0].classes()).toContain('is-active')  // 仍自动选中第一页

    // 页键点击切页（沿用 selectPage）
    await items[1].trigger('click')
    await flushPromises()
    expect(state.api.getWikiPage).toHaveBeenLastCalledWith('art-1', 'glossary-mop', { draft: false })
  })

  it('页不在任何节 → 归「其他」尾节', async () => {
    state.api.getWikiToc.mockResolvedValueOnce({
      sections: [{ section_title: '基础', page_keys: ['glossary-mop'] }],
      flat: false, revision: 3,
    })
    const wrapper = mountReader()
    await flushPromises()

    const titles = wrapper.findAll('[data-test="wiki-reader-toc-section"]')
    expect(titles).toHaveLength(2)
    expect(titles[1].text()).toContain('其他')
    expect(titles[1].text()).toContain('首页')   // home 未入节 → 其他节内
  })

  it('flat:true → 平铺渲染（无节标题），保持 ordinal 排序', async () => {
    const wrapper = mountReader()   // beforeEach 默认 mock：{sections:[], flat:true}
    await flushPromises()

    expect(wrapper.findAll('[data-test="wiki-reader-toc-section"]')).toHaveLength(0)
    const items = wrapper.findAll('[data-test="wiki-reader-toc-item"]')
    expect(items).toHaveLength(2)
    expect(items[0].text()).toContain('MOP 术语')   // ordinal 1 在前（平铺口径不变）
  })

  it('toc 404（rev0 反探测等）→ 回落平铺且不报错', async () => {
    state.api.getWikiToc.mockRejectedValueOnce({ response: { status: 404 } })
    const wrapper = mountReader()
    await flushPromises()

    expect(wrapper.findAll('[data-test="wiki-reader-toc-section"]')).toHaveLength(0)
    expect(wrapper.findAll('[data-test="wiki-reader-toc-item"]')).toHaveLength(2)
    expect(state.ui.error).not.toHaveBeenCalled()
  })
})

// ── Q01 溯源回原文（66号批次5 Task 5.3）：溯源切片点击 → unit-source 原文
// 截读弹层 + 「在知识库中查看」跳转；无权/失效（404 反探测单码）显示后端
// 文案不崩。

describe('WikiReaderPanel 溯源回原文（Q01）', () => {
  const unitSource: WikiUnitSource = {
    unit_id: 'unit-9', content_text: 'MOP 是移动对外门户……（原文片段，后端截 500 字）',
    kb_id: 'kb-1', snapshot_id: 'snap-1', section_ref: 's-0',
  }

  it('点击溯源切片 → 调 unit-source 并在弹层渲染原文与 kb/快照定位 + 「在知识库中查看」跳 kb 详情', async () => {
    state.api.getWikiUnitSource.mockResolvedValueOnce(unitSource)
    const wrapper = mountReader()
    await flushPromises()

    const units = wrapper.findAll('[data-test="wiki-reader-prov-unit"]')
    expect(units).toHaveLength(2)   // unit-9 / unit-10
    expect(units[0].text()).toContain('unit-9')

    await units[0].trigger('click')
    await flushPromises()

    // 阅读器运行态口径：只带 unit_id（draft=false 缺省）
    expect(state.api.getWikiUnitSource).toHaveBeenCalledWith('art-1', 'unit-9')
    const dialog = wrapper.find('[data-test="wiki-unit-source-dialog"]')
    expect(dialog.exists()).toBe(true)
    expect(dialog.text()).toContain('MOP 是移动对外门户')
    expect(dialog.text()).toContain('kb-1')
    expect(dialog.text()).toContain('snap-1')

    // 「在知识库中查看」→ /kb/{kb_id}（KB 详情路由先例）
    await dialog.find('[data-test="wiki-unit-source-kb-link"]').trigger('click')
    expect(state.routerPush).toHaveBeenCalledWith('/kb/kb-1')
  })

  it('无权/失效（404 unit_not_found 同面）→ 弹层显示后端文案，不崩不白屏', async () => {
    state.apiErrorDetail.mockResolvedValueOnce('来源单元不存在或无权限访问')
    state.api.getWikiUnitSource.mockRejectedValueOnce({
      response: {
        status: 404,
        data: { detail: { code: 'unit_not_found', message: '来源单元不存在或无权限访问' } },
      },
    })
    const wrapper = mountReader()
    await flushPromises()

    await wrapper.findAll('[data-test="wiki-reader-prov-unit"]')[0].trigger('click')
    await flushPromises()

    const dialog = wrapper.find('[data-test="wiki-unit-source-dialog"]')
    expect(dialog.exists()).toBe(true)
    expect(dialog.find('[data-test="wiki-unit-source-error"]').text())
      .toContain('来源单元不存在或无权限访问')
    // 不崩：三栏照常渲染（目录/正文仍在）
    expect(wrapper.findAll('[data-test="wiki-reader-toc-item"]')).toHaveLength(2)
    expect(wrapper.find('[data-test="wiki-reader-body"]').exists()).toBe(true)
  })

  it('切换点击另一切片 → 重新拉取并覆盖弹层内容（迟到的旧响应不残留）', async () => {
    state.api.getWikiUnitSource
      .mockResolvedValueOnce(unitSource)
      .mockResolvedValueOnce({ ...unitSource, unit_id: 'unit-10', content_text: '第二条切片的原文' })
    const wrapper = mountReader()
    await flushPromises()

    const units = wrapper.findAll('[data-test="wiki-reader-prov-unit"]')
    await units[0].trigger('click')
    await flushPromises()
    await units[1].trigger('click')
    await flushPromises()

    expect(state.api.getWikiUnitSource).toHaveBeenNthCalledWith(2, 'art-1', 'unit-10')
    const dialog = wrapper.find('[data-test="wiki-unit-source-dialog"]')
    expect(dialog.text()).toContain('第二条切片的原文')
    expect(dialog.text()).not.toContain('MOP 是移动对外门户')   // 旧内容被覆盖
  })
})


// ── 设计态（canWrite=true：draft 口径 + 编辑正文/页面属性/新建/删除/目录拖拽）──

describe('WikiReaderPanel 设计态（统一视图写入口）', () => {
  it('draft 口径：清单/页面/目录/引用全部带 draft=true；默认仍是阅读渲染', async () => {
    const wrapper = mountReader(true)
    await flushPromises()

    expect(state.api.listWikiPages).toHaveBeenCalledWith('art-1', { draft: true })
    expect(state.api.listWikiLinks).toHaveBeenCalledWith('art-1', { draft: true })
    expect(state.api.getWikiToc).toHaveBeenCalledWith('art-1', { draft: true })
    expect(state.api.getWikiPage).toHaveBeenLastCalledWith('art-1', 'glossary-mop', { draft: true })

    // 默认阅读（非编辑态）：渲染正文 + 编辑入口按钮存在
    expect(wrapper.find('[data-test="wiki-reader-body"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="wiki-body-editor"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="wiki-edit-body"]').exists()).toBe(true)
    // 目录写入口：新建页/新增节/删除页；拖拽开启（draggable）
    expect(wrapper.find('[data-test="wiki-new-page"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="wiki-toc-add-section"]').exists()).toBe(true)
    expect(wrapper.findAll('[data-test="wiki-reader-toc-item"]')[0].attributes('draggable')).toBe('true')
  })

  it('编辑正文：进入 markdown 编辑态 → upsert 整页（属性沿用）→ 刷新回阅读', async () => {
    state.api.upsertWikiPage.mockResolvedValueOnce(glossaryPage)
    const wrapper = mountReader(true)
    await flushPromises()
    const vm = wrapper.vm as unknown as ReaderVm

    vm.startEditBody()
    await flushPromises()
    expect(wrapper.find('[data-test="wiki-body-editor"]').exists()).toBe(true)

    vm.editBody = '# 改后的正文'
    await vm.saveBody()
    await flushPromises()

    expect(state.api.upsertWikiPage).toHaveBeenCalledWith('art-1', 'glossary-mop', {
      title: 'MOP 术语', page_type: 'glossary', frontmatter: {},
      body_md: '# 改后的正文', ordinal: 1,
      expected_updated_at: undefined,
    })
    expect(wrapper.vm.$emit).toBeDefined()   // emit changed 已由父级接线（此处仅烟测不崩）
    expect(vm.editing).toBe(false)
    expect(wrapper.find('[data-test="wiki-reader-body"]').exists()).toBe(true)
  })

  it('页面属性弹窗：新建页（缺省 page_key=标题）→ upsert 创建并跳转', async () => {
    const created = { ...glossaryPage, page_key: 'new-page', title: '新页' }
    state.api.upsertWikiPage.mockResolvedValueOnce(created)
    const wrapper = mountReader(true)
    await flushPromises()
    const vm = wrapper.vm as unknown as ReaderVm

    vm.startNewPage()
    await flushPromises()
    expect(wrapper.find('[data-test="wiki-page-key"]').exists()).toBe(true)

    // 表单字段经 vm 直填（el-input 桩不透传 v-model）
    const vmAny = wrapper.vm as unknown as { propsForm: { title: string; pageType: string; ordinal: number; aliases: string; tags: string } }
    vmAny.propsForm.title = '新页'
    const saveBtn = wrapper.find('[data-test="wiki-props-save"]')
    await saveBtn.trigger('click')
    await flushPromises()

    expect(state.api.upsertWikiPage).toHaveBeenCalledWith('art-1', '新页', expect.objectContaining({
      title: '新页', page_type: 'topic', body_md: '# 新页\n\n请填写知识正文。',
    }))
    expect(state.api.getWikiPage).toHaveBeenLastCalledWith('art-1', 'new-page', { draft: true })
  })

  it('页面属性改名：正文首行 h1 同步回写（阅读面「标题即正文 h1」契约，对抗审查 HIGH）', async () => {
    // 旧正文首行 h1 = 旧标题 → 改名后 upsert 的 body_md 首行必须换成新标题
    state.api.getWikiPage.mockResolvedValueOnce({
      ...glossaryPage, body_md: '# MOP 术语\n\n术语正文。参见 [[home]]',
    })
    state.api.upsertWikiPage.mockResolvedValueOnce({
      ...glossaryPage, title: '新术语', body_md: '# 新术语\n\n术语正文。参见 [[home]]',
    })
    const wrapper = mountReader(true)
    await flushPromises()
    const vm = wrapper.vm as unknown as {
      propsForm: { title: string }
      openProps: () => void
      saveProps: () => Promise<void>
    }

    vm.openProps()
    await flushPromises()
    vm.propsForm.title = '新术语'
    await vm.saveProps()
    await flushPromises()

    expect(state.api.upsertWikiPage).toHaveBeenCalledWith('art-1', 'glossary-mop', expect.objectContaining({
      title: '新术语',
      body_md: expect.stringMatching(/^# 新术语\n/),
    }))
  })

  it('删除页：确认后删除并刷新清单', async () => {
    const wrapper = mountReader(true)
    await flushPromises()
    const vm = wrapper.vm as unknown as ReaderVm

    await vm.removePage(metaPages[0])   // 删首页
    await flushPromises()

    expect(state.api.deleteWikiPage).toHaveBeenCalledWith('art-1', 'home', undefined)
    expect(state.ui.success).toHaveBeenCalledWith('已删除')
    expect(state.api.listWikiPages).toHaveBeenCalledTimes(2)   // 删除后刷新
  })

  it('目录拖拽调整：拖到目标节目标位 → dirty → 保存 PUT 节构', async () => {
    state.api.getWikiToc.mockResolvedValueOnce({
      sections: [
        { section_title: '基础', page_keys: ['glossary-mop'] },
        { section_title: '进阶', page_keys: ['home'] },
      ],
      flat: false, revision: 3, updated_at: 'toc-tok-1',
    })
    const wrapper = mountReader(true)
    await flushPromises()
    const vm = wrapper.vm as unknown as ReaderVm

    // glossary-mop 从「基础」拖到「进阶」尾部（节 1，位 1）
    vm.onDragStart('glossary-mop')
    vm.moveDraggedTo(1, 1)
    await flushPromises()
    expect(vm.tocDirty).toBe(true)
    expect(wrapper.find('[data-test="wiki-toc-save"]').exists()).toBe(true)

    await vm.saveToc()
    await flushPromises()
    expect(state.api.saveWikiToc).toHaveBeenCalledWith('art-1', {
      sections: [
        { section_title: '基础', page_keys: [] },
        { section_title: '进阶', page_keys: ['home', 'glossary-mop'] },
      ],
      expected_updated_at: 'toc-tok-1',
    })
    expect(vm.tocDirty).toBe(false)
  })
})
