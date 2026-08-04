<template>
  <div class="search-view">
    <!-- Search Bar -->
    <div class="search-view__bar">
      <el-input
        v-model="query"
        placeholder="输入你的问题，例如：SMF ADD UPF 的步骤是什么"
        size="large"
        clearable
        @keyup.enter="handleSearch"
        class="search-view__input"
      >
        <template #prefix>
          <el-icon><Search /></el-icon>
        </template>
      </el-input>
      <el-button type="primary" size="large" @click="handleSearch" :loading="searching">
        检索
      </el-button>
    </div>

    <!-- Domain & Options -->
    <div class="search-view__options">
      <label class="search-view__option">
        <span class="search-view__option-label">知识库</span>
        <el-select
          v-model="selectedKbIds"
          multiple
          collapse-tags
          collapse-tags-tooltip
          clearable
          filterable
          size="small"
          :loading="kbsLoading"
          placeholder="全部（当前生效发布）"
          class="search-view__kb-select"
        >
          <el-option
            v-for="kb in kbs"
            :key="kb.id"
            :label="kb.name"
            :value="kb.id"
          >
            <span>{{ kb.name }}</span>
            <span class="search-view__kb-meta">{{ kb.document_count }} 篇</span>
          </el-option>
        </el-select>
      </label>
      <label class="search-view__option">
        <el-switch v-model="debugMode" size="small" />
        <span>Debug 模式</span>
      </label>
      <span v-if="selectedKbIds.length" class="search-view__option-hint">
        仅检索所选知识库已挖掘的内容
      </span>
    </div>

    <el-alert
      v-if="searchError"
      :title="searchError"
      type="error"
      show-icon
      :closable="false"
      class="search-view__error"
    />

    <!-- Results -->
    <template v-if="result">
      <!-- Summary -->
      <div class="search-view__summary">
        <span>找到 <strong>{{ result.items?.length ?? 0 }}</strong> 条证据</span>
        <span v-if="result.relations?.length"> · {{ result.relations.length }} 条关系</span>
        <span v-if="result.debug?.trace"> · 耗时 {{ result.debug.trace.total_duration_ms.toFixed(0) }}ms</span>
      </div>

      <!-- Understanding Card -->
      <div class="search-view__understanding" v-if="result.debug?.understanding">
        <div class="search-view__understanding-items">
          <div class="understanding-tag">
            <span class="understanding-tag__label">意图</span>
            <span class="understanding-tag__value">{{ result.debug.understanding.intent }}</span>
          </div>
          <div class="understanding-tag" v-if="result.debug.understanding.source">
            <span class="understanding-tag__label">来源</span>
            <span class="understanding-tag__value">{{ result.debug.understanding.source }}</span>
          </div>
          <div class="understanding-tag" v-if="result.debug.understanding.keywords?.length">
            <span class="understanding-tag__label">关键词</span>
            <span class="understanding-tag__value">{{ result.debug.understanding.keywords.join(', ') }}</span>
          </div>
        </div>
      </div>

      <!-- Tabs -->
      <el-tabs v-model="activeTab" class="search-view__tabs">
        <!-- Evidence Tab -->
        <el-tab-pane label="证据列表" name="evidence">
          <div class="search-view__evidence-list">
            <EvidenceCard
              v-for="(item, idx) in result.items"
              :key="item.id"
              :item="item"
              :idx="idx"
              @view-full-text="openFullText"
            />
          </div>
          <EmptyState v-if="!result.items?.length" text="无检索结果" />
        </el-tab-pane>

        <!-- Pipeline Tab -->
        <el-tab-pane label="Pipeline 分析" name="pipeline" v-if="result.debug?.trace">
          <div class="search-view__pipeline-section">
            <h4 class="section-label">阶段耗时</h4>
            <PipelineTrace :stages="result.debug.trace.stages" />
          </div>
          <div class="search-view__pipeline-section" v-if="result.debug.route_plan">
            <h4 class="section-label">路由计划</h4>
            <div class="pipeline-info-grid">
              <div class="pipeline-info-item">
                <span class="pipeline-info-item__label">路由数</span>
                <span class="pipeline-info-item__value">{{ result.debug.route_plan.routes_count }}</span>
              </div>
              <div class="pipeline-info-item">
                <span class="pipeline-info-item__label">融合方法</span>
                <span class="pipeline-info-item__value">{{ result.debug.route_plan.fusion_method }}</span>
              </div>
              <div class="pipeline-info-item">
                <span class="pipeline-info-item__label">重排序</span>
                <span class="pipeline-info-item__value">{{ result.debug.route_plan.rerank_method }}</span>
              </div>
              <div class="pipeline-info-item" v-if="result.debug.candidate_count">
                <span class="pipeline-info-item__label">候选数</span>
                <span class="pipeline-info-item__value">{{ result.debug.candidate_count }}</span>
              </div>
            </div>
          </div>
        </el-tab-pane>

        <!-- Relations Tab -->
        <el-tab-pane :label="`关系 (${result.relations?.length ?? 0})`" name="relations">
          <div class="search-view__relations-list" v-if="result.relations?.length">
            <div
              v-for="rel in result.relations"
              :key="rel.id"
              class="relation-item"
            >
              <span class="relation-item__id">{{ rel.fromId.slice(0, 6) }}</span>
              <span class="relation-item__type">{{ rel.relationType }}</span>
              <span class="relation-item__id">{{ rel.toId.slice(0, 6) }}</span>
              <span class="relation-item__dist" v-if="rel.distance != null">d={{ rel.distance }}</span>
            </div>
          </div>
          <EmptyState v-else text="无关系数据" />
        </el-tab-pane>
      </el-tabs>
    </template>

    <!-- Empty -->
    <EmptyState v-if="!result && !searching && searchedOnce" text="未找到相关结果，换个关键词试试" />

    <FullTextDrawer
      v-model="fullTextOpen"
      :result="fullTextResult"
      :loading="fullTextLoading"
      :error="fullTextError"
      :domain="resultScope.domain"
      :kb-ids="resultScope.kbIds"
      :kbs="kbs"
    />
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { Search } from '@element-plus/icons-vue'
import { useDomainStore } from '@/stores/domain'
import { useServingApi } from '@/api/serving'
import { useKbApi } from '@/api/kb'
import { apiErrorDetail } from '@/api/proxyClient'
import type { FullTextResult, SearchContextItem, SearchResult } from '@/types'
import type { KbSummary } from '@/types/kb'
import EvidenceCard from '@/components/search/EvidenceCard.vue'
import FullTextDrawer from '@/components/search/FullTextDrawer.vue'
import PipelineTrace from '@/components/search/PipelineTrace.vue'
import EmptyState from '@/components/common/EmptyState.vue'

const domainStore = useDomainStore()
const servingApi = useServingApi()
const kbApi = useKbApi()

const query = ref('')
const searching = ref(false)
const searchedOnce = ref(false)
const searchError = ref('')
const kbs = ref<KbSummary[]>([])
const kbsLoading = ref(false)
const selectedKbIds = ref<string[]>([])
const result = ref<SearchResult | null>(null)
const activeTab = ref('evidence')
const debugMode = ref(true)

/** 产生当前结果的那次检索所用的范围，见 handleSearch。 */
const resultScope = ref<{ domain: string; kbIds: string[] }>({ domain: '', kbIds: [] })
const fullTextOpen = ref(false)
const fullTextLoading = ref(false)
const fullTextError = ref('')
const fullTextResult = ref<FullTextResult | null>(null)

/** 知识库列表按域取。列表本身已按 X-KB-User 过滤，所以选择器里只会出现可检索的库。 */
async function loadKbs() {
  const domain = domainStore.currentDomain
  if (!domain) return
  kbsLoading.value = true
  try {
    kbs.value = await kbApi.listKbs(domain)
  } catch (e) {
    // 取不到列表不该挡住检索——退化成「只能全域检索」。
    console.error('Failed to load knowledge bases:', e)
    kbs.value = []
  } finally {
    kbsLoading.value = false
  }
}

onMounted(loadKbs)

// 切域后旧域的 kb_id 一律失效（后端按 domain 过滤，留着必然 404），清空选择再重取。
watch(() => domainStore.currentDomain, () => {
  selectedKbIds.value = []
  loadKbs()
})

async function handleSearch() {
  if (!query.value.trim()) return
  searching.value = true
  searchedOnce.value = true
  searchError.value = ''
  try {
    const domain = domainStore.currentDomain
    const kbIds = [...selectedKbIds.value]
    result.value = await servingApi.search(query.value, {
      domain,
      debug: debugMode.value,
      kbIds,
    })
    // 钉住本次检索的范围。原文下钻必须用同一个范围去查，用「当前选择器的值」会在用户
    // 改了选择但没重新检索时把结果全部查成 out_of_scope。
    resultScope.value = { domain, kbIds }
    activeTab.value = 'evidence'
  } catch (e) {
    console.error('Search failed:', e)
    result.value = null
    searchError.value = await searchErrorMessage(e)
  } finally {
    searching.value = false
  }
}

/**
 * 展开某条证据的完整原文。
 *
 * ref.type 用条目自己的 kind：命中项是 retrieval_unit，上下文/支撑项是 raw_segment。
 * 不能靠 id 前缀猜——猜错会查错表，然后报成「找不到」。
 */
async function openFullText(item: SearchContextItem) {
  const type = item.kind === 'retrieval_unit' ? 'retrieval_unit' : 'raw_segment'
  fullTextOpen.value = true
  fullTextLoading.value = true
  fullTextError.value = ''
  fullTextResult.value = null
  try {
    fullTextResult.value = await servingApi.fetchFullText(
      [{ type, id: item.id }],
      // 带一段前后文：切分边界常把一句话劈成两段，只给命中段读起来是断的。
      { ...resultScope.value, granularity: 'window', windowRadius: 1 },
    )
  } catch (e) {
    console.error('Full text lookup failed:', e)
    fullTextError.value = await searchErrorMessage(e)
  } finally {
    fullTextLoading.value = false
  }
}

/** 把后端的错误码翻成人话——这几种检索侧最常见，其余回落到通用消息。 */
async function searchErrorMessage(e: unknown): Promise<string> {
  const code = (e as { response?: { data?: { error?: string } } })?.response?.data?.error
  switch (code) {
    case 'kb_not_found':
      // 后端对「不存在」和「无权限」返回同一个码，不泄露知识库是否存在。
      return '所选知识库不存在或无权访问，请刷新后重新选择'
    case 'no_active_kb_build':
      return '所选知识库还没有已挖掘的内容，请先在知识库里发起挖掘'
    case 'no_active_release':
      return '当前域没有生效的发布版本，无法检索'
    default:
      return await apiErrorDetail(e)
  }
}
</script>

<style scoped>
.search-view {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

/* Search bar */
.search-view__bar {
  display: flex;
  gap: 10px;
  align-items: stretch;
}

.search-view__input {
  flex: 1;
}

.search-view__input :deep(.el-input__wrapper) {
  border-radius: 10px;
  padding: 4px 16px;
}

/* Options */
.search-view__options {
  display: flex;
  gap: 16px;
  align-items: center;
}

.search-view__option {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--kb-text-secondary);
}

.search-view__option-label {
  white-space: nowrap;
}

.search-view__kb-select {
  width: 260px;
}

.search-view__kb-meta {
  float: right;
  margin-left: 12px;
  font-size: 11px;
  color: var(--kb-text-tertiary);
}

.search-view__option-hint {
  font-size: 12px;
  color: var(--kb-text-tertiary);
}

.search-view__error {
  margin-top: -4px;
}

/* Summary */
.search-view__summary {
  font-size: 13px;
  color: var(--kb-text-secondary);
  padding: 8px 0;
}

.search-view__summary strong {
  color: var(--kb-accent);
  font-weight: 700;
}

/* Understanding */
.search-view__understanding {
  background: var(--kb-accent-soft);
  border: 1px solid var(--kb-accent-medium);
  border-radius: var(--kb-radius-sm);
  padding: 12px 16px;
}

.search-view__understanding-items {
  display: flex;
  gap: 16px;
  flex-wrap: wrap;
}

.understanding-tag {
  display: flex;
  gap: 6px;
  align-items: center;
}

.understanding-tag__label {
  font-size: 11px;
  color: var(--kb-text-tertiary);
  text-transform: uppercase;
  font-weight: 600;
}

.understanding-tag__value {
  font-size: 13px;
  color: var(--kb-text-primary);
  font-weight: 600;
}

/* Tabs */
.search-view__tabs {
  margin-top: 4px;
}

.search-view__tabs :deep(.el-tabs__header) {
  margin-bottom: 14px;
}

/* Evidence list */
.search-view__evidence-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

/* Pipeline section */
.search-view__pipeline-section {
  margin-bottom: 20px;
}

.section-label {
  font-size: 13px;
  font-weight: 600;
  color: var(--kb-text-secondary);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin: 0 0 12px;
}

.pipeline-info-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 10px;
}

.pipeline-info-item {
  background: var(--kb-bg-card);
  border: 1px solid var(--kb-border-light);
  border-radius: var(--kb-radius-sm);
  padding: 10px 14px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.pipeline-info-item__label {
  font-size: 11px;
  color: var(--kb-text-tertiary);
  text-transform: uppercase;
  font-weight: 600;
}

.pipeline-info-item__value {
  font-size: 14px;
  font-weight: 700;
  color: var(--kb-text-primary);
}

/* Relations */
.search-view__relations-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.relation-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 12px;
  background: var(--kb-bg-card);
  border: 1px solid var(--kb-border-light);
  border-radius: var(--kb-radius-sm);
  font-size: 12px;
}

.relation-item__id {
  font-family: 'SF Mono', 'Cascadia Code', monospace;
  color: var(--kb-accent);
  font-weight: 500;
}

.relation-item__type {
  color: var(--kb-text-primary);
  font-weight: 600;
  background: var(--kb-border-light);
  padding: 1px 8px;
  border-radius: 3px;
}

.relation-item__dist {
  color: var(--kb-text-tertiary);
  font-variant-numeric: tabular-nums;
}
</style>
