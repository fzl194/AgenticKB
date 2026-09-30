/**
 * 57 号工作线①：文件管理器搜索框（名 + 目录 + 状态）。
 * - 关键词触发搜索：listDocuments/countDocuments 收到 query/directory_prefix，
 *   且不再传精确 directory；
 * - 状态单独选（无关键词）= 管理清单场景（如挖失败清单）；
 * - 范围切换：整库 = 不带 directory_prefix；
 * - 搜索态：隐藏文件夹行、文件行展示所在目录、清空关键词回到浏览态。
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

const ElInputStub = {
  name: 'ElInput',
  props: ['modelValue'],
  emits: ['update:modelValue', 'keyup', 'clear'],
  template: `<div class="el-input-stub" data-testid="fm-search-input">
    <input data-testid="fm-search-native" :value="modelValue"
      @input="$emit('update:modelValue', $event.target.value)"
      @keyup.enter="$emit('keyup', $event)" />
    <button data-testid="fm-search-clear" @click="$emit('update:modelValue', ''); $emit('clear')" />
  </div>`,
}

const ElSelectStub = {
  name: 'ElSelect',
  props: ['modelValue'],
  emits: ['update:modelValue', 'change'],
  template: `<div class="el-select-stub" data-testid="fm-search-status">
    <button data-testid="fm-status-failed" @click="$emit('update:modelValue', 'failed'); $emit('change', 'failed')">failed</button>
  </div>`,
}

const ElRadioGroupStub = {
  name: 'ElRadioGroup',
  props: ['modelValue'],
  emits: ['update:modelValue', 'change'],
  template: `<div><button data-testid="fm-scope-all"
    @click="$emit('update:modelValue', 'all'); $emit('change', 'all')">all</button></div>`,
}

function file(id: string, name: string, directory_path = '') {
  return {
    id, document_name: name, file_size: 10, created_at: '2026-09-01T00:00:00Z',
    modified_at: '2026-09-01T00:00:00Z', status: 'mined', directory_path,
  }
}

function mountFm() {
  return mount(KbFileManager, {
    props: { kbId: 'kb-1', canWrite: true, workflowId: 'wf-1', active: true },
    global: { stubs: { ElInput: ElInputStub, ElSelect: ElSelectStub, 'el-radio-group': ElRadioGroupStub } },
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  kbApi.listFolders.mockResolvedValue([
    { id: 'f-1', name: '产品文档', path: '产品文档', parent_id: null, created_at: '2026-09-01T00:00:00Z' },
  ])
  kbApi.listDocuments.mockResolvedValue([])
  kbApi.countDocuments.mockResolvedValue(0)
})

describe('KbFileManager 文件搜索（57 号）', () => {
  it('浏览态：不带搜索过滤，directory 现状口径', async () => {
    const w = mountFm()
    await flushPromises()
    expect(kbApi.listDocuments).toHaveBeenCalledWith('kb-1', '', 50, 0, undefined)
    expect(kbApi.countDocuments).toHaveBeenCalledWith('kb-1', '', undefined)
    w.unmount()
  })

  it('关键词 + 回车 → 搜索口径（根目录 tree 范围 = 整库，无 prefix）', async () => {
    const w = mountFm()
    await flushPromises()
    kbApi.listDocuments.mockResolvedValue([file('d-1', '设备手册.pdf', '产品文档')])

    await w.find('[data-testid="fm-search-native"]').setValue('手册')
    await w.find('[data-testid="fm-search-native"]').trigger('keyup.enter')
    await flushPromises()

    expect(kbApi.listDocuments).toHaveBeenLastCalledWith(
      'kb-1', undefined, 50, 0, { query: '手册' })
    expect(kbApi.countDocuments).toHaveBeenLastCalledWith('kb-1', undefined, { query: '手册' })
    // 搜索态：文件行展示所在目录
    expect(w.find('[data-testid="fm-search-dir"]').text()).toBe('产品文档/')
    w.unmount()
  })

  it('状态单独选 = 管理清单（无关键词也进入搜索态）', async () => {
    const w = mountFm()
    await flushPromises()
    await w.find('[data-testid="fm-status-failed"]').trigger('click')
    await flushPromises()
    expect(kbApi.listDocuments).toHaveBeenLastCalledWith(
      'kb-1', undefined, 50, 0, { status: 'failed' })
    w.unmount()
  })

  it('范围 = 当前目录及子目录（非根）→ 带 directory_prefix', async () => {
    const w = mountFm()
    await flushPromises()
    // 进入「产品文档」文件夹（folder 行点击 → enterFolder）
    const folderRow = w.find('.fm__row--folder')
    await folderRow.trigger('click')
    await flushPromises()

    await w.find('[data-testid="fm-search-native"]').setValue('告警')
    await w.find('[data-testid="fm-search-native"]').trigger('keyup.enter')
    await flushPromises()
    expect(kbApi.listDocuments).toHaveBeenLastCalledWith(
      'kb-1', undefined, 50, 0, { query: '告警', directory_prefix: '产品文档' })
    w.unmount()
  })

  it('切换到整库范围 → prefix 移除；清空关键词回到浏览态', async () => {
    const w = mountFm()
    await flushPromises()
    const folderRow = w.find('.fm__row--folder')
    await folderRow.trigger('click')
    await flushPromises()

    await w.find('[data-testid="fm-search-native"]').setValue('告警')
    await w.find('[data-testid="fm-search-native"]').trigger('keyup.enter')
    await flushPromises()
    // 切整库
    await w.find('[data-testid="fm-scope-all"]').trigger('click')
    await flushPromises()
    expect(kbApi.listDocuments).toHaveBeenLastCalledWith(
      'kb-1', undefined, 50, 0, { query: '告警' })
    // 清空 → 回到浏览态（恢复精确 directory）
    await w.find('[data-testid="fm-search-clear"]').trigger('click')
    await flushPromises()
    expect(kbApi.listDocuments).toHaveBeenLastCalledWith('kb-1', '产品文档', 50, 0, undefined)
    w.unmount()
  })

  it('搜索态隐藏文件夹行；清空后恢复', async () => {
    const w = mountFm()
    await flushPromises()
    expect(w.findAll('.fm__row--folder')).toHaveLength(1)

    await w.find('[data-testid="fm-search-native"]').setValue('x')
    await w.find('[data-testid="fm-search-native"]').trigger('keyup.enter')
    await flushPromises()
    expect(w.findAll('.fm__row--folder')).toHaveLength(0)
    expect(w.text()).toContain('没有匹配的文件') // 空结果文案（搜索态）

    await w.find('[data-testid="fm-search-clear"]').trigger('click')
    await flushPromises()
    expect(w.findAll('.fm__row--folder')).toHaveLength(1)
    w.unmount()
  })

  it('切库重置搜索态——回到新库浏览口径（审查L-1/搜索态重置）', async () => {
    const w = mountFm()
    await flushPromises()
    // 进入搜索态 + 翻到第 2 页
    await w.find('[data-testid="fm-search-native"]').setValue('手册')
    await w.find('[data-testid="fm-search-native"]').trigger('keyup.enter')
    await flushPromises()

    await w.setProps({ kbId: 'kb-2' })
    await flushPromises()
    // 新库：浏览口径（directory=''、无搜索过滤）+ 页码回 1
    expect(kbApi.listDocuments).toHaveBeenLastCalledWith('kb-2', '', 50, 0, undefined)
    const input = w.find('[data-testid="fm-search-native"]').element as HTMLInputElement
    expect(input.value).toBe('')
    w.unmount()
  })
})
