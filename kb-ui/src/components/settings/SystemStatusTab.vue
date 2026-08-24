<template>
  <div class="sys-status">
    <!-- ── 检索使用分析（明细）───────────────────────────────────────
      概览页放的是摘要（四个数字 + 零结果前 5 + 两张图），这里是全量：热门查询、
      各范式明细、意图与渠道分布。两边同源于 GET /api/ops/usage，职责不重叠。
    -->
    <section class="sys-status__section">
      <div class="sys-status__head">
        <h3 class="sys-status__title">检索使用分析</h3>
        <span class="sys-status__scope">近 {{ usage?.days ?? 7 }} 天 · 全域检索流量</span>
        <el-button text type="primary" size="small" :loading="usageLoading" @click="loadUsage">
          刷新
        </el-button>
      </div>

      <div v-if="usageError" class="sys-status__notice sys-status__notice--error">
        加载失败
        <el-button text type="primary" size="small" @click="loadUsage">重试</el-button>
      </div>

      <div v-else-if="usage && !usage.available" class="sys-status__notice sys-status__notice--info">
        <strong>尚未产生检索日志</strong>
        <span>
          检索服务还没有写入过查询日志（serving_query_logs 不存在）。这张表由 serving
          在启动时创建、在每次检索后写入；发生过检索之后这里就会有数据。
        </span>
      </div>

      <template v-else-if="usage">
        <div class="sys-status__stats">
          <StatsCard label="检索次数" :value="usage.summary.queries" icon="🔍" />
          <StatsCard label="零结果" :value="usage.summary.no_result" icon="🕳" />
          <StatsCard label="零结果率" :value="formatRate(usage.summary.no_result_rate)" icon="📉" />
          <StatsCard label="P95 延迟" :value="formatMs(usage.summary.p95_duration_ms)" icon="⏱" />
          <StatsCard label="平均延迟" :value="formatMs(usage.summary.avg_duration_ms)" icon="〽" />
        </div>

        <!-- 各范式明细：调用量之外还要给零结果率与 P95——「调用多」不等于「跑得好」 -->
        <div class="sys-status__chart">
          <h4 class="sys-status__subtitle">各检索范式</h4>
          <table v-if="usage.paradigms.length" class="ptable">
            <thead>
              <tr><th>范式</th><th>调用</th><th>零结果</th><th>P95</th></tr>
            </thead>
            <tbody>
              <tr v-for="p in usage.paradigms" :key="p.paradigm_id">
                <td class="ptable__name">{{ paradigmLabel(p.paradigm_id) }}</td>
                <td>{{ p.calls }}</td>
                <td>{{ p.no_result }}（{{ formatRate(p.calls ? p.no_result / p.calls : 0) }}）</td>
                <td>{{ formatMs(p.p95_duration_ms) }}</td>
              </tr>
            </tbody>
          </table>
          <p v-else class="sys-status__muted">窗口内没有检索调用</p>
        </div>

        <!--
          概览页只列前 5 条，这里给后端返回的全量。这是整块数据里最有行动价值的一段：
          零结果率说明「有缺口」，这份清单说明「缺口在哪、该补什么」。
        -->
        <div class="sys-status__chart">
          <h4 class="sys-status__subtitle">
            答不上来的问题
            <span class="sys-status__hint">用户输入原文，仅管理员可见</span>
          </h4>
          <QueryList v-if="noResultItems.length" :items="noResultItems" />
          <p v-else class="sys-status__muted">窗口内没有零结果查询</p>
        </div>

        <div class="sys-status__chart">
          <h4 class="sys-status__subtitle">
            热门查询
            <span class="sys-status__hint">用户输入原文，仅管理员可见</span>
          </h4>
          <QueryList v-if="topQueryItems.length" :items="topQueryItems" />
          <p v-else class="sys-status__muted">窗口内没有查询</p>
        </div>

        <div class="sys-status__split">
          <div>
            <h4 class="sys-status__subtitle">查询意图分布</h4>
            <BarChart
              v-if="intentBars.length"
              :data="intentBars"
              horizontal
              :height="barChartHeight(intentBars.length)"
            />
            <p v-else class="sys-status__muted">无数据</p>
          </div>
          <div>
            <h4 class="sys-status__subtitle">接入渠道分布</h4>
            <BarChart
              v-if="channelBars.length"
              :data="channelBars"
              horizontal
              :height="barChartHeight(channelBars.length)"
            />
            <p v-else class="sys-status__muted">无数据</p>
          </div>
        </div>
      </template>
    </section>

    <!-- ── 服务状态 ────────────────────────────────────────────────── -->
    <section class="sys-status__section">
      <div class="sys-status__head">
        <h3 class="sys-status__title">服务状态</h3>
        <el-button text type="primary" size="small" :loading="healthLoading" @click="loadHealth">
          刷新
        </el-button>
      </div>
      <div class="sys-status__health">
        <ServiceHealthCard
          v-for="svc in services"
          :key="svc.key"
          :name="svc.name"
          :status="svc.status"
          :detail="svc.detail"
          :icon="svc.icon"
        />
      </div>
    </section>

    <!-- ── 域级知识资产 ────────────────────────────────────────────── -->
    <section class="sys-status__section">
      <div class="sys-status__head">
        <h3 class="sys-status__title">知识资产</h3>
        <!--
          口径必须写在脸上：这几个数只统计域级 active release 的范围。KB 挖掘
          publish=false，永不产生 release，所以纯 KB 部署下它们恒为 0——那不是
          「没有知识」，是「这个口径不适用」。
        -->
        <span class="sys-status__scope">口径：域级 active release</span>
        <el-button text type="primary" size="small" :loading="statsLoading" @click="loadStats">
          刷新
        </el-button>
      </div>

      <div v-if="statsError" class="sys-status__notice sys-status__notice--error">
        加载失败
        <el-button text type="primary" size="small" @click="loadStats">重试</el-button>
      </div>

      <!--
        用普通标记而不是 el-alert：文案是真正的文本节点，读屏与测试都能拿到
        （el-alert 的 title/description 是 prop，渲染进组件内部）。
      -->
      <div v-else-if="noActiveRelease" class="sys-status__notice sys-status__notice--info">
        <strong>该域无发布语料</strong>
        <span>
          当前域没有 active release，因此这里没有可统计的资产。KB 挖掘只 build 不发布
          （publish=false），不会产生 release；域级发布语料只有 legacy /api/runs 那条线才产生。
        </span>
      </div>

      <template v-else>
        <div class="sys-status__stats">
          <StatsCard label="文档" :value="stats?.documents ?? '-'" icon="📄" />
          <StatsCard label="快照" :value="stats?.snapshots ?? '-'" icon="📸" />
          <StatsCard label="段落" :value="stats?.segments ?? '-'" icon="📝" />
          <StatsCard label="检索单元" :value="stats?.retrieval_units ?? '-'" icon="🔎" />
          <StatsCard label="关系" :value="stats?.relations ?? '-'" icon="🔗" />
        </div>

        <div class="sys-status__chart">
          <h4 class="sys-status__subtitle">检索单元类型分布</h4>
          <PieChart v-if="unitTypeData.length" :data="unitTypeData" height="240px" />
          <p v-else class="sys-status__muted">当前 release 内没有检索单元</p>
        </div>
      </template>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useDomainStore } from '@/stores/domain'
import { useMiningApi } from '@/api/mining'
import { useServingApi } from '@/api/serving'
import { useLlmApi } from '@/api/llm'
import { useOpsApi } from '@/api/ops'
import { breakdownBars, formatMs, formatRate, paradigmLabel } from '@/utils/opsStats'
import { barChartHeight } from '@/utils/dashboard'
import type { HealthStatus, KnowledgeStats } from '@/types'
import type { OpsUsage } from '@/types/ops'
import StatsCard from '@/components/common/StatsCard.vue'
import ServiceHealthCard from '@/components/common/ServiceHealthCard.vue'
import PieChart from '@/components/charts/PieChart.vue'
import BarChart from '@/components/charts/BarChart.vue'
import QueryList from '@/components/dashboard/QueryList.vue'
import type { QueryListItem } from '@/components/dashboard/QueryList.vue'

type Health = 'healthy' | 'degraded' | 'unhealthy' | 'unknown'

const domainStore = useDomainStore()
const miningApi = useMiningApi()
const servingApi = useServingApi()
const llmApi = useLlmApi()
const opsApi = useOpsApi()

const stats = ref<KnowledgeStats | null>(null)
const statsLoading = ref(false)
const statsError = ref(false)
const healthLoading = ref(false)

const usage = ref<OpsUsage | null>(null)
const usageLoading = ref(false)
const usageError = ref(false)

const intentBars = computed(() => breakdownBars(usage.value?.intents))
const channelBars = computed(() => breakdownBars(usage.value?.channels))

/** 「最近一次被问到」只给到日，精确到分秒对补知识这个动作没有帮助。 */
function formatDay(iso: string): string {
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '-' : d.toLocaleDateString('zh-CN')
}

// 两份清单共用 QueryList，差别只在注记：零结果给"最近被问到"的日期，热门查询给
// "N 次无结果"（染成警告色——问得多又答不上的那几条优先级最高）。
const noResultItems = computed<QueryListItem[]>(
  () => (usage.value?.no_result_queries ?? []).map(q => ({
    text: q.query_text,
    count: q.count,
    note: q.last_at ? formatDay(q.last_at) : undefined,
  })),
)

const topQueryItems = computed<QueryListItem[]>(
  () => (usage.value?.top_queries ?? []).map(q => ({
    text: q.query_text,
    count: q.count,
    note: q.no_result ? `${q.no_result} 次无结果` : undefined,
    noteTone: 'warn' as const,
  })),
)

const services = ref([
  { key: 'mining', name: '挖掘服务', icon: '⚙', status: 'unknown' as Health, detail: '' },
  { key: 'serving', name: '检索服务', icon: '🔍', status: 'unknown' as Health, detail: '' },
  { key: 'llm', name: 'LLM服务', icon: '🤖', status: 'unknown' as Health, detail: '' },
])

/**
 * 「没有发布语料」与「发布了但里面是空的」是两回事，后端刻意保留了这个区分：
 * 撤回最后一个文档会发布一个**空**的 active build，那时的 0 是真的 0。
 * 所以判据是有没有 release，不是计数是不是 0。
 */
const noActiveRelease = computed(() => {
  if (!stats.value) return false
  const releases = stats.value.active_releases
  return Array.isArray(releases) ? releases.length === 0 : stats.value.releases === 0
})

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

// 切域竞态守卫：慢的旧域响应不得覆盖新域的数字（与概览页同一套路数）。
// 两块**各用一个计数器**——共用一个的话，loadAll 里后调用的那个会把先调用的那个
// 直接作废，健康检查结果永远落不下来。
let statsGen = 0
let healthGen = 0
let usageGen = 0

async function loadUsage() {
  const gen = ++usageGen
  usageLoading.value = true
  usageError.value = false
  try {
    const data = await opsApi.getUsage(domainStore.currentDomain)
    if (gen !== usageGen) return
    usage.value = data
  } catch (e) {
    if (gen !== usageGen) return
    console.error('Failed to load ops usage:', e)
    usageError.value = true
    usage.value = null
  } finally {
    if (gen === usageGen) usageLoading.value = false
  }
}

async function loadStats() {
  const gen = ++statsGen
  statsLoading.value = true
  statsError.value = false
  try {
    const data = await miningApi.getStats()
    if (gen !== statsGen) return
    stats.value = data
  } catch (e) {
    if (gen !== statsGen) return
    console.error('Failed to load knowledge stats:', e)
    statsError.value = true
    stats.value = null
  } finally {
    if (gen === statsGen) statsLoading.value = false
  }
}

async function probe(fn: () => Promise<HealthStatus>): Promise<{ status: Health; detail: string }> {
  try {
    // 健康检查不该把整页拖住：超时按不健康处理。
    const h = await Promise.race([
      fn(),
      new Promise<never>((_, reject) => setTimeout(() => reject(new Error('timeout')), 3000)),
    ])
    const s = h.status
    const ok = s === 'healthy' || s === 'ok' || s === 'UP'
    return {
      status: ok ? 'healthy' : s === 'degraded' ? 'degraded' : 'unhealthy',
      detail: h.version ? `v${h.version}` : ok ? '正常' : String(s),
    }
  } catch {
    return { status: 'unhealthy', detail: '连接失败' }
  }
}

async function loadHealth() {
  const gen = ++healthGen
  healthLoading.value = true
  const probes = [
    () => miningApi.getHealth(),
    () => servingApi.getHealth(),
    () => llmApi.getHealth(),
  ]
  // probe 自己吞掉异常并回落成 unhealthy，所以这里不会 reject
  const results = await Promise.all(probes.map(probe))
  if (gen !== healthGen) return
  results.forEach((r, i) => {
    services.value[i].status = r.status
    services.value[i].detail = r.detail
  })
  healthLoading.value = false
}

function loadAll() {
  loadHealth()
  loadStats()
  loadUsage()
}

onMounted(loadAll)
onUnmounted(() => { statsGen++; healthGen++; usageGen++ })
watch(() => domainStore.currentDomain, loadAll)
</script>

<style scoped>
.sys-status {
  display: flex;
  flex-direction: column;
  gap: 24px;
}

.sys-status__head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 14px;
}

.sys-status__title {
  font-size: 13px;
  font-weight: 600;
  color: var(--kb-text-secondary);
  letter-spacing: 0.5px;
  margin: 0;
}

.sys-status__subtitle {
  font-size: 12px;
  font-weight: 600;
  color: var(--kb-text-secondary);
  margin: 0 0 10px;
}

.sys-status__scope {
  font-size: 11px;
  color: var(--kb-text-tertiary);
  padding: 1px 8px;
  border: 1px solid var(--kb-border-light);
  border-radius: 10px;
}

.sys-status__health {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 14px;
}

.sys-status__stats {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 12px;
}

.sys-status__chart {
  margin-top: 20px;
  max-width: 420px;
}

.sys-status__notice {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--kb-text-tertiary);
  padding: 16px 0;
}

.sys-status__notice--error {
  color: var(--kb-danger);
}

.sys-status__notice--info {
  flex-direction: column;
  align-items: flex-start;
  gap: 4px;
  padding: 14px 16px;
  background: var(--kb-accent-soft);
  border-radius: var(--kb-radius-sm);
  line-height: 1.6;
}

.sys-status__notice--info strong {
  color: var(--kb-text-primary);
}

.sys-status__muted {
  font-size: 12px;
  color: var(--kb-text-tertiary);
  margin: 0;
}

.sys-status__hint {
  font-weight: 400;
  font-size: 11px;
  color: var(--kb-text-tertiary);
  margin-left: 8px;
}

.sys-status__split {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 20px;
  margin-top: 20px;
}

/* ── 各范式明细表 ── */
.ptable {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.ptable th {
  text-align: left;
  font-weight: 600;
  font-size: 11px;
  color: var(--kb-text-tertiary);
  letter-spacing: 0.5px;
  padding: 6px 8px;
  border-bottom: 1px solid var(--kb-border-light);
}

.ptable td {
  padding: 7px 8px;
  border-bottom: 1px solid var(--kb-border-light);
  color: var(--kb-text-secondary);
  font-variant-numeric: tabular-nums;
}

.ptable tr:last-child td { border-bottom: none; }

.ptable__name {
  color: var(--kb-text-primary);
  font-weight: 500;
}

</style>
