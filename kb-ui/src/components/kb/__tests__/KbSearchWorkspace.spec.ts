import { describe, expect, it } from 'vitest'
import { shallowMount } from '@vue/test-utils'
import KbSearchWorkspace from '@/components/kb/KbSearchWorkspace.vue'
import type { KbSummary } from '@/types/kb'

const kb: KbSummary = {
  id: 'kb-1', name: 'KB', domain: 'domain-a', visibility: 'private',
  my_role: 'owner', document_count: 0,
  description: '', owner_id: 'u-1', mining_workflow_id: null, created_at: '2026-09-28T00:00:00Z',
}

describe('KB search workspace', () => {
  it('keeps online search as default and reuses records with the current kb id', async () => {
    const wrapper = shallowMount(KbSearchWorkspace, {
      props: { kb, canWrite: true, readiness: null },
    })

    expect(wrapper.text()).toContain('在线检索')
    expect(wrapper.text()).toContain('检索记录')
    expect(wrapper.findComponent({ name: 'KbSearchPanel' }).exists()).toBe(true)
    await wrapper.findAll('button')[1].trigger('click')
    expect(wrapper.findComponent({ name: 'RetrievalRecordsPanel' }).props('kbId')).toBe('kb-1')
  })
})
