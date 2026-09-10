import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import type { VueWrapper } from '@vue/test-utils'

const api = vi.hoisted(() => ({ replaceDocumentContent: vi.fn() }))
const messages = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }))
vi.mock('@/api/kb', () => ({ useKbApi: () => api }))
vi.mock('@/api/proxyClient', () => ({ apiErrorDetail: async () => '上传失败' }))
vi.mock('element-plus', () => ({ ElMessage: messages }))
import KbReplaceDocumentDialog from '../KbReplaceDocumentDialog.vue'

const document = { id: 'doc-1', document_name: 'original.md', content_revision: 4 }
const create = (canWrite = true, doc = document) => mount(KbReplaceDocumentDialog, {
  props: { kbId: 'kb-1', document: doc, canWrite },
})
async function selectFile(wrapper: VueWrapper) {
  const file = new File(['new bytes'], 'selected.md', { type: 'text/markdown' })
  const input = wrapper.get('input[type="file"]')
  Object.defineProperty(input.element, 'files', { configurable: true, value: [file] })
  await input.trigger('change')
  return file
}

describe('explicit file replacement', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.replaceDocumentContent.mockResolvedValue({ ...document, content_revision: 5 })
  })

  it('requires file selection and explicit confirmation, then refreshes without mining', async () => {
    const wrapper = create()
    expect(wrapper.text()).toContain('文件名和所在目录保持不变')
    expect(wrapper.text()).toContain('重新挖掘')
    expect(wrapper.get('[data-testid="replace-confirm"]').attributes('disabled')).toBeDefined()
    const file = await selectFile(wrapper)
    expect(api.replaceDocumentContent).not.toHaveBeenCalled()
    await wrapper.get('[data-testid="replace-confirm"]').trigger('click')
    await flushPromises()
    expect(api.replaceDocumentContent).toHaveBeenCalledWith('kb-1', 'doc-1', file, 4)
    expect(wrapper.emitted('replaced')).toHaveLength(1)
    expect(messages.success).toHaveBeenCalledWith(expect.stringContaining('重新挖掘'))
    wrapper.unmount()
  })

  it('does not expose replacement to viewers', () => {
    const wrapper = create(false)
    expect(wrapper.find('input[type="file"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="replace-confirm"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('cancel leaves the original document untouched', async () => {
    const wrapper = create()
    await selectFile(wrapper)
    await wrapper.get('[data-testid="replace-cancel"]').trigger('click')
    expect(wrapper.emitted('close')).toHaveLength(1)
    expect(api.replaceDocumentContent).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it.each([
    [409, '已被更新'], [403, '权限'], [404, '不存在'], [413, '大小限制'], [500, '上传失败'],
  ])('explains HTTP %s and never reports success', async (status, message) => {
    api.replaceDocumentContent.mockRejectedValue({ response: { status } })
    const wrapper = create()
    await selectFile(wrapper)
    await wrapper.get('[data-testid="replace-confirm"]').trigger('click')
    await flushPromises()
    expect(messages.error).toHaveBeenCalledWith(expect.stringContaining(message))
    expect(wrapper.emitted('replaced')).toBeUndefined()
    if (status === 409) {
      expect(wrapper.get('[data-testid="replace-confirm"]').attributes('disabled')).toBeDefined()
      expect(wrapper.emitted('refresh')).toHaveLength(1)
    }
    wrapper.unmount()
  })

  it('prevents duplicate submissions while the upload is pending', async () => {
    api.replaceDocumentContent.mockReturnValue(new Promise(() => {}))
    const wrapper = create()
    await selectFile(wrapper)
    await wrapper.get('[data-testid="replace-confirm"]').trigger('click')
    await wrapper.get('[data-testid="replace-confirm"]').trigger('click')
    expect(api.replaceDocumentContent).toHaveBeenCalledOnce()
    wrapper.unmount()
  })

  it('does not invent a missing document revision', async () => {
    const wrapper = create(true, { ...document, content_revision: undefined as unknown as number })
    await selectFile(wrapper)
    await wrapper.get('[data-testid="replace-confirm"]').trigger('click')
    expect(api.replaceDocumentContent).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('刷新')
    wrapper.unmount()
  })
})
