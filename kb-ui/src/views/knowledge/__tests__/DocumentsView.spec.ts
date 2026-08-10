import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, shallowMount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

const api = vi.hoisted(() => ({
  getDocuments: vi.fn(),
  getBatches: vi.fn(),
  downloadDocument: vi.fn(),
  removeDocument: vi.fn(),
  removeBatch: vi.fn(),
}))

const ui = vi.hoisted(() => ({
  confirm: vi.fn(),
  error: vi.fn(),
  success: vi.fn(),
}))

const downloads = vi.hoisted(() => ({
  filenameFromDisposition: vi.fn(() => 'download.pdf'),
  saveBlob: vi.fn(),
}))

vi.mock('@/api/mining', () => ({ useMiningApi: () => api }))
vi.mock('@/utils/download', () => downloads)
vi.mock('@/api/proxyClient', () => ({
  apiErrorDetail: async (error: unknown) =>
    (error as { response?: { data?: { detail?: string } } }).response?.data?.detail ?? '请求失败',
  installAuthInterceptors: vi.fn(),
}))
vi.mock('element-plus', () => ({
  ElMessageBox: { confirm: ui.confirm },
  ElMessage: { error: ui.error, success: ui.success },
}))

import { useDomainStore } from '@/stores/domain'
import DocumentsView from '../DocumentsView.vue'

const document = {
  id: 'doc-1',
  document_key: 'doc-key',
  document_name: '报告.pdf',
  document_type: 'pdf',
  created_at: '2026-07-20T00:00:00Z',
  source_batch_id: 'batch-a',
  batch_code: 'BATCH-A',
}

const batch = {
  source_batch_id: 'batch-a',
  batch_code: 'BATCH-A',
  mining_run_id: 'run-a',
  active_document_count: 2,
  created_at: '2026-07-20T00:00:00Z',
  deletable: true,
  unclassified: false,
}

function page(items = [document], total = items.length, offset = 0) {
  return { items, total, limit: 50, offset }
}

describe('DocumentsView lifecycle interactions', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
    const domainStore = useDomainStore()
    domainStore.currentDomain = 'odn'
    api.getDocuments.mockReset().mockResolvedValue(page())
    api.getBatches.mockReset().mockResolvedValue({ items: [batch] })
    api.downloadDocument.mockReset()
    api.removeDocument.mockReset()
    api.removeBatch.mockReset()
    ui.confirm.mockReset().mockResolvedValue('confirm')
    ui.error.mockReset()
    ui.success.mockReset()
    downloads.filenameFromDisposition.mockClear()
    downloads.saveBlob.mockClear()
  })

  it('loads_documents_and_batches_for_the_current_domain', async () => {
    const wrapper = shallowMount(DocumentsView)
    await flushPromises()

    expect(api.getDocuments).toHaveBeenCalledWith(expect.objectContaining({ domain: 'odn' }))
    expect(api.getBatches).toHaveBeenCalledWith('odn')
    wrapper.unmount()
  })

  it('filters_documents_by_batch_on_the_server', async () => {
    const wrapper = shallowMount(DocumentsView)
    await flushPromises()

    ;(wrapper.vm as unknown as { selectedBatch: string }).selectedBatch = 'batch-a'
    await flushPromises()

    expect(api.getDocuments).toHaveBeenLastCalledWith(expect.objectContaining({
      domain: 'odn', source_batch_id: 'batch-a',
    }))
    wrapper.unmount()
  })

  it('clears_batch_filter_and_resets_page_when_domain_changes', async () => {
    const wrapper = shallowMount(DocumentsView)
    await flushPromises()
    const vm = wrapper.vm as unknown as { selectedBatch: string; currentPage: number }
    vm.selectedBatch = 'batch-a'
    vm.currentPage = 3
    await flushPromises()

    useDomainStore().currentDomain = 'civil_engineering'
    await flushPromises()

    expect(vm.selectedBatch).toBe('')
    expect(vm.currentPage).toBe(1)
    expect(api.getDocuments).toHaveBeenLastCalledWith(expect.objectContaining({ domain: 'civil_engineering' }))
    expect(api.getBatches).toHaveBeenLastCalledWith('civil_engineering')
    wrapper.unmount()
  })

  it('does_not_let_a_slow_old_domain_response_replace_the_new_domain', async () => {
    let resolveOld!: (value: ReturnType<typeof page>) => void
    api.getDocuments
      .mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve }))
      .mockResolvedValueOnce(page([{ ...document, id: 'doc-new', document_name: '新领域.pdf' }]))
    const wrapper = shallowMount(DocumentsView)
    await flushPromises()

    useDomainStore().currentDomain = 'civil_engineering'
    await flushPromises()
    resolveOld(page([{ ...document, id: 'doc-old', document_name: '旧领域.pdf' }]))
    await flushPromises()

    expect((wrapper.vm as unknown as { documents: Array<{ id: string }> }).documents[0].id).toBe('doc-new')
    wrapper.unmount()
  })

  it('downloads_with_the_captured_domain_and_server_filename', async () => {
    const blob = new Blob(['pdf'])
    api.downloadDocument.mockResolvedValue({ blob, contentDisposition: 'attachment; filename="server.pdf"' })
    const wrapper = shallowMount(DocumentsView)
    await flushPromises()

    await (wrapper.vm as unknown as { downloadDocument: (row: typeof document) => Promise<void> })
      .downloadDocument(document)

    expect(api.downloadDocument).toHaveBeenCalledWith('doc-1', 'odn')
    expect(downloads.filenameFromDisposition).toHaveBeenCalledWith(
      'attachment; filename="server.pdf"', '报告.pdf',
    )
    expect(downloads.saveBlob).toHaveBeenCalledWith(blob, 'download.pdf')
    wrapper.unmount()
  })

  it('confirms_and_removes_a_document_then_refreshes_both_lists', async () => {
    api.removeDocument.mockResolvedValue({
      domain: 'odn', document_id: 'doc-1', removed_count: 1,
      build_id: 'build-new', release_id: 'release-new',
    })
    const wrapper = shallowMount(DocumentsView)
    await flushPromises()
    api.getDocuments.mockClear()
    api.getBatches.mockClear()

    await (wrapper.vm as unknown as { removeDocument: (row: typeof document) => Promise<void> })
      .removeDocument(document)
    await flushPromises()

    expect(ui.confirm).toHaveBeenCalledWith(
      expect.stringContaining('仅从当前领域的知识资产和检索结果中下架'),
      expect.any(String),
      expect.any(Object),
    )
    expect(api.removeDocument).toHaveBeenCalledWith('doc-1', 'odn')
    expect(api.getDocuments).toHaveBeenCalled()
    expect(api.getBatches).toHaveBeenCalled()
    wrapper.unmount()
  })

  it('does_not_refresh_the_new_domain_when_an_old_domain_document_removal_finishes', async () => {
    let resolveRemoval!: (value: unknown) => void
    api.removeDocument.mockImplementationOnce(() => new Promise(resolve => { resolveRemoval = resolve }))
    const wrapper = shallowMount(DocumentsView)
    await flushPromises()
    const vm = wrapper.vm as unknown as {
      selectedBatch: string
      currentPage: number
      removeDocument: (row: typeof document) => Promise<void>
    }

    const removal = vm.removeDocument(document)
    await flushPromises()
    expect(api.removeDocument).toHaveBeenCalledWith('doc-1', 'odn')

    useDomainStore().currentDomain = 'civil_engineering'
    await flushPromises()
    vm.selectedBatch = 'batch-b'
    await flushPromises()
    vm.currentPage = 3
    await flushPromises()
    api.getDocuments.mockClear()
    api.getBatches.mockClear()

    resolveRemoval({ domain: 'odn', document_id: 'doc-1', removed_count: 1 })
    await removal
    await flushPromises()

    expect(vm.selectedBatch).toBe('batch-b')
    expect(vm.currentPage).toBe(3)
    expect(api.getDocuments).not.toHaveBeenCalled()
    expect(api.getBatches).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('does_not_start_document_removal_after_the_domain_changes_during_confirmation', async () => {
    let resolveConfirm!: (value: unknown) => void
    ui.confirm.mockImplementationOnce(() => new Promise(resolve => { resolveConfirm = resolve }))
    const wrapper = shallowMount(DocumentsView)
    await flushPromises()

    const removal = (wrapper.vm as unknown as {
      removeDocument: (row: typeof document) => Promise<void>
    }).removeDocument(document)
    await flushPromises()
    useDomainStore().currentDomain = 'civil_engineering'
    await flushPromises()

    resolveConfirm('confirm')
    await removal

    expect(api.removeDocument).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('does_not_let_an_old_document_removal_clear_the_new_domain_loading_state', async () => {
    let resolveOldRemoval!: (value: unknown) => void
    let resolveNewRemoval!: (value: unknown) => void
    api.removeDocument
      .mockImplementationOnce(() => new Promise(resolve => { resolveOldRemoval = resolve }))
      .mockImplementationOnce(() => new Promise(resolve => { resolveNewRemoval = resolve }))
    const wrapper = shallowMount(DocumentsView)
    await flushPromises()
    const vm = wrapper.vm as unknown as {
      removingDocumentId: string
      removeDocument: (row: typeof document) => Promise<void>
    }

    const oldRemoval = vm.removeDocument(document)
    await flushPromises()
    useDomainStore().currentDomain = 'civil_engineering'
    await flushPromises()

    const newDocument = { ...document, id: 'doc-new' }
    const newRemoval = vm.removeDocument(newDocument)
    await flushPromises()
    expect(vm.removingDocumentId).toBe('doc-new')

    resolveOldRemoval({ domain: 'odn', document_id: 'doc-1', removed_count: 1 })
    await oldRemoval
    await flushPromises()

    expect(vm.removingDocumentId).toBe('doc-new')

    resolveNewRemoval({ domain: 'civil_engineering', document_id: 'doc-new', removed_count: 1 })
    await newRemoval
    wrapper.unmount()
  })

  it('does_not_request_removal_when_confirmation_is_cancelled', async () => {
    ui.confirm.mockRejectedValue('cancel')
    const wrapper = shallowMount(DocumentsView)
    await flushPromises()

    await (wrapper.vm as unknown as { removeDocument: (row: typeof document) => Promise<void> })
      .removeDocument(document)

    expect(api.removeDocument).not.toHaveBeenCalled()
    expect(ui.error).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('shows_batch_document_count_and_refuses_legacy_batch_removal', async () => {
    const wrapper = shallowMount(DocumentsView)
    await flushPromises()
    const vm = wrapper.vm as unknown as {
      removeBatch: (item: typeof batch) => Promise<void>
    }

    await vm.removeBatch(batch)
    expect(ui.confirm).toHaveBeenCalledWith(
      expect.stringContaining('2 个文档'), expect.any(String), expect.any(Object),
    )
    expect(api.removeBatch).toHaveBeenCalledWith('batch-a', 'odn')

    api.removeBatch.mockClear()
    await vm.removeBatch({ ...batch, source_batch_id: null, deletable: false } as never)
    expect(api.removeBatch).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('does_not_let_an_old_domain_batch_removal_reset_or_refresh_the_new_domain', async () => {
    let resolveRemoval!: (value: unknown) => void
    api.removeBatch.mockImplementationOnce(() => new Promise(resolve => { resolveRemoval = resolve }))
    const wrapper = shallowMount(DocumentsView)
    await flushPromises()
    const vm = wrapper.vm as unknown as {
      selectedBatch: string
      currentPage: number
      removeBatch: (item: typeof batch) => Promise<void>
    }

    const removal = vm.removeBatch(batch)
    await flushPromises()
    expect(api.removeBatch).toHaveBeenCalledWith('batch-a', 'odn')

    useDomainStore().currentDomain = 'civil_engineering'
    await flushPromises()
    vm.selectedBatch = 'batch-b'
    await flushPromises()
    vm.currentPage = 3
    await flushPromises()
    api.getDocuments.mockClear()
    api.getBatches.mockClear()

    resolveRemoval({ domain: 'odn', removed_count: 2 })
    await removal
    await flushPromises()

    expect(vm.selectedBatch).toBe('batch-b')
    expect(vm.currentPage).toBe(3)
    expect(api.getDocuments).not.toHaveBeenCalled()
    expect(api.getBatches).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('does_not_start_batch_removal_after_the_domain_changes_during_confirmation', async () => {
    let resolveConfirm!: (value: unknown) => void
    ui.confirm.mockImplementationOnce(() => new Promise(resolve => { resolveConfirm = resolve }))
    const wrapper = shallowMount(DocumentsView)
    await flushPromises()

    const removal = (wrapper.vm as unknown as {
      removeBatch: (item: typeof batch) => Promise<void>
    }).removeBatch(batch)
    await flushPromises()
    useDomainStore().currentDomain = 'civil_engineering'
    await flushPromises()

    resolveConfirm('confirm')
    await removal

    expect(api.removeBatch).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('does_not_let_an_old_batch_removal_clear_the_new_domain_loading_state', async () => {
    let resolveOldRemoval!: (value: unknown) => void
    let resolveNewRemoval!: (value: unknown) => void
    api.removeBatch
      .mockImplementationOnce(() => new Promise(resolve => { resolveOldRemoval = resolve }))
      .mockImplementationOnce(() => new Promise(resolve => { resolveNewRemoval = resolve }))
    const wrapper = shallowMount(DocumentsView)
    await flushPromises()
    const vm = wrapper.vm as unknown as {
      removingBatch: boolean
      removeBatch: (item: typeof batch) => Promise<void>
    }

    const oldRemoval = vm.removeBatch(batch)
    await flushPromises()
    useDomainStore().currentDomain = 'civil_engineering'
    await flushPromises()

    const newBatch = { ...batch, source_batch_id: 'batch-b', batch_code: 'BATCH-B' }
    const newRemoval = vm.removeBatch(newBatch)
    await flushPromises()
    expect(vm.removingBatch).toBe(true)

    resolveOldRemoval({ domain: 'odn', removed_count: 2 })
    await oldRemoval
    await flushPromises()

    expect(vm.removingBatch).toBe(true)

    resolveNewRemoval({ domain: 'civil_engineering', removed_count: 2 })
    await newRemoval
    wrapper.unmount()
  })

  it('moves_to_the_last_valid_page_after_removal', async () => {
    api.getDocuments
      .mockResolvedValueOnce(page([document], 51, 50))
      .mockResolvedValueOnce(page([], 50, 50))
      .mockResolvedValueOnce(page([document], 50, 0))
    api.removeDocument.mockResolvedValue({ domain: 'odn', removed_count: 1, build_id: 'b', release_id: 'r' })
    const wrapper = shallowMount(DocumentsView)
    const vm = wrapper.vm as unknown as {
      currentPage: number
      removeDocument: (row: typeof document) => Promise<void>
    }
    vm.currentPage = 2
    await flushPromises()

    await vm.removeDocument(document)
    await flushPromises()

    expect(vm.currentPage).toBe(1)
    expect(api.getDocuments).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 0 }))
    wrapper.unmount()
  })

  it('keeps_the_page_and_shows_backend_detail_when_removal_fails', async () => {
    api.removeDocument.mockRejectedValue({ response: { data: { detail: '文档已下架' } } })
    const wrapper = shallowMount(DocumentsView)
    await flushPromises()

    await (wrapper.vm as unknown as { removeDocument: (row: typeof document) => Promise<void> })
      .removeDocument(document)

    expect(ui.error).toHaveBeenCalledWith('文档已下架')
    expect((wrapper.vm as unknown as { documents: unknown[] }).documents).toHaveLength(1)
    wrapper.unmount()
  })
})
