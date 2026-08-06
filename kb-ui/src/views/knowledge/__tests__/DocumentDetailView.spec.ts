import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, shallowMount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

const api = vi.hoisted(() => ({
  getDocument: vi.fn(),
  getDocumentSegments: vi.fn(),
  getDocumentUnits: vi.fn(),
  getDocumentRelations: vi.fn(),
  downloadDocument: vi.fn(),
  removeDocument: vi.fn(),
}))

const ui = vi.hoisted(() => ({
  confirm: vi.fn(), error: vi.fn(), success: vi.fn(),
}))

const router = vi.hoisted(() => ({ push: vi.fn() }))
const downloads = vi.hoisted(() => ({
  filenameFromDisposition: vi.fn(() => '报告.pdf'), saveBlob: vi.fn(),
}))

vi.mock('@/api/mining', () => ({ useMiningApi: () => api }))
vi.mock('@/utils/download', () => downloads)
vi.mock('@/api/proxyClient', () => ({
  apiErrorDetail: async (error: unknown) =>
    (error as { response?: { data?: { detail?: string } } }).response?.data?.detail ?? '请求失败',
  installAuthInterceptors: vi.fn(),
}))
vi.mock('vue-router', () => ({ useRouter: () => router }))
vi.mock('element-plus', () => ({
  ElMessageBox: { confirm: ui.confirm },
  ElMessage: { error: ui.error, success: ui.success },
}))

import { useDomainStore } from '@/stores/domain'
import DocumentDetailView from '../DocumentDetailView.vue'

const document = {
  id: 'doc-1', document_key: 'key', document_name: '报告.pdf', document_type: 'pdf',
  created_at: '2026-07-20T00:00:00Z', source_batch_id: 'batch-a', batch_code: 'BATCH-A',
}

describe('DocumentDetailView lifecycle interactions', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
    useDomainStore().currentDomain = 'odn'
    api.getDocument.mockReset().mockResolvedValue(document)
    api.getDocumentSegments.mockReset().mockResolvedValue({ items: [], total: 0 })
    api.getDocumentUnits.mockReset().mockResolvedValue({ items: [], total: 0 })
    api.getDocumentRelations.mockReset().mockResolvedValue({ items: [], total: 0 })
    api.downloadDocument.mockReset()
    api.removeDocument.mockReset()
    ui.confirm.mockReset().mockResolvedValue('confirm')
    ui.error.mockReset()
    ui.success.mockReset()
    router.push.mockReset()
    downloads.filenameFromDisposition.mockClear()
    downloads.saveBlob.mockClear()
  })

  it('shows_download_and_remove_actions', async () => {
    const wrapper = shallowMount(DocumentDetailView, { props: { docId: 'doc-1' } })
    await flushPromises()

    expect(wrapper.find('[data-testid="download-document"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="remove-document"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it('downloads_using_document_name_as_fallback', async () => {
    const blob = new Blob(['pdf'])
    api.downloadDocument.mockResolvedValue({ blob, contentDisposition: null })
    const wrapper = shallowMount(DocumentDetailView, { props: { docId: 'doc-1' } })
    await flushPromises()

    await (wrapper.vm as unknown as { downloadDocument: () => Promise<void> }).downloadDocument()

    expect(api.downloadDocument).toHaveBeenCalledWith('doc-1', 'odn')
    expect(downloads.filenameFromDisposition).toHaveBeenCalledWith(null, '报告.pdf')
    expect(downloads.saveBlob).toHaveBeenCalledWith(blob, '报告.pdf')
    wrapper.unmount()
  })

  it('does_not_let_a_slow_old_domain_response_replace_the_new_domain', async () => {
    let resolveOld!: (value: typeof document) => void
    api.getDocument
      .mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve }))
      .mockResolvedValueOnce({ ...document, id: 'doc-new', document_name: '新领域.pdf' })
    const wrapper = shallowMount(DocumentDetailView, { props: { docId: 'doc-1' } })
    await flushPromises()

    useDomainStore().currentDomain = 'civil_engineering'
    await flushPromises()
    resolveOld({ ...document, id: 'doc-old', document_name: '旧领域.pdf' })
    await flushPromises()

    expect((wrapper.vm as unknown as { document: { id: string } }).document.id).toBe('doc-new')
    wrapper.unmount()
  })

  it('does_not_let_an_old_segment_load_start_or_invalidate_new_domain_preloads', async () => {
    let resolveOldSegments!: (value: { items: unknown[]; total: number }) => void
    let resolveNewUnits!: (value: { items: Array<{ id: string }>; total: number }) => void
    let resolveNewRelations!: (value: { items: Array<{ id: string }>; total: number }) => void
    api.getDocument
      .mockResolvedValueOnce(document)
      .mockResolvedValueOnce({ ...document, id: 'doc-new' })
    api.getDocumentSegments
      .mockImplementationOnce(() => new Promise(resolve => { resolveOldSegments = resolve }))
      .mockResolvedValueOnce({ items: [], total: 0 })
    api.getDocumentUnits
      .mockImplementationOnce(() => new Promise(resolve => { resolveNewUnits = resolve }))
      .mockResolvedValue({ items: [{ id: 'unit-old' }], total: 1 })
    api.getDocumentRelations
      .mockImplementationOnce(() => new Promise(resolve => { resolveNewRelations = resolve }))
      .mockResolvedValue({ items: [{ id: 'relation-old' }], total: 1 })
    const wrapper = shallowMount(DocumentDetailView, { props: { docId: 'doc-1' } })
    await flushPromises()

    useDomainStore().currentDomain = 'civil_engineering'
    await flushPromises()
    expect(api.getDocumentUnits).toHaveBeenCalledTimes(1)
    expect(api.getDocumentRelations).toHaveBeenCalledTimes(1)

    resolveOldSegments({ items: [], total: 0 })
    await flushPromises()
    resolveNewUnits({ items: [{ id: 'unit-new' }], total: 1 })
    resolveNewRelations({ items: [{ id: 'relation-new' }], total: 1 })
    await flushPromises()

    const vm = wrapper.vm as unknown as {
      units: Array<{ id: string }>
      relations: Array<{ id: string }>
    }
    expect(vm.units).toEqual([{ id: 'unit-new' }])
    expect(vm.relations).toEqual([{ id: 'relation-new' }])
    expect(api.getDocumentUnits).not.toHaveBeenCalledWith(
      'doc-1', 'odn', expect.any(Object),
    )
    expect(api.getDocumentRelations).not.toHaveBeenCalledWith(
      'doc-1', 'odn', expect.any(Object),
    )
    wrapper.unmount()
  })

  it('navigates_to_the_list_after_successful_removal', async () => {
    api.removeDocument.mockResolvedValue({ domain: 'odn', removed_count: 1, build_id: 'b', release_id: 'r' })
    const wrapper = shallowMount(DocumentDetailView, { props: { docId: 'doc-1' } })
    await flushPromises()

    await (wrapper.vm as unknown as { removeDocument: () => Promise<void> }).removeDocument()

    expect(api.removeDocument).toHaveBeenCalledWith('doc-1', 'odn')
    expect(router.push).toHaveBeenCalledWith({ name: 'knowledge' })
    wrapper.unmount()
  })

  it('does_not_navigate_away_from_a_new_domain_when_an_old_removal_finishes', async () => {
    let resolveRemoval!: (value: unknown) => void
    api.getDocument
      .mockResolvedValueOnce(document)
      .mockResolvedValueOnce({ ...document, id: 'doc-new' })
    api.removeDocument.mockImplementationOnce(() => new Promise(resolve => { resolveRemoval = resolve }))
    const wrapper = shallowMount(DocumentDetailView, { props: { docId: 'doc-1' } })
    await flushPromises()

    const removal = (wrapper.vm as unknown as { removeDocument: () => Promise<void> }).removeDocument()
    await flushPromises()
    expect(api.removeDocument).toHaveBeenCalledWith('doc-1', 'odn')

    useDomainStore().currentDomain = 'civil_engineering'
    await flushPromises()
    resolveRemoval({ domain: 'odn', document_id: 'doc-1', removed_count: 1 })
    await removal
    await flushPromises()

    expect(router.push).not.toHaveBeenCalled()
    expect((wrapper.vm as unknown as { document: { id: string } }).document.id).toBe('doc-new')
    wrapper.unmount()
  })

  it('does_not_start_removal_after_the_domain_changes_during_confirmation', async () => {
    let resolveConfirm!: (value: unknown) => void
    ui.confirm.mockImplementationOnce(() => new Promise(resolve => { resolveConfirm = resolve }))
    const wrapper = shallowMount(DocumentDetailView, { props: { docId: 'doc-1' } })
    await flushPromises()

    const removal = (wrapper.vm as unknown as { removeDocument: () => Promise<void> }).removeDocument()
    await flushPromises()
    useDomainStore().currentDomain = 'civil_engineering'
    await flushPromises()

    resolveConfirm('confirm')
    await removal

    expect(api.removeDocument).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('does_not_let_an_old_removal_clear_the_new_domain_loading_state', async () => {
    let resolveOldRemoval!: (value: unknown) => void
    let resolveNewRemoval!: (value: unknown) => void
    api.removeDocument
      .mockImplementationOnce(() => new Promise(resolve => { resolveOldRemoval = resolve }))
      .mockImplementationOnce(() => new Promise(resolve => { resolveNewRemoval = resolve }))
    const wrapper = shallowMount(DocumentDetailView, { props: { docId: 'doc-1' } })
    await flushPromises()
    const vm = wrapper.vm as unknown as {
      removing: boolean
      removeDocument: () => Promise<void>
    }

    const oldRemoval = vm.removeDocument()
    await flushPromises()
    useDomainStore().currentDomain = 'civil_engineering'
    await flushPromises()

    const newRemoval = vm.removeDocument()
    await flushPromises()
    expect(vm.removing).toBe(true)

    resolveOldRemoval({ domain: 'odn', document_id: 'doc-1', removed_count: 1 })
    await oldRemoval
    await flushPromises()

    expect(vm.removing).toBe(true)

    resolveNewRemoval({ domain: 'civil_engineering', document_id: 'doc-1', removed_count: 1 })
    await newRemoval
    wrapper.unmount()
  })

  it('keeps_the_document_and_shows_backend_detail_when_removal_fails', async () => {
    api.removeDocument.mockRejectedValue({ response: { data: { detail: '文档已下架' } } })
    const wrapper = shallowMount(DocumentDetailView, { props: { docId: 'doc-1' } })
    await flushPromises()

    await (wrapper.vm as unknown as { removeDocument: () => Promise<void> }).removeDocument()

    expect(ui.error).toHaveBeenCalledWith('文档已下架')
    expect(router.push).not.toHaveBeenCalled()
    expect((wrapper.vm as unknown as { document: unknown }).document).toEqual(document)
    wrapper.unmount()
  })
})
