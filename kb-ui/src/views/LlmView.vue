<template>
  <div class="llm-view">
    <!-- Header -->
    <div class="llm-view__header">
      <div class="llm-view__title-wrap">
        <span class="llm-view__scope">当前知识域：{{ domainStore.currentDomain }}</span>
      </div>
      <el-button @click="loadAll" :loading="loading">
        <el-icon><Refresh /></el-icon>
      </el-button>
    </div>

    <!-- Tabs -->
    <el-tabs v-model="activeTab" class="llm-view__tabs">
      <!-- Overview Tab -->
      <el-tab-pane name="overview">
        <template #label>概览</template>

        <!-- Metric Cards -->
        <div class="llm-view__metrics">
          <div class="metric-card">
            <div class="metric-card__icon">📋</div>
            <div class="metric-card__body">
              <span class="metric-card__label">总任务</span>
              <span class="metric-card__value">{{ totalTasks }}</span>
            </div>
          </div>
          <div class="metric-card">
            <div class="metric-card__icon">✅</div>
            <div class="metric-card__body">
              <span class="metric-card__label">成功率</span>
              <span class="metric-card__value" :class="successClass">{{ successRate }}%</span>
            </div>
          </div>
          <div class="metric-card">
            <div class="metric-card__icon">⏳</div>
            <div class="metric-card__body">
              <span class="metric-card__label">运行中</span>
              <span class="metric-card__value">{{ stats?.tasks_by_status?.running ?? 0 }}</span>
            </div>
            <span class="metric-card__sub">队列 {{ stats?.tasks_by_status?.queued ?? 0 }}</span>
          </div>
          <div class="metric-card">
            <div class="metric-card__icon">🔢</div>
            <div class="metric-card__body">
              <span class="metric-card__label">总 Tokens</span>
              <span class="metric-card__value">{{ formatTokens(stats?.total_tokens) }}</span>
            </div>
          </div>
          <div class="metric-card">
            <div class="metric-card__icon">⚡</div>
            <div class="metric-card__body">
              <span class="metric-card__label">平均延迟</span>
              <span class="metric-card__value">{{ formatMs(stats?.avg_latency_ms) }}</span>
            </div>
          </div>
          <div class="metric-card">
            <div class="metric-card__icon">💀</div>
            <div class="metric-card__body">
              <span class="metric-card__label">死信</span>
              <span class="metric-card__value metric-card__value--bad">{{ stats?.tasks_by_status?.dead_letter ?? 0 }}</span>
            </div>
          </div>
        </div>

        <!-- Charts -->
        <div class="llm-view__charts">
          <div class="llm-view__chart-card">
            <h4 class="card-heading">任务状态分布</h4>
            <PieChart v-if="statusDistribution.length" :data="statusDistribution" height="240px" />
            <EmptyState v-else text="暂无数据" />
          </div>
          <div class="llm-view__chart-card">
            <h4 class="card-heading">任务类型分布</h4>
            <PieChart v-if="typeDistribution.length" :data="typeDistribution" height="240px" />
            <EmptyState v-else text="暂无数据" />
          </div>
        </div>
      </el-tab-pane>

      <!-- Tasks Tab -->
      <el-tab-pane name="tasks">
        <template #label>
          任务 <span class="tab-count">{{ taskTotal }}</span>
        </template>

        <!-- Filters -->
        <div class="llm-view__filters">
          <el-select v-model="filterStatus" placeholder="状态" size="default" clearable style="width: 130px">
            <el-option v-for="s in allStatuses" :key="s.value" :label="s.label" :value="s.value" />
          </el-select>
          <el-select v-model="filterType" placeholder="类型" size="default" clearable style="width: 120px">
            <el-option label="Chat" value="chat" />
            <el-option label="Embedding" value="embedding" />
            <el-option label="Rerank" value="rerank" />
          </el-select>
          <el-select v-model="filterStage" placeholder="阶段" size="default" clearable style="width: 140px">
            <el-option v-for="s in filterStages" :key="s" :label="s" :value="s" />
          </el-select>
        </div>

        <!-- Table -->
        <div class="llm-view__table-wrap">
          <el-table
            :data="tasks"
            v-loading="loadingTasks"
            class="kb-table"
            :header-cell-style="{ background: 'transparent' }"
            @row-click="goToTask"
          >
            <el-table-column label="ID" width="110">
              <template #default="{ row }">
                <span class="text-id">{{ row.id.slice(0, 8) }}</span>
              </template>
            </el-table-column>
            <el-table-column label="类型" width="105">
              <template #default="{ row }">
                <span class="type-badge" :class="`type-badge--${row.task_type}`">{{ row.task_type }}</span>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="105">
              <template #default="{ row }">
                <StatusBadge :status="row.status" />
              </template>
            </el-table-column>
            <el-table-column label="知识域" width="150">
              <template #default="{ row }">
                <span class="domain-label">{{ row.knowledge_domain || domainStore.currentDomain }}</span>
              </template>
            </el-table-column>
            <el-table-column label="调用方" width="110">
              <template #default="{ row }">
                <span class="service-label">{{ row.caller_service || '-' }}</span>
              </template>
            </el-table-column>
            <el-table-column label="阶段" min-width="130">
              <template #default="{ row }">
                {{ row.pipeline_stage || '-' }}
              </template>
            </el-table-column>
            <el-table-column label="Tokens" width="100" align="right">
              <template #default="{ row }">
                <span class="num-mono">{{ row.total_tokens != null ? row.total_tokens.toLocaleString() : '-' }}</span>
              </template>
            </el-table-column>
            <el-table-column label="延迟" width="90" align="right">
              <template #default="{ row }">
                <span class="num-mono">{{ formatMs(row.latency_ms) }}</span>
              </template>
            </el-table-column>
            <el-table-column label="重试" width="70" align="center">
              <template #default="{ row }">
                {{ row.attempt_count }}/{{ row.max_attempts }}
              </template>
            </el-table-column>
            <el-table-column label="创建时间" width="170">
              <template #default="{ row }">
                {{ formatTime(row.created_at) }}
              </template>
            </el-table-column>
          </el-table>
        </div>

        <div class="llm-view__pagination" v-if="taskTotal > pageSize">
          <el-pagination
            v-model:current-page="currentPage"
            :page-size="pageSize"
            :total="taskTotal"
            layout="prev, pager, next"
            size="small"
          />
        </div>
      </el-tab-pane>

      <!-- Templates Tab -->
      <el-tab-pane name="templates">
        <template #label>
          模板 <span class="tab-count">{{ templates.length }}</span>
        </template>
        <div class="llm-view__table-wrap">
          <el-table
            :data="templates"
            v-loading="loadingTemplates"
            class="kb-table"
            :header-cell-style="{ background: 'transparent' }"
            @row-click="showTemplateDetail"
          >
            <el-table-column prop="template_key" label="Key" min-width="200">
              <template #default="{ row }">
                <span class="text-id">{{ row.template_key }}</span>
              </template>
            </el-table-column>
            <el-table-column label="知识域" width="150">
              <template #default="{ row }">
                <span class="domain-label">{{ row.knowledge_domain || 'global' }}</span>
              </template>
            </el-table-column>
            <el-table-column prop="output_type" label="输出类型" width="120">
              <template #default="{ row }">
                {{ row.expected_output_type || row.output_type || '-' }}
              </template>
            </el-table-column>
            <el-table-column label="系统提示" min-width="250">
              <template #default="{ row }">
                <span class="text-truncate">{{ row.system_prompt || row.system_template || '-' }}</span>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="90">
              <template #default="{ row }">
                <span class="type-badge">{{ row.status || 'active' }}</span>
              </template>
            </el-table-column>
            <el-table-column label="创建时间" width="170">
              <template #default="{ row }">
                {{ formatTime(row.created_at) }}
              </template>
            </el-table-column>
          </el-table>

          <!-- Template Detail Drawer -->
          <el-drawer
            v-model="templateDrawerVisible"
            :title="selectedTemplate?.template_key ?? '模板详情'"
            direction="rtl"
            size="560px"
            :append-to-body="true"
          >
            <template v-if="selectedTemplate">
              <div class="tpl-drawer__body">
                <div class="tpl-drawer__meta">
                  <table class="kv-table">
                    <tbody>
                      <tr><td>Key</td><td class="text-id">{{ selectedTemplate.template_key }}</td></tr>
                      <tr><td>知识域</td><td>{{ selectedTemplate.knowledge_domain || 'global' }}</td></tr>
                      <tr><td>输出类型</td><td>{{ selectedTemplate.expected_output_type || selectedTemplate.output_type || '-' }}</td></tr>
                      <tr><td>状态</td><td><span class="type-badge">{{ selectedTemplate.status || 'active' }}</span></td></tr>
                      <tr v-if="selectedTemplate.purpose"><td>用途</td><td>{{ selectedTemplate.purpose }}</td></tr>
                      <tr><td>创建时间</td><td>{{ formatTime(selectedTemplate.created_at) }}</td></tr>
                    </tbody>
                  </table>
                </div>

                <div v-if="selectedTemplate.system_prompt || selectedTemplate.system_template" class="tpl-section">
                  <div class="tpl-section__label">系统提示词</div>
                  <pre class="tpl-code">{{ selectedTemplate.system_prompt || selectedTemplate.system_template }}</pre>
                </div>

                <div v-if="selectedTemplate.user_prompt_template" class="tpl-section">
                  <div class="tpl-section__label">用户提示词模板</div>
                  <pre class="tpl-code">{{ selectedTemplate.user_prompt_template }}</pre>
                </div>

                <div v-if="selectedTemplate.output_schema_json" class="tpl-section">
                  <div class="tpl-section__label">输出 Schema</div>
                  <pre class="tpl-code">{{ formatTplJson(selectedTemplate.output_schema_json) }}</pre>
                </div>

                <div v-if="selectedTemplate.default_params_json" class="tpl-section">
                  <div class="tpl-section__label">默认参数</div>
                  <pre class="tpl-code">{{ formatTplJson(selectedTemplate.default_params_json) }}</pre>
                </div>

                <div class="tpl-section tpl-section--collapsible">
                  <el-collapse>
                    <el-collapse-item title="原始数据">
                      <pre class="tpl-code">{{ formatTplJson(selectedTemplate) }}</pre>
                    </el-collapse-item>
                  </el-collapse>
                </div>
              </div>
            </template>
          </el-drawer>
        </div>
        <EmptyState v-if="!loadingTemplates && !templates.length" text="暂无模板" />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { Refresh } from '@element-plus/icons-vue'
import { useDomainStore } from '@/stores/domain'
import { useLlmApi } from '@/api/llm'
import type { LlmTaskStats, LlmTask } from '@/types'
import { usePolling } from '@/composables/usePolling'
import StatusBadge from '@/components/common/StatusBadge.vue'
import PieChart from '@/components/charts/PieChart.vue'
import EmptyState from '@/components/common/EmptyState.vue'

const router = useRouter()
const route = useRoute()
const domainStore = useDomainStore()
const llmApi = useLlmApi()

const loading = ref(false)
const loadingTasks = ref(false)
const loadingTemplates = ref(false)
const activeTab = ref('overview')
const stats = ref<LlmTaskStats | null>(null)
const tasks = ref<LlmTask[]>([])
const taskTotal = ref(0)
const currentPage = ref(1)
const pageSize = 30
const templates = ref<Record<string, unknown>[]>([])
const templateDrawerVisible = ref(false)
const selectedTemplate = ref<Record<string, any> | null>(null)

function showTemplateDetail(row: Record<string, any>) {
  selectedTemplate.value = row
  templateDrawerVisible.value = true
}

function formatTplJson(obj: unknown): string {
  if (!obj) return ''
  if (typeof obj === 'string') {
    try { return JSON.stringify(JSON.parse(obj), null, 2) } catch { return obj as string }
  }
  return JSON.stringify(obj, null, 2)
}

// Filters
const filterStatus = ref('')
const filterType = ref('')
const filterStage = ref('')
const filterStages = computed(() => stats.value?.stages ?? [])

const allStatuses = [
  { value: 'queued', label: '排队中' },
  { value: 'running', label: '运行中' },
  { value: 'succeeded', label: '成功' },
  { value: 'failed', label: '失败' },
  { value: 'dead_letter', label: '死信' },
  { value: 'cancelled', label: '已取消' },
]

// Derived stats
const totalTasks = computed(() => {
  const s = stats.value?.tasks_by_status
  if (!s) return 0
  return Object.values(s).reduce((a, b) => a + b, 0)
})

const successRate = computed(() => {
  const s = stats.value?.tasks_by_status
  if (!s) return '-'
  const total = Object.values(s).reduce((a, b) => a + b, 0)
  if (total === 0) return '-'
  return ((s.succeeded / total) * 100).toFixed(1)
})

const successClass = computed(() => {
  const s = stats.value?.tasks_by_status
  if (!s) return ''
  const total = Object.values(s).reduce((a, b) => a + b, 0)
  if (total === 0) return ''
  const r = s.succeeded / total
  if (r >= 0.95) return 'metric-card__value--good'
  if (r >= 0.8) return 'metric-card__value--warn'
  return 'metric-card__value--bad'
})

const statusDistribution = computed(() => {
  const s = stats.value?.tasks_by_status
  if (!s) return []
  const colorMap: Record<string, string> = {
    succeeded: '#10b981', running: '#0891b2', queued: '#94a3b8',
    failed: '#ef4444', dead_letter: '#8b5cf6', cancelled: '#64748b',
  }
  const labelMap: Record<string, string> = {
    succeeded: '成功', running: '运行中', queued: '排队中',
    failed: '失败', dead_letter: '死信', cancelled: '已取消',
  }
  return Object.entries(s).filter(([, v]) => v > 0).map(([k, v]) => ({ name: labelMap[k] || k, value: v, color: colorMap[k] }))
})

const typeDistribution = computed(() => {
  const t = stats.value?.tasks_by_type
  if (!t) return []
  const colorMap: Record<string, string> = { chat: '#0891b2', embedding: '#10b981', rerank: '#f59e0b' }
  return Object.entries(t).filter(([, v]) => v > 0).map(([k, v]) => ({ name: k, value: v, color: colorMap[k] }))
})

function formatTokens(n?: number): string {
  if (n == null) return '-'
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`
  return String(n)
}

function formatMs(ms?: number | null): string {
  if (ms == null) return '-'
  if (ms < 1) return `${(ms * 1000).toFixed(0)}μs`
  if (ms < 1000) return `${ms.toFixed(0)}ms`
  return `${(ms / 1000).toFixed(2)}s`
}

function formatTime(t?: string | null) {
  if (!t) return '-'
  return new Date(t).toLocaleString('zh-CN')
}

async function loadStats(silent = false) {
  if (!silent) loading.value = true
  try { stats.value = await llmApi.getStats({ domain: domainStore.currentDomain }) } catch { stats.value = null }
  finally { if (!silent) loading.value = false }
}

async function loadTasks(silent = false) {
  if (!silent) loadingTasks.value = true
  try {
    const params: Record<string, unknown> = {
      domain: domainStore.currentDomain,
      page: currentPage.value,
      page_size: pageSize,
    }
    if (filterStatus.value) params.status = filterStatus.value
    if (filterType.value) params.task_type = filterType.value
    if (filterStage.value) params.stage = filterStage.value
    const res = await llmApi.getTasks(params)
    tasks.value = res.items
    taskTotal.value = res.total
  } catch { tasks.value = []; taskTotal.value = 0 }
  finally { if (!silent) loadingTasks.value = false }
}

async function loadTemplates() {
  loadingTemplates.value = true
  try {
    const list = await llmApi.getTemplates({ domain: domainStore.currentDomain })
    templates.value = Array.isArray(list) ? list : []
  } catch { templates.value = [] }
  finally { loadingTemplates.value = false }
}

async function refreshLiveData() {
  if (document.visibilityState !== 'visible') return
  await Promise.all([loadStats(true), loadTasks(true)])
}

async function loadAll() {
  await Promise.all([loadStats(), loadTasks(), loadTemplates()])
}

function goToTask(row: LlmTask) {
  router.push(`/llm/${row.id}`)
}

const { start: startPolling } = usePolling(refreshLiveData, 5000, { immediate: false })

watch([filterStatus, filterType, filterStage], () => { currentPage.value = 1; loadTasks() })
watch(currentPage, () => loadTasks())
watch(activeTab, (tab) => {
  // Sync tab to URL query param
  if (route.query.tab !== tab) {
    router.replace({ query: { ...route.query, tab } })
  }
})
onMounted(async () => {
  await loadAll()
  startPolling()
  // Read tab from query param
  const q = route.query
  if (q.tab && typeof q.tab === 'string') {
    activeTab.value = q.tab
  }
  // If ?tpl=KEY, open template drawer
  if (q.tpl && typeof q.tpl === 'string') {
    const tpl = templates.value.find((t: any) => t.template_key === q.tpl)
    if (tpl) {
      selectedTemplate.value = tpl as Record<string, any>
      templateDrawerVisible.value = true
    }
  }
})
watch(() => domainStore.currentDomain, () => {
  currentPage.value = 1
  loadAll()
})
</script>

<style scoped>
.llm-view { display: flex; flex-direction: column; gap: 16px; }
.llm-view__header { display: flex; align-items: center; justify-content: space-between; }
.llm-view__title-wrap { display: flex; flex-direction: column; gap: 4px; }
.llm-view__title { font-size: 16px; font-weight: 650; color: var(--kb-text-primary); margin: 0; letter-spacing: -0.2px; }
.llm-view__scope { font-size: 12px; color: var(--kb-text-tertiary); }

.llm-view__tabs :deep(.el-tabs__header) { margin-bottom: 14px; }
.tab-count { font-size: 11px; color: var(--kb-text-tertiary); margin-left: 4px; }

/* Metrics */
.llm-view__metrics { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-bottom: 4px; }
.metric-card { background: var(--kb-bg-card); border: 1px solid var(--kb-border-light); border-radius: var(--kb-radius); padding: 16px 18px; display: flex; align-items: center; gap: 14px; position: relative; }
.metric-card__icon { font-size: 24px; line-height: 1; }
.metric-card__body { display: flex; flex-direction: column; gap: 2px; }
.metric-card__label { font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; color: var(--kb-text-tertiary); font-weight: 600; }
.metric-card__value { font-size: 22px; font-weight: 700; color: var(--kb-text-primary); font-variant-numeric: tabular-nums; }
.metric-card__value--good { color: var(--kb-success); }
.metric-card__value--warn { color: var(--kb-warning); }
.metric-card__value--bad { color: var(--kb-danger); }
.metric-card__sub { position: absolute; bottom: 8px; right: 14px; font-size: 11px; color: var(--kb-text-tertiary); }

/* Charts */
.llm-view__charts { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.llm-view__chart-card { background: var(--kb-bg-card); border: 1px solid var(--kb-border-light); border-radius: var(--kb-radius); padding: 18px 20px; }
.card-heading { font-size: 13px; font-weight: 600; color: var(--kb-text-secondary); text-transform: uppercase; letter-spacing: 0.5px; margin: 0 0 14px; }

/* Filters */
.llm-view__filters { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 12px; }

/* Table */
.llm-view__table-wrap { overflow: hidden; border-radius: var(--kb-radius-sm); }
.llm-view__table-wrap :deep(.el-table__row) { cursor: pointer; }
.llm-view__table-wrap :deep(.el-table__row:hover) { background: var(--kb-bg-card-hover); }
.llm-view__pagination { display: flex; justify-content: center; margin-top: 14px; }

.text-id { font-family: 'SF Mono', 'Cascadia Code', monospace; font-size: 12px; color: var(--kb-accent); font-weight: 500; }
.type-badge { font-size: 11px; padding: 2px 8px; border-radius: 4px; font-weight: 600; background: var(--kb-accent-soft); color: var(--kb-accent); }
.type-badge--chat { background: rgba(8, 145, 178, 0.08); color: #0891b2; }
.type-badge--embedding { background: rgba(16, 185, 129, 0.1); color: #10b981; }
.type-badge--rerank { background: rgba(245, 158, 11, 0.1); color: #f59e0b; }
.domain-label { font-size: 12px; color: var(--kb-text-secondary); }
.service-label { font-size: 12px; color: var(--kb-text-secondary); font-weight: 600; }
.num-mono { font-size: 12px; font-variant-numeric: tabular-nums; color: var(--kb-text-secondary); }
.text-truncate { font-size: 12px; color: var(--kb-text-secondary); display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }

/* Template drawer */
.tpl-drawer__body { display: flex; flex-direction: column; gap: 18px; }
.tpl-drawer__meta { background: var(--kb-bg-card); border: 1px solid var(--kb-border-light); border-radius: var(--kb-radius); padding: 14px 16px; }
.tpl-drawer__meta .kv-table { width: 100%; border-collapse: collapse; }
.tpl-drawer__meta .kv-table td { padding: 4px 0; font-size: 12px; border: none; vertical-align: top; }
.tpl-drawer__meta .kv-table td:first-child { color: var(--kb-text-tertiary); width: 80px; white-space: nowrap; }
.tpl-drawer__meta .kv-table td:last-child { color: var(--kb-text-primary); word-break: break-all; }
.tpl-section {}
.tpl-section__label { font-size: 11px; font-weight: 600; color: var(--kb-text-tertiary); text-transform: uppercase; letter-spacing: 0.3px; margin-bottom: 6px; }
.tpl-code {
  background: var(--kb-bg-page); border: 1px solid var(--kb-border-light);
  border-radius: var(--kb-radius-sm); padding: 12px;
  font-family: 'SF Mono', 'Cascadia Code', monospace; font-size: 12px;
  line-height: 1.6; color: var(--kb-text-secondary);
  overflow-x: auto; max-height: 300px; overflow-y: auto; white-space: pre-wrap;
}
.tpl-section--collapsible :deep(.el-collapse) { border: none; }
.tpl-section--collapsible :deep(.el-collapse-item__header) { font-size: 11px; color: var(--kb-text-tertiary); background: transparent; border: none; height: 32px; line-height: 32px; }
.tpl-section--collapsible :deep(.el-collapse-item__wrap) { border: none; }
.tpl-section--collapsible :deep(.el-collapse-item__content) { padding-bottom: 8px; }
</style>
