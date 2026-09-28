<template>
  <div class="task-detail" v-loading="initialLoading">
    <!-- Header -->
    <div class="task-detail__header">
      <div class="task-detail__header-left">
        <el-button text @click="goBack" class="back-btn">
          <el-icon><ArrowLeft /></el-icon>
          <span>LLM 服务</span>
        </el-button>
        <span class="task-detail__id">{{ task?.id?.slice(0, 8) ?? '...' }}</span>
        <span class="type-badge" :class="`type-badge--${task?.task_type}`">{{ task?.task_type }}</span>
        <StatusBadge v-if="task" :status="task.status" size="small" />
      </div>
      <div class="task-detail__header-right">
        <el-button
          v-if="task && (task.status === 'queued' || task.status === 'running')"
          size="small"
          type="danger"
          plain
          @click="handleCancel"
          :loading="cancelling"
        >取消任务</el-button>
      </div>
    </div>

    <template v-if="task">
      <!-- Info Grid -->
      <div class="task-detail__grid">
        <div class="info-card">
          <h4 class="info-card__title">任务信息</h4>
          <table class="kv-table">
            <tbody>
              <tr><td>知识域</td><td>{{ task.knowledge_domain || '-' }}</td></tr>
              <tr><td>调用方</td><td>{{ task.caller_service || '-' }}</td></tr>
              <tr><td>阶段</td><td>{{ task.pipeline_stage || '-' }}</td></tr>
              <tr><td>优先级</td><td>{{ task.priority }}</td></tr>
              <tr><td>重试</td><td>{{ task.attempt_count }} / {{ task.max_attempts }}</td></tr>
              <tr><td>幂等键</td><td><span class="text-mono">{{ task.idempotency_key || '-' }}</span></td></tr>
            </tbody>
          </table>
        </div>
        <div class="info-card">
          <h4 class="info-card__title">时间</h4>
          <table class="kv-table">
            <tbody>
              <tr><td>创建</td><td>{{ formatTime(task.created_at) }}</td></tr>
              <tr><td>开始</td><td>{{ formatTime(task.started_at) }}</td></tr>
              <tr><td>完成</td><td>{{ formatTime(task.finished_at) }}</td></tr>
              <tr><td>耗时</td><td>{{ duration }}</td></tr>
            </tbody>
          </table>
        </div>
        <div class="info-card">
          <h4 class="info-card__title">模型配置</h4>
          <table class="kv-table">
            <tbody>
              <tr><td>Provider</td><td>{{ requestData?.provider || '-' }}</td></tr>
              <tr><td>Model</td><td>{{ requestData?.model || '-' }}</td></tr>
              <tr><td>输出类型</td><td>{{ requestData?.expected_output_type || '-' }}</td></tr>
            </tbody>
          </table>
        </div>
        <div class="info-card">
          <h4 class="info-card__title">Token 用量</h4>
          <table class="kv-table">
            <tbody>
              <tr>
                <td>总 Tokens</td>
                <td><span class="num-highlight">{{ totalTokens }}</span></td>
              </tr>
              <tr><td>延迟</td><td>{{ lastAttemptLatency }}</td></tr>
              <tr><td>尝试次数</td><td>{{ attempts.length }}</td></tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- ═══════════════════════════════════════════════════ -->
      <!-- Chat specific: Template + Prompt + Messages         -->
      <!-- ═══════════════════════════════════════════════════ -->
      <template v-if="task.task_type === 'chat'">
        <!-- Template Info (only when template was used) -->
        <div v-if="templateKey" class="section-card">
          <h4 class="section-card__title">
            使用模板
            <router-link :to="templateLink" class="template-link">{{ templateKey }}</router-link>
          </h4>
        </div>

        <!-- Messages / Prompts (always show for chat tasks) -->
        <div v-if="systemPrompt || userPromptTemplate" class="section-card">
          <h4 class="section-card__title">提示词</h4>
          <div v-if="systemPrompt" class="prompt-preview">
            <div class="prompt-preview__label">系统提示词</div>
            <pre class="code-block code-block--compact">{{ systemPrompt }}</pre>
          </div>
          <div v-if="userPromptTemplate" class="prompt-preview">
            <div class="prompt-preview__label">用户提示词</div>
            <pre class="code-block code-block--compact">{{ userPromptTemplate }}</pre>
          </div>
        </div>

        <!-- Full Messages (collapsible) -->
        <div v-if="messages.length" class="section-card section-card--collapsible">
          <el-collapse>
            <el-collapse-item title="完整消息列表">
              <el-table :data="messages" size="small" class="kb-table" :header-cell-style="{ background: 'transparent' }">
                <el-table-column label="Role" width="100">
                  <template #default="{ row }">
                    <span class="text-mono">{{ row.role }}</span>
                  </template>
                </el-table-column>
                <el-table-column label="Content">
                  <template #default="{ row }">
                    <span class="cell-value">{{ row.content }}</span>
                  </template>
                </el-table-column>
              </el-table>
            </el-collapse-item>
          </el-collapse>
        </div>

        <!-- Input Data -->
        <div v-if="inputData && hasKeys(inputData)" class="section-card">
          <h4 class="section-card__title">模板输入变量</h4>
          <el-table :data="inputDataEntries" size="small" class="kb-table" :header-cell-style="{ background: 'transparent' }">
            <el-table-column prop="key" label="变量名" width="200">
              <template #default="{ row }">
                <span class="text-mono">{{ row.key }}</span>
              </template>
            </el-table-column>
            <el-table-column prop="value" label="值">
              <template #default="{ row }">
                <span class="cell-value">{{ typeof row.value === 'object' ? JSON.stringify(row.value, null, 2) : row.value }}</span>
              </template>
            </el-table-column>
          </el-table>
        </div>

        <!-- Output Schema -->
        <div v-if="outputSchema && hasKeys(outputSchema)" class="section-card">
          <h4 class="section-card__title">输出 Schema</h4>
          <pre class="code-block">{{ formatJson(outputSchema) }}</pre>
        </div>
      </template>

      <!-- ═══════════════════════════════════════════════════ -->
      <!-- Embedding specific: Input Texts table + Result      -->
      <!-- ═══════════════════════════════════════════════════ -->
      <template v-if="task.task_type === 'embedding'">
        <!-- Input Texts table -->
        <div v-if="embedTexts.length" class="section-card">
          <h4 class="section-card__title">输入文本 ({{ embedTexts.length }} 条)</h4>
          <el-table :data="embedTableRows" size="small" class="kb-table" :header-cell-style="{ background: 'transparent' }" max-height="400">
            <el-table-column type="index" label="#" width="50" />
            <el-table-column label="文本内容">
              <template #default="{ row }">
                <ExpandableText :text="row.text" />
              </template>
            </el-table-column>
            <el-table-column label="字符数" width="90" align="right">
              <template #default="{ row }">
                <span class="num-mono">{{ row.length }}</span>
              </template>
            </el-table-column>
          </el-table>
        </div>

        <!-- Embedding Config -->
        <div v-if="embedConfig && hasKeys(embedConfig)" class="section-card">
          <h4 class="section-card__title">Embedding 参数</h4>
          <el-table :data="embedConfigEntries" size="small" class="kb-table" :header-cell-style="{ background: 'transparent' }">
            <el-table-column prop="key" label="参数" width="180">
              <template #default="{ row }">
                <span class="text-mono">{{ row.key }}</span>
              </template>
            </el-table-column>
            <el-table-column prop="value" label="值">
              <template #default="{ row }">
                <span>{{ row.value ?? '-' }}</span>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </template>

      <!-- ═══════════════════════════════════════════════════ -->
      <!-- Rerank specific: Query + Input Docs + Results       -->
      <!-- ═══════════════════════════════════════════════════ -->
      <template v-if="task.task_type === 'rerank'">
        <!-- Query -->
        <div v-if="rerankQuery" class="section-card">
          <h4 class="section-card__title">查询文本</h4>
          <div class="rerank-query">{{ rerankQuery }}</div>
        </div>

        <!-- Input Documents table -->
        <div v-if="rerankDocuments.length" class="section-card">
          <h4 class="section-card__title">输入文档 ({{ rerankDocuments.length }} 条)</h4>
          <el-table :data="rerankDocRows" size="small" class="kb-table" :header-cell-style="{ background: 'transparent' }" max-height="400">
            <el-table-column type="index" label="#" width="50" />
            <el-table-column label="文档内容">
              <template #default="{ row }">
                <ExpandableText :text="row.doc" />
              </template>
            </el-table-column>
            <el-table-column label="字符数" width="90" align="right">
              <template #default="{ row }">
                <span class="num-mono">{{ row.length }}</span>
              </template>
            </el-table-column>
          </el-table>
        </div>

        <!-- Rerank Config -->
        <div v-if="rerankConfig && hasKeys(rerankConfig)" class="section-card">
          <h4 class="section-card__title">Rerank 参数</h4>
          <el-table :data="rerankConfigEntries" size="small" class="kb-table" :header-cell-style="{ background: 'transparent' }">
            <el-table-column prop="key" label="参数" width="180">
              <template #default="{ row }">
                <span class="text-mono">{{ row.key }}</span>
              </template>
            </el-table-column>
            <el-table-column prop="value" label="值">
              <template #default="{ row }">
                <span>{{ row.value ?? '-' }}</span>
              </template>
            </el-table-column>
          </el-table>
        </div>

        <!-- Reranked Results table -->
        <div v-if="rerankResults.length" class="section-card">
          <h4 class="section-card__title">重排结果 ({{ rerankResults.length }} 条)</h4>
          <el-table :data="rerankResultRows" size="small" class="kb-table" :header-cell-style="{ background: 'transparent' }" default-sort="{ prop: 'score', order: 'descending' }">
            <el-table-column label="排名" width="60" align="center">
              <template #default="{ $index }">
                <span class="rank-num">{{ $index + 1 }}</span>
              </template>
            </el-table-column>
            <el-table-column label="原始序号" width="80" align="center">
              <template #default="{ row }">
                <span class="num-mono">#{{ row.index }}</span>
              </template>
            </el-table-column>
            <el-table-column label="相关性分数" width="140" sortable sort-by="score">
              <template #default="{ row }">
                <div class="score-cell">
                  <div class="score-bar">
                    <div class="score-bar__fill" :style="{ width: scoreWidth(row.score) }" />
                  </div>
                  <span class="score-value">{{ row.score.toFixed(4) }}</span>
                </div>
              </template>
            </el-table-column>
            <el-table-column label="文档内容">
              <template #default="{ row }">
                <ExpandableText :text="row.doc" />
              </template>
            </el-table-column>
          </el-table>
        </div>
      </template>

      <!-- ═══════════════════════════════════════════════════ -->
      <!-- Result: Parsed Output (skip for rerank, shown above)-->
      <!-- ═══════════════════════════════════════════════════ -->
      <div v-if="result && task.task_type !== 'rerank' && (parsedOutput || result.text_output)" class="section-card">
        <h4 class="section-card__title">
          执行结果
          <span v-if="result.parse_status" class="parse-status" :class="`parse-status--${result.parse_status}`">
            {{ result.parse_status }}
          </span>
        </h4>
        <!-- Embedding: collapse vectors by default -->
        <template v-if="task.task_type === 'embedding' && parsedOutput">
          <el-table :data="embeddingResultSummary" size="small" class="kb-table" :header-cell-style="{ background: 'transparent' }">
            <el-table-column type="index" label="#" width="50" />
            <el-table-column label="向量维度" width="120">
              <template #default="{ row }">
                <span class="num-mono">{{ row.dim }}D</span>
              </template>
            </el-table-column>
            <el-table-column label="向量预览">
              <template #default="{ row }">
                <span class="text-mono text-faint">{{ row.preview }}</span>
              </template>
            </el-table-column>
          </el-table>
          <div class="collapse-toggle">
            <el-collapse>
              <el-collapse-item title="展开原始 JSON (含完整向量)">
                <pre class="code-block">{{ formatJson(parsedOutput) }}</pre>
              </el-collapse-item>
            </el-collapse>
          </div>
        </template>
        <!-- Chat: show parsed output -->
        <template v-else>
          <pre v-if="parsedOutput" class="code-block">{{ formatJson(parsedOutput) }}</pre>
          <pre v-if="result.text_output && !parsedOutput" class="code-block">{{ result.text_output }}</pre>
        </template>
        <div v-if="result.parse_error" class="error-box">{{ result.parse_error }}</div>
        <div v-if="result.validation_errors && result.validation_errors.length" class="error-box">
          <strong>验证错误:</strong>
          <pre>{{ formatJson(result.validation_errors) }}</pre>
        </div>
      </div>

      <!-- Rerank: show parse errors only (results already shown in 重排结果 section) -->
      <div v-if="result && task.task_type === 'rerank' && (result.parse_error || (result.validation_errors && result.validation_errors.length))" class="section-card">
        <div v-if="result.parse_error" class="error-box">{{ result.parse_error }}</div>
        <div v-if="result.validation_errors && result.validation_errors.length" class="error-box">
          <strong>验证错误:</strong>
          <pre>{{ formatJson(result.validation_errors) }}</pre>
        </div>
      </div>

      <!-- Metadata -->
      <div v-if="taskMetadata && hasKeys(taskMetadata)" class="section-card section-card--collapsible">
        <el-collapse>
          <el-collapse-item title="元数据">
            <pre class="code-block code-block--compact">{{ formatJson(taskMetadata) }}</pre>
          </el-collapse-item>
        </el-collapse>
      </div>

      <!-- Attempts -->
      <div v-if="attempts.length" class="section-card">
        <h4 class="section-card__title">执行尝试 ({{ attempts.length }})</h4>
        <div class="attempt-list">
          <div v-for="a in attempts" :key="a.id" class="attempt-item">
            <div class="attempt-item__header">
              <span class="attempt-item__no">Attempt #{{ a.attempt_no }}</span>
              <StatusBadge :status="mapAttemptStatus(a.status)" size="small" />
            </div>
            <div class="attempt-item__stats">
              <span class="stat-chip">Tokens: <strong>{{ a.total_tokens ?? '-' }}</strong></span>
              <span class="stat-chip">Prompt: <strong>{{ a.prompt_tokens ?? '-' }}</strong></span>
              <span class="stat-chip">Completion: <strong>{{ a.completion_tokens ?? '-' }}</strong></span>
              <span class="stat-chip">延迟: <strong>{{ a.latency_ms != null ? `${a.latency_ms}ms` : '-' }}</strong></span>
            </div>
            <div v-if="a.error_message" class="error-box error-box--compact">
              <strong>{{ a.error_type || 'Error' }}:</strong> {{ a.error_message }}
            </div>
          </div>
        </div>
      </div>

      <!-- Event Timeline -->
      <div v-if="events.length" class="section-card">
        <h4 class="section-card__title">事件时间线 ({{ events.length }})</h4>
        <div class="timeline">
          <div v-for="e in events" :key="e.id" class="timeline-item">
            <div class="timeline-item__dot" :class="`timeline-item__dot--${e.event_type}`" />
            <div class="timeline-item__body">
              <span class="timeline-item__time">{{ formatTime(e.created_at) }}</span>
              <span class="timeline-item__event">{{ e.event_type }}</span>
              <span v-if="e.message" class="timeline-item__msg">{{ e.message }}</span>
            </div>
          </div>
        </div>
      </div>

      <!-- Raw JSON -->
      <div class="section-card section-card--collapsible">
        <el-collapse>
          <el-collapse-item title="原始数据">
            <pre class="code-block code-block--compact">{{ formatJson(task) }}</pre>
          </el-collapse-item>
        </el-collapse>
      </div>
    </template>

    <EmptyState v-if="!loading && !task" text="任务不存在" />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ArrowLeft } from '@element-plus/icons-vue'
import { useLlmApi } from '@/api/llm'
import { usePolling } from '@/composables/usePolling'
import { useDomainStore } from '@/stores/domain'
import StatusBadge from '@/components/common/StatusBadge.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import ExpandableText from './ExpandableText.vue'

const props = defineProps<{ taskId: string }>()
const router = useRouter()
const llmApi = useLlmApi()
const domainStore = useDomainStore()

const loading = ref(true)
const initialLoading = ref(true)
const cancelling = ref(false)
const task = ref<Record<string, any> | null>(null)
const requestData = ref<Record<string, any> | null>(null)
const result = ref<Record<string, any> | null>(null)
const attempts = ref<Record<string, any>[]>([])
const events = ref<Record<string, any>[]>([])

// ─── Derived: Chat ───
const templateKey = computed(() => requestData.value?.prompt_template_key || null)

const templateLink = computed(() => `/llm?tab=templates&tpl=${encodeURIComponent(templateKey.value ?? '')}`)

const systemPrompt = computed(() => {
  // System prompt comes from messages_json first element with role=system
  if (!messages.value.length) return null
  const sys = messages.value.find((m: any) => m.role === 'system')
  return sys?.content ?? null
})

const userPromptTemplate = computed(() => {
  // User template from messages_json first element with role=user
  if (!messages.value.length) return null
  const user = messages.value.find((m: any) => m.role === 'user')
  return user?.content ?? null
})

const messages = computed(() => {
  const m = requestData.value?.messages_json
  return Array.isArray(m) ? m : []
})

const inputData = computed(() => {
  const d = requestData.value?.input_json
  return d && typeof d === 'object' ? d : null
})

const inputDataEntries = computed(() => {
  if (!inputData.value) return []
  return Object.entries(inputData.value).map(([key, value]) => ({ key, value }))
})

const outputSchema = computed(() => {
  const s = requestData.value?.output_schema_json
  return s && typeof s === 'object' ? s : null
})

// ─── Derived: Embedding ───
const embedTexts = computed(() => {
  const texts = (inputData.value as any)?.texts
  return Array.isArray(texts) ? texts : []
})

const embedTableRows = computed(() =>
  embedTexts.value.map((text: string) => ({
    text,
    length: text.length,
  }))
)

const embedConfig = computed(() => {
  const d = inputData.value as any
  if (!d) return null
  // Extract config fields (exclude texts array)
  const { texts, ...rest } = d
  return rest && Object.keys(rest).length > 0 ? rest : null
})

const embedConfigEntries = computed(() => {
  if (!embedConfig.value) return []
  return Object.entries(embedConfig.value).map(([key, value]) => ({ key, value }))
})

// ─── Derived: Rerank ───
const rerankQuery = computed(() => (inputData.value as any)?.query ?? '')

const rerankDocuments = computed(() => {
  const docs = (inputData.value as any)?.documents
  return Array.isArray(docs) ? docs : []
})

const rerankDocRows = computed(() =>
  rerankDocuments.value.map((d: any) => ({
    doc: typeof d === 'string' ? d : JSON.stringify(d),
    length: typeof d === 'string' ? d.length : JSON.stringify(d).length,
  }))
)

const rerankConfig = computed(() => {
  const d = inputData.value as any
  if (!d) return null
  const { query, documents, ...rest } = d
  return rest && Object.keys(rest).length > 0 ? rest : null
})

const rerankConfigEntries = computed(() => {
  if (!rerankConfig.value) return []
  return Object.entries(rerankConfig.value).map(([key, value]) => ({ key, value }))
})

const rerankResults = computed(() => {
  const parsed = result.value?.parsed_output
  if (!parsed || typeof parsed !== 'object') return []
  const results = (parsed as any).results
  return Array.isArray(results) ? results : []
})

const rerankResultRows = computed(() =>
  rerankResults.value.map((r: any) => ({
    index: r.index ?? 0,
    score: typeof r.relevance_score === 'number' ? r.relevance_score : 0,
    doc: typeof r.document === 'string' ? r.document : JSON.stringify(r.document ?? ''),
  }))
)

// ─── Derived: Result ───
const parsedOutput = computed(() => {
  const p = result.value?.parsed_output
  return p && typeof p === 'object' && Object.keys(p).length > 0 ? p : null
})

// Embedding result summary (without full vectors)
const embeddingResultSummary = computed(() => {
  const p = parsedOutput.value
  if (!p) return []
  const data = (p as any).data
  if (!Array.isArray(data)) return []
  return data.map((item: any) => {
    const emb = Array.isArray(item?.embedding) ? item.embedding : []
    return {
      dim: emb.length,
      preview: emb.length > 0
        ? `[${emb.slice(0, 4).map((v: number) => v.toFixed(4)).join(', ')}, ... ] (共 ${emb.length} 维)`
        : '(无向量数据)',
    }
  })
})

const taskMetadata = computed(() => {
  const m = task.value?.metadata
  return m && typeof m === 'object' ? m : null
})

const totalTokens = computed(() => {
  const last = attempts.value[attempts.value.length - 1]
  return last?.total_tokens?.toLocaleString() ?? '-'
})

const lastAttemptLatency = computed(() => {
  const last = attempts.value[attempts.value.length - 1]
  if (!last?.latency_ms) return '-'
  const ms = last.latency_ms
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(2)}s`
})

const duration = computed(() => {
  if (!task.value?.started_at || !task.value?.finished_at) return '-'
  const start = new Date(task.value.started_at).getTime()
  const end = new Date(task.value.finished_at).getTime()
  const ms = end - start
  if (ms < 1000) return `${ms}ms`
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`
  return `${(ms / 60000).toFixed(1)}min`
})

function hasKeys(obj: unknown): boolean {
  return !!obj && typeof obj === 'object' && Object.keys(obj as object).length > 0
}

function formatTime(t?: string | null) {
  if (!t) return '-'
  return new Date(t).toLocaleString('zh-CN')
}

function formatJson(obj: unknown): string {
  if (!obj) return ''
  return JSON.stringify(obj, null, 2)
}

function scoreWidth(score: number): string {
  if (typeof score !== 'number') return '0%'
  return `${Math.max(0, Math.min(100, score * 100))}%`
}

function mapAttemptStatus(s: string): 'succeeded' | 'failed' | 'running' | 'queued' {
  if (s === 'succeeded') return 'succeeded'
  if (s === 'running') return 'running'
  if (s === 'failed' || s === 'timeout' || s === 'rate_limited') return 'failed'
  return 'queued'
}

function isTerminalTaskStatus(status?: string | null): boolean {
  return status === 'succeeded'
    || status === 'failed'
    || status === 'dead_letter'
    || status === 'cancelled'
}

function goBack() {
  router.push('/llm?tab=tasks')
}

async function handleCancel() {
  cancelling.value = true
  try {
    await llmApi.cancelTask(props.taskId)
    await loadAll()
  } finally {
    cancelling.value = false
  }
}

async function loadAll(silent = false) {
  if (!domainStore.currentDomain) {
    task.value = null
    requestData.value = null
    result.value = null
    attempts.value = []
    events.value = []
    loading.value = false
    initialLoading.value = false
    return
  }
  if (!silent) loading.value = true
  try {
    const detail = await llmApi.getTask(props.taskId).catch(() => null) as Record<string, any> | null
    if (detail) {
      task.value = detail.task ?? detail
      requestData.value = detail.request ?? null
      result.value = detail.result ?? null
      attempts.value = Array.isArray(detail.attempts) ? detail.attempts : []
      events.value = Array.isArray(detail.events) ? detail.events : []
    }
  } finally {
    if (!silent) loading.value = false
    initialLoading.value = false
  }
}

async function refreshTaskDetail() {
  if (document.visibilityState !== 'visible') return
  if (isTerminalTaskStatus(task.value?.status)) {
    stopPolling()
    return
  }
  await loadAll(true)
}

const { start: startPolling, stop: stopPolling } = usePolling(refreshTaskDetail, 3000, {
  immediate: false,
  shouldRun: () => Boolean(domainStore.currentDomain),
})

watch(() => task.value?.status, (status) => {
  if (isTerminalTaskStatus(status)) {
    stopPolling()
  }
})

onMounted(async () => {
  await loadAll()
  if (!isTerminalTaskStatus(task.value?.status)) {
    startPolling()
  }
})
</script>

<style scoped>
.task-detail { display: flex; flex-direction: column; gap: 16px; }

/* Header */
.task-detail__header {
  display: flex; align-items: center; justify-content: space-between;
  padding-bottom: 12px; border-bottom: 1px solid var(--kb-border-light);
}
.task-detail__header-left { display: flex; align-items: center; gap: 10px; }
.task-detail__header-right { display: flex; gap: 8px; }
.back-btn { color: var(--kb-text-secondary); font-size: 13px; }
.back-btn:hover { color: var(--kb-accent); }
.task-detail__id { font-family: 'SF Mono', 'Cascadia Code', monospace; font-size: 14px; font-weight: 600; color: var(--kb-accent); }

/* Info Grid */
.task-detail__grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }
.info-card { background: var(--kb-bg-card); border: 1px solid var(--kb-border-light); border-radius: var(--kb-radius); padding: 16px; }
.info-card__title {
  font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px;
  color: var(--kb-text-tertiary); font-weight: 600; margin: 0 0 10px;
}
.kv-table { width: 100%; border-collapse: collapse; }
.kv-table td { padding: 3px 0; font-size: 12px; border: none; vertical-align: top; }
.kv-table td:first-child { color: var(--kb-text-tertiary); width: 80px; white-space: nowrap; }
.kv-table td:last-child { color: var(--kb-text-primary); word-break: break-all; }

/* Section cards */
.section-card {
  background: var(--kb-bg-card); border: 1px solid var(--kb-border-light);
  border-radius: var(--kb-radius); padding: 18px 20px;
}
.section-card__title {
  font-size: 13px; font-weight: 600; color: var(--kb-text-secondary);
  text-transform: uppercase; letter-spacing: 0.5px; margin: 0 0 14px;
  display: flex; align-items: center; gap: 8px;
}
.section-card--collapsible { padding: 0; }
.section-card--collapsible :deep(.el-collapse) { border: none; }
.section-card--collapsible :deep(.el-collapse-item__header) {
  padding: 14px 20px; font-size: 12px; color: var(--kb-text-tertiary);
  background: transparent; border: none;
}
.section-card--collapsible :deep(.el-collapse-item__wrap) { border: none; }
.section-card--collapsible :deep(.el-collapse-item__content) { padding: 0 20px 16px; }

/* Collapse toggle inside section */
.collapse-toggle { margin-top: 10px; }
.collapse-toggle :deep(.el-collapse) { border: none; }
.collapse-toggle :deep(.el-collapse-item__header) {
  font-size: 11px; color: var(--kb-text-tertiary); background: transparent; border: none;
  height: 32px; line-height: 32px;
}
.collapse-toggle :deep(.el-collapse-item__wrap) { border: none; }
.collapse-toggle :deep(.el-collapse-item__content) { padding-bottom: 8px; }

/* Code blocks */
.code-block {
  background: var(--kb-bg-page); border: 1px solid var(--kb-border-light);
  border-radius: var(--kb-radius-sm); padding: 14px;
  font-family: 'SF Mono', 'Cascadia Code', monospace; font-size: 12px;
  line-height: 1.6; color: var(--kb-text-secondary);
  overflow-x: auto; max-height: 400px; overflow-y: auto; white-space: pre-wrap;
}
.code-block--compact { max-height: 250px; font-size: 11px; }

/* Parse status badge */
.parse-status {
  font-size: 10px; padding: 1px 6px; border-radius: 4px; font-weight: 600;
  text-transform: none; letter-spacing: 0;
}
.parse-status--succeeded { background: var(--kb-success-soft); color: var(--kb-success); }
.parse-status--failed { background: var(--kb-danger-soft); color: var(--kb-danger); }
.parse-status--schema_invalid { background: var(--kb-warning-soft); color: var(--kb-warning); }

/* Template link */
.template-link {
  font-size: 12px; font-weight: 600; color: var(--kb-accent);
  text-decoration: none; padding: 2px 8px;
  background: var(--kb-accent-soft); border-radius: 4px;
}
.template-link:hover { text-decoration: underline; }

/* Prompt preview */
.prompt-preview { margin-bottom: 12px; }
.prompt-preview__label {
  font-size: 11px; font-weight: 600; color: var(--kb-text-tertiary);
  margin-bottom: 6px; text-transform: uppercase; letter-spacing: 0.3px;
}

/* Cell values in tables */
.cell-value { font-size: 12px; color: var(--kb-text-secondary); white-space: pre-wrap; word-break: break-all; line-height: 1.5; }
.num-mono { font-size: 12px; font-variant-numeric: tabular-nums; color: var(--kb-text-secondary); }
.rank-num { font-size: 12px; font-weight: 700; color: var(--kb-accent); }

/* Rerank */
.rerank-query {
  font-size: 14px; font-weight: 600; color: var(--kb-text-primary);
  padding: 10px 14px; background: var(--kb-bg-page);
  border-radius: var(--kb-radius-sm); border: 1px solid var(--kb-border-light);
}
.score-cell { display: flex; align-items: center; gap: 8px; }
.score-bar { width: 80px; height: 6px; background: var(--kb-border-light); border-radius: 3px; overflow: hidden; flex-shrink: 0; }
.score-bar__fill { height: 100%; background: var(--kb-accent); border-radius: 3px; transition: width 0.3s ease; }
.score-value { font-family: 'SF Mono', monospace; font-size: 11px; font-weight: 600; color: var(--kb-text-primary); }

/* Error box */
.error-box {
  background: var(--kb-danger-soft); color: var(--kb-danger); border-radius: var(--kb-radius-sm);
  padding: 10px 14px; font-size: 12px; line-height: 1.5; margin-top: 8px;
}
.error-box--compact { margin-top: 8px; padding: 8px 12px; }
.error-box pre { margin: 4px 0 0; font-size: 11px; white-space: pre-wrap; }

/* Attempts */
.attempt-list { display: flex; flex-direction: column; gap: 8px; }
.attempt-item {
  padding: 12px 14px; background: var(--kb-bg-page);
  border-radius: var(--kb-radius-sm); border: 1px solid var(--kb-border-light);
}
.attempt-item__header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
.attempt-item__no { font-size: 12px; font-weight: 700; color: var(--kb-accent); }
.attempt-item__stats { display: flex; gap: 14px; flex-wrap: wrap; }
.stat-chip { font-size: 11px; color: var(--kb-text-tertiary); }
.stat-chip strong { color: var(--kb-text-primary); font-variant-numeric: tabular-nums; }

/* Event Timeline */
.timeline { position: relative; padding-left: 18px; }
.timeline-item { display: flex; gap: 12px; padding: 6px 0; position: relative; }
.timeline-item__dot {
  width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; margin-top: 5px;
  position: absolute; left: -18px;
}
.timeline-item__dot--submitted { background: var(--kb-accent); }
.timeline-item__dot--claimed { background: var(--kb-warning); }
.timeline-item__dot--succeeded { background: var(--kb-success); }
.timeline-item__dot--failed { background: var(--kb-danger); }
.timeline-item__dot--retried { background: var(--kb-warning); }
.timeline-item__dot--cancelled { background: var(--kb-text-tertiary); }
.timeline-item__dot--dead_letter { background: #8b5cf6; }
.timeline-item__body { display: flex; align-items: baseline; gap: 10px; font-size: 12px; }
.timeline-item__time { color: var(--kb-text-tertiary); font-family: 'SF Mono', monospace; font-size: 11px; min-width: 150px; }
.timeline-item__event { font-weight: 600; color: var(--kb-accent); min-width: 80px; }
.timeline-item__msg { color: var(--kb-text-secondary); }

/* Shared */
.text-mono { font-family: 'SF Mono', 'Cascadia Code', monospace; font-size: 11px; }
.text-faint { color: var(--kb-text-tertiary); font-size: 11px; }
.num-highlight { font-size: 18px; font-weight: 700; color: var(--kb-text-primary); font-variant-numeric: tabular-nums; }
.type-badge { font-size: 11px; padding: 2px 8px; border-radius: 4px; font-weight: 600; background: var(--kb-accent-soft); color: var(--kb-accent); }
.type-badge--chat { background: rgba(8, 145, 178, 0.08); color: #0891b2; }
.type-badge--embedding { background: rgba(16, 185, 129, 0.1); color: #10b981; }
.type-badge--rerank { background: rgba(245, 158, 11, 0.1); color: #f59e0b; }
</style>
