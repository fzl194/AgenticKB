/**
 * 文件管理器分页 + 文件夹多选（2026-09-08 用户反馈修复）：
 * - 文件多时分页（默认 50/页，服务端 limit/offset + count 总数）；
 * - 文件夹行也有复选框：勾选 = 递归选中该文件夹（含子文件夹）全部文件，
 *   批量挖掘/删除覆盖文件夹内容；
 * - 表头全选 = 当前页（服务端分页下不能假装全库）。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

const kbApi = vi.hoisted(() => ({
  listFolders: vi.fn(),
  listDocuments: vi.fn(),
  countDocuments: vi.fn(),
  mineKb: vi.fn(),
  deleteDocument: vi.fn(),
  deleteFolder: vi.fn(),
}))

vi.mock('@/api/kb', () => ({ useKbApi: () => kbApi }))
vi.mock('@/api/proxyClient', () => ({
  apiErrorDetail: async () => '网络错误',
  createProxyClient: () => ({ get: vi.fn(), post: vi.fn(), delete: vi.fn() }),
}))
vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn() }),
  useRoute: () => ({ query: {}, params: {} }),
}))

import KbFileManager from '@/components/kb/KbFileManager.vue'

// el-checkbox 在测试环境未注册全局组件——本地桩渲染原生 input 并转发 change
const ElCheckboxStub = {
  name: 'ElCheckbox',
  props: { modelValue: { type: Boolean, default: false } },
  emits: ['change', 'update:modelValue'],
  setup(_: Record<string, unknown>, { emit }: { emit: (e: string, v: boolean) => void }) {
    const onChange = (ev: Event) => {
      const checked = (ev.target as HTMLInputElement).checked
      emit('update:modelValue', checked)
      emit('change', checked)
    }
    return { onChange }
  },
  template: `<input type="checkbox" :checked="modelValue" @change="onChange" />`,
}

// setup.ts 把 ElPagination 全局 stub 成 true（不透传事件）——本地桩转发生事件
const ElPaginationStub = {
  name: 'ElPagination',
  props: ['currentPage', 'pageSize', 'total'],
  emits: ['update:current-page', 'update:page-size', 'current-change', 'size-change'],
  template: `<div class="el-pagination-stub" data-testid="fm-pagination">
    <span class="pager-total">共 {{ total }} 条</span>
    <button class="pager-next" @click="$emit('update:current-page', (currentPage ?? 1) + 1);
      $emit('current-change', (currentPage ?? 1) + 1)">下一页</button>
  </div>`,
}

function file(id: string, name: string) {
  return {
    id, document_name: name, file_size: 1024, created_at: '2026-09-01T00:00:00Z',
    modified_at: '2026-09-01T00:00:00Z', status: 'mined', directory_path: '',
  }
}

function folder(id: string, name: string, path: string, parent_id: string | null) {
  return { id, name, path, parent_id, created_at: '2026-09-01T00:00:00Z' }
}

/** 根目录：1 个子文件夹（内嵌 Q1）+ 60 个文件（超过默认页大小 50） */
function seedRoot(opts: { files?: number } = {}) {
  const n = opts.files ?? 60
  // 真实语义：listFolders 不返回虚拟根——parent_id=null 即根下第一层
  kbApi.listFolders.mockResolvedValue([
    folder('f-2025', '2025 报表', '2025 报表', null),
    folder('f-2025-q1', 'Q1', '2025 报表/Q1', 'f-2025'),
  ])
  kbApi.listDocuments.mockImplementation(
    async (_kb: string, dir: string | undefined, limit = 200, offset = 0) => {
      if (dir === '2025 报表/Q1') {
        const all = [file('q1-1', 'q1-一月.xlsx'), file('q1-2', 'q1-二月.xlsx')]
        return all.slice(offset, offset + limit)
      }
      if (dir === '2025 报表') return [file('r-1', '年报.xlsx')]
      return Array.from({ length: n }, (_, i) => file(`root-${i}`, `手册-${i}.md`))
        .slice(offset, offset + limit)
    },
  )
  kbApi.countDocuments.mockImplementation(
    async (_kb: string, dir: string | undefined) =>
      dir === '2025 报表/Q1' ? 2 : dir === '2025 报表' ? 1 : n,
  )
}

async function mountFm() {
  const wrapper = mount(KbFileManager, {
    props: { kbId: 'kb-1', canWrite: true, workflowId: 'wf-1', active: true },
    global: { stubs: { ElPagination: ElPaginationStub, ElCheckbox: ElCheckboxStub, ElTooltip: { template: '<span><slot /></span>' } } },
  })
  await flushPromises()
  return wrapper
}

describe('文件管理器：默认分页', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    seedRoot()
  })

  it('首页只请求 50 条（服务端 limit/offset），分页器展示总数', async () => {
    const wrapper = await mountFm()
    expect(kbApi.listDocuments).toHaveBeenCalledWith('kb-1', '', 50, 0)
    // 行渲染 = 当前页 50 条（不是全量 60，也不是旧版静默截断 200）
    expect(wrapper.findAll('.fm__row--file')).toHaveLength(50)
    expect(wrapper.find('[data-testid="fm-pagination"]').exists()).toBe(true)
  })

  it('翻页按 offset 请求下一页', async () => {
    const wrapper = await mountFm()
    await wrapper.find('[data-testid="fm-pagination"] .pager-next').trigger('click')
    await flushPromises()
    expect(kbApi.listDocuments).toHaveBeenLastCalledWith(
      'kb-1', '', 50, 50,
    )
  })

  it('文件少于页大小时不渲染分页器', async () => {
    seedRoot({ files: 10 })
    const wrapper = await mountFm()
    expect(wrapper.findAll('.fm__row--file')).toHaveLength(10)
    expect(wrapper.find('[data-testid="fm-pagination"]').exists()).toBe(false)
  })
})

describe('文件管理器：文件夹多选', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    seedRoot()
  })

  it('文件夹行有复选框；勾选 = 递归选中子树全部文件（含嵌套）', async () => {
    const wrapper = await mountFm()
    // 根视图的子文件夹行（Q1 嵌套在「2025 报表」内，根视图只显示后者）
    const folderRows = wrapper.findAll('.fm__row--folder')
    expect(folderRows).toHaveLength(1)
    // 勾选「2025 报表」= 递归（含子文件夹 Q1 的 2 个文件 + 自己 1 个）
    const box = folderRows[0].find('input[type="checkbox"]')
    expect(box.exists()).toBe(true)
    await box.setValue(true)
    await flushPromises()
    const batch = wrapper.find('.fm__batch')
    expect(batch.text()).toContain('3')
    expect(batch.text()).toContain('2025 报表')
  })

  it('批量挖掘把文件夹内容一并送 mine', async () => {
    kbApi.mineKb.mockResolvedValue({ run_id: 'run-123', auto_force_redo: false })
    const wrapper = await mountFm()
    const folderRows = wrapper.findAll('.fm__row--folder')
    await folderRows[0].find('input[type="checkbox"]').setValue(true)
    await flushPromises()
    // 再直接勾一个根目录文件
    const fileBox = wrapper.findAll('.fm__row--file')[0].find('input[type="checkbox"]')
    await fileBox.setValue(true)
    await flushPromises()
    await wrapper.find('[data-testid="fm-batch-mine"]').trigger('click')
    await flushPromises()
    expect(kbApi.mineKb).toHaveBeenCalledWith(
      'kb-1',
      expect.arrayContaining(['r-1', 'q1-1', 'q1-2', 'root-0']),
      false,
    )
  })

  it('取消勾选文件夹 = 移除其内容（不影响其他选择）', async () => {
    const wrapper = await mountFm()
    const folderRows = wrapper.findAll('.fm__row--folder')
    const folderBox = folderRows[0].find('input[type="checkbox"]')
    await folderBox.setValue(true)
    await flushPromises()
    const fileBox = wrapper.findAll('.fm__row--file')[0].find('input[type="checkbox"]')
    await fileBox.setValue(true)
    await flushPromises()
    expect(wrapper.find('.fm__batch').text()).toContain('4')
    await folderBox.setValue(false)
    await flushPromises()
    expect(wrapper.find('.fm__batch').text()).toContain('1')
  })

  it('表头全选只选当前页（服务端分页下不假装全库）', async () => {
    const wrapper = await mountFm()
    const headerBox = wrapper.find('.fm__row--head input[type="checkbox"]')
    await headerBox.setValue(true)
    await flushPromises()
    expect(wrapper.find('.fm__batch').text()).toContain('50')
    // 提示语明确「本页」
    expect(wrapper.find('.fm__batch').text()).toContain('本页')
  })
})
