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
      <el-button
        type="primary"
        size="large"
        @click="handleSearch"
        :loading="searching"
        :disabled="!canSearch"
      >
        检索
      </el-button>
    </div>

    <!-- Domain & Options -->
    <div class="search-view__options">
      <label class="search-view__option">
        <span class="search-view__option-label">范围</span>
        <el-select
          v-model="selectedKbIds"
          multiple
          collapse-tags
          collapse-tags-tooltip
          clearable
          filterable
          size="small"
          :loading="scopeLoading"
          placeholder="请选择检索范围"
          class="search-view__kb-select"
          @change="onScopeChange"
        >
          <!-- 只在该域真有 active release 时出现：纯 KB 部署下它是个不存在的范围 -->
          <el-option
            v-if="hasActiveRelease"
            :key="DOMAIN_RELEASE_SCOPE"
            label="域级生效发布"
            :value="DOMAIN_RELEASE_SCOPE"
          >
            <span>域级生效发布</span>
            <span class="search-view__kb-meta">含未归属知识库的历史语料</span>
          </el-option>
          <el-option
            v-for="kb in kbs"
            :key="kb.id"
            :label="kb.name"
            :value="kb.id"
          >
            <span>{{ kb.name }}</span>
            <span class="search-view__kb-meta">{{ kb.status_counts.total }} 篇</span>
          </el-option>
        </el-select>
      </label>
      <label class="search-view__option">
        <el-switch v-model="debugMode" size="small" />
        <span>Debug 模式</span>
      </label>
      <span v-if="scopeError" class="search-view__option-warn">
        {{ scopeError }}
        <el-button text type="primary" size="small" @click="loadScope">重试</el-button>
      </span>
      <span v-else-if="!canSearch" class="search-view__option-warn">
        {{ kbs.length ? '请至少选择一个知识库' : '你还没有可检索的知识库' }}
      </span>
      <span v-else-if="isDomainReleaseScope" class="search-view__option-hint">
        检索该域当前生效发布的语料
      </span>
      <span v-else class="search-view__option-hint">
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
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { Search } from '@element-plus/icons-vue'
import { useDomainStore } from '@/stores/domain'
import { useServingApi } from '@/api/serving'
import { useKbApi } from '@/api/kb'
import { apiErrorDetail } from '@/api/proxyClient'
import {
  DOMAIN_RELEASE_SCOPE,
  canSearchWithScope,
  defaultScopeSelection,
  reconcileScopeSelection,
  resolveRequestKbIds,
  scopeFromQuery,
} from '@/utils/searchScope'
import type { FullTextResult, SearchContextItem, SearchResult } from '@/types'
import type { KbOverviewItem } from '@/types/kb'
import EvidenceCard from '@/components/search/EvidenceCard.vue'
import FullTextDrawer from '@/components/search/FullTextDrawer.vue'
import PipelineTrace from '@/components/search/PipelineTrace.vue'
import EmptyState from '@/components/common/EmptyState.vue'

const domainStore = useDomainStore()
const servingApi = useServingApi()
const kbApi = useKbApi()

const route = useRoute()
const query = ref('')
const searching = ref(false)
const searchedOnce = ref(false)
const searchError = ref('')
const kbs = ref<KbOverviewItem[]>([])
const scopeLoading = ref(false)
const scopeError = ref('')
const hasActiveRelease = ref(false)
const selectedKbIds = ref<string[]>([])
/** reconcileScopeSelection 需要"改动前"的选择才能判断哪边该让位。 */
let previousScope: string[] = []
const result = ref<SearchResult | null>(null)
const activeTab = ref('evidence')
const debugMode = ref(true)

/** 产生当前结果的那次检索所用的范围，见 handleSearch。 */
const resultScope = ref<{ domain: string; kbIds: string[] }>({ domain: '', kbIds: [] })
const fullTextOpen = ref(false)
const fullTextLoading = ref(false)
const fullTextError = ref('')
const fullTextResult = ref<FullTextResult | null>(null)

const isDomainReleaseScope = computed(() =>
  selectedKbIds.value.includes(DOMAIN_RELEASE_SCOPE))
const canSearch = computed(() => canSearchWithScope(selectedKbIds.value))

/**
 * 取检索范围。走 overview 而不是 listKbs：还需要 has_active_release 才能决定
 * 要不要给出「域级发布」这个选项（列表本身已按身份过滤，只会出现可检索的库）。
 */
async function loadScope({ fromQuery = false } = {}) {
  const domain = domainStore.currentDomain
  if (!domain) return
  scopeLoading.value = true
  scopeError.value = ''
  try {
    const overview = await kbApi.getOverview(domain)
    kbs.value = overview.kbs
    hasActiveRelease.value = overview.has_active_release
    // 默认全选 —— 留空会被 serving.ts 省掉 kbIds 键，
    // 后端转而去找域级 active release，而 KB 挖掘永不产生 release。
    // URL 上的 kbIds 只在首次进入时作数：它描述的是那一次跳转的范围，切域后
    // 那些 id 属于旧域、已经没有意义（照抄过去也会被过滤掉，但别依赖那个副作用）。
    setScope(fromQuery
      ? scopeFromQuery(route.query.kbIds as string | string[] | undefined, overview.kbs)
      : defaultScopeSelection(overview.kbs))
  } catch (e) {
    // 取不到范围就没法检索了。以前这里静默退化成"全域检索"，而那条路径在纯 KB
    // 部署下必然报 no_active_release——把配置/网络问题伪装成"没有数据"。
    console.error('Failed to load search scope:', e)
    kbs.value = []
    hasActiveRelease.value = false
    setScope([])
    scopeError.value = '检索范围加载失败'
  } finally {
    scopeLoading.value = false
  }
}

function setScope(next: string[]) {
  selectedKbIds.value = next
  previousScope = [...next]
}

/** 强制「域级发布」与具体知识库互斥（规则与理由见 utils/searchScope.ts）。 */
function onScopeChange(next: string[]) {
  setScope(reconcileScopeSelection(previousScope, next))
}

onMounted(async () => {
  const q = route.query.q
  if (typeof q === 'string' && q.trim()) query.value = q
  await loadScope({ fromQuery: true })
  // 带 ?q= 进来（首页搜索框跳转）时直接出结果，不用再点一次
  if (query.value.trim() && canSearch.value) await handleSearch()
})

// 切域后旧域的 kb_id 一律失效（后端按 domain 过滤，留着必然 404 kb_not_found），
// 重取范围并重新全选。
watch(() => domainStore.currentDomain, () => {
  setScope([])
  loadScope()
})

async function handleSearch() {
  if (!query.value.trim()) return
  // 范围为空时不发请求：空 kbIds 会被 serving.ts 省掉键 → 域级 release 分支，
  // 那正是 D8。按钮已 disabled，这里再挡一道（回车也走这个函数）。
  if (!canSearch.value) return
  searching.value = true
  searchedOnce.value = true
  searchError.value = ''
  try {
    const domain = domainStore.currentDomain
    const kbIds = resolveRequestKbIds(selectedKbIds.value)
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

/* 范围为空 / 加载失败：检索按钮已 disabled，这里说清为什么 */
.search-view__option-warn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: var(--kb-warning);
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
