import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, shallowMount } from '@vue/test-utils'

const api = vi.hoisted(() => ({ listDocuments: vi.fn(), listFolders: vi.fn() }))
vi.mock('@/api/kb', () => ({ useKbApi: () => api }))
vi.mock('vue-router', () => ({ useRouter: () => ({ push: vi.fn() }) }))
vi.mock('element-plus', () => ({ ElMessage: { error: vi.fn() }, ElMessageBox: {} }))
import KbFileManager from '../KbFileManager.vue'
import KbReplaceDocumentDialog from '../KbReplaceDocumentDialog.vue'

const doc = { id: 'doc-1', document_name: 'manual.md', content_revision: 4 }
async function create(canWrite = true) {
  const wrapper = shallowMount(KbFileManager, {
    props: { kbId: 'kb-1', canWrite, workflowId: 'wf-1', active: true },
    global: { stubs: { teleport: true } },
  })
  await flushPromises()
  await wrapper.get('.fm__row--file').trigger('contextmenu', { clientX: 10, clientY: 10 })
  return wrapper
}

describe('file manager replacement entry', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.listFolders.mockResolvedValue([])
    api.listDocuments.mockResolvedValue([doc])
  })

  it('opens with the selected revision and refreshes the list after success', async () => {
    const wrapper = await create()
    await wrapper.get('[data-testid="replace-document"]').trigger('click')
    const dialog = wrapper.getComponent(KbReplaceDocumentDialog)
    expect(dialog.props('document')).toEqual(doc)
    expect(dialog.props('document')).not.toBe(doc)
    api.listDocuments.mockResolvedValue([{ ...doc, content_revision: 5, status: 'mined', knowledge_outdated: true }])
    dialog.vm.$emit('replaced', { ...doc, content_revision: 5 })
    await flushPromises()
    expect(api.listDocuments).toHaveBeenCalledTimes(2)
    expect(wrapper.findComponent(KbReplaceDocumentDialog).exists()).toBe(false)
    expect(wrapper.get('[data-testid="knowledge-outdated"]').text()).toBe('待更新（旧知识可用）')
    wrapper.unmount()
  })

  it.each([['mining', '处理中'], ['update_failed', '更新失败，仍用上一版本']])(
    'keeps %s visible alongside the pending knowledge update', async (status, label) => {
      api.listDocuments.mockResolvedValue([{ ...doc, status, knowledge_outdated: true }])
      const wrapper = await create()
      expect(wrapper.get('.fm__row--file .fm__col--status').text()).toContain(label)
      expect(wrapper.get('[data-testid="knowledge-outdated"]').text()).toContain('旧知识可用')
      wrapper.unmount()
    },
  )

  it.each([
    { status: 'uploaded', knowledge_outdated: false },
    { status: 'mined', knowledge_outdated: false },
    { status: 'mined' },
  ])('does not infer outdated knowledge from $status alone', async (fields) => {
    api.listDocuments.mockResolvedValue([{ ...doc, ...fields }])
    const wrapper = await create()
    expect(wrapper.find('[data-testid="knowledge-outdated"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('keeps download available but hides replacement for a viewer', async () => {
    const wrapper = await create(false)
    expect(wrapper.text()).toContain('下载最新原文件')
    expect(wrapper.find('[data-testid="replace-document"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('closes an outstanding replacement when switching knowledge bases', async () => {
    const wrapper = await create()
    await wrapper.get('[data-testid="replace-document"]').trigger('click')
    await wrapper.setProps({ kbId: 'kb-2' })
    await flushPromises()
    expect(wrapper.findComponent(KbReplaceDocumentDialog).exists()).toBe(false)
    wrapper.unmount()
  })

  it('clears a stale file context menu when switching knowledge bases', async () => {
    const wrapper = await create()
    await wrapper.setProps({ kbId: 'kb-2' })
    await flushPromises()
    expect(wrapper.find('[data-testid="replace-document"]').exists()).toBe(false)
    wrapper.unmount()
  })
})
