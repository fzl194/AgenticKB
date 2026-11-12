import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

const api = vi.hoisted(() => ({
  listMcpKeys: vi.fn(),
  deleteMcpKey: vi.fn(),
  listKbs: vi.fn(),
}))
const artifactsApi = vi.hoisted(() => ({ list: vi.fn() }))
const ui = vi.hoisted(() => ({
  confirm: vi.fn(), success: vi.fn(), error: vi.fn(), warning: vi.fn(),
}))

vi.mock('@/api/kb', () => ({ useKbApi: () => api }))
vi.mock('@/api/artifacts', () => ({ useArtifactsApi: () => artifactsApi }))
vi.mock('@/api/proxyClient', () => ({
  apiErrorDetail: async () => '失败',
  createProxyClient: vi.fn(() => ({ get: vi.fn() })),
  extractOne: vi.fn(),
  installAuthInterceptors: vi.fn(),
}))
vi.mock('@/stores/domain', () => ({
  useDomainStore: () => ({ currentDomain: 'generic', enabledDomains: [], fetchDomains: vi.fn() }),
}))
vi.mock('@/stores/auth', () => ({
  useAuthStore: () => ({ user: { username: 'alice' } }),
}))
vi.mock('element-plus', () => ({
  ElMessage: { success: ui.success, error: ui.error, warning: ui.warning },
  ElMessageBox: { confirm: ui.confirm },
}))

import McpAccessView from '../McpAccessView.vue'

describe('McpAccessView revoked-key deletion', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    ui.confirm.mockResolvedValue(undefined)
    api.listMcpKeys.mockResolvedValue({ keys: [{
      id: 'key-1', name: '旧钥匙', domain: 'generic', key_prefix: 'kbm_old',
      status: 'revoked', created_at: '2026-09-28T00:00:00Z', rotated_at: null,
      last_used_at: null, domain_bound: true, open_kb_ids: [], open_tools: null,
      instructions: null, tool_descriptions: null,
    }] })
    api.deleteMcpKey.mockResolvedValue(undefined)
    api.listKbs.mockResolvedValue([])
    artifactsApi.list.mockResolvedValue([])
  })

  it('confirms deletion, hides the revoked row, and keeps active keys untouched', async () => {
    const wrapper = mount(McpAccessView, {
      global: { stubs: { McpKeyConfigPanel: true } },
    })
    await flushPromises()
    const revoked = wrapper.vm.keys[0]

    await wrapper.vm.deleteRevoked(revoked)

    expect(ui.confirm).toHaveBeenCalledWith(
      expect.stringContaining('旧钥匙'),
      '删除钥匙记录',
      expect.objectContaining({ type: 'warning' }),
    )
    expect(api.deleteMcpKey).toHaveBeenCalledWith('key-1')
    expect(api.listMcpKeys).toHaveBeenCalledTimes(2)
    expect(ui.success).toHaveBeenCalledWith('钥匙记录已删除')
  })

  it('only offers published artifacts from the key domain', async () => {
    const wrapper = mount(McpAccessView, {
      global: { stubs: { McpKeyConfigPanel: true } },
    })
    await flushPromises()
    artifactsApi.list.mockResolvedValue([
      { artifact_id: 'published', domain_id: 'generic', title: '正式制品', status: 'published' },
      { artifact_id: 'draft', domain_id: 'generic', title: '草稿制品', status: 'draft' },
      { artifact_id: 'deprecated', domain_id: 'generic', title: '停用制品', status: 'deprecated' },
      { artifact_id: 'other', domain_id: 'other', title: '其他域制品', status: 'published' },
    ])
    const vm = wrapper.vm as unknown as {
      keys: Record<string, unknown>[]
      openConfig: (row: Record<string, unknown>) => Promise<void>
      configDomainArtifacts: { artifact_id: string }[]
    }
    await vm.openConfig({ ...vm.keys[0], status: 'active' })
    expect(artifactsApi.list).toHaveBeenCalledWith({ domain_id: 'generic' })
    expect(vm.configDomainArtifacts.map(a => a.artifact_id))
      .toEqual(['published'])
  })
})
