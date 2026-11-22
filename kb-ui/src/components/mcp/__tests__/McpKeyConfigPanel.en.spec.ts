/**
 * McpKeyConfigPanel 的 EN 渲染断言（四区英化 Task 7 / 规则 R11）。
 *
 * mock 与挂载代码复制自同目录 McpKeyConfigPanel.spec.ts，区别只在语言：
 * 全局语言键必须在 pinia 创建**之前**写入——locale store 在实例化时读它。
 * 装进插件的 pinia 与活动 pinia 是同一个实例（否则 t() 读不到切换）。
 *
 * 与样板 harness 的唯一差异：多注册一个只渲染 #title 插槽的 ElAlert stub——
 * element-plus 是部分 mock，未解析的 <el-alert> 不渲染具名插槽，
 * 轮换后的一次性明文提示（#title）无从断言。
 *
 * 夹具数据一律 ASCII：EN 态下整块界面（含未解析组件上的 label/description/
 * title/placeholder 属性）出现任何 CJK 字符或全角标点都算泄漏。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import type { Pinia } from 'pinia'

const api = vi.hoisted(() => ({
  getMcpToolMeta: vi.fn(),
  putMcpKeyConfig: vi.fn(),
  rotateMcpKey: vi.fn(),
  revokeMcpKey: vi.fn(),
  putMcpKeyOpenKbs: vi.fn(),
  putMcpKeyOpenArtifacts: vi.fn(),
}))
const ui = vi.hoisted(() => ({
  confirm: vi.fn(), success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn(),
}))

vi.mock('@/api/kb', () => ({ useKbApi: () => api }))
vi.mock('@/api/proxyClient', () => ({ apiErrorDetail: async () => 'failed' }))
vi.mock('element-plus', () => ({
  ElMessage: { success: ui.success, error: ui.error, warning: ui.warning, info: ui.info },
  ElMessageBox: { confirm: ui.confirm },
}))

import McpKeyConfigPanel, { resetMcpToolMetaCache } from '../McpKeyConfigPanel.vue'
import type { McpKeyItem } from '@/types/kb'
import { useLocaleStore } from '@/stores/locale'

enableAutoUnmount(afterEach)

/** CJK 文字（4E00–9FFF）+ CJK 标点「」等（3000–303F）+ 全角形（）：等（FF00–FFEF）都算泄漏。 */
const LEAK = /[\u3000-\u303f\u4e00-\u9fff\uff00-\uffef]/

const META = {
  instructions: 'Default prompt: multi-domain knowledge evidence retrieval.',
  tools: [
    {
      name: 'search_knowledge',
      description: 'Default: search knowledge evidence',
      parameters: {
        properties: {
          query: { type: 'string', description: 'The original user question.' },
          domain: { anyOf: [{ type: 'string' }, { type: 'null' }], description: 'Optional, validation only.' },
        },
        required: ['query'],
      },
    },
    { name: 'get_knowledge', description: 'Default: deep read', parameters: { properties: {}, required: [] } },
    { name: 'manage_files', description: 'Default: manage files', parameters: { properties: {}, required: [] } },
  ],
}

const KEY = {
  id: 'key-1', name: 'dev-assistant', domain: 'generic', key_prefix: 'kbm_x',
  status: 'active' as const, created_at: '2026-09-28T00:00:00Z', rotated_at: null,
  last_used_at: null, domain_bound: true, open_kb_ids: [], open_tools: null,
  instructions: null, tool_descriptions: null,
}

/** 期望文案独立手写（不从字典导入）——字典改坏时这里才会红。 */
const EN = {
  configTitle: 'Agent Access Configuration',
  configDesc: 'Pick a format, click Copy, and paste it into your Agent / MCP client ({endpoint}).',
  placeholderPrefix: 'Normally shows ',
  placeholderSuffix: ' as a placeholder — the plaintext secret is shown only once, when the key is generated or rotated; if it is lost, the only option is to rotate.',
  formatGeneric: 'Generic',
  openToolsTitle: 'Exposed Tools',
  openToolsDesc: 'Tools turned off are completely invisible to the Agent using this key. Keep at least one turned on.',
  toolLabels: [
    'Search knowledge evidence (the domain argument can be omitted for a single domain)',
    'Deep read: browse the hierarchy / walk directories level by level / read the original text / view capabilities / navigate / query tables',
    'Manage files: upload to a specified directory / replace any local file (zip archives are extracted automatically and queued for mining; delete, move, and rename are not provided)',
  ],
  openKbsTitle: 'Exposed Knowledge Bases (generic domain)',
  openKbsDesc: 'This key can only access the knowledge bases checked here. The data source is fixed to the domain the key belongs to, regardless of the domain currently selected at the top of the page.',
  openKbsEmpty: 'This domain has no knowledge base you can see',
  openArtifactsTitle: 'Exposed Knowledge Artifacts (generic domain)',
  openArtifactsDesc: 'Through this key, the Agent can only query the checked knowledge artifacts (tables: description card and row queries; Wiki: description card, page search, full-page reading, and link navigation). Candidates = published artifacts you can see in this domain (public, or you are a member); unchecked = the artifact channel is closed for this key.',
  openArtifactsEmpty: 'This domain has no published knowledge artifact you can expose',
  promptTitle: 'Prompt and Tool Descriptions',
  promptDesc: 'The default text is pre-filled; edit it directly. Content identical to the default is delivered as the default (so it also follows future updates to the default text).',
  restoreDefaults: 'Restore Defaults',
  promptPlaceholder: 'Loading default prompt…',
  descPlaceholder: 'Loading default description…',
  rotateButton: 'Rotate Key (the old secret stops working immediately)',
  revokeButton: 'Revoke This Key (irreversible)',
  rotateMessage: 'After rotation, the old secret of "dev-assistant" stops working immediately, and Agents still using it will be disconnected. Continue?',
  keyRotated: 'Secret rotated; the old secret is now invalid',
  freshKeyTitle: 'New secret (shown only this once): ',
  revokeMessage: 'After revocation, "dev-assistant" stops working immediately and cannot be recovered; Agents using it will be disconnected. Revoke?',
  keyRevoked: 'Key revoked',
  defaultsRestored: 'Defaults restored; click Save to apply',
  keepOneTool: 'Keep at least one tool turned on',
  toolsSaved: 'Tool switches saved',
  openKbsUpdated: 'Exposed knowledge bases updated',
  artifactsPartiallySaved: 'Some artifacts were not saved: port-spec-table, mop-zone. Refresh the page, check the artifact permissions, and try again.',
  artifactsUpdated: 'Exposed artifacts updated',
  promptSaved: 'Prompt saved',
  copied: 'Copied',
  copyFailed: 'Copy failed; please select the text and copy it manually',
}

let pinia: Pinia

// 顺序铁律：全局键先于 pinia 写入——store 在创建时读它
beforeEach(() => {
  localStorage.setItem('kb-ui-lang', 'en')
  pinia = createPinia()
  setActivePinia(pinia)
  vi.clearAllMocks()
  resetMcpToolMetaCache()
  ui.confirm.mockResolvedValue(undefined)
  api.getMcpToolMeta.mockResolvedValue(META)
  api.putMcpKeyConfig.mockResolvedValue(KEY)
  api.rotateMcpKey.mockResolvedValue({ key: 'kbm_plain_new_key' })
})
afterEach(() => localStorage.removeItem('kb-ui-lang'))

type Kb = { id: string; name: string; document_count: number }
type Artifact = { artifact_id: string; title: string; status: string; visibility?: string }

function mountPanel(keyItem: McpKeyItem = KEY, domainKbs: Kb[] = [], domainArtifacts: Artifact[] = []) {
  return mount(McpKeyConfigPanel, {
    props: { keyItem, domainKbs, domainArtifacts },
    global: {
      plugins: [pinia],
      stubs: { ElAlert: { template: '<div><slot name="title" /></div>' } },
    },
  })
}

/** 未 defineExpose 的内部状态与动作：经 setupState 代理访问（与 McpAccessDelete.spec.ts 同法）。 */
interface PanelInternals {
  toolOn: Record<string, boolean>
  selectedKbs: string[]
  saveTools: () => Promise<void>
  saveOpenKbs: () => Promise<void>
  revoke: () => Promise<void>
  copy: (text: string) => Promise<void>
}
const internals = (wrapper: ReturnType<typeof mountPanel>) => wrapper.vm as unknown as PanelInternals

/** 未解析组件上的静态属性（label/description/title/placeholder）同样不得残留中文。 */
function attributeLeaks(wrapper: ReturnType<typeof mountPanel>): string[] {
  return wrapper.findAll('*').flatMap(element =>
    Object.values(element.attributes()).filter(value => LEAK.test(value)),
  )
}

describe('McpKeyConfigPanel (EN)', () => {
  it('renders every section heading in English, Agent access configuration first', async () => {
    const wrapper = mountPanel()
    await flushPromises()

    expect(wrapper.findAll('.mkey-panel__title').map(heading => heading.text())).toEqual([
      EN.configTitle,
      EN.openToolsTitle,
      EN.openKbsTitle,
      EN.openArtifactsTitle,
      EN.promptTitle,
    ])
    // el-descriptions-item 未进全局 stub：label 以属性落在未解析元素上
    expect(wrapper.findAll('el-descriptions-item').map(item => item.attributes('label'))).toEqual([
      'Name', 'Domain', 'Secret ID', 'Last Used', 'Created At', 'Last Rotated',
    ])
    expect(wrapper.findAll('el-descriptions-item').map(item => item.text())).toEqual([
      'dev-assistant', 'generic', 'kbm_x…', 'Never', '2026-09-28 00:00:00', '—',
    ])
  })

  it('translates the tool labels that come from the ALL_TOOLS constant table (R8: labelKey + t() at render)', async () => {
    const wrapper = mountPanel()
    await flushPromises()

    expect(wrapper.findAll('.mkey-panel__tool-name').map(name => name.text()))
      .toEqual(['search_knowledge', 'get_knowledge', 'manage_files'])
    expect(wrapper.findAll('.mkey-panel__tool-desc').map(desc => desc.text())).toEqual(EN.toolLabels)
    expect(wrapper.findAll('.mkey-panel__desc-name').map(name => name.text()))
      .toEqual(['search_knowledge', 'get_knowledge', 'manage_files'])
  })

  it('renders the access-config description in English: endpoint interpolated, <code> placeholder kept inline', async () => {
    const wrapper = mountPanel()
    await flushPromises()

    expect(wrapper.findAll('.mkey-panel__desc')[0].text()).toBe(
      EN.configDesc.replace('{endpoint}', 'localhost:9000/mcp')
        + ' ' + EN.placeholderPrefix + '__KEY__' + EN.placeholderSuffix,
    )
    expect(wrapper.find('.mkey-panel__desc code').text()).toBe('__KEY__')
    expect(wrapper.find('el-radio-button').text()).toBe(EN.formatGeneric)
    // 接入 JSON 是协议数据，不随语言变化
    expect(wrapper.find('.mkey-panel__code').text()).toContain('"url": "http://localhost:9000/mcp"')
    expect(wrapper.find('button.mkey-panel__copy').attributes('title')).toBe('Copy')
    expect(wrapper.find('button.mkey-panel__copy').text()).toBe('Copy')
  })

  it('translates static attributes: empty texts, placeholders, parameter table headers and the collapse title', async () => {
    const wrapper = mountPanel()
    await flushPromises()

    expect(wrapper.findAll('el-empty').map(empty => empty.attributes('description')))
      .toEqual([EN.openKbsEmpty, EN.openArtifactsEmpty])
    expect(wrapper.findAll('input[type="textarea"]').map(input => input.attributes('placeholder')))
      .toEqual([EN.promptPlaceholder, EN.descPlaceholder, EN.descPlaceholder, EN.descPlaceholder])
    expect(wrapper.find('el-collapse-item').attributes('title')).toBe('Parameters (2)')
    expect(wrapper.findAll('.mkey-panel__param-table th').map(th => th.text()))
      .toEqual(['Parameter', 'Type', 'Required', 'Description'])
    // 参数名/类型/说明来自服务端 schema，原样透出；只有「必填」的是/否走界面语言
    expect(wrapper.findAll('.mkey-panel__param-table tbody td').map(td => td.text())).toEqual([
      'query', 'string', 'Yes', 'The original user question.',
      'domain', 'string', 'No', 'Optional, validation only.',
    ])
    // 卡头与提示词操作区的按钮
    expect(wrapper.findAll('.mkey-panel__card-head > button, .mkey-panel__prompt-actions button')
      .map(button => button.text())).toEqual(['Save', 'Save', 'Save', 'Restore Defaults', 'Save'])
    expect(wrapper.findAll('.mkey-panel__card--danger button').map(button => button.text()))
      .toEqual([EN.rotateButton, EN.revokeButton])
    expect(wrapper.findAll('.mkey-panel__desc')[4].text()).toBe(EN.promptDesc)
  })

  it('labels open knowledge bases, artifact status badges and section descriptions in English', async () => {
    const wrapper = mountPanel(
      KEY,
      [{ id: 'kb-1', name: 'manual-a', document_count: 3 }],
      [
        { artifact_id: 'art-a', title: 'port-spec-table', status: 'published', visibility: 'public' },
        { artifact_id: 'art-b', title: 'mop-zone', status: 'deprecated' },
        { artifact_id: 'art-c', title: 'draft-wiki', status: 'draft' },
      ],
    )
    await flushPromises()

    expect(wrapper.findAll('.mkey-panel__title').map(heading => heading.text())).toEqual([
      EN.configTitle, EN.openToolsTitle, EN.openKbsTitle, EN.openArtifactsTitle, EN.promptTitle,
    ])
    const descs = wrapper.findAll('.mkey-panel__desc').map(desc => desc.text())
    expect(descs).toEqual([
      expect.any(String), EN.openToolsDesc, EN.openKbsDesc, EN.openArtifactsDesc, EN.promptDesc,
    ])
    expect(wrapper.findAll('.mkey-panel__kb-meta').map(meta => meta.text())).toEqual([
      '3 documents', 'Published · Public', 'Deprecated', 'Draft',
    ])
    expect(wrapper.find('el-empty').exists()).toBe(false)
  })

  it('emits English feedback for the prompt, tool switches, open bases and open artifacts', async () => {
    const wrapper = mountPanel(
      KEY,
      [{ id: 'kb-1', name: 'manual-a', document_count: 1 }],
      [{ artifact_id: 'art-a', title: 'port-spec-table', status: 'published' }],
    )
    await flushPromises()
    const vm = internals(wrapper)

    wrapper.vm.restoreDefaults()
    expect(ui.info).toHaveBeenCalledWith(EN.defaultsRestored)

    vm.toolOn = { search_knowledge: false, get_knowledge: false, manage_files: false }
    await vm.saveTools()
    expect(api.putMcpKeyConfig).not.toHaveBeenCalled()
    expect(ui.warning).toHaveBeenCalledWith(EN.keepOneTool)

    vm.toolOn = { search_knowledge: false, get_knowledge: true, manage_files: true }
    await vm.saveTools()
    expect(api.putMcpKeyConfig).toHaveBeenCalledWith('key-1', { open_tools: ['get_knowledge', 'manage_files'] })
    expect(ui.success).toHaveBeenCalledWith(EN.toolsSaved)

    vm.selectedKbs = ['kb-1']
    api.putMcpKeyOpenKbs.mockResolvedValue({ open_kb_ids: ['kb-1'] })
    await vm.saveOpenKbs()
    expect(api.putMcpKeyOpenKbs).toHaveBeenCalledWith('key-1', ['kb-1'])
    expect(ui.success).toHaveBeenCalledWith(EN.openKbsUpdated)

    // 服务端剔除越权制品 → 未保存提示的标题清单用英文分隔符拼接
    wrapper.vm.selectedArtifacts = ['art-a']
    api.putMcpKeyOpenArtifacts.mockResolvedValue({ open_artifact_ids: ['art-a'] })
    await wrapper.vm.saveOpenArtifacts()
    expect(ui.success).toHaveBeenCalledWith(EN.artifactsUpdated)

    wrapper.vm.instructions = 'Custom prompt for my agent.'
    await wrapper.vm.savePrompt()
    expect(api.putMcpKeyConfig).toHaveBeenCalledWith('key-1', {
      instructions: 'Custom prompt for my agent.',
      tool_descriptions: {},
    })
    expect(ui.success).toHaveBeenCalledWith(EN.promptSaved)
  })

  it('joins unsaved artifact titles with the English list separator', async () => {
    const wrapper = mountPanel(KEY, [], [
      { artifact_id: 'art-a', title: 'port-spec-table', status: 'published' },
      { artifact_id: 'art-b', title: 'mop-zone', status: 'published' },
    ])
    await flushPromises()
    // 服务端两个都没落（越权被静默剔除）→ 两个标题都进「未保存」提示，用英文分隔符拼接
    wrapper.vm.selectedArtifacts = ['art-a', 'art-b']
    api.putMcpKeyOpenArtifacts.mockResolvedValue({ open_artifact_ids: [] })
    await wrapper.vm.saveOpenArtifacts()

    expect(api.putMcpKeyOpenArtifacts).toHaveBeenCalledWith('key-1', ['art-a', 'art-b'])
    expect(ui.success).not.toHaveBeenCalled()
    expect(ui.warning).toHaveBeenCalledWith(EN.artifactsPartiallySaved)
  })

  it('confirms rotation in English and shows the one-time secret alert', async () => {
    const wrapper = mountPanel()
    await flushPromises()

    await wrapper.vm.rotate()
    await flushPromises()

    expect(ui.confirm).toHaveBeenCalledWith(
      EN.rotateMessage, 'Rotate Key',
      { type: 'warning', confirmButtonText: 'Rotate', cancelButtonText: 'Cancel' },
    )
    expect(ui.success).toHaveBeenCalledWith(EN.keyRotated)
    expect(wrapper.find('.mkey-panel__fresh').text()).toBe(`${EN.freshKeyTitle}kbm_plain_new_keyCopy`)
    // 轮换后的新明文预填进接入 JSON
    expect(wrapper.find('.mkey-panel__code').text()).toContain('Bearer kbm_plain_new_key')
  })

  it('confirms revocation in English', async () => {
    const wrapper = mountPanel()
    await flushPromises()

    await internals(wrapper).revoke()
    await flushPromises()

    expect(ui.confirm).toHaveBeenCalledWith(
      EN.revokeMessage, 'Revoke Key',
      { type: 'warning', confirmButtonText: 'Revoke', cancelButtonText: 'Cancel' },
    )
    expect(api.revokeMcpKey).toHaveBeenCalledWith('key-1')
    expect(ui.success).toHaveBeenCalledWith(EN.keyRevoked)
  })

  it('reports copy success and failure in English', async () => {
    const wrapper = mountPanel()
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

  it('leaks no Chinese in EN and follows a runtime language switch reactively', async () => {
    const wrapper = mountPanel(
      KEY,
      [{ id: 'kb-1', name: 'manual-a', document_count: 2 }],
      [{ artifact_id: 'art-a', title: 'port-spec-table', status: 'published', visibility: 'public' }],
    )
    await flushPromises()

    expect(wrapper.text()).not.toMatch(LEAK)
    expect(attributeLeaks(wrapper)).toEqual([])

    // 常量表只存 labelKey：切语言后重渲染才求值，R8 的响应性在这里兑现
    useLocaleStore().setLang('zh')
    await nextTick()

    expect(wrapper.find('.mkey-panel__title').text()).toBe('Agent 接入配置')
    expect(wrapper.findAll('.mkey-panel__tool-desc')[0].text()).toBe('检索知识证据（domain 单域免传）')
    expect(wrapper.findAll('el-descriptions-item')[0].attributes('label')).toBe('名称')
    expect(wrapper.findAll('el-empty').length).toBe(0)
    expect(wrapper.findAll('.mkey-panel__kb-meta').map(meta => meta.text())).toEqual(['2 文档', '已发布 · 公开'])
  })
})
