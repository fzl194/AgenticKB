<template>
  <div class="doc-preview" v-loading="loading">
    <!-- Header -->
    <div class="doc-preview__header">
      <div class="doc-preview__head-left">
        <el-button text @click="back">
          <el-icon><ArrowLeft /></el-icon> 返回
        </el-button>
        <el-icon class="doc-preview__icon"><component :is="icon" /></el-icon>
        <span class="doc-preview__name">{{ doc?.document_name || '…' }}</span>
        <el-tag v-if="doc?.status" :type="docStatusTagType(doc.status)" size="small" effect="light">
          {{ docStatusLabel(doc.status) }}
        </el-tag>
      </div>
      <div class="doc-preview__head-right">
        <el-button size="small" :loading="downloading" @click="download">
          <el-icon class="el-icon--left"><Download /></el-icon>下载
        </el-button>
      </div>
    </div>

    <!-- Tabs：未挖掘时仅「原始预览」，已挖掘追加三组知识 -->
    <el-tabs v-model="activeTab" class="doc-preview__tabs">
      <el-tab-pane label="原始预览" name="preview">
        <!-- Body -->
        <div class="doc-preview__body">
          <div v-if="error" class="doc-preview__state">
            <el-icon :size="32"><WarningFilled /></el-icon>
            <p>{{ error }}</p>
            <el-button size="small" @click="download">下载查看</el-button>
          </div>

          <div v-else-if="tooLarge" class="doc-preview__state">
            <el-icon :size="32"><Document /></el-icon>
            <p>文件较大（{{ (blobSize / 1024 / 1024).toFixed(1) }} MB），未在线渲染。</p>
            <el-button type="primary" size="small" @click="download">下载</el-button>
          </div>

          <div v-else-if="html" class="doc-preview__rich" v-html="html" />

          <div v-else-if="kind === 'image' && objectUrl" class="doc-preview__img">
            <img :src="objectUrl" :alt="doc?.document_name" />
          </div>

          <div v-else-if="kind === 'pdf' && objectUrl" class="doc-preview__pdf">
            <iframe :src="objectUrl" :title="doc?.document_name" />
          </div>

          <pre v-else-if="text !== null" class="doc-preview__text">{{ text }}</pre>

          <div v-else-if="kind === 'unsupported'" class="doc-preview__state">
            <el-icon :size="32"><Document /></el-icon>
            <p>该类型暂不支持在线预览（.{{ ext || '未知' }}）。</p>
            <p class="doc-preview__sub">支持：md / html / 纯文本代码（txt/json/yaml/csv/xml 等） / 图片（png/jpg/gif/webp/svg）/ PDF</p>
            <el-button size="small" @click="download">下载查看</el-button>
          </div>
        </div>
      </el-tab-pane>

      <template v-if="knowledgeMined">
        <el-tab-pane v-if="segments.length" :label="`切片分段 (${segments.length})`" name="segments">
          <div class="doc-preview__knowledge">
            <el-collapse>
              <el-collapse-item
                v-for="seg in segments"
                :key="seg.segment_index"
                :name="seg.segment_index"
              >
                <template #title>
                  <span class="doc-preview__seg-title">
                    <span class="doc-preview__seg-idx">#{{ seg.segment_index }}</span>
                    <span>{{ seg.section_title || seg.semantic_role || seg.block_type || '切片' }}</span>
                  </span>
                </template>
                <div class="doc-preview__seg-meta">
                  <el-tag v-if="seg.block_type" size="small" effect="plain">{{ seg.block_type }}</el-tag>
                  <el-tag v-if="seg.semantic_role" size="small" effect="light">{{ seg.semantic_role }}</el-tag>
                </div>
                <pre v-if="seg.raw_text" class="doc-preview__seg-text">{{ seg.raw_text }}</pre>
              </el-collapse-item>
            </el-collapse>
          </div>
        </el-tab-pane>

        <el-tab-pane v-if="units.length" :label="`检索单元 (${units.length})`" name="units">
          <div class="doc-preview__knowledge">
            <div class="doc-preview__units">
              <div v-for="(u, i) in units" :key="u.unit_key || i" class="doc-preview__unit">
                <div class="doc-preview__unit-head">
                  <el-tag v-if="u.unit_type" size="small" effect="plain">{{ u.unit_type }}</el-tag>
                  <el-tag v-if="u.semantic_role" size="small" effect="light">{{ u.semantic_role }}</el-tag>
                  <span class="doc-preview__unit-title">{{ u.title || u.unit_key }}</span>
                </div>
                <pre v-if="u.text" class="doc-preview__seg-text">{{ u.text }}</pre>
              </div>
            </div>
          </div>
        </el-tab-pane>

        <el-tab-pane v-if="mentions.length" :label="`实体提及 (${mentions.length})`" name="mentions">
          <div class="doc-preview__knowledge">
            <el-table :data="mentions" class="kb-table" :header-cell-style="{ background: 'transparent' }">
              <el-table-column label="节点类型" min-width="120">
                <template #default="{ row }">
                  <span v-if="row.node_type">{{ row.node_type }}</span>
                  <span v-else class="doc-preview__muted">—</span>
                </template>
              </el-table-column>
              <el-table-column label="提及文本" min-width="200">
                <template #default="{ row }">
                  <span v-if="row.mention_text">{{ row.mention_text }}</span>
                  <span v-else class="doc-preview__muted">—</span>
                </template>
              </el-table-column>
              <el-table-column label="规范名" min-width="180">
                <template #default="{ row }">
                  <span v-if="row.canonical_name">{{ row.canonical_name }}</span>
                  <span v-else class="doc-preview__muted">—</span>
                </template>
              </el-table-column>
              <el-table-column label="解析状态" width="120">
                <template #default="{ row }">
                  <el-tag
                    v-if="row.resolve_status"
                    :type="resolveStatusTagType(row.resolve_status)"
                    size="small"
                    effect="plain"
                  >
                    {{ row.resolve_status }}
                  </el-tag>
                  <span v-else class="doc-preview__muted">—</span>
                </template>
              </el-table-column>
            </el-table>
          </div>
        </el-tab-pane>

        <el-tab-pane v-if="relations.length" :label="`关系图谱 (${relations.length})`" name="relations">
          <div class="doc-preview__knowledge">
            <el-table :data="relations" class="kb-table" :header-cell-style="{ background: 'transparent' }">
              <el-table-column label="关系类型" width="120">
                <template #default="{ row }">
                  <el-tag size="small" effect="plain">{{ relationLabel(row.relation_type) }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column label="源段" min-width="220">
                <template #default="{ row }">
                  <span class="doc-preview__rel-text">{{ row.source_segment_text }}</span>
                </template>
              </el-table-column>
              <el-table-column label="目标段" min-width="220">
                <template #default="{ row }">
                  <span class="doc-preview__rel-text">{{ row.target_segment_text }}</span>
                </template>
              </el-table-column>
              <el-table-column label="权重" width="80">
                <template #default="{ row }">{{ fmtNum(row.weight) }}</template>
              </el-table-column>
              <el-table-column label="置信度" width="90">
                <template #default="{ row }">{{ fmtNum(row.confidence) }}</template>
              </el-table-column>
            </el-table>
          </div>
        </el-tab-pane>
      </template>
    </el-tabs>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ArrowLeft, Document, Download, Picture, Tickets, WarningFilled } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import { useKbApi } from '@/api/kb'
import { apiErrorDetail } from '@/api/proxyClient'
import { filenameFromDisposition, saveBlob } from '@/utils/download'
import { docStatusLabel, docStatusTagType } from '@/views/kb/kbMeta'
import type { Component } from 'vue'
import type { KbDocEntityMention, KbDocRelation, KbDocument, KbDocRetrievalUnit, KbDocSegment } from '@/types/kb'

const PREVIEW_MAX_BYTES = 50 * 1024 * 1024

const props = defineProps<{ kbId: string; docId: string }>()
const router = useRouter()
const kbApi = useKbApi()

const doc = ref<KbDocument | null>(null)
const loading = ref(false)
const downloading = ref(false)
const error = ref('')
const text = ref<string | null>(null)
const html = ref('')
const objectUrl = ref<string | null>(null)
const blobSize = ref(0)

const segments = ref<KbDocSegment[]>([])
const units = ref<KbDocRetrievalUnit[]>([])
const mentions = ref<KbDocEntityMention[]>([])
const relations = ref<KbDocRelation[]>([])
const knowledgeMined = ref(false)
const activeTab = ref<'preview' | 'segments' | 'units' | 'mentions' | 'relations'>('preview')

const ext = computed(() => {
  const n = doc.value?.document_name || ''
  const i = n.lastIndexOf('.')
  return i >= 0 ? n.slice(i + 1).toLowerCase() : ''
})
const kind = computed<'md' | 'html' | 'image' | 'pdf' | 'text' | 'unsupported'>(() => {
  const e = ext.value
  if (['md', 'markdown'].includes(e)) return 'md'
  if (['htm', 'html'].includes(e)) return 'html'
  if (['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'bmp', 'ico'].includes(e)) return 'image'
  if (e === 'pdf') return 'pdf'
  if ([
    'txt', 'log', 'csv', 'json', 'yaml', 'yml', 'xml', 'js', 'ts', 'py',
    'sh', 'java', 'c', 'cpp', 'h', 'hpp', 'go', 'rs', 'sql', 'ini', 'conf', 'toml',
  ].includes(e)) return 'text'
  return 'unsupported'
})
const icon = computed<Component>(() => {
  if (kind.value === 'image') return Picture
  if (kind.value === 'pdf' || ['doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx'].includes(ext.value)) return Tickets
  return Document
})
const tooLarge = computed(() =>
  blobSize.value > PREVIEW_MAX_BYTES && (kind.value === 'pdf' || kind.value === 'image' || kind.value === 'text'),
)

function cleanup() {
  if (objectUrl.value) { URL.revokeObjectURL(objectUrl.value); objectUrl.value = null }
  text.value = null
  html.value = ''
  error.value = ''
  blobSize.value = 0
}

function resetKnowledge() {
  segments.value = []
  units.value = []
  mentions.value = []
  relations.value = []
  knowledgeMined.value = false
  activeTab.value = 'preview'
}

async function load() {
  loading.value = true
  cleanup()
  resetKnowledge()
  try {
    // 元信息 + 字节并行；知识接口仅在文档已挖掘时才请求（单一权威状态 = doc.status，修 Bug B）
    const [d, blob] = await Promise.all([
      kbApi.getDocument(props.kbId, props.docId),
      kbApi.downloadDocument(props.kbId, props.docId),
    ])
    doc.value = d
    blobSize.value = blob.size
    const k = kind.value
    if (k === 'md') html.value = DOMPurify.sanitize(await marked(await blob.text()))
    else if (k === 'html') html.value = DOMPurify.sanitize(await blob.text())
    else if (k === 'text') text.value = await blob.text()
    else if ((k === 'image' || k === 'pdf') && blob.size <= PREVIEW_MAX_BYTES) {
      objectUrl.value = URL.createObjectURL(blob)
    }
    // 已挖掘文档：拉知识资产（切片/检索单元/实体/关系）；接口失败时按 status==='mined' 回退
    if (d.status === 'mined' || d.status === 'published') {
      const knowledge = await kbApi.getDocumentKnowledge(props.kbId, props.docId).catch(() => null)
      if (knowledge && knowledge.mined) {
        knowledgeMined.value = true
        segments.value = knowledge.segments ?? []
        units.value = knowledge.retrieval_units ?? []
        mentions.value = knowledge.entity_mentions ?? []
        relations.value = knowledge.relations ?? []
      } else {
        knowledgeMined.value = true // 状态显示已挖但接口失败：允许进入知识视图，Tab 按实际数据动态出
      }
    }
  } catch (e) {
    error.value = await apiErrorDetail(e)
  } finally {
    loading.value = false
  }
}

async function download() {
  downloading.value = true
  try {
    const blob = await kbApi.downloadDocument(props.kbId, props.docId)
    saveBlob(blob, filenameFromDisposition(null, doc.value?.document_name || 'download'))
  } catch (e) {
    ElMessage.error(await apiErrorDetail(e))
  } finally {
    downloading.value = false
  }
}

function back() {
  router.push(`/kb/${props.kbId}`)
}

const RELATION_LABELS: Record<string, string> = {
  elaborates: '详述', contrast: '对比', contrasts_with: '对比',
  sequences: '顺序', previous: '前序', next: '后继',
  causes: '因果', results_in: '因果', evidences: '佐证',
  summarizes: '总结', justifies: '论证', enables: '使能',
  purposes: '目的', references: '引用', exemplifies: '例证',
  same_section: '同节', same_parent_section: '同级',
  section_header_of: '标题',
}
function relationLabel(t: string | null): string {
  if (!t) return '—'
  return RELATION_LABELS[t] ?? t
}
function fmtNum(n: number | null): string {
  if (n === null || n === undefined) return '—'
  return Number.isInteger(n) ? String(n) : n.toFixed(2)
}

function resolveStatusTagType(s: string): 'success' | 'warning' | 'info' {
  switch (s) {
    case 'resolved':
    case 'linked':
      return 'success'
    case 'unresolved':
    case 'ambiguous':
      return 'warning'
    default:
      return 'info'
  }
}

onMounted(load)
watch(() => props.docId, load)
onUnmounted(cleanup)
</script>

<style scoped>
.doc-preview { display: flex; flex-direction: column; gap: 12px; }
.doc-preview__header {
  display: flex; align-items: center; justify-content: space-between; gap: 12px;
  background: var(--kb-bg-card); border: 1px solid var(--kb-border-light);
  border-radius: var(--kb-radius); padding: 12px 16px; flex-wrap: wrap;
}
.doc-preview__head-left { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.doc-preview__icon { font-size: 18px; color: var(--kb-accent); }
.doc-preview__name { font-size: 15px; font-weight: 600; color: var(--kb-text-primary); }

.doc-preview__tabs :deep(.el-tabs__header) { margin-bottom: 12px; }

.doc-preview__body {
  background: var(--kb-bg-card); border: 1px solid var(--kb-border-light);
  border-radius: var(--kb-radius); padding: 20px 24px; min-height: 320px;
}
.doc-preview__state {
  display: flex; flex-direction: column; align-items: center; gap: 10px;
  padding: 48px 24px; color: var(--kb-text-tertiary); text-align: center;
}
.doc-preview__state p { margin: 0; font-size: 13px; }
.doc-preview__sub { font-size: 11.5px; color: var(--kb-text-tertiary); max-width: 420px; }

.doc-preview__rich { font-size: 14px; line-height: 1.75; color: var(--kb-text-primary); max-width: 900px; }
.doc-preview__rich :deep(h1), .doc-preview__rich :deep(h2), .doc-preview__rich :deep(h3) { margin: 1em 0 0.4em; }
.doc-preview__rich :deep(pre) {
  background: var(--kb-bg-sidebar-hover); border: 1px solid var(--kb-border-light);
  border-radius: 6px; padding: 12px; overflow-x: auto; font-size: 12.5px;
}
.doc-preview__rich :deep(code) { font-family: 'SF Mono', 'Cascadia Code', monospace; }
.doc-preview__rich :deep(table) { border-collapse: collapse; width: 100%; margin: 0.8em 0; }
.doc-preview__rich :deep(th), .doc-preview__rich :deep(td) { border: 1px solid var(--kb-border); padding: 6px 10px; }
.doc-preview__rich :deep(img) { max-width: 100%; border-radius: 4px; }

.doc-preview__img { display: flex; justify-content: center; }
.doc-preview__img img { max-width: 100%; border-radius: 6px; box-shadow: var(--kb-shadow-card); }
.doc-preview__pdf { height: calc(100vh - 200px); }
.doc-preview__pdf iframe { width: 100%; height: 100%; border: 0; border-radius: 6px; }
.doc-preview__text {
  font-family: 'SF Mono', 'Cascadia Code', monospace; font-size: 12.5px; line-height: 1.6;
  white-space: pre-wrap; word-break: break-word; color: var(--kb-text-secondary);
  margin: 0; max-width: 100%; overflow-x: auto;
}

/* 已挖掘知识 tab */
.doc-preview__knowledge {
  background: var(--kb-bg-card); border: 1px solid var(--kb-border-light);
  border-radius: var(--kb-radius); padding: 16px 20px; min-height: 200px;
}
.doc-preview__empty { color: var(--kb-text-tertiary); font-size: 13px; padding: 16px 0; }
.doc-preview__muted { color: var(--kb-text-tertiary); }

.doc-preview__seg-title { display: inline-flex; align-items: center; gap: 8px; font-size: 13px; }
.doc-preview__seg-idx {
  font-family: 'SF Mono', 'Cascadia Code', monospace; font-size: 11.5px;
  color: var(--kb-text-tertiary);
}
.doc-preview__seg-meta { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 8px; }
.doc-preview__seg-text {
  font-family: 'SF Mono', 'Cascadia Code', monospace; font-size: 12.5px; line-height: 1.6;
  white-space: pre-wrap; word-break: break-word; color: var(--kb-text-secondary);
  margin: 0; background: var(--kb-bg-sidebar-hover); border: 1px solid var(--kb-border-light);
  border-radius: 6px; padding: 10px 12px; max-height: 320px; overflow-y: auto;
}

.doc-preview__units { display: flex; flex-direction: column; gap: 12px; }
.doc-preview__unit {
  border: 1px solid var(--kb-border-light); border-radius: 6px; padding: 10px 12px;
}
.doc-preview__unit-head {
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 6px;
}
.doc-preview__unit-title { font-size: 13.5px; font-weight: 600; color: var(--kb-text-primary); }
.doc-preview__rel-text {
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
  overflow: hidden; font-size: 12.5px; line-height: 1.5; color: var(--kb-text-secondary);
}
</style>
