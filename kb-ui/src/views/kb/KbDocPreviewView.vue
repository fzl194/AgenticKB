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
        <div class="doc-preview__body" v-loading="previewLoading">
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

          <!-- 预签名直连：浏览器对 MinIO 按 Range 分页按需加载 -->
          <div v-else-if="kind === 'image' && directUrl" class="doc-preview__img">
            <img :src="directUrl" :alt="doc?.document_name" />
          </div>

          <div v-else-if="kind === 'pdf' && directUrl" class="doc-preview__pdf">
            <iframe :src="directUrl" :title="doc?.document_name" />
          </div>

          <div v-else-if="html" class="doc-preview__rich">
            <div v-html="html" />
            <div v-if="textTruncated" class="doc-preview__more">
              <span>已展示前 {{ (renderLimit / 1000).toFixed(0) }}k 字符</span>
              <el-button size="small" @click="showMoreText">展示更多</el-button>
            </div>
          </div>

          <div v-else-if="kind === 'image' && objectUrl" class="doc-preview__img">
            <img :src="objectUrl" :alt="doc?.document_name" />
          </div>

          <div v-else-if="kind === 'pdf' && objectUrl" class="doc-preview__pdf">
            <iframe :src="objectUrl" :title="doc?.document_name" />
          </div>

          <template v-else-if="text !== null">
            <pre class="doc-preview__text">{{ text }}</pre>
            <div v-if="textTruncated" class="doc-preview__more">
              <span>已展示前 {{ (renderLimit / 1000).toFixed(0) }}k 字符</span>
              <el-button size="small" @click="showMoreText">展示更多</el-button>
            </div>
          </template>

          <div v-else-if="kind === 'unsupported'" class="doc-preview__state">
            <el-icon :size="32"><Document /></el-icon>
            <p>该类型暂不支持在线预览（.{{ ext || '未知' }}）。</p>
            <p class="doc-preview__sub">支持：md / html / 纯文本代码（txt/json/yaml/csv/xml 等） / 图片（png/jpg/gif/webp/svg）/ PDF</p>
            <el-button size="small" @click="download">下载查看</el-button>
          </div>
        </div>
      </el-tab-pane>


      <!-- M5：结构化数据（新解析链产出的知识快照视图） -->
      <el-tab-pane name="structured">
        <template #label>
          结构化数据
          <el-tag v-if="parseResult" size="small" effect="light" class="doc-preview__tab-tag">
            {{ parseResult.snapshot.quality_status }}
          </el-tag>
        </template>
        <div class="doc-preview__knowledge">
          <div v-if="parseLoading" v-loading="true" class="doc-preview__structured-loading" />

          <div v-else-if="parseError" class="doc-preview__state">
            <el-icon :size="32"><WarningFilled /></el-icon>
            <p>{{ parseError }}</p>
            <el-button v-if="parseRetryable" size="small" @click="loadParseResult">重试</el-button>
          </div>

          <template v-else-if="parseResult">
            <!-- A0-1 版本横幅：当前可搜索版本 vs 最新上传版本 -->
            <div
              v-if="parseResult.versioning"
              class="doc-preview__version-banner"
              :class="{ 'doc-preview__version-banner--stale': !parseResult.versioning.in_sync }"
              data-testid="doc-version-banner"
            >
              <template v-if="parseResult.versioning.in_sync">
                当前可搜索版本与最新上传版本一致（rev.{{ parseResult.versioning.latest?.source_content_revision ?? '—' }}）
              </template>
              <template v-else-if="parseResult.versioning.serving">
                当前可搜索 rev.{{ parseResult.versioning.serving.source_content_revision ?? '—' }}；
                最新上传 rev.{{ parseResult.versioning.latest?.source_content_revision ?? '—' }} 尚未进入搜索
              </template>
              <template v-else>
                以下为最新上传版本（rev.{{ parseResult.versioning.latest?.source_content_revision ?? '—' }}）的解析结果，
                尚未进入搜索——搜索与 Agent 读取暂不可用
              </template>
              <el-button
                v-if="parseResult.versioning.serving && !parseResult.versioning.in_sync"
                size="small" text type="primary"
                data-testid="doc-version-toggle"
                @click="toggleParseView"
              >
                {{ parseView === 'current_serving' ? '查看最新解析' : '查看当前可搜索版本' }}
              </el-button>
            </div>

            <!-- 各区块独立收缩；顺序：快照 → 结构图 → 大纲 → 切片 → 元素 → 表格 -->
            <el-collapse v-model="openCards" class="doc-preview__cards">
              <!-- 知识快照 -->
              <el-collapse-item name="snapshot" title="知识快照">
                <div class="doc-preview__meta-grid">
                  <div class="doc-preview__meta-item">
                    <span class="doc-preview__meta-label">质量结论</span>
                    <el-tag :type="parseResult.snapshot.quality_status === 'PASS' ? 'success' : 'warning'" size="small">
                      {{ parseResult.snapshot.quality_status }}
                    </el-tag>
                  </div>
                  <div class="doc-preview__meta-item">
                    <span class="doc-preview__meta-label">内容版本</span>
                    <span>rev.{{ parseResult.snapshot.source_content_revision ?? '—' }}</span>
                  </div>
                  <div class="doc-preview__meta-item">
                    <span class="doc-preview__meta-label">元素 / 容器 / 关系</span>
                    <span>{{ parseResult.elements.count }} / {{ parseResult.diagnostics.containers }} / {{ parseResult.diagnostics.relations }}</span>
                  </div>
                  <div class="doc-preview__meta-item">
                    <span class="doc-preview__meta-label">切片数</span>
                    <span>{{ parseResult.segments.count }}</span>
                  </div>
                  <div class="doc-preview__meta-item">
                    <span class="doc-preview__meta-label">解析管线</span>
                    <span class="doc-preview__mono">{{ parseResult.snapshot.parser_fingerprint || '—' }}</span>
                  </div>
                  <div class="doc-preview__meta-item">
                    <span class="doc-preview__meta-label">切片策略</span>
                    <span class="doc-preview__mono">{{ parseResult.snapshot.compiler_fingerprint || '默认' }}</span>
                  </div>
                </div>
              </el-collapse-item>

              <!-- 将同一份线上结构化结果编排成确定性文档结构图，不引入推断关系。 -->
              <el-collapse-item
                v-if="parseResult.outline.length || parseResult.tables.length"
                name="structure-graph"
                title="文档结构图"
              >
                <DocumentStructureGraph
                  v-if="activeTab === 'structured' && openCards.includes('structure-graph')"
                  :document-title="doc?.document_name || parseResult.snapshot.title || '未命名文档'"
                  :result="parseResult"
                />
              </el-collapse-item>

              <!-- 文档大纲 -->
              <el-collapse-item v-if="parseResult.outline.length" name="outline" title="文档大纲">
                <el-tree
                  :data="outlineTree"
                  :props="{ label: 'title', children: 'children' }"
                  default-expand-all
                  class="doc-preview__outline"
                />
              </el-collapse-item>

              <!-- 切片 -->
              <el-collapse-item
                v-if="parseResult.segments.items.length"
                name="segments"
                :title="`切片（${parseResult.segments.count}）`"
              >
                <el-collapse>
                  <el-collapse-item
                    v-for="seg in parseResult.segments.items"
                    :key="seg.segment_index"
                    :name="seg.segment_index"
                  >
                    <template #title>
                      <span class="doc-preview__seg-title">
                        <span class="doc-preview__seg-idx">#{{ seg.segment_index }}</span>
                        <span v-if="seg.heading_chain.length" class="doc-preview__seg-path">
                          {{ seg.heading_chain.map(h => h.title).join(' › ') }}
                        </span>
                        <el-tag v-if="seg.semantic_role" size="small" effect="light">{{ seg.semantic_role }}</el-tag>
                        <el-tag size="small" effect="plain" class="doc-preview__seg-type">{{ seg.block_type }}</el-tag>
                      </span>
                    </template>
                    <pre class="doc-preview__seg-text">{{ seg.text }}</pre>
                  </el-collapse-item>
                </el-collapse>
                <p v-if="parseResult.segments.count > parseResult.segments.items.length" class="doc-preview__muted">
                  当前仅展示前 {{ parseResult.segments.items.length }} 条切片。
                </p>
              </el-collapse-item>

              <!-- 结构元素 -->
              <el-collapse-item
                v-if="parseResult.elements.items.length"
                name="elements"
                :title="`结构元素（${parseResult.elements.count}）`"
              >
                <el-collapse>
                  <el-collapse-item
                    v-for="element in parseResult.elements.items"
                    :key="element.element_id"
                    :name="element.element_id"
                  >
                    <template #title>
                      <el-tag size="small" effect="plain">{{ element.element_type }}</el-tag>
                      <span class="doc-preview__element-title">{{ element.text || '空元素' }}</span>
                    </template>
                    <pre class="doc-preview__seg-text">{{ element.text }}</pre>
                    <p v-if="element.has_evidence" class="doc-preview__muted">已保留来源定位</p>
                  </el-collapse-item>
                </el-collapse>
                <p v-if="parseResult.elements.count > parseResult.elements.items.length" class="doc-preview__muted">
                  当前仅展示前 {{ parseResult.elements.items.length }} 个元素。
                </p>
              </el-collapse-item>

              <!-- 表格 -->
              <el-collapse-item
                v-if="parseResult.tables.length"
                name="tables"
                :title="`表格（${parseResult.tables.length}）`"
              >
                <div v-for="t in parseResult.tables" :key="t.table_id" class="doc-preview__table-block">
                  <div class="doc-preview__table-caption">{{ t.rows }} 行 × {{ t.columns }} 列</div>
                  <el-table :data="tableRows(t)" size="small" border class="kb-table">
                    <el-table-column
                      v-for="(_, ci) in tableRows(t)[0] || []"
                      :key="ci"
                      :label="t.header[ci] || String(ci + 1)"
                    >
                      <template #default="{ row }">
                        <span>{{ row[ci] }}</span>
                      </template>
                    </el-table-column>
                  </el-table>
                  <p v-if="t.rows > t.preview.length" class="doc-preview__muted">
                    仅预览前 {{ t.preview.length }} 行（共 {{ t.rows }} 行数据行）——完整表格查询目前可通过 Agent 的 get_knowledge 表格查询能力使用。
                  </p>
                </div>
              </el-collapse-item>
            </el-collapse>

            <div v-if="parseResult.diagnostics.warnings.length" class="doc-preview__card">
              <div class="doc-preview__card-title">解析诊断</div>
              <ul class="doc-preview__diagnostics">
                <li v-for="warning in parseResult.diagnostics.warnings" :key="warning">{{ warning }}</li>
              </ul>
            </div>
          </template>
        </div>
      </el-tab-pane>
      <template v-if="knowledgeMined">
        <el-tab-pane v-if="units.length || assistUnits.length" :label="`检索单元 (${units.length})`" name="units">
          <div class="doc-preview__knowledge">
            <div v-if="!units.length" class="doc-preview__muted">
              当前可搜索版本没有可返回的原始证据表示——可能挖掘未完成或未生成检索表示。
            </div>
            <div v-else class="doc-preview__units" data-testid="doc-retrieval-units">
              <div v-for="(u, i) in units" :key="u.representation_id || i" class="doc-preview__unit">
                <div class="doc-preview__unit-head">
                  <el-tag v-if="u.unit_type" size="small" effect="plain">{{ u.unit_type }}</el-tag>
                  <span v-if="u.structural_context" class="doc-preview__seg-path">{{ u.structural_context }}</span>
                </div>
                <pre v-if="u.text" class="doc-preview__seg-text">{{ u.text }}</pre>
              </div>
            </div>

            <!-- A0-5：搜索辅助表示（alias）——只助召回，不作为可引用原文 -->
            <el-collapse v-if="assistUnits.length" class="doc-preview__assist">
              <el-collapse-item name="assist">
                <template #title>
                  <span class="doc-preview__assist-title">
                    搜索辅助表示（{{ assistUnits.length }}）——帮助召回，不是原文
                  </span>
                </template>
                <div class="doc-preview__units" data-testid="doc-search-assist">
                  <div v-for="(u, i) in assistUnits" :key="u.representation_id || i" class="doc-preview__unit">
                    <div class="doc-preview__unit-head">
                      <el-tag size="small" effect="plain">{{ u.unit_type }}</el-tag>
                      <span class="doc-preview__seg-path">搜索辅助</span>
                    </div>
                    <pre v-if="u.text" class="doc-preview__seg-text">{{ u.text }}</pre>
                  </div>
                </div>
              </el-collapse-item>
            </el-collapse>
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
import type { ParseResult } from '@/api/mining'
import DocumentStructureGraph from '@/components/kb/DocumentStructureGraph.vue'
import { apiErrorDetail } from '@/api/proxyClient'
import { filenameFromDisposition, saveBlob } from '@/utils/download'
import { docStatusLabel, docStatusTagType } from '@/views/kb/kbMeta'
import type { Component } from 'vue'
import type { KbDocument, KbDocRetrievalUnit, KbDocSearchAssistUnit } from '@/types/kb'

const PREVIEW_MAX_BYTES = 50 * 1024 * 1024
const TEXT_RENDER_LIMIT = 200_000

const props = defineProps<{ kbId: string; docId: string }>()
const router = useRouter()
const kbApi = useKbApi()

const doc = ref<KbDocument | null>(null)
const loading = ref(false)
const previewLoading = ref(false)
const downloading = ref(false)
const error = ref('')
const text = ref<string | null>(null)
const html = ref('')
const objectUrl = ref<string | null>(null)
// 大文件预览直连（预签名 URL）：iframe/img 直接指向对象存储，浏览器
// 自带 Range 分页按需加载——替代全量下载后再首屏。
const directUrl = ref<string | null>(null)
const blobSize = ref(0)
// 大文本/md 分段渲染：初始限量 + 「展示更多」翻倍，避免巨型 DOM 单节点。
const fullText = ref<string | null>(null)
const renderLimit = ref(TEXT_RENDER_LIMIT)

const units = ref<KbDocRetrievalUnit[]>([])
const assistUnits = ref<KbDocSearchAssistUnit[]>([])
const knowledgeMined = ref(false)
const activeTab = ref<'preview' | 'structured' | 'units'>('preview')
// A0-1：结构化数据视图（默认 current_serving；最新解析显式切换）
const parseView = ref<'current_serving' | 'latest_revision'>('current_serving')
let parseGeneration = 0

// 结构图承担主要浏览入口；原始大纲、切片、元素和表格清单按需展开。
const openCards = ref<string[]>(['snapshot', 'structure-graph'])

// M5 结构化数据（新链知识快照）：尽力加载，404 = 尚未走新链更新知识
const parseResult = ref<ParseResult | null>(null)
const parseLoading = ref(false)
const parseError = ref('')
const parseRetryable = ref(false)

const outlineTree = computed(() => {
  type Node = { title: string; children: Node[] }
  const roots: Node[] = []
  const stack: Node[] = []
  for (const node of parseResult.value?.outline ?? []) {
    const item: Node = { title: node.title, children: [] }
    while (stack.length >= node.level) stack.pop()
    if (stack.length) stack[stack.length - 1].children.push(item)
    else roots.push(item)
    stack.push(item)
  }
  return roots
})

function tableRows(t: { preview: string[][] }): string[][] {
  return t.preview.map(row => row.map(cell => cell ?? ''))
}

async function loadParseResult() {
  // 代际守卫：快速切换视图时旧响应不得覆盖新视图（对齐 KbRunDetailView.fetchTrace）
  const generation = ++parseGeneration
  parseResult.value = null
  parseError.value = ''
  parseRetryable.value = false
  parseLoading.value = true
  try {
    // A0-1：默认（current_serving）不传 view——后端默认即当前可搜索版本
    const result = parseView.value === 'latest_revision'
      ? await kbApi.getDocumentParseResult(props.kbId, props.docId, 'latest_revision')
      : await kbApi.getDocumentParseResult(props.kbId, props.docId)
    if (generation === parseGeneration) parseResult.value = result
  } catch (e) {
    if (generation !== parseGeneration) return
    // 对抗评审 HIGH-1：按 HTTP 状态码分支（文案正则永远匹配不到后端
    // detail）；404 = 未走新链（引导），503 = 未接线，其余原样展示。
    const status = (e as { response?: { status?: number } })?.response?.status
    parseRetryable.value = status !== 404 && status !== 503
    if (status === 404) {
      parseError.value = '该文档还没有新链解析结果。触发「更新知识」后，这里会展示解析出的标题树、表格网格与切片。'
    } else if (status === 503) {
      parseError.value = '结构化数据视图未在本部署启用。'
    } else {
      parseError.value = await apiErrorDetail(e)
    }
  } finally {
    if (generation === parseGeneration) parseLoading.value = false
  }
}


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
  directUrl.value = null
  text.value = null
  html.value = ''
  error.value = ''
  blobSize.value = 0
  fullText.value = null
  renderLimit.value = TEXT_RENDER_LIMIT
}

function resetKnowledge() {
  units.value = []
  assistUnits.value = []
  knowledgeMined.value = false
  activeTab.value = 'preview'
  parseResult.value = null
  parseError.value = ''
  parseRetryable.value = false
  parseView.value = 'current_serving'
}

/** A0-1：当前可搜索版本 ↔ 最新解析 切换 */
function toggleParseView() {
  parseView.value = parseView.value === 'current_serving'
    ? 'latest_revision' : 'current_serving'
  void loadParseResult()
}

const textTruncated = computed(() => (fullText.value?.length ?? 0) > renderLimit.value)

/** 渲染可见文本/HTML（按 renderLimit 截断，大文件分步渲染）.*/
function renderTextual(content: string) {
  fullText.value = content
  const shown = content.slice(0, renderLimit.value)
  const k = kind.value
  if (k === 'md') html.value = DOMPurify.sanitize(marked.parse(shown) as string)
  else if (k === 'html') html.value = DOMPurify.sanitize(shown)
  else text.value = shown
}

function showMoreText() {
  renderLimit.value *= 2
  if (fullText.value !== null) renderTextual(fullText.value)
}

async function loadPreview() {
  previewLoading.value = true
  try {
    // 1) PDF/图片：预签名直连（浏览器 Range 按需加载，首屏只拉首页字节）。
    let url: string | null = null
    try {
      url = await kbApi.getDocumentPreviewUrl(props.kbId, props.docId)
    } catch {
      url = null // legacy 本地文档 / 对象缺失 → 走回落
    }
    const k = kind.value
    if (url && (k === 'pdf' || k === 'image')) {
      directUrl.value = url
      return
    }
    // 2) 文本类：直连取内容（省一跳后端全量代理）；CORS 不通则回落
    //    带鉴权的 download 代理。
    let blob: Blob | null = null
    if (url && (k === 'md' || k === 'html' || k === 'text')) {
      try {
        const resp = await fetch(url)
        if (!resp.ok) throw new Error(`preview fetch ${resp.status}`)
        blob = await resp.blob()
      } catch {
        blob = null
      }
    }
    if (blob === null) {
      try {
        blob = await kbApi.downloadDocument(props.kbId, props.docId)
      } catch (e) {
        error.value = await apiErrorDetail(e)
        return
      }
    }
    blobSize.value = blob.size
    if ((k === 'image' || k === 'pdf')) {
      if (blob.size > PREVIEW_MAX_BYTES) return // tooLarge 分支提示下载
      objectUrl.value = URL.createObjectURL(blob)
      return
    }
    if (k === 'md' || k === 'html' || k === 'text') {
      if (blob.size > PREVIEW_MAX_BYTES) return // tooLarge 分支提示下载
      renderTextual(await blob.text())
    }
  } finally {
    previewLoading.value = false
  }
}

async function load() {
  loading.value = true
  cleanup()
  resetKnowledge()
  try {
    // 元信息先出（不等文件字节）；预览与知识数据各自异步。
    const d = await kbApi.getDocument(props.kbId, props.docId)
    doc.value = d
    loading.value = false
    void loadPreview()
    // 已挖掘文档只呈现正式检索资产；研究实体/关系不进入产品面。
    if (d.status === 'mined' || d.status === 'published') {
      const knowledge = await kbApi.getDocumentKnowledge(props.kbId, props.docId).catch(() => null)
      if (knowledge && knowledge.mined) {
        knowledgeMined.value = true
        units.value = knowledge.retrieval_units ?? []
        assistUnits.value = knowledge.search_assist_units ?? []
      } else {
        knowledgeMined.value = true // 状态显示已挖但接口失败：允许进入知识视图，Tab 按实际数据动态出
      }
    }
    void loadParseResult()
  } catch (e) {
    error.value = await apiErrorDetail(e)
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
.doc-preview__element-title { margin-left: 8px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.doc-preview__diagnostics { margin: 0; padding-left: 20px; color: var(--el-color-warning); }

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

.doc-preview__more {
  display: flex; align-items: center; gap: 10px; justify-content: center;
  padding: 10px 0 2px; color: var(--kb-text-tertiary); font-size: 12px;
}

/* M5 结构化数据 */
.doc-preview__version-banner {
  display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
  border: 1px solid var(--kb-border-light); border-radius: 8px;
  padding: 10px 14px; margin-bottom: 12px;
  font-size: 13px; color: var(--kb-text-secondary);
  background: var(--kb-bg-card);
}
.doc-preview__version-banner--stale {
  border-color: var(--el-color-warning-light-5);
  background: var(--el-color-warning-light-9);
  color: var(--el-color-warning-dark-2);
}
.doc-preview__assist-title { font-size: 13px; font-weight: 600; }
.doc-preview__assist { margin-top: 12px; }
.doc-preview__cards {
  --el-collapse-border-color: var(--kb-border-light);
}
.doc-preview__cards :deep(.el-collapse-item__header) {
  font-weight: 600; font-size: 13px; color: var(--kb-text-primary);
}
.doc-preview__cards :deep(.el-collapse-item__content) {
  padding-bottom: 12px;
}
.doc-preview__card {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  padding: 12px 16px;
  margin-bottom: 16px;
  background: var(--el-bg-color);
}
.doc-preview__card-title {
  font-weight: 600;
  font-size: 13px;
  color: var(--el-text-color-primary);
  margin-bottom: 10px;
}
.doc-preview__meta-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 8px 16px;
}
.doc-preview__meta-item {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
}
.doc-preview__meta-label {
  color: var(--el-text-color-secondary);
  flex-shrink: 0;
}
.doc-preview__mono {
  font-family: var(--el-font-family-mono, monospace);
  font-size: 12px;
  word-break: break-all;
}
.doc-preview__outline {
  background: transparent;
  --el-tree-node-content-height: 28px;
}
.doc-preview__table-block {
  margin-bottom: 12px;
}
.doc-preview__table-caption {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-bottom: 6px;
}
.doc-preview__seg-path {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  margin-right: 8px;
}
.doc-preview__seg-type {
  flex-shrink: 0;
}
.doc-preview__structured-loading {
  min-height: 160px;
}
.doc-preview__tab-tag {
  margin-left: 6px;
}
</style>
