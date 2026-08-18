<template>
  <div class="dash">
    <!-- ── 区块 1：汇总数字 ──────────────────────────────────────────── -->
    <section class="dash__block">
      <div class="dash__block-head">
        <h3 class="dash__block-title">知识库概况</h3>
        <div class="dash__block-actions">
          <span class="dash__scope">口径：本域我可见的 {{ stats?.kb_count ?? kbs.length }} 个知识库</span>
          <el-button text type="primary" size="small" :loading="loading" @click="load">
            刷新
          </el-button>
        </div>
      </div>

      <div v-if="loading" class="dash__skeleton-grid dash__skeleton-grid--tiles">
        <div v-for="i in 6" :key="i" class="dash__skeleton dash__skeleton--tile" />
      </div>
      <BlockError v-else-if="statsError" @retry="load" />
      <div v-else class="dash__tiles">
        <StatsCard
          v-for="tile in tiles"
          :key="tile.label"
          :label="tile.label"
          :value="tile.value"
          :icon="tile.icon"
        />
      </div>
    </section>

    <!-- ── 区块 2：待处理（有内容才渲染，无则整块不出现）─────────────── -->
    <section v-if="tasks.length" class="dash__block dash__block--pending">
      <h3 class="dash__block-title">⚠ 待处理</h3>
      <div
        v-for="task in tasks"
        :key="task.key"
        class="task"
        @click="router.push(task.to)"
      >
        <span class="task__kb">{{ task.kbName }}</span>
        <span class="task__text">{{ task.text }}</span>
        <el-button text type="primary" size="small">{{ task.actionLabel }} →</el-button>
      </div>
    </section>

    <!-- ── 区块 3：图表 ──────────────────────────────────────────────── -->
    <!--
      一个库都没有时不画四张空图：一排 0 和一条贴地的线会被读成「系统没在干活」，
      真相是「还没建库」。与 设置→系统状态 处理无 release 的做法同一条原则。
    -->
    <section v-if="!loading && !statsError && !hasAnyKb" class="dash__block">
      <EmptyState text="还没有知识库，建一个就能看到统计">
        <template #action>
          <el-button type="primary" size="small" @click="router.push('/kb')">
            新建知识库
          </el-button>
        </template>
      </EmptyState>
    </section>

    <div v-else class="dash__charts">
      <!-- 文档状态分布 -->
      <section class="dash__block">
        <h3 class="dash__block-title">文档状态分布</h3>
        <div v-if="loading" class="dash__skeleton dash__skeleton--chart" />
        <BlockError v-else-if="statsError" @retry="load" />
        <div v-else class="dash__donut">
          <div class="dash__donut-chart">
            <PieChart :data="statusSlices" height="220px" :show-legend="false" />
          </div>
          <!--
            数值表不是"顺便加的"：状态色里绿/黄对浅底的对比度低于 3:1，而红↔绿在
            deuteranopia 下的分离度只擦着下限过。颜色永远不是唯一的区分手段——
            名称与数字必须并排给出。
          -->
          <ul class="legend">
            <li v-for="slice in statusSlices" :key="slice.name" class="legend__row">
              <span class="legend__dot" :style="{ background: slice.color }" />
              <span class="legend__name">{{ slice.name }}</span>
              <span class="legend__value">{{ slice.value }}</span>
            </li>
          </ul>
        </div>
      </section>

      <!-- 挖掘趋势 -->
      <section class="dash__block">
        <div class="dash__block-head">
          <h3 class="dash__block-title">近 {{ trendDays }} 天入库文档数</h3>
          <span class="dash__scope">
            共 {{ totals.documents }} 篇 · {{ totals.runs }} 次挖掘
          </span>
        </div>
        <div v-if="loading" class="dash__skeleton dash__skeleton--chart" />
        <BlockError v-else-if="statsError" @retry="load" />
        <!-- 单系列不配图例：标题已经说明了这条线是什么 -->
        <LineChart
          v-else
          :labels="trend.labels"
          :series="[{ name: '入库文档数', data: trend.data }]"
          height="220px"
        />
      </section>

      <!-- 各知识库文档数 -->
      <section class="dash__block">
        <h3 class="dash__block-title">各知识库文档数（前 {{ TOP_KB_BAR_LIMIT }}）</h3>
        <div v-if="loading" class="dash__skeleton dash__skeleton--chart" />
        <BlockError v-else-if="overviewError" @retry="load" />
        <!-- 单一色相：谁是谁由 y 轴标签说明，颜色不承担身份，不必也不该上分类色 -->
        <BarChart
          v-else-if="kbBars.length"
          :data="kbBars"
          horizontal
          :height="barHeight(kbBars.length)"
        />
        <p v-else class="dash__muted">还没有文档</p>
      </section>

      <!-- 检索单元类型 -->
      <section class="dash__block">
        <h3 class="dash__block-title">检索单元类型分布</h3>
        <div v-if="loading" class="dash__skeleton dash__skeleton--chart" />
        <BlockError v-else-if="statsError" @retry="load" />
        <BarChart
          v-else-if="unitBars.length"
          :data="unitBars"
          horizontal
          :height="barHeight(unitBars.length)"
        />
        <p v-else class="dash__muted">还没有挖掘出检索单元</p>
      </section>
    </div>

    <!-- ── 区块 4：我的知识库 ────────────────────────────────────────── -->
    <section class="dash__block">
      <div class="dash__block-head">
        <h3 class="dash__block-title">我的知识库</h3>
        <div class="dash__block-actions">
          <el-button text type="primary" size="small" @click="router.push('/kb')">
            + 新建
          </el-button>
          <el-button
            v-if="kbs.length > cards.length"
            text
            type="primary"
            size="small"
            @click="router.push('/kb')"
          >
            查看全部 →
          </el-button>
        </div>
      </div>

      <div v-if="loading" class="dash__skeleton-grid">
        <div v-for="i in 3" :key="i" class="dash__skeleton" />
      </div>
      <BlockError v-else-if="overviewError" @retry="load" />
      <div v-else-if="cards.length" class="dash__kb-grid">
        <KbCard
          v-for="kb in cards"
          :key="kb.id"
          :kb="kb"
          @open="router.push(`/kb/${$event}`)"
        />
      </div>
      <EmptyState v-else text="还没有知识库">
        <template #action>
          <el-button type="primary" size="small" @click="router.push('/kb')">
            新建知识库
          </el-button>
        </template>
      </EmptyState>
    </section>

    <!-- ── 区块 5：最近挖掘 ──────────────────────────────────────────── -->
    <section class="dash__block">
      <h3 class="dash__block-title">最近挖掘</h3>

      <div v-if="loading" class="dash__skeleton-list">
        <div v-for="i in 3" :key="i" class="dash__skeleton dash__skeleton--row" />
      </div>
      <BlockError v-else-if="overviewError" @retry="load" />
      <div v-else-if="recentRuns.length" class="dash__runs">
        <div
          v-for="run in recentRuns"
          :key="run.id"
          class="run-row"
          @click="router.push(`/kb/${run.kb_id}/run/${run.id}`)"
        >
          <span class="run-row__kb">{{ run.kb_name }}</span>
          <StatusBadge :status="run.status as never" size="small">
            {{ runStatusLabel(run.status) }}
          </StatusBadge>
          <span class="run-row__docs">{{ docDelta(run) }}</span>
          <span class="run-row__dur">{{ formatDuration(run.started_at, run.finished_at) }}</span>
          <!-- 「开始时间」用 started_at：mining_runs 根本没有 created_at 列 -->
          <span class="run-row__time">{{ formatTime(run.started_at) }}</span>
        </div>
      </div>
      <EmptyState v-else text="还没有挖掘记录" />
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, h, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElButton } from 'element-plus'
import { useDomainStore } from '@/stores/domain'
import { useKbApi } from '@/api/kb'
import {
  TOP_KB_BAR_LIMIT, documentStatusSlices, kbDocumentBars, pendingTasks,
  trendSeries, trendTotals, unitTypeBars, visibleKbCards,
} from '@/utils/dashboard'
import { runStatusLabel } from '@/utils/runStatus'
import type { KbOverviewItem, KbOverviewRun, KbStats } from '@/types/kb'
import KbCard from '@/components/dashboard/KbCard.vue'
import StatsCard from '@/components/common/StatsCard.vue'
import StatusBadge from '@/components/common/StatusBadge.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import PieChart from '@/components/charts/PieChart.vue'
import BarChart from '@/components/charts/BarChart.vue'
import LineChart from '@/components/charts/LineChart.vue'

/** 区块级失败提示。单块失败只在该块显示，不牵连其余。 */
const BlockError = (_: unknown, { emit }: { emit: (e: 'retry') => void }) => h(
  'div',
  { class: 'dash__block-error' },
  [
    '加载失败',
    h(ElButton, { text: true, type: 'primary', size: 'small', onClick: () => emit('retry') },
      () => '重试'),
  ],
)

const router = useRouter()
const domainStore = useDomainStore()
const kbApi = useKbApi()

const kbs = ref<KbOverviewItem[]>([])
const recentRuns = ref<KbOverviewRun[]>([])
const stats = ref<KbStats | null>(null)
const loading = ref(true)
/**
 * 两个数据源各记各的错。共用一个 error 的话，统计接口挂了会把知识库卡片和最近挖掘
 * 一起变成「加载失败」——那两块的数据其实好好地在手里。
 */
const overviewError = ref(false)
const statsError = ref(false)

const cards = computed(() => visibleKbCards(kbs.value))
const tasks = computed(() => pendingTasks(kbs.value))
const hasAnyKb = computed(() => (stats.value?.kb_count ?? kbs.value.length) > 0)

const statusSlices = computed(() => (stats.value ? documentStatusSlices(stats.value) : []))
const kbBars = computed(() => kbDocumentBars(kbs.value))
const unitBars = computed(() => unitTypeBars(stats.value?.retrieval_unit_types))
const trend = computed(() => trendSeries(stats.value))
const totals = computed(() => trendTotals(stats.value))
const trendDays = computed(() => stats.value?.trend_days ?? 30)

/**
 * 顶部数字条。
 *
 * 文档总数取 document_status 的六档之和而不是各库 total 相加：两个数来自不同查询，
 * 迟早会因为并发写入而对不上，而对不上的两个"文档总数"出现在同一屏是最伤信任的那种错。
 */
const tiles = computed(() => {
  const a = stats.value?.assets
  const s = stats.value?.document_status
  const docTotal = s ? Object.values(s).reduce((x, y) => x + y, 0) : 0
  return [
    { label: '知识库', value: stats.value?.kb_count ?? kbs.value.length, icon: '📚' },
    { label: '文档', value: docTotal, icon: '📄' },
    { label: '已挖掘', value: s?.mined ?? 0, icon: '✅' },
    { label: '检索单元', value: a?.retrieval_units ?? 0, icon: '🔎' },
    { label: '实体提及', value: a?.entity_mentions ?? 0, icon: '🏷' },
    { label: '切片关系', value: a?.relations ?? 0, icon: '🔗' },
  ]
})

/** 横向柱：每根 28px + 上下留白。固定高度会让 2 根柱子的图和 8 根的一样高。 */
function barHeight(count: number): string {
  return `${Math.max(140, count * 28 + 40)}px`
}

/**
 * 切域竞态守卫。`alive` 只挡 unmount，挡不住切域——组件还活着，只是数据属于
 * 上一个域了。两个请求共用一个 generation：它们同批发出、同批作废。
 *
 * 用 allSettled 而不是 all：一个接口挂掉不该把另一个已经成功的结果一起丢掉。
 * 但每个 rejected 都要落到自己的 error 旗标上——早先那版把失败整个吞了，
 * 用户看到的是一片空白，分不清「没有数据」和「没加载出来」。
 */
let generation = 0

async function load() {
  const gen = ++generation
  const domain = domainStore.currentDomain
  if (!domain) return
  loading.value = true
  overviewError.value = false
  statsError.value = false

  const [ov, st] = await Promise.allSettled([
    kbApi.getOverview(domain),
    kbApi.getStats(domain),
  ])
  if (gen !== generation) return   // 旧域的响应，整批丢弃

  if (ov.status === 'fulfilled') {
    kbs.value = ov.value.kbs
    recentRuns.value = ov.value.recent_runs
  } else {
    console.error('Failed to load overview:', ov.reason)
    overviewError.value = true
    kbs.value = []
    recentRuns.value = []
  }

  if (st.status === 'fulfilled') {
    stats.value = st.value
  } else {
    console.error('Failed to load kb stats:', st.reason)
    statsError.value = true
    stats.value = null
  }

  loading.value = false
}

function docDelta(run: KbOverviewRun): string {
  const parts: string[] = []
  if (run.new_count) parts.push(`+${run.new_count}`)
  if (run.updated_count) parts.push(`~${run.updated_count}`)
  return parts.length ? parts.join(' ') : String(run.total_documents ?? 0)
}

function formatTime(t: string | null): string {
  if (!t) return '-'
  const d = new Date(t)
  if (Number.isNaN(d.getTime())) return '-'
  return d.toLocaleString('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  })
}

function formatDuration(start?: string | null, end?: string | null): string {
  if (!start) return '-'
  const s = new Date(start).getTime()
  const e = end ? new Date(end).getTime() : Date.now()
  if (Number.isNaN(s) || Number.isNaN(e)) return '-'
  const diff = Math.round((e - s) / 1000)
  if (diff < 0) return '-'
  if (diff < 60) return `${diff}s`
  if (diff < 3600) return `${Math.floor(diff / 60)}m${diff % 60}s`
  return `${Math.floor(diff / 3600)}h${Math.floor((diff % 3600) / 60)}m`
}

onMounted(load)
// unmount 后到达的响应同样要丢弃：generation 递增即可让它作废
onUnmounted(() => { generation++ })
watch(() => domainStore.currentDomain, load)
</script>

<style scoped>
.dash {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

/* ── 通用区块 ── */
.dash__block {
  background: var(--kb-bg-card);
  border: 1px solid var(--kb-border-light);
  border-radius: var(--kb-radius);
  box-shadow: var(--kb-shadow-card);
  padding: 18px 20px;
}

.dash__block--pending {
  border-color: var(--kb-warning);
}

.dash__block-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 12px;
}

.dash__block-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--kb-text-secondary);
  letter-spacing: 0.5px;
  margin: 0 0 12px;
}

.dash__block-head .dash__block-title { margin: 0; }

.dash__block-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.dash__scope {
  font-size: 11px;
  color: var(--kb-text-tertiary);
  padding: 1px 8px;
  border: 1px solid var(--kb-border-light);
  border-radius: 10px;
  white-space: nowrap;
}

.dash__muted {
  font-size: 12px;
  color: var(--kb-text-tertiary);
  margin: 0;
  padding: 24px 0;
  text-align: center;
}

.dash__block-error {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 24px;
  font-size: 13px;
  color: var(--kb-text-tertiary);
}

/* ── 数字条 ── */
.dash__tiles {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 12px;
}

/* ── 图表网格 ── */
.dash__charts {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(360px, 1fr));
  gap: 16px;
}

/* 环形图 + 数值表并排。窄屏下表落到图下方，不挤成两条竖线。 */
.dash__donut {
  display: flex;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
}

/* 给图一个明确的伸缩容器，而不是靠 :deep 去够 PieChart 的根节点 */
.dash__donut-chart {
  flex: 1 1 200px;
  min-width: 180px;
}

.legend {
  list-style: none;
  margin: 0;
  padding: 0;
  flex: 0 1 auto;
  min-width: 132px;
}

.legend__row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 0;
  font-size: 12px;
}

.legend__dot {
  width: 8px;
  height: 8px;
  border-radius: 2px;
  flex-shrink: 0;
}

.legend__name {
  color: var(--kb-text-secondary);
}

.legend__value {
  margin-left: auto;
  color: var(--kb-text-primary);
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}

/* ── 待处理 ── */
.task {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 9px 4px;
  cursor: pointer;
  border-radius: var(--kb-radius-sm);
}
.task:hover { background: var(--kb-bg-hover, rgba(0, 0, 0, 0.03)); }

.task__kb {
  font-size: 13px;
  font-weight: 600;
  color: var(--kb-text-primary);
  min-width: 120px;
}

.task__text {
  flex: 1;
  font-size: 13px;
  color: var(--kb-text-secondary);
}

/* ── 知识库卡片 ── */
.dash__kb-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 12px;
}

/* ── 最近挖掘 ── */
.dash__runs {
  display: flex;
  flex-direction: column;
}

.run-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 9px 4px;
  cursor: pointer;
  border-radius: var(--kb-radius-sm);
  font-size: 13px;
}
.run-row:hover { background: var(--kb-bg-hover, rgba(0, 0, 0, 0.03)); }

.run-row__kb {
  font-weight: 500;
  color: var(--kb-text-primary);
  min-width: 120px;
}

.run-row__docs { color: var(--kb-text-secondary); min-width: 70px; }
.run-row__dur { color: var(--kb-text-tertiary); min-width: 60px; }
.run-row__time { color: var(--kb-text-tertiary); margin-left: auto; }

/* ── 骨架屏 ── */
.dash__skeleton-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 12px;
}

.dash__skeleton-grid--tiles {
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
}

.dash__skeleton-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.dash__skeleton {
  height: 84px;
  border-radius: var(--kb-radius);
  background: linear-gradient(
    90deg,
    var(--kb-border-light) 25%,
    transparent 50%,
    var(--kb-border-light) 75%
  );
  background-size: 200% 100%;
  animation: dash-shimmer 1.4s infinite;
}

.dash__skeleton--row { height: 34px; }
.dash__skeleton--tile { height: 66px; }
.dash__skeleton--chart { height: 220px; }

@keyframes dash-shimmer {
  0% { background-position: 200% 0; }
  100% { background-position: -200% 0; }
}
</style>
