/**
 * 一键重启卡片（仅 site-admin）。
 *
 * 要钉的行为：
 * - member 不可见；
 * - 确认 → POST → 进入轮询观测（control 重启窗口的 502 不终止轮询）；
 * - done/failed 终态渲染 + restarted 事件（父组件借此刷新健康卡）；
 * - 409（别处已触发）不报错，直接进入观测。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'

const controlPlaneApi = vi.hoisted(() => ({
  triggerRestart: vi.fn(),
  getRestartStatus: vi.fn(),
}))
const messages = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }))
const confirmBox = vi.hoisted(() => ({ confirm: vi.fn() }))
const roleRef = vi.hoisted(() => ({ current: null as { value: string } | null }))

vi.mock('@/api/controlPlane', () => ({ useControlPlaneApi: () => controlPlaneApi }))
vi.mock('element-plus', () => ({
  ElMessage: messages,
  ElMessageBox: { confirm: confirmBox.confirm },
}))
vi.mock('@/stores/auth', async () => {
  const { ref } = await import('vue')
  roleRef.current = ref('admin')
  return {
    useAuthStore: () => ({
      get siteRole() { return roleRef.current!.value },
    }),
  }
})

import ServiceRestartCard from '@/components/settings/ServiceRestartCard.vue'

const PLAN = ['control', 'llm_service', 'mining', 'serving', 'mcp']

function mountCard() {
  const wrapper = mount(ServiceRestartCard)
  return wrapper
}

describe('一键重启卡片', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.useFakeTimers()
    roleRef.current!.value = 'admin'
    controlPlaneApi.getRestartStatus.mockResolvedValue({ state: 'idle', active: false })
    confirmBox.confirm.mockResolvedValue('confirm')
    controlPlaneApi.triggerRestart.mockResolvedValue({ ok: true, triggered_by: 'admin' })
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('member 不可见', async () => {
    roleRef.current!.value = 'member'
    const wrapper = mountCard()
    await vi.advanceTimersByTimeAsync(0)

    expect(wrapper.find('[data-testid="service-restart"]').exists()).toBe(false)
    expect(controlPlaneApi.getRestartStatus).not.toHaveBeenCalled()
  })

  it('admin 确认后触发重启并轮询到 done，发出 restarted 事件', async () => {
    controlPlaneApi.getRestartStatus
      .mockResolvedValueOnce({ state: 'idle', active: false })
      .mockResolvedValueOnce({
        state: 'running', active: true, plan: PLAN,
        completed: ['control'], current: 'llm_service',
      })
      .mockResolvedValueOnce({
        state: 'done', active: false,
        services: [
          { name: 'control', status: 'RUNNING' },
          { name: 'nginx', status: 'RUNNING' },
        ],
      })

    const wrapper = mountCard()
    await vi.advanceTimersByTimeAsync(0) // onMounted 拉初始状态

    await wrapper.get('[data-testid="restart-btn"]').trigger('click')
    expect(confirmBox.confirm).toHaveBeenCalled()
    expect(controlPlaneApi.triggerRestart).toHaveBeenCalled()

    await vi.advanceTimersByTimeAsync(0) // 第一次 pollOnce → 仍在 running
    expect(wrapper.find('[data-testid="restart-running"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('正在重启 LLM服务')

    await vi.advanceTimersByTimeAsync(2000) // 第二次 pollOnce → done
    expect(wrapper.find('[data-testid="restart-done"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('主控服务 RUNNING')
    expect(wrapper.emitted('restarted')).toHaveLength(1)
    expect(messages.success).toHaveBeenCalled()
  })

  it('轮询期间的 502（control 正在重启）不终止观测', async () => {
    controlPlaneApi.getRestartStatus
      .mockResolvedValueOnce({ state: 'idle', active: false })
      .mockRejectedValueOnce(new Error('502')) // control 重启窗口
      .mockResolvedValueOnce({ state: 'done', active: false, services: [] })

    const wrapper = mountCard()
    await vi.advanceTimersByTimeAsync(0)

    await wrapper.get('[data-testid="restart-btn"]').trigger('click')
    await vi.advanceTimersByTimeAsync(0) // 第一次 poll：502 → 继续等
    expect(wrapper.find('[data-testid="restart-running"]').exists()).toBe(true)
    expect(messages.error).not.toHaveBeenCalled()

    await vi.advanceTimersByTimeAsync(2000) // 第二次 poll：done
    expect(wrapper.find('[data-testid="restart-done"]').exists()).toBe(true)
  })

  it('failed 终态展示失败步骤与重试入口', async () => {
    controlPlaneApi.getRestartStatus
      .mockResolvedValueOnce({ state: 'idle', active: false })
      .mockResolvedValueOnce({
        state: 'failed', active: false,
        error: 'mining: 180s 内健康检查未通过（supervisor 状态 FATAL）',
      })

    const wrapper = mountCard()
    await vi.advanceTimersByTimeAsync(0)

    await wrapper.get('[data-testid="restart-btn"]').trigger('click')
    await vi.advanceTimersByTimeAsync(0)

    const failed = wrapper.find('[data-testid="restart-failed"]')
    expect(failed.exists()).toBe(true)
    expect(failed.text()).toContain('健康检查未通过')
    expect(wrapper.text()).toContain('重试重启')
    expect(wrapper.emitted('restarted')).toBeUndefined()
  })

  it('409（别处已触发）不报错，直接进入观测', async () => {
    controlPlaneApi.triggerRestart.mockRejectedValue({ response: { status: 409 } })
    controlPlaneApi.getRestartStatus
      .mockResolvedValueOnce({ state: 'idle', active: false })
      .mockResolvedValueOnce({ state: 'running', active: true, plan: PLAN })

    const wrapper = mountCard()
    await vi.advanceTimersByTimeAsync(0)

    await wrapper.get('[data-testid="restart-btn"]').trigger('click')
    await vi.advanceTimersByTimeAsync(0)

    expect(wrapper.find('[data-testid="restart-running"]').exists()).toBe(true)
    expect(messages.error).not.toHaveBeenCalled()
  })

  it('用户取消确认则不触发', async () => {
    confirmBox.confirm.mockRejectedValue(new Error('cancel'))

    const wrapper = mountCard()
    await vi.advanceTimersByTimeAsync(0)

    await wrapper.get('[data-testid="restart-btn"]').trigger('click')
    await vi.advanceTimersByTimeAsync(0)

    expect(controlPlaneApi.triggerRestart).not.toHaveBeenCalled()
    expect(wrapper.find('[data-testid="restart-running"]').exists()).toBe(false)
  })

  it('管理员在重启中途打开页面：active 状态自动接管轮询', async () => {
    controlPlaneApi.getRestartStatus
      .mockResolvedValueOnce({
        state: 'running', active: true, plan: PLAN,
        completed: ['control', 'llm_service'], current: 'mining',
      })
      // onMounted 接管后的第一次轮询（t=0 立即执行）仍 running，再下一轮才 done
      .mockResolvedValueOnce({
        state: 'running', active: true, plan: PLAN,
        completed: ['control', 'llm_service'], current: 'mining',
      })
      .mockResolvedValueOnce({ state: 'done', active: false, services: [] })

    const wrapper = mountCard()
    await vi.advanceTimersByTimeAsync(0) // onMounted 看到 active → startPolling

    expect(wrapper.find('[data-testid="restart-running"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('正在重启 挖掘服务')

    await vi.advanceTimersByTimeAsync(2000)
    expect(wrapper.find('[data-testid="restart-done"]').exists()).toBe(true)
  })
})
