import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, shallowMount } from '@vue/test-utils'
import type { VueWrapper } from '@vue/test-utils'

const state = vi.hoisted(() => ({
  api: { listRevisions: vi.fn(), rollback: vi.fn(), listMembers: vi.fn() },
  ui: { confirm: vi.fn(), success: vi.fn(), error: vi.fn() },
}))

vi.mock('@/api/artifacts', () => ({ useArtifactsApi: () => state.api }))
vi.mock('@/api/proxyClient', () => ({ apiErrorDetail: async () => 'mock-error' }))
vi.mock('element-plus', () => ({
  ElMessage: { success: state.ui.success, error: state.ui.error },
  ElMessageBox: { confirm: state.ui.confirm },
}))

import ArtifactVersionsPanel from '../ArtifactVersionsPanel.vue'
import type { RevisionInfo } from '@/types/artifacts'

interface VersionsVm {
  revisions: RevisionInfo[]
  loading: boolean
  /** V01：计数列头（wiki=页数 / table=行数）。 */
  countColumnLabel: string
  loadRevisions: () => Promise<void>
}

const revisions: RevisionInfo[] = [
  {
    artifact_id: 'art-1', revision: 2, row_count: 120,
    change_summary: '补充两页', published_by: 'u2',
    published_at: '2026-10-20T09:00:00+00:00',
  },
  {
    artifact_id: 'art-1', revision: 1, row_count: 118,
    change_summary: '首版', published_by: 'u1',
    published_at: '2026-10-18T09:00:00+00:00',
  },
]

function mountPanel(kind?: 'table' | 'wiki'): VueWrapper {
  return shallowMount(ArtifactVersionsPanel, {
    props: {
      artifactId: 'art-1',
      canWrite: true,
      currentRevision: 2,
      ...(kind ? { kind } : {}),
    },
    global: {
      // 列头桩渲染 label（V01 断言落点）+ default 作用域插槽以固定 row 喂一行
      // （「当前」tag / 回滚按钮的条件渲染可断言；真实多行循环属 el-table 内部）
      stubs: {
        ElTable: { template: '<div class="el-table-stub"><slot /><slot name="empty" /></div>' },
        ElTableColumn: {
          props: ['label', 'prop'],
          template: '<div class="el-table-column-stub">{{ label }}|{{ prop }}<slot :row="scopeRow" /></div>',
          data: () => ({ scopeRow: revisions[0] }),
        },
        ElTag: { template: '<span class="el-tag-stub"><slot /></span>' },
        ElButton: { template: '<span class="el-button-stub"><slot /></span>' },
      },
      directives: { loading: {} },
    },
  })
}

beforeEach(() => {
  state.api.listRevisions.mockReset().mockResolvedValue(revisions)
  state.api.rollback.mockReset().mockResolvedValue({ revision: 1 })
  // 发布人目录（2026-11-13）：u2→陈工 命中映射；u1 无成员行兜底原值
  state.api.listMembers.mockReset().mockResolvedValue([
    { user_id: 'u2', role: 'editor', username: 'chen', user_name: '陈工' },
  ])
  state.ui.confirm.mockReset().mockResolvedValue('confirm')
  state.ui.success.mockReset()
  state.ui.error.mockReset()
})

describe('ArtifactVersionsPanel', () => {
  it('loads revisions with the current-revision tag on the pointer row', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    const vm = wrapper.vm as unknown as VersionsVm

    expect(state.api.listRevisions).toHaveBeenCalledWith('art-1')
    expect(vm.revisions).toHaveLength(2)
    // r2=当前指针（success tag 文本）；r1 无 tag
    const tags = wrapper.findAll('.el-tag-stub').map(t => t.text())
    expect(tags).toEqual(['当前'])
  })

  it('发布人目录映射：成员命中显示 user_name（r2 行 u2 → 陈工）', async () => {
    const wrapper = mountPanel()
    await flushPromises()

    expect(state.api.listMembers).toHaveBeenCalledWith('art-1')
    expect(wrapper.text()).toContain('陈工')   // 桩固定渲染 revisions[0]（published_by=u2）
  })

  it('V01：table 制品计数列头为「行数」（row_count=行数口径）', async () => {
    const wrapper = mountPanel('table')
    await flushPromises()

    expect(wrapper.text()).toContain('行数')
    expect(wrapper.text()).not.toContain('页数')
    expect((wrapper.vm as unknown as VersionsVm).countColumnLabel).toBe('行数')
  })

  it("V01：wiki 制品计数列头为「页数」（db_wiki.publish 写 page_count 进 row_count）", async () => {
    const wrapper = mountPanel('wiki')
    await flushPromises()

    expect(wrapper.text()).toContain('页数')
    expect(wrapper.text()).not.toContain('行数')
    expect((wrapper.vm as unknown as VersionsVm).countColumnLabel).toBe('页数')
  })

  it('V01：缺省（未传 kind）保持「行数」——调用点不传不回归', async () => {
    const wrapper = mountPanel()
    await flushPromises()

    expect((wrapper.vm as unknown as VersionsVm).countColumnLabel).toBe('行数')
  })
})
