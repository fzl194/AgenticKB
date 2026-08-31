<template>
  <div class="doc-detail" v-loading="loading">
    <!-- Back -->
    <div class="doc-detail__back">
      <el-button text @click="$router.push('/knowledge')">
        <el-icon><ArrowLeft /></el-icon> 返回列表
      </el-button>
    </div>

    <!-- Meta -->
    <div class="doc-detail__meta" v-if="document">
      <div class="doc-detail__heading">
        <div>
          <h3 class="doc-detail__name">{{ document.document_name }}</h3>
          <div class="doc-detail__tags">
            <span class="type-badge">{{ document.document_type }}</span>
            <span class="doc-detail__date">创建于 {{ formatTime(document.created_at) }}</span>
          </div>
        </div>
        <div class="doc-detail__actions">
          <el-button
            data-testid="download-document"
            :loading="downloading"
            @click="downloadDocument"
          >下载</el-button>
          <el-button
            type="danger"
            plain
            data-testid="remove-document"
            :loading="removing"
            @click="removeDocument"
          >下架</el-button>
        </div>
      </div>
    </div>

    <!-- Tabs -->
    <el-tabs v-model="activeTab" v-if="document" class="doc-detail__tabs" @tab-change="onTabChange">
      <!-- Segments Tab -->
      <el-tab-pane name="segments">
        <template #label>
          段落 <span class="tab-count">{{ segTotal }}</span>
        </template>
        <el-table
          :data="segments"
          class="kb-table"
          :header-cell-style="{ background: 'transparent' }"
          v-loading="segLoading"
        >
          <el-table-column label="#" width="60" prop="segment_index" />
          <el-table-column label="类型" width="100" prop="block_type" />
          <el-table-column label="角色" width="100">
            <template #default="{ row }">
              <span v-if="row.semantic_role" class="role-tag">{{ row.semantic_role }}</span>
              <span v-else>-</span>
            </template>
          </el-table-column>
          <el-table-column label="标题" min-width="150">
            <template #default="{ row }">
              {{ row.section_title || '-' }}
            </template>
          </el-table-column>
          <el-table-column label="内容" min-width="300">
            <template #default="{ row }">
              <span class="text-preview expandable" :class="{ 'is-expanded': expandedKeys.has(`seg-${row.segment_index}`) }" @click="toggleExpand(`seg-${row.segment_index}`)">{{ row.raw_text || '-' }}</span>
            </template>
          </el-table-column>
          <el-table-column label="Token" width="80" prop="token_count" />
        </el-table>
        <EmptyState v-if="!segLoading && !segments.length" text="无段落数据" />
        <div class="tab-pagination" v-if="segTotal > PAGE_SIZE">
          <el-pagination v-model:current-page="segPage" :page-size="PAGE_SIZE" :total="segTotal" layout="prev, pager, next" size="small" />
        </div>
      </el-tab-pane>

      <!-- Units Tab -->
      <el-tab-pane name="units">
        <template #label>
          检索单元 <span class="tab-count">{{ unitTotal }}</span>
        </template>
        <el-table
          :data="units"
          class="kb-table"
          :header-cell-style="{ background: 'transparent' }"
          v-loading="unitLoading"
        >
          <el-table-column label="类型" width="120">
            <template #default="{ row }">
              <span class="type-tag">{{ unitTypeLabel(row.unit_type) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="标题" min-width="200" prop="title" />
          <el-table-column label="内容" min-width="300">
            <template #default="{ row }">
              <span class="text-preview expandable" :class="{ 'is-expanded': expandedKeys.has(`unit-${row.id ?? row.title}`) }" @click="toggleExpand(`unit-${row.id ?? row.title}`)">{{ row.text || '-' }}</span>
            </template>
          </el-table-column>
          <el-table-column label="权重" width="80" prop="weight" />
        </el-table>
        <EmptyState v-if="!unitLoading && !units.length" text="无检索单元数据" />
        <div class="tab-pagination" v-if="unitTotal > PAGE_SIZE">
          <el-pagination v-model:current-page="unitPage" :page-size="PAGE_SIZE" :total="unitTotal" layout="prev, pager, next" size="small" />
        </div>
      </el-tab-pane>

      <!-- Raw Content Tab -->
      <el-tab-pane name="raw-content">
        <template #label>
          原始文本
        </template>
        <div v-loading="rawLoading" class="raw-content-wrapper">
          <div v-if="rawError" class="raw-content-error">
            <el-icon><WarningFilled /></el-icon>
            {{ rawError }}
          </div>
          <div v-else-if="rawHtml" class="raw-content-body" v-html="rawHtml" />
          <EmptyState v-else-if="!rawLoading" text="无可渲染的原始文件" />
        </div>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, watch } from 'vue'
import { ArrowLeft, WarningFilled } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useRouter } from 'vue-router'
import { useDomainStore } from '@/stores/domain'
import { useMiningApi } from '@/api/mining'
import { apiErrorDetail } from '@/api/proxyClient'
import { filenameFromDisposition, saveBlob } from '@/utils/download'
import type { KnowledgeDocument, KnowledgeSegment, KnowledgeUnit } from '@/types'
import EmptyState from '@/components/common/EmptyState.vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'

const PAGE_SIZE = 50

const props = defineProps<{ docId: string }>()
const domainStore = useDomainStore()
const miningApi = useMiningApi()
const router = useRouter()

const loading = ref(false)
const downloading = ref(false)
const removing = ref(false)
const document = ref<KnowledgeDocument | null>(null)
const activeTab = ref('segments')
const expandedKeys = ref(new Set<string>())

const WITHDRAWAL_NOTICE = '仅从当前领域的知识资产和检索结果中下架，原始文件与历史记录会保留。'
// 领域/文档切换时 bump，配合各加载与下架操作的竞态守卫，丢弃过期结果。
let documentRequestToken = 0
let removalRequestToken = 0
let segmentRequestToken = 0
let unitRequestToken = 0

// Segments
const segments = ref<KnowledgeSegment[]>([])
const segTotal = ref(0)
const segPage = ref(1)
const segLoading = ref(false)

// Units
const units = ref<KnowledgeUnit[]>([])
const unitTotal = ref(0)
const unitPage = ref(1)
const unitLoading = ref(false)

// Raw content
const rawLoading = ref(false)
const rawHtml = ref('')
const rawError = ref('')

function toggleExpand(key: string) {
  if (expandedKeys.value.has(key)) {
    expandedKeys.value.delete(key)
  } else {
    expandedKeys.value.add(key)
  }
  expandedKeys.value = new Set(expandedKeys.value)
}

function unitTypeLabel(type: string) {
  const map: Record<string, string> = {
    raw_text: '原始文本', contextual_text: '上下文', summary: '摘要',
    generated_question: '生成问题',
  }
  return map[type] || type
}

function formatTime(t: string) {
  if (!t) return '-'
  return new Date(t).toLocaleString('zh-CN')
}

async function loadSegments(domain = domainStore.currentDomain) {
  const requestToken = ++segmentRequestToken
  segLoading.value = true
  try {
    const res = await miningApi.getDocumentSegments(props.docId, {
      limit: PAGE_SIZE,
      offset: (segPage.value - 1) * PAGE_SIZE,
    })
    if (requestToken === segmentRequestToken && domain === domainStore.currentDomain) {
      segments.value = res.items
      segTotal.value = res.total
    }
  } catch {
    if (requestToken === segmentRequestToken && domain === domainStore.currentDomain) {
      segments.value = []
      segTotal.value = 0
    }
  } finally {
    if (requestToken === segmentRequestToken) segLoading.value = false
  }
}

async function loadUnits(domain = domainStore.currentDomain) {
  const requestToken = ++unitRequestToken
  unitLoading.value = true
  try {
    const res = await miningApi.getDocumentUnits(props.docId, {
      limit: PAGE_SIZE,
      offset: (unitPage.value - 1) * PAGE_SIZE,
    })
    if (requestToken === unitRequestToken && domain === domainStore.currentDomain) {
      units.value = res.items
      unitTotal.value = res.total
    }
  } catch {
    if (requestToken === unitRequestToken && domain === domainStore.currentDomain) {
      units.value = []
      unitTotal.value = 0
    }
  } finally {
    if (requestToken === unitRequestToken) unitLoading.value = false
  }
}

async function loadRawContent() {
  rawLoading.value = true
  rawError.value = ''
  rawHtml.value = ''
  try {
    const res = await miningApi.getDocumentRawContent(props.docId)
    if (res.format === 'markdown') {
      rawHtml.value = DOMPurify.sanitize(await marked(res.content))
    } else if (res.format === 'html') {
      rawHtml.value = DOMPurify.sanitize(res.content)
    } else {
      rawHtml.value = `<pre class="raw-plain">${escapeHtml(res.content)}</pre>`
    }
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : '加载失败'
    if (msg.includes('not renderable') || msg.includes('404')) {
      rawError.value = '该文件类型不支持在线预览'
    } else {
      rawError.value = msg
    }
  } finally {
    rawLoading.value = false
  }
}

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

function isConfirmCancel(error: unknown) {
  return error === 'cancel' || error === 'close'
}

async function downloadDocument(): Promise<void> {
  const currentDocument = document.value
  if (!currentDocument) return
  const requestedDomain = domainStore.currentDomain
  downloading.value = true
  try {
    const result = await miningApi.downloadDocument(currentDocument.id, requestedDomain)
    const filename = filenameFromDisposition(result.contentDisposition, currentDocument.document_name)
    saveBlob(result.blob, filename)
  } catch (error) {
    ElMessage.error(await apiErrorDetail(error))
  } finally {
    downloading.value = false
  }
}

async function removeDocument(): Promise<void> {
  const currentDocument = document.value
  if (!currentDocument) return
  const requestedDomain = domainStore.currentDomain
  const requestedDocumentToken = documentRequestToken
  try {
    await ElMessageBox.confirm(
      `${WITHDRAWAL_NOTICE}\n确认下架文档“${currentDocument.document_name}”吗？`,
      '确认下架文档',
      { confirmButtonText: '确认下架', cancelButtonText: '取消', type: 'warning' },
    )
  } catch (error) {
    if (!isConfirmCancel(error)) ElMessage.error(await apiErrorDetail(error))
    return
  }
  if (
    requestedDomain !== domainStore.currentDomain ||
    requestedDocumentToken !== documentRequestToken
  ) return

  const removalToken = ++removalRequestToken
  removing.value = true
  try {
    await miningApi.removeDocument(currentDocument.id, requestedDomain)
    if (
      requestedDomain !== domainStore.currentDomain ||
      requestedDocumentToken !== documentRequestToken ||
      removalToken !== removalRequestToken
    ) return
    ElMessage.success('文档已从当前领域下架')
    await router.push({ name: 'knowledge' })
  } catch (error) {
    if (
      requestedDomain === domainStore.currentDomain &&
      requestedDocumentToken === documentRequestToken &&
      removalToken === removalRequestToken
    ) {
      ElMessage.error(await apiErrorDetail(error))
    }
  } finally {
    if (removalToken === removalRequestToken) removing.value = false
  }
}

function onTabChange(tab: string | number) {
  if (tab === 'segments' && segments.value.length === 0) loadSegments()
  else if (tab === 'units' && units.value.length === 0) loadUnits()
  else if (tab === 'raw-content' && !rawHtml.value && !rawError.value) loadRawContent()
}

async function loadData() {
  const requestedDomain = domainStore.currentDomain
  const requestToken = ++documentRequestToken
  ++removalRequestToken
  ++segmentRequestToken
  ++unitRequestToken
  loading.value = true
  segLoading.value = false
  unitLoading.value = false
  removing.value = false
  document.value = null
  segments.value = []
  units.value = []
  rawHtml.value = ''
  rawError.value = ''
  try {
    const doc = await miningApi.getDocument(props.docId)
    if (requestToken !== documentRequestToken || requestedDomain !== domainStore.currentDomain) return
    document.value = doc
    // Load the active tab data
    segPage.value = 1
    unitPage.value = 1
    segments.value = []
    units.value = []
    await loadSegments(requestedDomain)
    if (requestToken !== documentRequestToken || requestedDomain !== domainStore.currentDomain) return
    // Preload totals for other tabs (lightweight: just first page)
    loadUnits(requestedDomain)
  } catch (error) {
    if (requestToken === documentRequestToken && requestedDomain === domainStore.currentDomain) {
      document.value = null
      ElMessage.error(await apiErrorDetail(error))
    }
  } finally {
    if (requestToken === documentRequestToken) loading.value = false
  }
}

// Watch page changes
watch(segPage, () => loadSegments())
watch(unitPage, () => loadUnits())

onMounted(loadData)
watch(() => domainStore.currentDomain, loadData)
</script>

<style scoped>
.doc-detail {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.doc-detail__back { margin-bottom: 0; }

.doc-detail__meta {
  background: var(--kb-bg-card);
  border-radius: var(--kb-radius);
  padding: 20px 22px;
  border: 1px solid var(--kb-border-light);
}

.doc-detail__heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.doc-detail__actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}

.doc-detail__name {
  font-size: 17px;
  font-weight: 650;
  color: var(--kb-text-primary);
  margin: 0 0 8px;
}

.doc-detail__tags {
  display: flex;
  align-items: center;
  gap: 10px;
}

.type-badge {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 4px;
  background: var(--kb-accent-soft);
  color: var(--kb-accent);
  font-weight: 600;
}

.doc-detail__date {
  font-size: 12px;
  color: var(--kb-text-tertiary);
}

.doc-detail__tabs :deep(.el-tabs__header) {
  margin-bottom: 14px;
}

.tab-count {
  font-size: 11px;
  color: var(--kb-text-tertiary);
  margin-left: 4px;
}

.tab-pagination {
  display: flex;
  justify-content: center;
  margin-top: 12px;
}

/* Inline tags — consistent with RunDocumentDetailView */
.role-tag {
  display: inline-block;
  padding: 1px 6px;
  border-radius: 3px;
  background: var(--kb-warning-soft);
  color: var(--kb-warning);
  font-size: 11px;
  font-weight: 600;
}

.type-tag {
  display: inline-block;
  padding: 1px 6px;
  border-radius: 3px;
  background: var(--kb-success-soft);
  color: var(--kb-success);
  font-size: 11px;
  font-weight: 600;
}

.relation-type-tag {
  display: inline-block;
  padding: 1px 8px;
  border-radius: 3px;
  background: var(--kb-accent-soft);
  color: var(--kb-accent);
  font-size: 11px;
  font-weight: 600;
}

.text-preview {
  font-size: 12px;
  color: var(--kb-text-secondary);
  line-height: 1.4;
}

.text-preview.expandable {
  cursor: pointer;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  word-break: break-all;
  transition: color 0.15s ease;
}

.text-preview.expandable:hover {
  color: var(--kb-accent);
}

.text-preview.expandable.is-expanded {
  -webkit-line-clamp: unset;
  display: block;
  white-space: pre-wrap;
}

/* Raw content */
.raw-content-wrapper {
  min-height: 200px;
}

.raw-content-error {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 20px;
  color: var(--kb-text-tertiary);
  font-size: 13px;
}

.raw-content-body {
  font-size: 14px;
  line-height: 1.7;
  color: var(--kb-text-primary);
  max-height: 70vh;
  overflow-y: auto;
  padding: 4px 0;
}

.raw-content-body :deep(h1),
.raw-content-body :deep(h2),
.raw-content-body :deep(h3),
.raw-content-body :deep(h4) {
  color: var(--kb-text-primary);
  margin: 1em 0 0.5em;
}

.raw-content-body :deep(p) {
  margin: 0.5em 0;
}

.raw-content-body :deep(pre) {
  background: var(--kb-bg-card);
  border: 1px solid var(--kb-border-light);
  border-radius: var(--kb-radius-sm);
  padding: 12px 16px;
  overflow-x: auto;
  font-size: 13px;
  line-height: 1.5;
}

.raw-content-body :deep(code) {
  font-family: 'SF Mono', 'Cascadia Code', monospace;
  font-size: 0.9em;
  background: var(--kb-bg-card);
  padding: 2px 4px;
  border-radius: 3px;
}

.raw-content-body :deep(table) {
  border-collapse: collapse;
  width: 100%;
  margin: 0.8em 0;
}

.raw-content-body :deep(th),
.raw-content-body :deep(td) {
  border: 1px solid var(--kb-border);
  padding: 6px 10px;
  text-align: left;
}

.raw-content-body :deep(th) {
  background: var(--kb-bg-card);
  font-weight: 600;
}

.raw-content-body :deep(blockquote) {
  border-left: 3px solid var(--kb-accent);
  padding-left: 12px;
  color: var(--kb-text-secondary);
  margin: 0.8em 0;
}

.raw-content-body :deep(img) {
  max-width: 100%;
  border-radius: var(--kb-radius-sm);
}

.raw-content-body :deep(.raw-plain) {
  white-space: pre-wrap;
  word-break: break-word;
  font-family: 'SF Mono', 'Cascadia Code', monospace;
  font-size: 13px;
  line-height: 1.5;
  color: var(--kb-text-secondary);
}
</style>
