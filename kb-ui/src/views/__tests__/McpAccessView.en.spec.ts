/**
 * McpAccessView 的 EN 渲染断言（四区英化 Task 7 / 规则 R11）。
 *
 * mock 与挂载代码复制自同目录 McpAccessDelete.spec.ts / McpAccessRecordsLink.spec.ts，
 * 区别只在语言：全局语言键必须在 pinia 创建**之前**写入——locale store 在实例化时读它。
 * 装进插件的 pinia 与活动 pinia 是同一个实例（否则 t() 读不到切换）。
 *
 * 与样板 harness 的两处差异：
 * 1. ElAlert 补一个渲染 #title 插槽的 stub——element-plus 是部分 mock，
 *    未解析的 <el-alert> 不渲染具名插槽，加载失败/明文一次性的标题无从断言；
 * 2. 表格换成行级 stub（见 RowProvider）：全局 stub 的 ElTableColumn 只回传空 row，
 *    状态列的三种标签与按状态出现的操作按钮无从断言。
 *
 * 夹具数据一律 ASCII：EN 态下整块界面（含未解析组件上的 label/description/title/content/
 * placeholder 属性）出现任何 CJK 字符或全角标点都算泄漏。服务端返回的中文错误原文是数据，不算泄漏。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { defineComponent, h, inject, nextTick, provide } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import type { Pinia } from 'pinia'

const api = vi.hoisted(() => ({
  listMcpKeys: vi.fn(),
  createMcpKey: vi.fn(),
  deleteMcpKey: vi.fn(),
  rotateMcpKey: vi.fn(),
  revokeMcpKey: vi.fn(),
  listKbs: vi.fn(),
}))
const artifactsApi = vi.hoisted(() => ({ list: vi.fn() }))
const ui = vi.hoisted(() => ({
  confirm: vi.fn(), success: vi.fn(), error: vi.fn(), warning: vi.fn(),
}))
const push = vi.hoisted(() => vi.fn())
const domainStore = vi.hoisted(() => ({
  currentDomain: 'domain-a', enabledDomains: [] as { domain_id: string }[],
  fetchDomains: vi.fn(), switchDomain: vi.fn(),
}))

vi.mock('@/api/kb', () => ({ useKbApi: () => api }))
vi.mock('@/api/artifacts', () => ({ useArtifactsApi: () => artifactsApi }))
vi.mock('@/stores/domain', () => ({ useDomainStore: () => domainStore }))
vi.mock('@/stores/auth', () => ({ useAuthStore: () => ({ user: { username: 'alice' } }) }))
vi.mock('vue-router', () => ({ useRouter: () => ({ push }) }))
vi.mock('element-plus', () => ({
  ElMessage: { success: ui.success, error: ui.error, warning: ui.warning },
  ElMessageBox: { confirm: ui.confirm },
}))
// 回传服务端 detail 原文：建钥 409 分支据此比对「上限」匹配数据（R2：不翻）
vi.mock('@/api/proxyClient', () => ({
  apiErrorDetail: async (error: unknown) =>
    (error as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? 'failed',
  createProxyClient: vi.fn(() => ({ get: vi.fn() })),
  extractOne: vi.fn(),
  installAuthInterceptors: vi.fn(),
}))

import McpAccessView from '@/views/McpAccessView.vue'
import type { McpKeyItem } from '@/types/kb'
import { useLocaleStore } from '@/stores/locale'

enableAutoUnmount(afterEach)

/** CJK 文字（4E00–9FFF）+ CJK 标点「」等（3000–303F）+ 全角形（）：等（FF00–FFEF）都算泄漏。 */
const LEAK = /[\u3000-\u303f\u4e00-\u9fff\uff00-\uffef]/

/** 期望文案独立手写（不从字典导入）——字典改坏时这里才会红。 */
const EN = {
  desc: 'The entry point for your Agent (Dify / Coze / Claude, etc.) to connect to the platform\'s knowledge bases. '
    + 'One key per Agent, and one domain per key: each key\'s tools, open knowledge bases, and prompts do not affect one another.',
  atLimit: 'Limit reached',
  createKey: 'Create Key',
  loadFailedTitle: 'Failed to load the key list. Your active keys are unaffected and Agent connections will not be interrupted.',
  retryLoad: 'Retry Loading',
  emptyTitle: 'No MCP keys yet',
  emptyTipDomain: 'One key per Agent, and one domain per key.',
  emptyTipStart: 'Create a key for your first Agent to get started.',
  headers: ['Name', 'Domain', 'Secret', 'Status', 'Last Used', 'Created At', 'Actions'],
  revoked: 'Revoked',
  domainUnboundTip: 'The domain bound to this key has been unbound; contact an administrator to reassign a domain, then recreate the key',
  domainUnbound: 'Domain unbound',
  active: 'Active',
  never: 'Never',
  callRecords: 'Call Records',
  configure: 'Configure',
  rotate: 'Rotate',
  revoke: 'Revoke',
  deleteRecord: 'Delete Record',
  createTitle: 'Create MCP Key',
  namePlaceholder: 'Give the key a name, e.g. R&D Knowledge Assistant',
  domainPlaceholder: 'Select the domain this key serves',
  cancel: 'Cancel',
  create: 'Create',
  freshTitle: 'Key Created',
  freshAlert: 'The secret is shown in plaintext only this once and cannot be viewed again after closing. Copy it now and paste it into your Agent configuration.',
  copy: 'Copy',
  freshNote: 'The connection address is derived from the current deployment address (localhost:9000/mcp); the JSON is pre-filled with this secret.',
  savedSecret: 'I Have Saved the Secret',
  configTitle: 'Key Configuration',
  configTitleNamed: 'Key Configuration · Agent A',
  domainNotBound: 'You are not bound to this domain and cannot create a key',
  limitReached: 'The limit of 10 keys has been reached; revoke the keys you no longer need first',
  rotateMessage: 'After rotation, the old secret of "Agent A" stops working immediately, and Agents still using it will be disconnected. Continue?',
  rotateTitle: 'Rotate Key',
  revokeMessage: 'After revocation, "Agent A" stops working immediately and cannot be recovered; Agents using it will be disconnected. Revoke?',
  revokeTitle: 'Revoke Key',
  keyRevoked: 'Key revoked',
  deleteMessage: 'Delete the list record of the revoked key "Agent C"? Its old secret remains permanently invalid; this does not restore it or affect other keys.',
  deleteTitle: 'Delete Key Record',
  keyRecordDeleted: 'Key record deleted',
  copied: 'Copied',
  copyFailed: 'Copy failed; please select the text and copy it manually',
}

const KEY_ACTIVE: McpKeyItem = {
  id: 'key-1', name: 'Agent A', domain: 'domain-a', domain_bound: true, key_prefix: 'kbm_a',
  status: 'active', created_at: '2026-09-28T00:00:00Z', last_used_at: '2026-09-29T01:02:03Z',
  rotated_at: null, open_kb_ids: [], open_tools: null, instructions: null, tool_descriptions: null,
}
const KEY_UNBOUND: McpKeyItem = { ...KEY_ACTIVE, id: 'key-2', name: 'Agent B', domain_bound: false }
const KEY_REVOKED: McpKeyItem = { ...KEY_ACTIVE, id: 'key-3', name: 'Agent C', status: 'revoked', domain_bound: false }
const activeKeys = (count: number): McpKeyItem[] =>
  Array.from({ length: count }, (_, index) => ({ ...KEY_ACTIVE, id: `key-${index}`, name: `Agent ${index}` }))

let pinia: Pinia

// 顺序铁律：全局键先于 pinia 写入——store 在创建时读它
beforeEach(() => {
  localStorage.setItem('kb-ui-lang', 'en')
  pinia = createPinia()
  setActivePinia(pinia)
  vi.clearAllMocks()
  ui.confirm.mockResolvedValue(undefined)
  domainStore.enabledDomains = [{ domain_id: 'domain-a' }, { domain_id: 'domain-b' }]
  api.listMcpKeys.mockResolvedValue({ keys: [KEY_ACTIVE] })
  api.listKbs.mockResolvedValue([])
  artifactsApi.list.mockResolvedValue([])
})
afterEach(() => localStorage.removeItem('kb-ui-lang'))

const ROW_KEY = 'mcp-access-view-en-spec:row'

/** 每行把真实 row 经 provide 交给列 stub；列 stub 按 el-table-column 的 scoped-slot 契约回传。 */
const RowProvider = defineComponent({
  props: { row: { type: Object, required: true } },
  setup(props, { slots }) {
    provide(ROW_KEY, props.row)
    return () => h('div', { class: 'table-row' }, slots.default?.())
  },
})
const ElTableStub = defineComponent({
  props: { data: { type: Array, default: () => [] } },
  components: { RowProvider },
  template: '<div><RowProvider v-for="row in data" :key="row.id" :row="row"><slot :row="row" /></RowProvider></div>',
})
/** 列 stub：把 provide 来的真实 row 按 scoped-slot 契约回传给 `<template #default="{ row }">`；label 等属性照常透传。 */
const ElTableColumnStub = defineComponent({
  setup(_props, { slots }) {
    const row = inject<Record<string, unknown>>(ROW_KEY, {})
    return () => h('div', { class: 'table-cell' }, slots.default?.({ row }))
  },
})

type ViewWrapper = ReturnType<typeof mountView>

function mountView() {
  return mount(McpAccessView, {
    global: {
      plugins: [pinia],
      stubs: {
        McpKeyConfigPanel: true,
        ElAlert: { template: '<div><slot name="title" /><slot /></div>' },
        ElTable: ElTableStub,
        ElTableColumn: ElTableColumnStub,
      },
    },
  })
}

/** 未 defineExpose 的内部状态与动作：经 setupState 代理访问（与 McpAccessDelete.spec.ts 同法）。 */
interface ViewInternals {
  createForm: { name: string; domain: string }
  submitCreate: () => Promise<void>
  quickRotate: (row: McpKeyItem) => Promise<void>
  quickRevoke: (row: McpKeyItem) => Promise<void>
  openConfig: (row: McpKeyItem) => Promise<void>
  copy: (text: string) => Promise<void>
}
const internals = (wrapper: ViewWrapper) => wrapper.vm as unknown as ViewInternals

/** 某一行里按顺序的全部标签文本：[0] 是域列的域标识（服务端数据），[1] 是状态标签。 */
const rowTags = (wrapper: ViewWrapper, index: number) =>
  wrapper.findAll('.table-row')[index].findAll('.el-tag').map(tag => tag.text())
const rowButtons = (wrapper: ViewWrapper, index: number) =>
  wrapper.findAll('.table-row')[index].findAll('button').map(button => button.text())
/** 行内第 5 个单元格 = 「最近使用」列。 */
const lastUsedCell = (wrapper: ViewWrapper, index: number) =>
  wrapper.findAll('.table-row')[index].findAll('.table-cell')[4].text()

/** 未解析/透传组件上的静态属性（label/description/title/content/placeholder）同样不得残留中文。 */
function attributeLeaks(wrapper: ViewWrapper): string[] {
  return wrapper.findAll('*').flatMap(element =>
    Object.values(element.attributes()).filter(value => LEAK.test(value)),
  )
}

describe('McpAccessView (EN)', () => {
  it('renders the page head, table and dialogs in English, then follows a runtime switch to zh', async () => {
    const wrapper = mountView()
    await flushPromises()

    // 源文件两行的页头说明：渲染成一个空格衔接（与转换前的空白折叠结果一致）
    expect(wrapper.find('.mcp-view__desc').text()).toBe(EN.desc)
    const head = wrapper.find('.mcp-view__head')
    expect(head.find('button').text()).toBe(EN.createKey)
    expect(head.find('[content]').exists()).toBe(false)
    expect(head.find('button').attributes('disabled')).toBeUndefined()

    expect(wrapper.findAll('.table-cell').map(cell => cell.attributes('label'))).toEqual(EN.headers)
    expect(wrapper.findAll('.table-row')).toHaveLength(1)
    expect(wrapper.find('.table-row .mcp-view__name').text()).toBe('Agent A')
    expect(wrapper.find('.table-row .mcp-view__prefix').text()).toBe('kbm_a…')
    expect(rowTags(wrapper, 0)).toEqual(['domain-a', EN.active])
    expect(wrapper.find('.table-row [content]').exists()).toBe(false)
    expect(lastUsedCell(wrapper, 0)).toBe('2026-09-29 01:02:03')
    expect(rowButtons(wrapper, 0)).toEqual([EN.callRecords, EN.configure, EN.rotate, EN.revoke])

    // 新建钥匙弹窗（ElDialog stub 恒渲染 default + footer 插槽）
    const create = wrapper.find('[width="440px"]')
    expect(create.attributes('title')).toBe(EN.createTitle)
    // el-form-item 的 label（el-option 的 label 是选项值，同名的属性不算文案）
    expect(create.findAll('[label][required]').map(item => item.attributes('label'))).toEqual(['Name', 'Domain'])
    expect(create.find('input[data-test="mcp-key-name"]').attributes('placeholder')).toBe(EN.namePlaceholder)
    expect(create.find('select[data-test="mcp-key-domain"]').attributes('placeholder')).toBe(EN.domainPlaceholder)
    // 选项显示的是域标识（服务端数据），不走文案
    expect(create.findAll('option').map(option => option.attributes('label'))).toEqual(['domain-a', 'domain-b'])
    expect(create.findAll('button').map(button => button.text())).toEqual([EN.cancel, EN.create])

    // 明文一次性展示弹窗
    const fresh = wrapper.find('[width="640px"]')
    expect(fresh.attributes('title')).toBe(EN.freshTitle)
    expect(fresh.find('[type="warning"]').text()).toBe(EN.freshAlert)
    expect(fresh.findAll('button').map(button => button.text())).toEqual([EN.copy, EN.copy, EN.savedSecret])
    expect(fresh.find('button.mcp-view__copy').attributes('title')).toBe(EN.copy)
    expect(fresh.find('.mcp-view__note').text()).toBe(EN.freshNote)

    expect(wrapper.find('el-drawer').attributes('title')).toBe(EN.configTitle)

    useLocaleStore().setLang('zh')
    await nextTick()

    expect(head.find('button').text()).toBe('新建钥匙')
    expect(rowTags(wrapper, 0)).toEqual(['domain-a', '正常'])
    expect(create.attributes('title')).toBe('新建 MCP 钥匙')
    expect(wrapper.find('el-drawer').attributes('title')).toBe('钥匙配置')
  })

  it('labels a revoked key, an unbound key and the never-used fallback in English', async () => {
    api.listMcpKeys.mockResolvedValue({ keys: [KEY_REVOKED, { ...KEY_UNBOUND, last_used_at: null }] })
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.findAll('.table-row .mcp-view__name').map(name => name.text())).toEqual(['Agent C', 'Agent B'])
    expect(rowTags(wrapper, 0)).toEqual(['domain-a', EN.revoked])
    expect(rowButtons(wrapper, 0)).toEqual([EN.callRecords, EN.deleteRecord])

    expect(rowTags(wrapper, 1)).toEqual(['domain-a', EN.domainUnbound])
    // 域已解绑：标签外包一层 tooltip（stub 透传 content 属性）
    expect(wrapper.findAll('.table-row')[1].findAll('.el-tag')[1].element.parentElement?.getAttribute('content'))
      .toBe(EN.domainUnboundTip)
    // 已吊销/正常的钥匙不带这层提示
    expect(wrapper.findAll('.table-row')[0].find('[content]').exists()).toBe(false)
    expect(rowButtons(wrapper, 1)).toEqual([EN.callRecords, EN.configure, EN.rotate, EN.revoke])
    expect(lastUsedCell(wrapper, 1)).toBe(EN.never)
  })

  it('disables key creation at the limit with an English tooltip', async () => {
    api.listMcpKeys.mockResolvedValue({ keys: activeKeys(10) })
    const wrapper = mountView()
    await flushPromises()

    const head = wrapper.find('.mcp-view__head')
    expect(head.find('[content]').attributes('content')).toBe(EN.atLimit)
    expect(head.find('button').text()).toBe(EN.createKey)
    expect(head.find('button').attributes('disabled')).toBeDefined()
    expect(wrapper.findAll('.table-row')).toHaveLength(10)
  })

  it('renders the empty-state guide in English', async () => {
    api.listMcpKeys.mockResolvedValue({ keys: [] })
    const wrapper = mountView()
    await flushPromises()

    const hero = wrapper.find('.mcp-view__card--hero')
    expect(hero.exists()).toBe(true)
    expect(wrapper.find('el-empty').attributes('description')).toBe(EN.emptyTitle)
    // 被 <br> 切开的两句：各自成 key，<br> 保留
    const tip = hero.find('.mcp-view__empty-tip')
    expect(tip.find('br').exists()).toBe(true)
    expect(tip.text()).toBe(EN.emptyTipDomain + EN.emptyTipStart)
    expect(hero.find('button').text()).toBe(EN.createKey)
    expect(hero.find('button').attributes('disabled')).toBeUndefined()
    expect(wrapper.find('.mcp-view__table').exists()).toBe(false)
  })

  it('renders the load-failure alert with an English retry button', async () => {
    api.listMcpKeys.mockRejectedValue(new Error('list down'))
    const wrapper = mountView()
    await flushPromises()

    const alert = wrapper.find('.mcp-view > [type="error"]')
    expect(alert.text()).toBe(`${EN.loadFailedTitle}${EN.retryLoad}`)
    expect(alert.find('button').text()).toBe(EN.retryLoad)
    expect(wrapper.find('.mcp-view__card--hero').exists()).toBe(false)
    expect(wrapper.find('.mcp-view__table').exists()).toBe(true)
  })

  it('creates a key and shows the one-time secret with the English note', async () => {
    const wrapper = mountView()
    await flushPromises()
    api.createMcpKey.mockResolvedValue({ id: 'key-9', name: 'agent-b', domain: 'domain-b', key: 'kbm_plain_secret' })

    internals(wrapper).createForm = { name: ' agent-b ', domain: 'domain-b' }
    await internals(wrapper).submitCreate()
    await flushPromises()

    expect(api.createMcpKey).toHaveBeenCalledWith({ name: 'agent-b', domain: 'domain-b' })
    expect(wrapper.find('.mcp-view__fresh-key').text()).toBe('kbm_plain_secret')
    // 接入 JSON 是协议数据，不随语言变化
    expect(wrapper.find('.mcp-view__code').text()).toContain('"Authorization": "Bearer kbm_plain_secret"')
    expect(wrapper.find('.mcp-view__code').text()).toContain('"url": "http://localhost:9000/mcp"')
  })

  it('maps key-creation failures to English: unbound domain, the 10-key limit, and server pass-through', async () => {
    const wrapper = mountView()
    await flushPromises()
    const vm = internals(wrapper)
    vm.createForm = { name: 'agent-b', domain: 'domain-b' }

    // 403 域未绑定：后端未带 message → 前端兜底文案
    api.createMcpKey.mockRejectedValue({ response: { status: 403, data: { detail: { code: 'domain_not_bound' } } } })
    await vm.submitCreate()
    expect(ui.error).toHaveBeenLastCalledWith(EN.domainNotBound)

    // 409：匹配数据「上限」比对的是服务端中文原文——EN 态下这条比对必须照常生效（R2）
    api.createMcpKey.mockRejectedValue({ response: { status: 409, data: { detail: '已达钥匙数量上限' } } })
    await vm.submitCreate()
    expect(ui.error).toHaveBeenLastCalledWith(EN.limitReached)

    // 409 但服务端文案不含「上限」：原文透传
    api.createMcpKey.mockRejectedValue({ response: { status: 409, data: { detail: 'conflict: duplicate name' } } })
    await vm.submitCreate()
    expect(ui.error).toHaveBeenLastCalledWith('conflict: duplicate name')
  })

  it('confirms quick rotate, quick revoke and record deletion in English', async () => {
    api.listMcpKeys.mockResolvedValue({ keys: [KEY_ACTIVE, KEY_REVOKED] })
    api.rotateMcpKey.mockResolvedValue({ key: 'kbm_rotated' })
    const wrapper = mountView()
    await flushPromises()
    const vm = internals(wrapper)

    await vm.quickRotate(KEY_ACTIVE)
    await flushPromises()
    expect(ui.confirm).toHaveBeenLastCalledWith(
      EN.rotateMessage, EN.rotateTitle,
      { type: 'warning', confirmButtonText: EN.rotate, cancelButtonText: EN.cancel },
    )
    expect(api.rotateMcpKey).toHaveBeenCalledWith('key-1')
    expect(wrapper.find('.mcp-view__fresh-key').text()).toBe('kbm_rotated')

    await vm.quickRevoke(KEY_ACTIVE)
    await flushPromises()
    expect(ui.confirm).toHaveBeenLastCalledWith(
      EN.revokeMessage, EN.revokeTitle,
      { type: 'warning', confirmButtonText: EN.revoke, cancelButtonText: EN.cancel },
    )
    expect(api.revokeMcpKey).toHaveBeenCalledWith('key-1')
    expect(ui.success).toHaveBeenLastCalledWith(EN.keyRevoked)

    await wrapper.vm.deleteRevoked(KEY_REVOKED)
    await flushPromises()
    expect(ui.confirm).toHaveBeenLastCalledWith(
      EN.deleteMessage, EN.deleteTitle,
      { type: 'warning', confirmButtonText: EN.deleteRecord, cancelButtonText: EN.cancel },
    )
    expect(api.deleteMcpKey).toHaveBeenCalledWith('key-3')
    expect(ui.success).toHaveBeenLastCalledWith(EN.keyRecordDeleted)
  })

  it('titles the config drawer with the key name in English', async () => {
    const wrapper = mountView()
    await flushPromises()

    await internals(wrapper).openConfig(KEY_ACTIVE)
    await flushPromises()

    expect(wrapper.find('el-drawer').attributes('title')).toBe(EN.configTitleNamed)
    expect(artifactsApi.list).toHaveBeenCalledWith({ domain_id: 'domain-a' })
  })

  it('reports copy success and failure in English', async () => {
    const wrapper = mountView()
    await flushPromises()
    const writeText = vi.fn().mockResolvedValue(undefined)
    const ownClipboard = Object.getOwnPropertyDescriptor(navigator, 'clipboard')
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    const ownExecCommand = Object.getOwnPropertyDescriptor(document, 'execCommand')
    document.execCommand = vi.fn(() => false)

    try {
      await internals(wrapper).copy('abc')
      expect(writeText).toHaveBeenCalledWith('abc')
      expect(ui.success).toHaveBeenCalledWith(EN.copied)

      // 非安全上下文无 navigator.clipboard → execCommand 兜底也失败
      writeText.mockRejectedValue(new Error('denied'))
      await internals(wrapper).copy('abc')
      expect(document.execCommand).toHaveBeenCalledWith('copy')
      expect(ui.warning).toHaveBeenCalledWith(EN.copyFailed)
    } finally {
      if (ownExecCommand) Object.defineProperty(document, 'execCommand', ownExecCommand)
      else delete (document as { execCommand?: unknown }).execCommand
      if (ownClipboard) Object.defineProperty(navigator, 'clipboard', ownClipboard)
      else delete (navigator as { clipboard?: unknown }).clipboard
    }
  })

  it('leaks no Chinese anywhere in EN', async () => {
    api.listMcpKeys.mockResolvedValue({ keys: [KEY_ACTIVE, KEY_REVOKED, KEY_UNBOUND] })
    api.createMcpKey.mockResolvedValue({ id: 'key-9', name: 'agent-b', domain: 'domain-b', key: 'kbm_plain_secret' })
    const wrapper = mountView()
    await flushPromises()
    internals(wrapper).createForm = { name: 'agent-b', domain: 'domain-b' }
    await internals(wrapper).submitCreate()
    await flushPromises()

    expect(wrapper.text()).not.toMatch(LEAK)
    expect(attributeLeaks(wrapper)).toEqual([])
  })
})
