<template>
  <div class="kb-search">
    <!-- 管线选择区：库级绑定 + 生效说明 -->
    <div class="kb-search__pipeline">
      <div class="kb-search__pipeline-row">
        <span class="kb-search__label">{{ t('kbSearch.panel.paradigmLabel') }}</span>
        <el-select
          v-model="selectedParadigmId"
          :disabled="!canWrite || savingBinding"
          :placeholder="t('kbSearch.panel.followOfficialPlaceholder')"
          clearable
          size="default"
          class="kb-search__select"
          @change="saveBinding"
        >
          <el-option
            v-for="p in activeParadigms"
            :key="p.id"
            :value="p.id"
            :label="p.name"
          />
        </el-select>
        <el-tag v-if="resolveInfo" size="small" :type="sourceTagType" effect="light">
          {{ sourceLabel }}
        </el-tag>
        <el-tag
          v-if="resolveInfo?.degraded"
          size="small" type="warning" effect="plain"
        >{{ t('kbSearch.panel.bindingInvalid') }}</el-tag>
        <span v-if="savingBinding" class="kb-search__muted">{{ t('kbSearch.panel.saving') }}</span>
      </div>
      <p class="kb-search__pipeline-hint">
        {{ t('kbSearch.panel.bindingHint1') }}
        {{ t('kbSearch.panel.bindingHint2') }}
      </p>
      <div v-if="configurationError" class="kb-search__error">
        {{ configurationError }}
        <el-button size="small" @click="reload">{{ t('common.retry') }}</el-button>
      </div>
    </div>

    <!-- 搜索区 -->
    <div class="kb-search__bar">
      <el-input
        v-model="query"
        size="large"
        :placeholder="t('kbSearch.panel.queryPlaceholder')"
        :disabled="searching"
        clearable
        @keyup.enter="run"
      >
        <template #append>
          <el-button :loading="searching" @click="run">{{ t('kbSearch.panel.searchButton') }}</el-button>
        </template>
      </el-input>
      <el-select
        v-if="folderOptions.length"
        v-model="directoryScope"
        class="kb-search__dir"
        size="large" clearable :placeholder="t('kbSearch.panel.anyDirectory')"
        :disabled="searching"
        data-testid="kb-search-directory"
      >
        <el-option v-for="p in folderOptions" :key="p" :label="p" :value="p" />
      </el-select>
    </div>

    <!-- A2 范围徽标：本节/本节及子节（从文档页大纲进入），可一键回整篇 -->
    <div v-if="scope || scopeDocumentRef" class="kb-search__scope" data-testid="kb-search-scope">
      <el-tag size="small" type="warning" effect="light" closable @close="clearScope">
        {{ t('kbSearch.panel.scopeTag', { title: scope?.title || scopeDocumentTitle, mode: scope ? scopeModeLabel : t('kbSearch.panel.scopeWholeDoc') }) }}
      </el-tag>
      <el-button link size="small" @click="clearScope">{{ scope && scopeDocumentRef ? t('kbSearch.panel.backToWholeDoc') : t('kbSearch.panel.searchWholeKb') }}</el-button>
    </div>

    <!-- 生效管线 -->
    <el-alert v-if="effective" class="kb-search__effective" :closable="false" type="info">
      <template #title>
        {{ t('kbSearch.panel.effectiveLine', { name: effective.name }) }}
        <span class="kb-search__muted">{{ t('kbSearch.panel.effectiveMeta', { source: effective.sourceLabel, version: effective.version }) }}</span>
      </template>
    </el-alert>

    <!-- 结果（批次8 R8：EvidenceResponse 协议——type/content/source/truncated，无 score/rank） -->
    <div v-if="error" class="kb-search__error">{{ error }}</div>
    <template v-if="evidence.length">
      <div class="kb-search__meta">
        {{ t('kbSearch.panel.evidenceCount', { n: evidence.length }) }}<span v-if="hasMore">{{ t('kbSearch.panel.evidenceHasMore') }}</span>
      </div>
      <div class="kb-search__results">
        <div v-for="(ev, i) in evidence" :key="ev.ref ?? i" class="kb-search__item">
          <div class="kb-search__item-head">
            <span class="kb-search__item-type">{{ ev.type || t('kbSearch.panel.evidence') }}</span>
            <span v-if="ev.ref?.startsWith('ev_')" class="kb-search__item-actions">
              <span v-if="ev.truncated && !expanded[ev.ref ?? '']" class="kb-search__item-trunc" :title="t('kbSearch.panel.truncatedTitle')">{{ t('kbSearch.panel.truncated') }}</span>
              <el-button
                size="small" text type="primary"
                :loading="expanding === ev.ref"
                @click="expandEvidence(ev)"
              >{{ expanded[ev.ref!] ? t('kbSearch.panel.collapse') : t('kbSearch.panel.viewFull') }}</el-button>
            </span>
          </div>
          <p class="kb-search__item-text" :class="{ 'is-clamp': !expanded[ev.ref ?? ''] && ev.content && ev.content.length > 400 }">
            {{ expanded[ev.ref ?? ''] ? (fullContent[ev.ref ?? ''] ?? ev.content) : ev.content }}
          </p>
          <!-- A1（37 号 P0-6）：来源成为结构入口——文件名开原件、章节进大纲锚、
               位置徽标展示 locator；跳转目标全部由服务端解析端点给出。 -->
          <div v-if="sourceLabelOf(ev) || locatorLabelOf(ev)" class="kb-search__item-src">
            <template v-if="sourceLabelOf(ev)">
              {{ t('kbSearch.panel.sourcePrefix') }}
              <!-- 跳转仅支持 ev_ 前缀（doc_/st_ 直接证据走 Agent 结构工具，无解析端点） -->
              <el-button
                v-if="ev.ref?.startsWith('ev_')"
                text type="primary" size="small" class="kb-search__src-link"
                :loading="navigating === ev.ref"
                :title="t('kbSearch.panel.openOriginalTitle')"
                @click="goToSource(ev, 'document')"
              >{{ ev.source?.file_name || t('kbSearch.graph.kindDocument') }}</el-button>
              <span v-else>{{ ev.source?.file_name || t('kbSearch.graph.kindDocument') }}</span>
              <el-button
                v-if="ev.ref?.startsWith('ev_') && ev.source?.section"
                text type="primary" size="small" class="kb-search__src-link"
                :loading="navigating === ev.ref"
                :title="t('kbSearch.panel.locateSectionTitle')"
                @click="goToSource(ev, 'section')"
              >{{ ev.source.section }}</el-button>
              <span v-else-if="ev.source?.section">{{ ev.source.section }}</span>
              <span v-if="ev.source?.knowledge_base">{{ t('kbSearch.panel.kbParens', { name: ev.source.knowledge_base }) }}</span>
            </template>
            <span v-if="locatorLabelOf(ev)" class="kb-search__loc" :data-testid="`loc-${ev.ref}`">
              {{ locatorLabelOf(ev) }}
            </span>
            <el-button
              v-if="ev.ref?.startsWith('ev_') && ev.source?.locator?.table_ref"
              text type="primary" size="small" class="kb-search__src-link"
              :loading="navigating === ev.ref"
              @click="goToSource(ev, 'table')"
            >{{ t('kbSearch.panel.viewTable') }}</el-button>
          </div>
        </div>
      </div>
    </template>
    <el-empty
      v-else-if="searched && !searching && !error"
      :description="emptyHint"
      :image-size="80"
    >
      <el-button v-if="readiness && readiness.retrieval_units === 0" text type="primary" @click="goMine">
        {{ t('kbSearch.panel.goMining') }}
      </el-button>
    </el-empty>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useKbApi } from '@/api/kb'
import { useOperatorApi } from '@/api/operator'
import { useServingApi, type ParadigmResolveResult } from '@/api/serving'
import { apiErrorDetail } from '@/api/proxyClient'
import { useDomainStore } from '@/stores/domain'
import { t } from '@/locales/t'
import type { KbSummary } from '@/types/kb'
import type { EvidenceItem, ParadigmView } from '@/types/operator'

const props = defineProps<{
  kb: KbSummary
  canWrite: boolean
  /** 批次4 readiness：units=0 时空结果给"去挖掘"引导 */
  readiness?: { retrieval_units: number } | null
}>()
const emit = defineEmits<{ updated: []; goMining: [] }>()

const kbApi = useKbApi()
const operatorApi = useOperatorApi()
const servingApi = useServingApi()
const domainStore = useDomainStore()
const router = useRouter()
const route = useRoute()

const paradigms = ref<ParadigmView[]>([])
const selectedParadigmId = ref<string | null>(null)
const resolveInfo = ref<ParadigmResolveResult | null>(null)
const savingBinding = ref(false)

const query = ref('')
const searching = ref(false)

// A2 章节范围（39 号 §2.4）：从文档页大纲「本节(及子节)搜索」跳转带入；
// ref 是服务端投影的章节内部 ref（within 直收），前端不拼接。
const scope = ref<{ ref: string; title: string; mode: 'exact' | 'descendants' } | null>(null)
const scopeDocumentRef = ref<string | null>(null)
const scopeDocumentTitle = ref(t('kbStruct.currentDoc'))
function clearScope() {
  if (scope.value) scope.value = null
  else scopeDocumentRef.value = null
  evidence.value = []
  searched.value = false
}
const scopeModeLabel = computed(() =>
  scope.value?.mode === 'descendants' ? t('kbSearch.panel.scopeDescendants') : t('kbSearch.panel.scopeExact'))

// 57号目录范围（含子目录递归）：目录树来自 folders 接口，filters.directory_prefix
// 直收服务端递归语义；与章节/整篇范围可叠加（serving 平铺合并为 AND）。
const folderOptions = ref<string[]>([])
const directoryScope = ref('')
async function loadFolderOptions(generation: number, kbId: string) {
  try {
    const folders = await kbApi.listFolders(props.kb.id)
    // 57号审查M-1：快速切库时旧库的目录树不得落到新库（下拉显示 A 库目录）
    if (generation !== reloadGeneration || kbId !== props.kb.id) return
    folderOptions.value = folders.map((f) => f.path).sort((a, b) => a.localeCompare(b))
  } catch {
    if (generation !== reloadGeneration || kbId !== props.kb.id) return
    // codex P2：加载失败不仅清选项——已选目录范围也必须清（控件隐藏后
    // 旧 prefix 会变成"看不见却生效"的过滤）
    folderOptions.value = []
    directoryScope.value = ''
  }
}
const searched = ref(false)
const error = ref('')
/** codex P1-4：检索请求代际——kb 切换（reloadGeneration 递增）与新旧响应隔离。 */
let searchGeneration = 0
const evidence = ref<EvidenceItem[]>([])
// 证据展开（2026-09-01）：默认长文本折叠 400 字，点「查看完整」取回全文
const expanded = ref<Record<string, boolean>>({})
const fullContent = ref<Record<string, string>>({})
const expanding = ref('')

async function expandEvidence(ev: EvidenceItem) {
  const ref = ev.ref ?? ''
  if (!ref) return
  if (expanded.value[ref]) {
    expanded.value[ref] = false
    return
  }
  if (!fullContent.value[ref]) {
    expanding.value = ref
    try {
      // 一律 whole_document：用户点的是「查看完整」——parent 粒度常与已展示
      // 内容相同，点了没视觉变化（2026-09-01 用户反馈）。
      const item = await servingApi.getEvidenceFull(
        ref, domainStore.currentDomain, props.kb.id, 'whole_document')
      fullContent.value[ref] = item.content ?? ''
    } catch (e) {
      ElMessage.error(t('kbSearch.panel.fetchFullFailed'))
      return
    } finally {
      expanding.value = ''
    }
  }
  expanded.value[ref] = true
}
const hasMore = ref(false)
const effective = ref<{ name: string; version: number; sourceLabel: string } | null>(null)
const configurationError = ref('')
let reloadGeneration = 0

const activeParadigms = computed(() => paradigms.value.filter(p => p.status === 'active'))

const sourceLabel = computed(() => {
  const s = resolveInfo.value?.source
  if (s === 'library') return t('kbSearch.panel.sourceLibrary')
  if (s === 'official') return t('kbSearch.panel.sourceOfficial')
  return s ?? ''
})
const sourceTagType = computed(() =>
  resolveInfo.value?.source === 'library' ? 'success' : 'info')

const emptyHint = computed(() =>
  props.readiness && props.readiness.retrieval_units === 0
    ? t('kbSearch.panel.emptyNoUnits')
    : t('kbSearch.panel.emptyNoHit'))

async function reload() {
  const domain = domainStore.currentDomain
  const kbId = props.kb.id
  if (!domain) return
  const generation = ++reloadGeneration
  // codex 三审：上下文重载（切库/切域/挂载）作废在途检索并复位 loading——
  // 否则旧请求 finally 因代际失效跳过 searching=false，页面永久禁用
  searchGeneration++
  searching.value = false
  selectedParadigmId.value = props.kb.default_paradigm_id ?? null
  configurationError.value = ''
  void loadFolderOptions(generation, kbId) // 57号目录过滤选项：失败自吞（可选增强）
  try {
    paradigms.value = await operatorApi.listParadigms()
    if (generation !== reloadGeneration || domain !== domainStore.currentDomain || kbId !== props.kb.id) return
  } catch (e) {
    paradigms.value = []
    configurationError.value = await apiErrorDetail(e)
    resolveInfo.value = null
    return
  }
  await refreshResolve({ generation, domain, kbId })
}

async function refreshResolve(expected?: { generation: number; domain: string; kbId: string }) {
  if (!domainStore.currentDomain) return
  try {
    const resolved = await servingApi.resolveParadigm(
      domainStore.currentDomain, [props.kb.id])
    if (expected && (
      expected.generation !== reloadGeneration
      || expected.domain !== domainStore.currentDomain
      || expected.kbId !== props.kb.id
    )) return
    resolveInfo.value = resolved
  } catch (e) {
    if (expected && expected.generation !== reloadGeneration) return
    resolveInfo.value = null
    configurationError.value = await apiErrorDetail(e)
  }
}

async function saveBinding(value: string | null) {
  savingBinding.value = true
  try {
    await kbApi.updateKb(props.kb.id, { default_paradigm_id: value ?? null })
    ElMessage.success(value ? t('kbSearch.panel.bound') : t('kbSearch.panel.unbound'))
    emit('updated')
    await refreshResolve()
  } catch (e) {
    ElMessage.error(await apiErrorDetail(e))
    // 失败回滚选择器到库里现存值
    selectedParadigmId.value = props.kb.default_paradigm_id ?? null
  } finally {
    savingBinding.value = false
  }
}

async function run() {
  const q = query.value.trim()
  if (!q) return
  const resolved = resolveInfo.value
  if (configurationError.value) {
    error.value = configurationError.value
    return
  }
  if (!resolved?.bound || !resolved.paradigmId) {
    error.value = t('kbSearch.panel.noParadigm')
    evidence.value = []
    hasMore.value = false
    searched.value = true
    return
  }
  searching.value = true
  error.value = ''
  // codex P1-4：请求代际+目录快照——A 库/旧目录的慢响应不得覆盖切换后的页面
  const generation = ++searchGeneration
  const kbId = props.kb.id
  const dirSnapshot = directoryScope.value
  try {
    const out = await servingApi.runParadigmSearch(resolved.paradigmId, q, {
      domain: domainStore.currentDomain ?? undefined,
      kbIds: [props.kb.id],
      within: scope.value
        ? { section_refs: [scope.value.ref], section_scope: scope.value.mode }
        : scopeDocumentRef.value ? { document_refs: [scopeDocumentRef.value] } : undefined,
      filters: directoryScope.value ? { directory_prefix: directoryScope.value } : undefined,
    })
    if (generation !== searchGeneration || kbId !== props.kb.id
        || dirSnapshot !== directoryScope.value) return
    evidence.value = out.evidenceResponse?.evidence ?? []
    hasMore.value = out.evidenceResponse?.has_more ?? false
    effective.value = {
      name: resolved.name ?? resolved.paradigmId,
      version: resolved.version ?? 0,
      sourceLabel: resolved.degraded
        ? t('kbSearch.panel.sourceDegraded', { source: sourceLabel.value })
        : sourceLabel.value,
    }
    searched.value = true
  } catch (e) {
    if (generation !== searchGeneration || kbId !== props.kb.id
        || dirSnapshot !== directoryScope.value) return
    error.value = await apiErrorDetail(e)
    evidence.value = []
    hasMore.value = false
    searched.value = true
  } finally {
    if (generation === searchGeneration) searching.value = false
  }
}

function goMine() {
  emit('goMining')
}

/** 来源行：文件名（+章节路径）；库同名时附库名。 */
function sourceLabelOf(ev: EvidenceItem): string {
  const src = ev.source
  if (!src) return ''
  const parts: string[] = []
  if (src.file_name) parts.push(src.file_name)
  if (src.section) parts.push(src.section)
  const base = parts.join(' · ')
  if (src.knowledge_base && parts.length) return t('kbSearch.panel.sourceWithKb', { base, name: src.knowledge_base })
  return base || (src.knowledge_base ?? '')
}

/**
 * A1 位置徽标（34 号 P0-4）：页码 1 基；行号 0 基 end-exclusive 转人读
 * 「第 a–b 行」；sheet_cell 显示 Sheet!A1；native 显示挖掘侧 description。
 */
function locatorLabelOf(ev: EvidenceItem): string {
  const loc = ev.source?.locator
  if (!loc) return ''
  if (loc.kind === 'page') return t('kbSearch.panel.locatorPage', { page: String(loc.page) })
  if (loc.kind === 'line_range' && loc.line_start != null) {
    const first = loc.line_start + 1
    const last = loc.line_end != null ? loc.line_end : first
    return last > first
      ? t('kbSearch.panel.locatorLines', { first, last })
      : t('kbSearch.panel.locatorLine', { first })
  }
  if (loc.kind === 'sheet_cell') {
    if (loc.sheet && loc.cell) return `${loc.sheet}!${loc.cell}`
    return loc.cell ?? ''
  }
  return loc.description ?? ''
}

/**
 * A1 结构入口跳转（34 号 P0-6）：ev_ ref 先经服务端解析端点拿到导航锚
 * （document_id/大纲锚/表格锚），前端只路由不拼内部编号。
 * - document：打开原文（文档页默认原始预览 tab）
 * - section：文档页结构化视图 + 大纲锚高亮
 * - table：文档页结构化视图 + 表格锚（含所在章节锚）
 */
const navigating = ref('')
async function goToSource(ev: EvidenceItem, mode: 'document' | 'section' | 'table') {
  const ref = ev.ref ?? ''
  if (!ref.startsWith('ev_')) {
    ElMessage.warning(t('kbSearch.panel.noSourceRef'))
    return
  }
  navigating.value = ref
  try {
    const nav = await servingApi.getEvidenceSource(
      ref, domainStore.currentDomain ?? '', props.kb.id)
    const query: Record<string, string> = {}
    if (mode === 'section' && nav.section_element_id) query.anchor = nav.section_element_id
    if (mode === 'table') {
      if (nav.table_ref) query.table = nav.table_ref
      if (nav.row_index != null) query.row = String(nav.row_index)
      if (nav.section_element_id) query.anchor = nav.section_element_id
    }
    await router.push({
      name: 'kb-doc-preview',
      params: { kbId: props.kb.id, docId: nav.document_id },
      query,
    })
  } catch (e) {
    ElMessage.error(await apiErrorDetail(e))
  } finally {
    navigating.value = ''
  }
}

// A2：进入面板时吸收路由携带的范围（文档页「本节搜索」跳转）
{
  const q = route.query as Record<string, string | undefined>
  if (q.scopeRef && typeof q.scopeRef === 'string' && q.scopeRef) {
    scope.value = {
      ref: q.scopeRef,
      title: q.scopeTitle || t('kbSearch.panel.selectedSection'),
      mode: q.scopeMode === 'descendants' ? 'descendants' : 'exact',
    }
    scopeDocumentRef.value = typeof q.scopeDocumentRef === 'string' && q.scopeDocumentRef ? q.scopeDocumentRef : null
    scopeDocumentTitle.value = typeof q.scopeDocumentTitle === 'string' && q.scopeDocumentTitle ? q.scopeDocumentTitle : t('kbStruct.currentDoc')
  }
}

watch(() => props.kb.id, () => {
  // 切库必须清上一库的检索结果与管线横幅——只重载范式配置的话，
  // B 库页面会显示 A 库挖出的证据（2026-08-31 前端审查 M9）。
  evidence.value = []
  hasMore.value = false
  effective.value = null
  scope.value = null
  scopeDocumentRef.value = null
  // 57号审查H-2：目录范围一并清——否则 A 库的 prefix 静默过滤 B 库（且 B 库
  // 无文件夹时选择器隐藏，过滤条件生效却不可见不可清）。
  directoryScope.value = ''
  folderOptions.value = []
  searchGeneration++ // codex P1-4：作废在途响应，晚到的 A 库结果不落 B 库
  searched.value = false
  error.value = ''
  reload()
})
watch(() => props.kb.default_paradigm_id, (v) => { selectedParadigmId.value = v ?? null })
watch(() => domainStore.currentDomain, reload)
onMounted(reload)
</script>

<style scoped>
.kb-search {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.kb-search__pipeline {
  padding: 12px 16px;
  border: 1px solid var(--kb-border-light, var(--el-border-color-light));
  border-radius: 8px;
  background: var(--el-fill-color-extra-light);
}

.kb-search__pipeline-row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.kb-search__label {
  font-size: 13px;
  font-weight: 600;
  color: var(--kb-text-secondary);
}

.kb-search__select {
  width: 280px;
}

.kb-search__pipeline-hint {
  margin: 8px 0 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--kb-text-tertiary);
}

.kb-search__bar :deep(.el-input-group__append) {
  padding: 0 6px;
}

/* 57号目录范围选择器：与搜索框同行，窄屏换行 */
.kb-search__bar { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
.kb-search__bar > .el-input { flex: 1 1 320px; }
.kb-search__dir { width: 220px; flex: 0 0 auto; }

.kb-search__effective {
  padding: 6px 12px;
}

.kb-search__error {
  padding: 10px 14px;
  border-radius: 8px;
  background: var(--kb-danger-soft, #fef0f0);
  color: var(--kb-danger, #f56c6c);
  font-size: 13px;
  line-height: 1.6;
}

.kb-search__meta {
  font-size: 12.5px;
  color: var(--kb-text-tertiary);
}

.kb-search__results {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.kb-search__item {
  padding: 12px 16px;
  border: 1px solid var(--kb-border-light, var(--el-border-color-light));
  border-radius: 8px;
  background: var(--kb-bg-card, var(--el-bg-color));
}

.kb-search__item-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
}

.kb-search__item-type {
  font-size: 12px;
  font-weight: 600;
  color: #6366f1;
  letter-spacing: 0.02em;
}

.kb-search__item-trunc {
  font-size: 11.5px;
  color: #e6a23c;
}

.kb-search__item-text {
  margin: 6px 0 0;
  font-size: 13px;
  line-height: 1.7;
  color: var(--kb-text-secondary);
  white-space: pre-wrap;
  word-break: break-word;
}

.kb-search__item-src {
  margin-top: 6px;
  font-size: 12px;
  color: var(--kb-text-tertiary);
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 2px;
}

.kb-search__src-link {
  padding: 0 2px;
  height: auto;
  font-size: 12px;
}

.kb-search__loc {
  margin-left: 8px;
  padding: 1px 8px;
  border-radius: 10px;
  font-size: 11.5px;
  background: var(--el-fill-color);
  color: var(--kb-text-secondary);
}

.kb-search__muted {
  color: var(--kb-text-tertiary);
  font-size: 12px;
}

.kb-search__item-text.is-clamp {
  display: -webkit-box;
  -webkit-line-clamp: 6;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.kb-search__item-actions { display: inline-flex; align-items: center; gap: 4px; margin-left: auto; }
</style>
