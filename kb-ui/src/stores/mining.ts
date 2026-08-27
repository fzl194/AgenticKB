import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { MiningRun, MiningRunStage, MiningRunDocument } from '@/types'
import { useMiningApi } from '@/api/mining'
import { useDomainStore } from '@/stores/domain'

export const useMiningStore = defineStore('mining', () => {
  const runs = ref<MiningRun[]>([])
  const currentRun = ref<MiningRun | null>(null)
  const stages = ref<MiningRunStage[]>([])
  const documents = ref<MiningRunDocument[]>([])
  const documentsTotal = ref(0)
  const documentsPage = ref(1)
  const DOCUMENTS_PAGE_SIZE = 50
  const progress = ref<{
    total: number; completed: number; failed: number; skipped: number
    processing: number; progress_percent: number; current_stage: string | null
    stage_summary: Record<string, { done: number; failed: number }>
  } | null>(null)
  const currentDocument = ref<MiningRunDocument | null>(null)
  const documentStages = ref<MiningRunStage[]>([])
  const documentArtifacts = ref<{
    run_id: string; document_id: string; snapshot_id: string | null
    segment_count: number; unit_count: number; relation_count: number
  } | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)

  const miningApi = useMiningApi()
  const domainStore = useDomainStore()
  let runListGeneration = 0
  let runDetailGeneration = 0
  let progressGeneration = 0
  let documentGeneration = 0

  async function fetchRuns(options?: { silent?: boolean }) {
    const generation = ++runListGeneration
    const domain = domainStore.currentDomain
    if (!options?.silent) loading.value = true
    error.value = null
    try {
      const result = await miningApi.getRuns(domain)
      if (generation !== runListGeneration || domain !== domainStore.currentDomain) return
      runs.value = result
    } catch (e: unknown) {
      if (generation === runListGeneration && domain === domainStore.currentDomain) {
        error.value = e instanceof Error ? e.message : 'Failed to fetch runs'
      }
    } finally {
      if (!options?.silent && generation === runListGeneration && domain === domainStore.currentDomain) loading.value = false
    }
  }

  async function fetchRunDetail(runId: string, options?: { silent?: boolean }) {
    const generation = ++runDetailGeneration
    const domain = domainStore.currentDomain
    if (!options?.silent) loading.value = true
    error.value = null
    try {
      const [run, runStages, runDocsResult] = await Promise.all([
        miningApi.getRun(runId),
        miningApi.getRunStages(runId),
        miningApi.getRunDocuments(runId, { page: documentsPage.value, page_size: DOCUMENTS_PAGE_SIZE }),
      ])
      if (generation !== runDetailGeneration || domain !== domainStore.currentDomain) return
      currentRun.value = run
      stages.value = runStages
      documents.value = runDocsResult.documents
      documentsTotal.value = runDocsResult.total
    } catch (e: unknown) {
      if (generation === runDetailGeneration && domain === domainStore.currentDomain) {
        error.value = e instanceof Error ? e.message : 'Failed to fetch run detail'
      }
    } finally {
      if (!options?.silent && generation === runDetailGeneration && domain === domainStore.currentDomain) loading.value = false
    }
  }

  async function fetchRunDocuments(runId: string) {
    const generation = ++documentGeneration
    const domain = domainStore.currentDomain
    try {
      const runDocsResult = await miningApi.getRunDocuments(runId, { page: documentsPage.value, page_size: DOCUMENTS_PAGE_SIZE })
      if (generation !== documentGeneration || domain !== domainStore.currentDomain) return
      documents.value = runDocsResult.documents
      documentsTotal.value = runDocsResult.total
    } catch {
      // silently ignore
    }
  }

  async function cancelRun(runId: string) {
    try {
      await miningApi.cancelRun(runId)
      const run = runs.value.find(r => r.id === runId)
      if (run) run.status = 'cancelled'
      if (currentRun.value?.id === runId) currentRun.value.status = 'cancelled'
    } catch (e: unknown) {
      error.value = e instanceof Error ? e.message : 'Failed to cancel run'
    }
  }

  async function publishRun(runId: string) {
    try {
      await miningApi.publishRun(runId, domainStore.currentDomain)
      const run = runs.value.find(r => r.id === runId)
      if (run) run.build_id = `bld_${runId}`
      if (currentRun.value?.id === runId) currentRun.value.build_id = `bld_${runId}`
    } catch (e: unknown) {
      error.value = e instanceof Error ? e.message : 'Failed to publish run'
    }
  }

  async function fetchProgress(runId: string) {
    const generation = ++progressGeneration
    const domain = domainStore.currentDomain
    try {
      const result = await miningApi.getRunProgress(runId)
      if (generation !== progressGeneration || domain !== domainStore.currentDomain) return
      progress.value = result
    } catch {
      // silently ignore progress fetch failures
    }
  }

  async function fetchDocumentDetail(runId: string, docId: string) {
    loading.value = true
    error.value = null
    try {
      const [doc, docStages, docArts] = await Promise.all([
        miningApi.getRunDocument(runId, docId),
        miningApi.getRunDocumentStages(runId, docId),
        miningApi.getRunDocumentArtifacts(runId, docId),
      ])
      currentDocument.value = doc
      documentStages.value = docStages
      documentArtifacts.value = docArts
    } catch (e: unknown) {
      error.value = e instanceof Error ? e.message : 'Failed to fetch document detail'
    } finally {
      loading.value = false
    }
  }

  function clearCurrentRun() {
    runDetailGeneration += 1
    progressGeneration += 1
    documentGeneration += 1
    currentRun.value = null
    stages.value = []
    documents.value = []
    documentsTotal.value = 0
    documentsPage.value = 1
    progress.value = null
    currentDocument.value = null
    documentStages.value = []
    documentArtifacts.value = null
  }

  return {
    runs, currentRun, stages, documents, documentsTotal, documentsPage,
    progress,
    currentDocument, documentStages, documentArtifacts,
    loading, error,
    fetchRuns, fetchRunDetail, fetchRunDocuments, fetchProgress,
    fetchDocumentDetail,
    cancelRun, publishRun, clearCurrentRun,
  }
})
