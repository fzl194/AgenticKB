/**
 * McpKeyConfigPanel——钥匙配置抽屉面板。
 *
 * 钉四件事：
 * 1. 默认文案预填（tool-meta 晚到也能补齐，用户已动手则不打扰）；
 * 2. 保存语义：内容与默认一致 → 提交空（后端 空=恢复默认，不冻结默认副本）；
 * 3. 恢复默认值按钮一键填回；
 * 4. 轮换后父组件 reload 换新 keyItem 对象不得抹掉一次性明文（内网实发缺陷）。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'

const api = vi.hoisted(() => ({
  getMcpToolMeta: vi.fn(),
  putMcpKeyConfig: vi.fn(),
  rotateMcpKey: vi.fn(),
  revokeMcpKey: vi.fn(),
  putMcpKeyOpenKbs: vi.fn(),
}))
const ui = vi.hoisted(() => ({
  confirm: vi.fn(), success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn(),
}))

vi.mock('@/api/kb', () => ({ useKbApi: () => api }))
vi.mock('@/api/proxyClient', () => ({ apiErrorDetail: async () => '失败' }))
vi.mock('element-plus', () => ({
  ElMessage: { success: ui.success, error: ui.error, warning: ui.warning, info: ui.info },
  ElMessageBox: { confirm: ui.confirm },
}))

import McpKeyConfigPanel, { resetMcpToolMetaCache } from '../McpKeyConfigPanel.vue'
import type { McpKeyItem } from '@/types/kb'

enableAutoUnmount(afterEach)

const META = {
  instructions: '默认提示词——多领域知识证据检索服务。',
  tools: [
    {
      name: 'search_knowledge',
      description: '默认：检索知识证据',
      parameters: {
        properties: {
          query: { type: 'string', description: '用户原问题。' },
          domain: { anyOf: [{ type: 'string' }, { type: 'null' }], description: '可选，仅校验。' },
        },
        required: ['query'],
      },
    },
    { name: 'get_knowledge', description: '默认：深入读取', parameters: { properties: {}, required: [] } },
    { name: 'upload_document', description: '默认：上传文件', parameters: { properties: {}, required: [] } },
  ],
}

const KEY = {
  id: 'key-1', name: '研发助手', domain: 'generic', key_prefix: 'kbm_x',
  status: 'active' as const, created_at: '2026-09-28T00:00:00Z', rotated_at: null,
  last_used_at: null, domain_bound: true, open_kb_ids: [], open_tools: null,
  instructions: null, tool_descriptions: null,
}

function mountPanel(
  keyItem: McpKeyItem = KEY,
  domainKbs: Array<{ id: string; name: string; document_count: number }> = [],
) {
  return mount(McpKeyConfigPanel, { props: { keyItem, domainKbs } })
}

describe('McpKeyConfigPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    resetMcpToolMetaCache()
    ui.confirm.mockResolvedValue(undefined)
    api.getMcpToolMeta.mockResolvedValue(META)
    api.putMcpKeyConfig.mockResolvedValue(KEY)
    api.rotateMcpKey.mockResolvedValue({ key: 'kbm_plain_new_key' })
  })

  it('预填默认提示词与默认工具说明；参数表按 schema 渲染', async () => {
    const wrapper = mountPanel()
    await flushPromises()

    expect(wrapper.vm.instructions).toBe(META.instructions)
    expect(wrapper.vm.toolDescs['search_knowledge']).toBe('默认：检索知识证据')

    const table = wrapper.find('.mkey-panel__param-table')
    expect(table.exists()).toBe(true)
    const text = table.text()
    expect(text).toContain('query')
    expect(text).toContain('用户原问题')
    expect(text).toContain('string')          // anyOf [string,null] 归并出可读类型
    expect(text).toContain('是')              // query 必填
    expect(text).toContain('domain')          // 可选参数也在表里
    expect(text).toContain('否')
  })

  it('已存自定义优先生效值；恢复默认按钮一键填回', async () => {
    const wrapper = mountPanel({
      ...KEY, instructions: '我的定制提示词', tool_descriptions: { search_knowledge: '我的定制说明' },
    })
    await flushPromises()

    expect(wrapper.vm.instructions).toBe('我的定制提示词')
    expect(wrapper.vm.toolDescs['search_knowledge']).toBe('我的定制说明')

    wrapper.vm.restoreDefaults()
    expect(wrapper.vm.instructions).toBe(META.instructions)
    expect(wrapper.vm.toolDescs['search_knowledge']).toBe('默认：检索知识证据')
  })

  it('保存：与默认一致的文案不作为自定义提交（instructions 传空、descs 省略默认项）', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    // 未改动：预填的默认与默认一致 → 脏检查为否
    expect(wrapper.vm.promptDirty).toBe(false)

    wrapper.vm.instructions = `${META.instructions}\n补充：优先引用表格。`
    wrapper.vm.toolDescs['search_knowledge'] = `${META.tools[0].description}（改）`
    wrapper.vm.toolDescs['get_knowledge'] = META.tools[1].description  // 保持默认

    await wrapper.vm.savePrompt()
    await flushPromises()

    expect(api.putMcpKeyConfig).toHaveBeenCalledWith('key-1', {
      instructions: `${META.instructions}\n补充：优先引用表格。`,
      tool_descriptions: { search_knowledge: `${META.tools[0].description}（改）` },  // get_knowledge 与默认一致被省略
    })
  })

  it('轮换后父组件换新 keyItem 对象（同 id）不抹掉一次性明文', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    ui.confirm.mockResolvedValue(undefined)

    await wrapper.vm.rotate()
    await flushPromises()
    expect(wrapper.vm.freshKey).toBe('kbm_plain_new_key')

    // 父组件 @rotated=reload 的效果：同 id 新对象 + 新 domainKbs 数组触发 initFromKey
    await wrapper.setProps({
      keyItem: { ...KEY, rotated_at: '2026-09-29T00:00:00Z' },
      domainKbs: [{ id: 'kb-1', name: '手册库', document_count: 2 }],
    })
    await flushPromises()

    expect(wrapper.vm.freshKey).toBe('kbm_plain_new_key')
  })

  it('轮换后同钥匙的保存刷新（updated 换新对象）同样不抹掉明文', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    await wrapper.vm.rotate()
    await flushPromises()
    expect(wrapper.vm.freshKey).toBe('kbm_plain_new_key')

    // 用户在明文还展示着的时候改了工具说明并保存 → 父组件 onPanelUpdated 换新对象
    wrapper.vm.toolDescs['search_knowledge'] = '轮换后顺手改的说明'
    await wrapper.vm.savePrompt()
    await flushPromises()
    await wrapper.setProps({
      keyItem: { ...KEY, tool_descriptions: { search_knowledge: '轮换后顺手改的说明' } },
    })
    await flushPromises()

    expect(wrapper.vm.freshKey).toBe('kbm_plain_new_key')
  })

  it('换一把钥匙则明文清空（一次性展示不跨钥匙泄漏）', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    await wrapper.vm.rotate()
    await flushPromises()
    expect(wrapper.vm.freshKey).toBe('kbm_plain_new_key')

    await wrapper.setProps({ keyItem: { ...KEY, id: 'key-2', name: '另一把' } })
    await flushPromises()

    expect(wrapper.vm.freshKey).toBe('')
  })

  it('meta 拉失败不打断配置：字段为空、保存仍走自定义提交', async () => {
    api.getMcpToolMeta.mockRejectedValue(new Error('meta down'))
    const wrapper = mountPanel()
    await flushPromises()

    expect(wrapper.vm.instructions).toBe('')
    wrapper.vm.instructions = '纯自定义提示词'
    await wrapper.vm.savePrompt()
    await flushPromises()

    expect(api.putMcpKeyConfig).toHaveBeenCalledWith('key-1', {
      instructions: '纯自定义提示词',
      tool_descriptions: {},
    })
  })
})
