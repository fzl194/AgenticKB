/**
 * KbListView 的 EN 渲染断言（四区英化 Task 8 / 规则 R11）。
 *
 * mock 块复制自同目录 KbListViewPermissions.spec.ts，区别只在语言：全局语言键必须在
 * pinia 创建**之前**写入——locale store 在实例化时读它。装进插件的 pinia 与活动 pinia
 * 是同一个实例（否则 t() 读不到切换）。
 *
 * 与样板 harness 的差异：
 * 1. 用 mount 代替 shallowMount——页头的「新建知识库」经全局 ElButton stub 渲染，
 *    EmptyState 要真实渲染才能断言空态文案；
 * 2. 三个子弹窗（KbCreateDialog / KbCopyDialog / KbExportDialog）显式 stub——
 *    它们属于文件库管理簇（Task 10），此时尚未英化，真实渲染会把中文带进整页泄漏断言。
 *
 * 夹具数据一律 ASCII：EN 态下整块界面（含未解析组件上的 title 等属性）出现任何 CJK 字符
 * 或全角标点都算泄漏。服务端返回的错误原文是数据，不算泄漏。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import type { Pinia } from 'pinia'

const state = vi.hoisted(() => ({
  auth: { siteRole: 'member' as 'admin' | 'member' },
  domain: {
    currentDomain: 'domain_a',
    currentDomainInfo: {
      domain_id: 'domain_a', display_name: 'Domain A', enabled: true,
      default_channel: 'prod', scenario_pack_ref: 'domain_a',
      domain_role: 'admin' as 'admin' | 'member',
      capabilities: ['domain.users.manage', 'domain.kbs.manage'] as string[],
    },
  },
  api: {
    listKbs: vi.fn(),
    listDeletedKbs: vi.fn(),
    purgeTasks: vi.fn(),
    restoreKb: vi.fn(),
    mineKb: vi.fn(),
    updateKb: vi.fn(),
    deleteKb: vi.fn(),
  },
  ui: {
    success: vi.fn(),
    error: vi.fn(),
    confirm: vi.fn(),
    prompt: vi.fn(),
  },
  push: vi.fn(),
}))

vi.mock('@/stores/auth', () => ({ useAuthStore: () => state.auth }))
vi.mock('@/stores/domain', () => ({ useDomainStore: () => state.domain }))
vi.mock('@/api/kb', () => ({ useKbApi: () => state.api }))
vi.mock('vue-router', () => ({ useRouter: () => ({ push: state.push }) }))
vi.mock('element-plus', () => ({
  ElMessage: { success: state.ui.success, error: state.ui.error },
  ElMessageBox: { confirm: state.ui.confirm, prompt: state.ui.prompt },
}))

import KbListView from '@/views/kb/KbListView.vue'
import type { DeletedKbRow, KbPurgeTask, KbSummary } from '@/types/kb'
import { useLocaleStore } from '@/stores/locale'
import { zh } from '@/locales/zh'
import { en } from '@/locales/en'
import { zhKbView } from '@/locales/zh/kbView'
import listSource from '@/views/kb/KbListView.vue?raw'
import detailSource from '@/views/kb/KbDetailView.vue?raw'
import previewSource from '@/views/kb/KbDocPreviewView.vue?raw'
import runSource from '@/views/kb/KbRunDetailView.vue?raw'
import runDocSource from '@/views/kb/KbRunDocDetailView.vue?raw'
import traceSource from '@/components/mining/workflow/MiningWorkflowTrace.vue?raw'

enableAutoUnmount(afterEach)

/**
 * Task 8 六文件的源码原文。t(key: string) 无类型——key 拼错要到运行时才以原文露出，
 * vitest 与 vue-tsc 都发现不了；末尾的「字典 key 对账」用例把它钉成闸门。
 * 列表页之外的五个文件只有中文断言的既有 spec，它们的 en 输出同样靠这组对账兜底。
 */
const TASK8_SOURCES: readonly string[] = [
  listSource, detailSource, previewSource, runSource, runDocSource, traceSource,
]
/** 形如 `a.b.c` 的单引号字面量——六文件里只有字典 key 长这样（'zh-CN'、'doc:/' 都不符合）。 */
const DOTTED_KEY_LITERAL = /'([a-z][a-zA-Z0-9]*(?:\.[a-zA-Z0-9_]+)+)'/g
/** Task 3/4 已在 kbView 字典里的 14 个 key（消费方是 kbMeta.ts / api/serving.ts / stores/mining.ts）。 */
const PRE_EXISTING_KB_VIEW_KEYS: readonly string[] = [
  'kbView.visibility.public', 'kbView.visibility.private',
  'kbView.role.owner', 'kbView.role.editor', 'kbView.role.viewer',
  'kbView.docStatus.minedUnchanged', 'kbView.docStatus.uploaded', 'kbView.docStatus.updateFailed',
  'kbView.docStatus.failed', 'kbView.docStatus.unknown',
  'kbView.search.noActiveBuild', 'kbView.search.kbNotFound', 'kbView.search.forbidden',
  'kbView.runDetail.partialLoad',
]

/** 六文件引用到的全部字典 key（直接 t('…') 与各 *_KEYS 映射表里的 key 一并计入）。 */
function referencedKeys(): Set<string> {
  const keys = new Set<string>()
  for (const source of TASK8_SOURCES) {
    for (const match of source.matchAll(DOTTED_KEY_LITERAL)) keys.add(match[1])
  }
  return keys
}

/** CJK 文字（4E00–9FFF）+ CJK 标点「」等（3000–303F）+ 全角形（）：等（FF00–FFEF）都算泄漏。 */
const LEAK = /[\u3000-\u303f\u4e00-\u9fff\uff00-\uffef]/

/** 期望文案独立手写（不从字典导入）——字典改坏时这里才会红。 */
const EN = {
  newKb: 'New Knowledge Base',
  count: '2 total',
  public: 'Public',
  private: 'Private',
  meOwner: 'Me: Owner',
  meViewer: 'Me: Viewer',
  noDescription: 'No description',
  docCount: '3 documents',
  creator: 'Creator: alice',
  mine: 'Mine',
  copy: 'Copy',
  exportDownload: 'Export & Download',
  rename: 'Rename',
  deleteKb: 'Delete Knowledge Base',
  phaseQueued: 'Queued',
  phasePrepare: 'Prepare',
  phaseDocuments: 'Delete documents',
  phaseSnapshots: 'Reclaim knowledge snapshots',
  phaseObjects: 'Reclaim storage objects',
  phaseFinalize: 'Finalize',
  snapshotsProgress: '5/10 snapshots',
  objectsProgress: '2/8 objects',
  finishing: 'Finishing',
  unknownReason: 'Unknown reason',
  deletedTitle: 'Deleted Knowledge Bases (1)',
  deletedOn: 'Deleted on ',
  restore: 'Restore',
  purge: 'Purge',
  empty: 'No knowledge bases in the current domain yet; '
    + 'click "New Knowledge Base" in the upper-right corner to get started',
  retry: 'Retry',
  mineQueued: 'Mining queued (run abcdef12)',
  newName: 'New name',
  renameTitle: 'Rename Knowledge Base',
  nameRequired: 'Name cannot be empty',
  save: 'Save',
  cancel: 'Cancel',
  renamed: 'Renamed',
  deleteMessage: 'This will permanently delete the knowledge base "Plant Manual" together with all its documents, '
    + 'mined knowledge, and history. This cannot be undone. Type the full name to confirm:',
  deleteTitle: 'Permanently Delete Knowledge Base',
  deletePermanently: 'Delete Permanently',
  nameMismatch: 'Name does not match',
  deleteStarted: 'Background deletion started: the knowledge base is disabled immediately (invisible to members); '
    + 'see the progress below the list',
  purgeMessage: 'This will purge all residual data of "Old Plant Manual" (documents/knowledge/storage). '
    + 'This cannot be undone. Type the full name to confirm:',
  purgeStarted: 'Background purge started; see the progress below the list',
  restoreMessage: 'Restore knowledge base "Old Plant Manual"? '
    + 'Restoration will be rejected if a knowledge base with the same name already exists in the current domain.',
  restoreTitle: 'Restore Knowledge Base',
  restored: 'Knowledge base restored',
}

const purgeFailed = (name: string, reason: string): string =>
  `"${name}" deletion failed: ${reason} — you can start the deletion again`

const OWNER_KB: KbSummary = {
  id: 'kb-1', domain: 'domain_a', name: 'Plant Manual', description: null,
  owner_id: 'u-owner', owner_name: 'alice', visibility: 'public', mining_workflow_id: null,
  created_at: '2026-09-03T00:00:00Z', my_role: 'owner', document_count: 3,
}
const VIEWER_KB: KbSummary = {
  ...OWNER_KB, id: 'kb-2', name: 'Safety Rules', description: 'Rules for the shop floor',
  owner_name: undefined, visibility: 'private', my_role: 'viewer', document_count: 0,
}
const DELETED_KB: DeletedKbRow = {
  id: 'kb-9', domain: 'domain_a', name: 'Old Plant Manual', owner_id: 'u-owner',
  visibility: 'public', status: 'deleted', deleted_at: '2026-09-02T00:00:00Z',
  created_at: '2026-09-01T00:00:00Z', updated_at: '2026-09-02T00:00:00Z',
}

function purgeTask(overrides: Partial<KbPurgeTask> = {}): KbPurgeTask {
  return {
    id: 'purge-1', kb_id: 'kb-9', kb_name: 'Old Plant Manual', domain: 'domain_a',
    status: 'running', phase: 'snapshots',
    progress: { snapshots_total: 10, snapshots_reclaimed: 5 },
    requested_by: 'alice', error: null,
    created_at: '2026-09-03T00:00:00Z', updated_at: '2026-09-03T00:00:00Z',
    ...overrides,
  }
}

let pinia: Pinia

// 顺序铁律：全局键先于 pinia 写入——store 在创建时读它
beforeEach(() => {
  localStorage.setItem('kb-ui-lang', 'en')
  pinia = createPinia()
  setActivePinia(pinia)
  vi.clearAllMocks()
  state.auth.siteRole = 'member'
  state.domain.currentDomainInfo.domain_role = 'admin'
  state.domain.currentDomainInfo.capabilities = ['domain.users.manage', 'domain.kbs.manage']
  state.api.listKbs.mockResolvedValue([])
  state.api.listDeletedKbs.mockResolvedValue([])
  state.api.purgeTasks.mockResolvedValue([])
  state.api.restoreKb.mockResolvedValue({})
  state.api.mineKb.mockResolvedValue({ run_id: 'abcdef123456' })
  state.api.updateKb.mockResolvedValue({})
  state.api.deleteKb.mockResolvedValue({})
  state.ui.confirm.mockResolvedValue(undefined)
})
afterEach(() => localStorage.removeItem('kb-ui-lang'))

type ViewWrapper = ReturnType<typeof mountView>

function mountView() {
  return mount(KbListView, {
    global: {
      plugins: [pinia],
      stubs: { KbCreateDialog: true, KbCopyDialog: true, KbExportDialog: true },
    },
  })
}

async function mountReady() {
  const wrapper = mountView()
  await flushPromises()
  return wrapper
}

/** 未 defineExpose 的内部状态与动作：经 setupState 代理访问（与 McpAccessView.en.spec.ts 同法）。 */
interface ViewInternals {
  mine: (kb: KbSummary) => Promise<void>
  rename: (kb: KbSummary) => Promise<void>
  remove: (kb: KbSummary) => Promise<void>
  removeDeleted: (row: DeletedKbRow) => Promise<void>
  restoreDeleted: (row: DeletedKbRow) => Promise<void>
}
const internals = (wrapper: ViewWrapper) => wrapper.vm as unknown as ViewInternals

/** ElMessageBox.prompt / confirm 的第三个参数（按钮文案、校验器都在里面）。 */
type PromptOptions = {
  type?: string
  confirmButtonText?: string
  cancelButtonText?: string
  confirmButtonClass?: string
  inputValidator?: (value: string | undefined) => boolean | string
}
const lastPromptOptions = (mock: typeof state.ui.prompt | typeof state.ui.confirm): PromptOptions =>
  (mock.mock.calls.at(-1)?.[2] ?? {}) as PromptOptions

const card = (wrapper: ViewWrapper, index: number) => wrapper.findAll('.kb-card')[index]
const cardTags = (wrapper: ViewWrapper, index: number) =>
  card(wrapper, index).findAll('.el-tag').map(tag => tag.text())
const cardItems = (wrapper: ViewWrapper, index: number) =>
  card(wrapper, index).findAll('.el-dropdown-item').map(item => item.text())

/** 未解析/透传组件上的静态属性（title/content 等）同样不得残留中文。 */
function attributeLeaks(wrapper: ViewWrapper): string[] {
  return wrapper.findAll('*').flatMap(element =>
    Object.values(element.attributes()).filter(value => LEAK.test(value)),
  )
}

describe('KbListView (EN)', () => {
  it('renders the header, the card grid and the card actions in English, then follows a runtime switch to zh', async () => {
    state.api.listKbs.mockResolvedValue([OWNER_KB, VIEWER_KB])
    const wrapper = await mountReady()

    // 页头：计数 + 主按钮（第一个按钮只有图标）
    expect(wrapper.find('.kb-list__count').text()).toBe(EN.count)
    expect(wrapper.find('.kb-list__actions').findAll('button').map(button => button.text()))
      .toEqual(['', EN.newKb])

    // 拥有者卡：kbMeta 的可见性/角色标签与页面自身文案一起切换
    expect(cardTags(wrapper, 0)).toEqual([EN.public, EN.meOwner])
    expect(card(wrapper, 0).find('.kb-card__desc').text()).toBe(EN.noDescription)
    const meta = card(wrapper, 0).findAll('.kb-card__meta-item').map(item => item.text())
    expect(meta[0]).toBe(EN.docCount)
    expect(meta[1]).toBe(EN.creator)
    expect(card(wrapper, 0).find('.kb-card__footer button').text()).toBe(EN.mine)
    expect(card(wrapper, 0).find('.kb-card__footer button').attributes('disabled')).toBeUndefined()
    expect(cardItems(wrapper, 0)).toEqual([EN.copy, EN.exportDownload, EN.rename, EN.deleteKb])

    // 只读卡：挖掘禁用，下拉里没有改名/删除
    expect(cardTags(wrapper, 1)).toEqual([EN.private, EN.meViewer])
    expect(card(wrapper, 1).find('.kb-card__desc').text()).toBe('Rules for the shop floor')
    expect(card(wrapper, 1).findAll('.kb-card__meta-item')[0].text()).toBe('0 documents')
    expect(card(wrapper, 1).findAll('.kb-card__meta-item')[1].text()).toBe('Creator: —')
    expect(card(wrapper, 1).find('.kb-card__footer button').attributes('disabled')).toBeDefined()
    expect(cardItems(wrapper, 1)).toEqual([EN.copy, EN.exportDownload])

    // 渲染期切换语言：模板与 kbMeta 映射都重算
    useLocaleStore().setLang('zh')
    await nextTick()

    expect(wrapper.find('.kb-list__count').text()).toBe('2 个')
    expect(wrapper.find('.kb-list__actions').findAll('button')[1].text()).toBe('新建知识库')
    expect(cardTags(wrapper, 0)).toEqual(['公开', '我：拥有者'])
    expect(cardItems(wrapper, 0)).toEqual(['拷贝', '导出下载', '改名', '删除知识库'])
  })

  it('labels every purge phase, its progress and the failed deletions in English', async () => {
    state.api.purgeTasks.mockResolvedValue([
      purgeTask({ id: 'purge-1', phase: 'queued', progress: {} }),
      purgeTask({ id: 'purge-2', phase: 'prepare', progress: {} }),
      purgeTask({ id: 'purge-3', phase: 'documents', progress: {} }),
      purgeTask({ id: 'purge-4', phase: 'snapshots' }),
      purgeTask({ id: 'purge-5', phase: 'objects', progress: { objects_total: 8, objects_reclaimed: 2 } }),
      purgeTask({ id: 'purge-6', phase: 'finalize', progress: {} }),
      purgeTask({ id: 'purge-7', status: 'failed', phase: 'documents', progress: {}, error: null }),
      purgeTask({ id: 'purge-8', status: 'failed', kb_name: 'Broken KB', phase: 'finalize', progress: {}, error: 'timeout' }),
    ])
    const wrapper = await mountReady()

    const rows = wrapper.findAll('.kb-purging__row')
    expect(rows).toHaveLength(6)
    expect(rows.map(row => row.find('.kb-purging__phase').text())).toEqual([
      EN.phaseQueued, EN.phasePrepare, EN.phaseDocuments,
      EN.phaseSnapshots, EN.phaseObjects, EN.phaseFinalize,
    ])
    // 其余阶段只显示阶段名；快照阶段带百分比进度条
    expect(rows.map(row => row.find('.kb-purging__meta').text())).toEqual([
      '', '', '', EN.snapshotsProgress, EN.objectsProgress, EN.finishing,
    ])
    expect(rows.filter(row => row.find('el-progress').exists())).toHaveLength(1)
    expect(rows[3].find('el-progress').attributes('percentage')).toBe('50')

    expect(wrapper.find('el-alert').findAll('div').map(line => line.text())).toEqual([
      purgeFailed('Old Plant Manual', EN.unknownReason),
      purgeFailed('Broken KB', 'timeout'),
    ])
  })

  it('lists the soft-deleted knowledge bases with English actions for a domain admin only', async () => {
    state.api.listDeletedKbs.mockResolvedValue([DELETED_KB])
    const wrapper = await mountReady()

    expect(wrapper.find('el-collapse-item').attributes('title')).toBe(EN.deletedTitle)
    const row = wrapper.find('.kb-deleted__row')
    expect(row.find('.kb-deleted__name').text()).toBe('Old Plant Manual')
    expect(row.find('.kb-deleted__meta').text()).toContain(EN.deletedOn)
    expect(row.findAll('button').map(button => button.text())).toEqual([EN.restore, EN.purge])
    wrapper.unmount()

    state.domain.currentDomainInfo.domain_role = 'member'
    state.domain.currentDomainInfo.capabilities = []
    const member = await mountReady()
    expect(member.find('.kb-deleted').exists()).toBe(false)
  })

  it('renders the empty state and the load-failure retry in English', async () => {
    const empty = await mountReady()
    expect(empty.find('.empty-state__text').text()).toBe(EN.empty)
    empty.unmount()

    state.api.listKbs.mockRejectedValue(new Error('list down'))
    const failed = await mountReady()
    expect(failed.find('.kb-list__error span').text()).toBe('list down')
    expect(failed.find('.kb-list__error button').text()).toBe(EN.retry)
  })

  it('reports mining, rename, delete, purge and restore flows in English', async () => {
    state.api.listKbs.mockResolvedValue([OWNER_KB])
    state.api.listDeletedKbs.mockResolvedValue([DELETED_KB])
    const wrapper = await mountReady()
    const vm = internals(wrapper)

    await vm.mine(OWNER_KB)
    await flushPromises()
    expect(state.api.mineKb).toHaveBeenCalledWith('kb-1')
    expect(state.ui.success).toHaveBeenLastCalledWith(EN.mineQueued)

    // 挖掘失败：错误原文透传（服务端数据不翻译）
    state.api.mineKb.mockRejectedValue(new Error('queue down'))
    await vm.mine(OWNER_KB)
    expect(state.ui.error).toHaveBeenLastCalledWith('queue down')

    state.ui.prompt.mockResolvedValue({ value: '  Plant Manual v2  ' })
    await vm.rename(OWNER_KB)
    await flushPromises()
    expect(state.ui.prompt).toHaveBeenLastCalledWith(
      EN.newName, EN.renameTitle,
      expect.objectContaining({
        inputValue: 'Plant Manual', confirmButtonText: EN.save, cancelButtonText: EN.cancel,
      }),
    )
    const renameOptions = lastPromptOptions(state.ui.prompt)
    expect(renameOptions.inputValidator?.('   ')).toBe(EN.nameRequired)
    expect(renameOptions.inputValidator?.('x')).toBe(true)
    expect(state.api.updateKb).toHaveBeenCalledWith('kb-1', { name: 'Plant Manual v2' })
    expect(state.ui.success).toHaveBeenLastCalledWith(EN.renamed)

    state.ui.prompt.mockResolvedValue({ value: 'Plant Manual' })
    await vm.remove(OWNER_KB)
    await flushPromises()
    expect(state.ui.prompt).toHaveBeenLastCalledWith(
      EN.deleteMessage, EN.deleteTitle,
      expect.objectContaining({
        type: 'warning', confirmButtonText: EN.deletePermanently, cancelButtonText: EN.cancel,
      }),
    )
    const deleteOptions = lastPromptOptions(state.ui.prompt)
    expect(deleteOptions.confirmButtonClass).toBe('el-button--danger')
    expect(deleteOptions.inputValidator?.('Plant Manual v2')).toBe(EN.nameMismatch)
    expect(deleteOptions.inputValidator?.('Plant Manual')).toBe(true)
    expect(state.api.deleteKb).toHaveBeenCalledWith('kb-1', 'Plant Manual')
    expect(state.ui.success).toHaveBeenLastCalledWith(EN.deleteStarted)

    state.ui.prompt.mockResolvedValue({ value: 'Old Plant Manual' })
    await vm.removeDeleted(DELETED_KB)
    await flushPromises()
    expect(state.ui.prompt).toHaveBeenLastCalledWith(
      EN.purgeMessage, EN.purge,
      expect.objectContaining({ type: 'warning', confirmButtonText: EN.purge, cancelButtonText: EN.cancel }),
    )
    expect(state.api.deleteKb).toHaveBeenLastCalledWith('kb-9', 'Old Plant Manual')
    expect(state.ui.success).toHaveBeenLastCalledWith(EN.purgeStarted)

    await vm.restoreDeleted(DELETED_KB)
    await flushPromises()
    expect(state.ui.confirm).toHaveBeenLastCalledWith(
      EN.restoreMessage, EN.restoreTitle,
      { type: 'warning', confirmButtonText: EN.restore, cancelButtonText: EN.cancel },
    )
    expect(state.api.restoreKb).toHaveBeenCalledWith('kb-9')
    expect(state.ui.success).toHaveBeenLastCalledWith(EN.restored)
  })

  it('leaks no Chinese anywhere in EN', async () => {
    state.api.listKbs.mockResolvedValue([OWNER_KB, VIEWER_KB])
    state.api.listDeletedKbs.mockResolvedValue([DELETED_KB])
    state.api.purgeTasks.mockResolvedValue([
      purgeTask(),
      purgeTask({ id: 'purge-8', status: 'failed', phase: 'finalize', progress: {}, error: 'timeout' }),
    ])
    const wrapper = await mountReady()

    expect(wrapper.text()).not.toMatch(LEAK)
    expect(attributeLeaks(wrapper)).toEqual([])
  })
})

describe('kb 视图字典 key 对账（Task 8 六文件）', () => {
  it('六文件引用的每个 key 在 zh / en 字典里都有定义，且 en 值不残留中文', () => {
    const referenced = [...referencedKeys()].sort()

    expect(referenced.length).toBeGreaterThan(200)
    expect(referenced.filter(key => !(key in zh))).toEqual([])
    expect(referenced.filter(key => !(key in en))).toEqual([])
    expect(referenced.filter(key => LEAK.test(en[key as keyof typeof en]))).toEqual([])
  })

  it('为本任务新增的 kbView key 都被六文件引用到（没有拼错后落单的死 key）', () => {
    const referenced = referencedKeys()
    const unused = Object.keys(zhKbView)
      .filter(key => !PRE_EXISTING_KB_VIEW_KEYS.includes(key) && !referenced.has(key))

    expect(unused).toEqual([])
  })
})
