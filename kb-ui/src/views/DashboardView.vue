<template>
  <div class="dashboard">
    <!-- Row 1: Service Health -->
    <section class="dashboard__section">
      <h3 class="section-title">服务状态</h3>
      <div class="dashboard__health-row">
        <ServiceHealthCard
          name="挖掘服务"
          :status="miningHealth"
          :detail="miningDetail"
          icon="⚙"
        />
        <ServiceHealthCard
          name="检索服务"
          :status="servingHealth"
          :detail="servingDetail"
          icon="🔍"
        />
        <ServiceHealthCard
          name="LLM服务"
          :status="llmHealth"
          :detail="llmDetail"
          icon="🤖"
        />
      </div>
    </section>

    <!-- Row 2: Stats Cards -->
    <section class="dashboard__section">
      <h3 class="section-title">知识资产</h3>
      <div class="dashboard__stats-row">
        <StatsCard label="文档" :value="stats?.documents ?? '-'" icon="📄" />
        <StatsCard label="快照" :value="stats?.snapshots ?? '-'" icon="📸" />
        <StatsCard label="段落" :value="stats?.segments ?? '-'" icon="📝" />
        <StatsCard label="检索单元" :value="stats?.retrieval_units ?? '-'" icon="🔎" />
        <StatsCard label="关系" :value="stats?.relations ?? '-'" icon="🔗" />
      </div>
    </section>

    <!-- Row 3: Charts -->
    <section class="dashboard__charts">
      <div class="dashboard__chart-card">
        <h3 class="section-title">检索单元类型分布</h3>
        <PieChart :data="unitTypeData" height="240px" />
      </div>
      <div class="dashboard__chart-card dashboard__chart-card--wide">
        <h3 class="section-title">最近挖掘任务</h3>
        <div class="dashboard__table-wrap">
          <el-table
            :data="recentRuns"
            v-loading="loading"
            class="kb-table"
            :header-cell-style="{ background: 'transparent' }"
          >
            <el-table-column label="Run ID" min-width="120">
              <template #default="{ row }">
                <router-link :to="`/mining/${row.id}`" class="table-link">{{ row.id.slice(0, 8) }}</router-link>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="120">
              <template #default="{ row }">
                <StatusBadge :status="row.status" size="small">{{ statusLabel(row.status) }}</StatusBadge>
              </template>
            </el-table-column>
            <el-table-column label="文档" width="120">
              <template #default="{ row }">
                <span class="doc-stats">
                  <span class="doc-stats__new" v-if="row.new_count">+{{ row.new_count }}</span>
                  <span class="doc-stats__updated" v-if="row.updated_count">~{{ row.updated_count }}</span>
                  <span v-if="!row.new_count && !row.updated_count">{{ row.total_documents }}</span>
                </span>
              </template>
            </el-table-column>
            <el-table-column label="耗时" width="100">
              <template #default="{ row }">
                {{ formatDuration(row.started_at, row.finished_at) }}
              </template>
            </el-table-column>
            <el-table-column label="创建时间" min-width="160">
              <template #default="{ row }">
                {{ formatTime(row.created_at) }}
              </template>
            </el-table-column>
          </el-table>
          <div class="dashboard__table-footer">
            <el-button text type="primary" size="small" @click="$router.push('/kb')">
              查看全部 →
            </el-button>
          </div>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useDomainStore } from '@/stores/domain'
import { useMiningStore } from '@/stores/mining'
import { useMiningApi } from '@/api/mining'
import { useServingApi } from '@/api/serving'
import { useLlmApi } from '@/api/llm'
import type { KnowledgeStats, HealthStatus } from '@/types'
import StatsCard from '@/components/common/StatsCard.vue'
import ServiceHealthCard from '@/components/common/ServiceHealthCard.vue'
import StatusBadge from '@/components/common/StatusBadge.vue'
import PieChart from '@/components/charts/PieChart.vue'

const domainStore = useDomainStore()
const miningStore = useMiningStore()
const miningApi = useMiningApi()
const servingApi = useServingApi()
const llmApi = useLlmApi()

const stats = ref<KnowledgeStats | null>(null)
const miningHealth = ref<'healthy' | 'degraded' | 'unhealthy' | 'unknown'>('unknown')
const servingHealth = ref<'healthy' | 'degraded' | 'unhealthy' | 'unknown'>('unknown')
const llmHealth = ref<'healthy' | 'degraded' | 'unhealthy' | 'unknown'>('unknown')
const miningDetail = ref('')
const servingDetail = ref('')
const llmDetail = ref('')
const loading = ref(false)
const recentRuns = computed(() => miningStore.runs.slice(0, 5))
let alive = true

const unitTypeData = computed(() => {
  const byType = stats.value?.retrieval_units_by_type
  if (!byType) return []
  const nameMap: Record<string, string> = {
    raw_text: '原始文本', contextual_text: '上下文文本', summary: '摘要',
    generated_question: '生成问题', entity_card: '实体卡片',
  }
  return Object.entries(byType)
    .filter(([, v]) => v > 0)
    .map(([k, v]) => ({ name: nameMap[k] || k, value: v }))
})

onUnmounted(() => { alive = false })

async function loadData() {
  loading.value = true
  const healthCheck = async (
    fn: () => Promise<HealthStatus>,
    setHealth: (v: 'healthy' | 'degraded' | 'unhealthy') => void,
    setDetail: (v: string) => void,
  ) => {
    try {
      const h = await Promise.race([
        fn(),
        new Promise<never>((_, reject) => setTimeout(() => reject(new Error('timeout')), 3000)),
      ])
      if (!alive) return
      const s = h.status
      const ok = s === 'healthy' || s === 'ok' || s === 'UP'
      setHealth(ok ? 'healthy' : s === 'degraded' ? 'degraded' : 'unhealthy')
      setDetail(h.version ? `v${h.version}` : ok ? '正常' : s)
    } catch {
      if (alive) { setHealth('unhealthy'); setDetail('连接失败') }
    }
  }

  await Promise.allSettled([
    healthCheck(() => miningApi.getHealth(), v => { miningHealth.value = v }, v => { miningDetail.value = v }),
    healthCheck(() => servingApi.getHealth(), v => { servingHealth.value = v }, v => { servingDetail.value = v }),
    healthCheck(() => llmApi.getHealth(), v => { llmHealth.value = v }, v => { llmDetail.value = v }),
    miningApi.getStats().then(s => { if (alive) stats.value = s }),
    miningStore.fetchRuns(),
  ])
  if (alive) loading.value = false
}

function statusLabel(status: string) {
  const map: Record<string, string> = {
    running: '运行中', completed: '已完成', failed: '失败', cancelled: '已取消', pending: '等待中',
  }
  return map[status] || status
}

function formatTime(t: string) {
  if (!t) return '-'
  return new Date(t).toLocaleString('zh-CN')
}

function formatDuration(start?: string, end?: string) {
  if (!start) return '-'
  const s = new Date(start).getTime()
  const e = end ? new Date(end).getTime() : Date.now()
  const diff = Math.round((e - s) / 1000)
  if (diff < 60) return `${diff}s`
  if (diff < 3600) return `${Math.floor(diff / 60)}m ${diff % 60}s`
  return `${Math.floor(diff / 3600)}h ${Math.floor((diff % 3600) / 60)}m`
}

onMounted(loadData)
watch(() => domainStore.currentDomain, loadData)
</script>

<style scoped>
.dashboard {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.dashboard__section {
  background: var(--kb-bg-card);
  border-radius: var(--kb-radius);
  padding: 20px 22px;
  box-shadow: var(--kb-shadow-card);
  border: 1px solid var(--kb-border-light);
}

.section-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--kb-text-secondary);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin: 0 0 16px;
}

/* Health cards */
.dashboard__health-row {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 14px;
}

/* Stats cards */
.dashboard__stats-row {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 12px;
}

/* Charts row */
.dashboard__charts {
  display: grid;
  grid-template-columns: 320px 1fr;
  gap: 16px;
}

.dashboard__chart-card {
  background: var(--kb-bg-card);
  border-radius: var(--kb-radius);
  padding: 20px 22px;
  box-shadow: var(--kb-shadow-card);
  border: 1px solid var(--kb-border-light);
}

.dashboard__chart-card--wide {
  display: flex;
  flex-direction: column;
}

.dashboard__table-wrap {
  flex: 1;
  min-height: 0;
}

.dashboard__table-footer {
  display: flex;
  justify-content: center;
  padding-top: 8px;
  border-top: 1px solid var(--kb-border-light);
  margin-top: 8px;
}

/* Table link */
.table-link {
  color: var(--kb-accent);
  text-decoration: none;
  font-weight: 500;
  font-size: 13px;
  font-family: 'SF Mono', 'Cascadia Code', monospace;
}
.table-link:hover { text-decoration: underline; }

/* Doc stats */
.doc-stats {
  display: inline-flex;
  gap: 6px;
  font-size: 12px;
}
.doc-stats__new { color: var(--kb-success); font-weight: 600; }
.doc-stats__updated { color: var(--kb-accent); font-weight: 600; }
</style>
