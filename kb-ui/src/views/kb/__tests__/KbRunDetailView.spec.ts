/**
 * A0-6（34 号 §P0）：Run 详情页阶段展示不再伪造固定流程。
 *
 * 要钉的行为：
 * - trace 加载失败且无真实阶段事件 → 显示「任务阶段暂不可用」+ 重试入口
 *   （不回退到含实体抽取/本体/落图的固定 12 阶段）；
 * - 历史 legacy Run（真实记录过实体阶段事件）→ 显示「历史任务」标识，
 *   阶段按真实事件渲染；
 * - 正常新 Run（trace 有 workflow）→ MiningWorkflowTrace（现状不回归）。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

const miningStore = vi.hoisted(() => ({
  currentRun: null as Record<string, unknown> | null,
  error: null as string | null,
  progress: null as Record<string, unknown> | null,
  stages: [] as Array<Record<string, unknown>>,
  documents: [] as Array<Record<string, unknown>>,
  documentsTotal: 0,
  documentsPage: 1,
  fetchProgress: vi.fn(),
  fetchRunDetail: vi.fn(),
  fetchRunDocuments: vi.fn(),
  clearCurrentRun: vi.fn(),
}))
const miningApi = vi.hoisted(() => ({
  getRunTrace: vi.fn(),
  resumeRun: vi.fn(),
}))
const domainRef = vi.hoisted(() => ({ current: 'odn' }))

vi.mock('@/stores/mining', () => ({ useMiningStore: () => miningStore }))
vi.mock('@/stores/domain', () => ({
  useDomainStore: () => ({ get currentDomain() { return domainRef.current } }),
}))
vi.mock('@/api/mining', () => ({ useMiningApi: () => miningApi }))

import KbRunDetailView from '@/views/kb/KbRunDetailView.vue'

async function mountView() {
  const wrapper = mount(KbRunDetailView, {
    props: { kbId: 'kb-1', runId: 'run-1' },
  })
  await flushPromises()
  return wrapper
}

function run(overrides: Record<string, unknown> = {}) {
  return {
    id: 'run-1', status: 'completed', total_documents: 2,
    started_at: '2026-09-03T00:00:00Z', finished_at: '2026-09-03T00:05:00Z',
    ...overrides,
  }
}

function ev(stage: string) {
  return { id: `${stage}-1`, stage, status: 'completed', created_at: '2026-09-03T00:00:00Z', duration_ms: 5 }
}

describe('A0-6 Run 详情阶段展示', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    miningStore.currentRun = run()
    miningStore.error = null
    miningStore.progress = { total: 2, completed: 2, failed: 0, skipped: 0, processing: 0, progress_percent: 100 }
    miningStore.stages = []
    miningStore.documents = []
    miningStore.documentsTotal = 0
    miningApi.getRunTrace.mockResolvedValue({ workflow: null })
  })

  it('trace 不可用且无阶段事件 → 显示「任务阶段暂不可用」+ 重试，不显示实体/落图固定阶段', async () => {
    miningApi.getRunTrace.mockRejectedValue(new Error('boom'))
    const wrapper = await mountView()

    const unavailable = wrapper.find('[data-testid="stages-unavailable"]')
    expect(unavailable.exists()).toBe(true)
    expect(unavailable.text()).toContain('任务阶段暂不可用')
    expect(unavailable.text()).toContain('重试')

    const flow = wrapper.findComponent({ name: 'PipelineFlow' })
    if (flow.exists()) {
      expect(flow.text()).not.toContain('实体抽取')
      expect(flow.text()).not.toContain('落图')
    }
    const body = wrapper.text()
    expect(body).not.toContain('实体抽取')
    expect(body).not.toContain('落图')
  })

  it('重试按钮再次拉取 trace（恢复后阶段可见）', async () => {
    miningApi.getRunTrace.mockRejectedValueOnce(new Error('boom'))
    const wrapper = await mountView()
    const retry = wrapper.find('[data-testid="stages-retry"]')
    expect(retry.exists()).toBe(true)
    miningApi.getRunTrace.mockResolvedValue({ workflow: null })
    await retry.trigger('click')
    await flushPromises()
    expect(miningApi.getRunTrace.mock.calls.length).toBeGreaterThanOrEqual(2)
  })

  it('历史 legacy Run（真实实体阶段事件）→ 显示「历史任务」标识与真实阶段', async () => {
    miningStore.stages = [ev('parse'), ev('entity_extract'), ev('graph_write')]
    const wrapper = await mountView()

    expect(wrapper.find('[data-testid="legacy-run-tag"]').exists()).toBe(true)
    const body = wrapper.text()
    expect(body).toContain('实体抽取')
    expect(body).toContain('落图')
  })

  it('新链 Run（无实体事件）→ 不显示历史任务标识与实体阶段', async () => {
    miningStore.stages = [ev('parse'), ev('segment'), ev('embedding')]
    const wrapper = await mountView()

    expect(wrapper.find('[data-testid="legacy-run-tag"]').exists()).toBe(false)
    const body = wrapper.text()
    expect(body).not.toContain('实体抽取')
    expect(body).not.toContain('落图')
  })

  it('完整现行链事件集（含 enrich/discourse 无条件阶段）→ 不误标历史任务', async () => {
    // run.py 现行无条件阶段列表（has_ontology=False 的 KB Run）
    miningStore.stages = [
      ev('parse'), ev('segment'), ev('enrich'), ev('discourse'),
      ev('retrieval_units'), ev('embedding'), ev('db_write'),
    ]
    const wrapper = await mountView()

    expect(wrapper.find('[data-testid="legacy-run-tag"]').exists()).toBe(false)
    // 段落理解/语篇分析是当前链阶段，照常渲染
    const body = wrapper.text()
    expect(body).toContain('段落理解')
    expect(body).toContain('语篇分析')
  })

  it('trace 有 workflow → MiningWorkflowTrace（现状不回归）', async () => {
    miningApi.getRunTrace.mockResolvedValue({
      workflow: { id: 'wf-1', name: '官方混合', version: 2, graph_hash: 'g1', nodes: [] },
      node_events: [],
    })
    const wrapper = await mountView()
    expect(wrapper.findComponent({ name: 'MiningWorkflowTrace' }).exists()).toBe(true)
  })
})
