import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, shallowMount } from '@vue/test-utils'

const kbApi = vi.hoisted(() => ({
  getKbRuns: vi.fn(), mineKb: vi.fn(), updateKb: vi.fn(),
}))
const workflowApi = vi.hoisted(() => ({ options: vi.fn() }))
const messages = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn() }))

vi.mock('@/api/kb', () => ({ useKbApi: () => kbApi }))
vi.mock('@/api/miningWorkflow', () => ({ useMiningWorkflowApi: () => workflowApi }))
vi.mock('vue-router', () => ({ useRouter: () => ({ push: vi.fn() }) }))
vi.mock('element-plus', () => ({ ElMessage: messages }))

import KbMiningPanel from '../KbMiningPanel.vue'

function run(status: string) {
  return {
    id: `run-${status}`, status, current_stage: null, execution_engine: 'workflow',
    workflow_id: 'wf', workflow_version: 1, started_at: '2026-09-01T00:00:00Z',
    finished_at: null, error_summary: null, total_documents: 1, new_count: 1,
    updated_count: 0, skipped_count: 0, failed_count: 0, committed_count: 0,
  }
}

describe('KbMiningPanel serial queue feedback', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    workflowApi.options.mockResolvedValue([])
    kbApi.getKbRuns.mockResolvedValue([])
    kbApi.mineKb.mockResolvedValue({ run_id: 'run-new', auto_force_redo: false })
  })

  it.each([
    ['queued', '排队中'],
    ['running', '挖掘中'],
  ])('disables duplicate submission while the KB is %s', async (status, label) => {
    kbApi.getKbRuns.mockResolvedValue([run(status)])
    const wrapper = shallowMount(KbMiningPanel, {
      props: { kbId: 'kb-1', selectedWorkflowId: 'wf', canWrite: true },
    })
    await flushPromises()

    const button = wrapper.get('[data-testid="mine-kb"]')
    expect(button.attributes('disabled')).toBeDefined()
    expect(button.text()).toContain(label)
    await button.trigger('click')
    expect(kbApi.mineKb).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('submits an idle KB and reports that it joined the queue', async () => {
    const wrapper = shallowMount(KbMiningPanel, {
      props: { kbId: 'kb-1', selectedWorkflowId: 'wf', canWrite: true },
    })
    await flushPromises()

    await wrapper.get('[data-testid="mine-kb"]').trigger('click')
    await flushPromises()

    expect(kbApi.mineKb).toHaveBeenCalledWith('kb-1', undefined, false)
    expect(messages.success).toHaveBeenCalledWith(expect.stringContaining('已加入队列'))
    wrapper.unmount()
  })
})
