import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import type { VueWrapper } from '@vue/test-utils'

const state = vi.hoisted(() => ({
  ui: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
  clipboard: { copy: vi.fn() },
}))

vi.mock('element-plus', () => ({
  ElMessage: {
    success: state.ui.success, error: state.ui.error, warning: state.ui.warning,
  },
}))
vi.mock('@/utils/clipboard', () => ({ copyToClipboard: state.clipboard.copy }))

import ArtifactUsagePanel from '../ArtifactUsagePanel.vue'
import type { Artifact } from '@/types/artifacts'

// 手写桩（与 DetailView.spec 同套路）：descriptions 行渲染 label prop + 插槽；
// el-button 桩单根原生 button——@click 监听器 fallthrough 到根元素，可触发；
// RouterLink 桩把 to（string 路由）暴露为 href 供断言。
const stubs = {
  ElDescriptions: { template: '<div class="el-descriptions-stub"><slot /></div>' },
  ElDescriptionsItem: {
    props: ['label'],
    template: '<div class="el-descriptions-item-stub"><span class="stub-label">{{ label }}</span><slot /></div>',
  },
  ElButton: { template: '<button class="el-button-stub"><slot /></button>' },
  RouterLink: {
    props: ['to'],
    template: '<a class="router-link-stub" :href="to"><slot /></a>',
  },
}

const baseArtifact: Artifact = {
  artifact_id: 'art-1', domain_id: 'plant-a', kind: 'table',
  title: 'MOP 表', description: 'MOP 责任域对照', usage_note: '按责任域查询',
  owner_user_id: 'u1', status: 'published', visibility: 'private',
  current_revision: 2, draft_open: false,
  created_at: '2026-10-16T08:00:00+00:00', updated_at: '2026-10-16T09:00:00+00:00',
  my_role: 'owner', artifact_ref: 'art:art-1',
}

function mountPanel(overrides: Partial<Artifact> = {}): VueWrapper {
  return mount(ArtifactUsagePanel, {
    props: { artifact: { ...baseArtifact, ...overrides } },
    global: { stubs },
  })
}

beforeEach(() => {
  state.ui.success.mockReset()
  state.ui.error.mockReset()
  state.ui.warning.mockReset()
  state.clipboard.copy.mockReset().mockResolvedValue(true)
})

describe('ArtifactUsagePanel — C01 运行态说明卡', () => {
  it('table 制品：显示描述/使用说明/当前版本与结构化查询消费方式', () => {
    const text = mountPanel().text()
    expect(text).toContain('MOP 责任域对照')
    expect(text).toContain('按责任域查询')
    expect(text).toContain('r2')
    expect(text).toContain('结构化查询')
    expect(text).toContain('筛选列')
    expect(text).toContain('聚合')
    expect(text).toContain('网页端运行态直接浏览表格')
    // 消费方式按 kind 分派——表格不出现 wiki 措辞
    expect(text).not.toContain('链接导航')
  })

  it('wiki 制品：消费方式为搜索页面/整页读取/链接导航', () => {
    const text = mountPanel({ kind: 'wiki' }).text()
    expect(text).toContain('搜索')
    expect(text).toContain('整页读取')
    expect(text).toContain('链接导航')
    expect(text).toContain('网页端运行态直接阅读')
    expect(text).not.toContain('结构化查询')
  })

  it('usage_note 未填写：占位提示（发布前必填）', () => {
    expect(mountPanel({ usage_note: '' }).text()).toContain('未填写（发布前必填）')
  })

  it('rev0 未发布：当前版本显示「未发布」', () => {
    expect(mountPanel({ current_revision: 0 }).text()).toContain('未发布')
  })
})

describe('ArtifactUsagePanel — artifact_ref 引用键（不前端拼装）', () => {
  it('响应带 artifact_ref：显示值，点击复制写剪贴板并提示成功', async () => {
    const wrapper = mountPanel()
    expect(wrapper.text()).toContain('art:art-1')
    expect(wrapper.find('[data-test="copy-artifact-ref"]').exists()).toBe(true)

    await wrapper.find('[data-test="copy-artifact-ref"]').trigger('click')
    expect(state.clipboard.copy).toHaveBeenCalledWith('art:art-1')
    expect(state.ui.success).toHaveBeenCalled()
  })

  it('复制失败（降级 execCommand 也失败）：提示手动复制，不报成功', async () => {
    state.clipboard.copy.mockResolvedValue(false)
    const wrapper = mountPanel()
    await wrapper.find('[data-test="copy-artifact-ref"]').trigger('click')
    expect(state.ui.warning).toHaveBeenCalled()
    expect(state.ui.success).not.toHaveBeenCalled()
  })

  it('响应缺 artifact_ref：引用键行与复制按钮整块隐藏（不构造看似可用的引用）', () => {
    const wrapper = mountPanel({ artifact_ref: undefined })
    expect(wrapper.find('[data-test="copy-artifact-ref"]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('引用键')
    expect(wrapper.text()).not.toContain('art:')
  })
})

describe('ArtifactUsagePanel — Agent 开放卡（三条件清单已撤，2026-11-13 用户拍板）', () => {
  it('未停用：状态一句话 + 去 MCP 配置链接（不显示推测的开放态）', () => {
    const wrapper = mountPanel()
    expect(wrapper.text()).toContain('需显式开放')
    const link = wrapper.find('.usage-panel__cell-link')
    expect(link.attributes('href')).toBe('/mcp')
    // 钥匙条不查询钥匙状态（66号 M01）——不出现 ✓/✗ 判定符号
    expect(wrapper.text()).not.toContain('✓')
    expect(wrapper.text()).not.toContain('✗')
  })

  it('已停用：只报事实，不给配置链接', () => {
    const wrapper = mountPanel({ status: 'deprecated' })
    expect(wrapper.text()).toContain('制品已停用')
    expect(wrapper.find('.usage-panel__cell-link').exists()).toBe(false)
  })
})
