<template>
  <section class="ops">
    <div class="ops__head">
      <h3 class="ops__title">⚙ 运维概览</h3>
      <div class="ops__head-right">
        <!--
          口径必须写在脸上：这些数是"最近 N 天的检索流量"，与上面按知识库收敛的统计
          不是一个口径。不标出来，两组数字放在同一页会被当成同一件事的两种说法。
        -->
        <span class="ops__scope">近 {{ usage?.days ?? 7 }} 天检索流量 · 全域</span>
        <el-button text type="primary" size="small" @click="$emit('detail')">
          详情 →
        </el-button>
      </div>
    </div>

    <div v-if="loading" class="ops__skeleton-row">
      <div v-for="i in 4" :key="i" class="ops__skeleton ops__skeleton--tile" />
    </div>

    <div v-else-if="error" class="ops__notice ops__notice--error">
      运维数据加载失败
      <el-button text type="primary" size="small" @click="$emit('retry')">重试</el-button>
    </div>

    <!--
      「表不存在」与「用量为 0」必须分开说。serving 从没启动过时画一屏 0，会被读成
      「没人用系统」，真相是「还没有日志可统计」。同 设置→系统状态 对无 release 的处理。
    -->
    <div v-else-if="!usage?.available" class="ops__notice ops__notice--info">
      <strong>尚未产生检索日志</strong>
      <span>
        检索服务还没有写入过查询日志（serving_query_logs 不存在）。发生过检索之后，
        这里会显示检索量、答不上来的问题与各范式的调用情况。
      </span>
    </div>

    <template v-else>
      <div class="ops__tiles">
        <StatsCard label="检索次数" :value="usage.summary.queries" icon="🔍" />
        <StatsCard
          label="零结果率"
          :value="formatRate(usage.summary.no_result_rate)"
          icon="🕳"
        />
        <StatsCard
          label="P95 延迟"
          :value="formatMs(usage.summary.p95_duration_ms)"
          icon="⏱"
        />
        <StatsCard label="活跃范式" :value="usage.summary.active_paradigms" icon="🧩" />
      </div>

      <div v-if="alertNoResult" class="ops__alert">
        零结果率 {{ formatRate(usage.summary.no_result_rate) }} —— 用户有相当一部分问题没被答上，
        建议从下面的清单补充知识。
      </div>

      <!-- ── 答不上来的问题：整个区块里最该被看见的一段 ────────────────── -->
      <div class="ops__block">
        <h4 class="ops__subtitle">
          答不上来的问题
          <span class="ops__hint">来自检索日志的用户输入原文，仅管理员可见</span>
        </h4>
        <ul v-if="usage.no_result_queries.length" class="qlist">
          <li v-for="q in topNoResult" :key="q.query_text" class="qlist__row">
            <span class="qlist__text" :title="q.query_text">{{ q.query_text }}</span>
            <span class="qlist__count">{{ q.count }} 次</span>
          </li>
        </ul>
        <p v-else class="ops__muted">窗口内没有零结果查询</p>
      </div>

      <div class="ops__charts">
        <div class="ops__block">
          <h4 class="ops__subtitle">近 {{ usage.trend_days }} 天检索量</h4>
          <!-- 单系列不配图例：标题已说明这条线是什么 -->
          <LineChart
            :labels="trend.labels"
            :series="[{ name: '检索次数', data: trend.data }]"
            height="200px"
          />
        </div>

        <div class="ops__block">
          <h4 class="ops__subtitle">范式调用排行</h4>
          <BarChart
            v-if="paradigmData.length"
            :data="paradigmData"
            horizontal
            :height="barHeight(paradigmData.length)"
          />
          <p v-else class="ops__muted">窗口内没有范式调用</p>
        </div>
      </div>
    </template>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import {
  formatMs, formatRate, paradigmBars, shouldAlertNoResult, usageTrendSeries,
} from '@/utils/opsStats'
import type { OpsUsage } from '@/types/ops'
import StatsCard from '@/components/common/StatsCard.vue'
import BarChart from '@/components/charts/BarChart.vue'
import LineChart from '@/components/charts/LineChart.vue'

const props = withDefaults(defineProps<{
  usage: OpsUsage | null
  loading?: boolean
  error?: boolean
}>(), { loading: false, error: false })

defineEmits<{ detail: []; retry: [] }>()

/** 首页只列前 5 条；完整清单在 设置→系统状态。 */
const DASHBOARD_NO_RESULT_LIMIT = 5

const topNoResult = computed(
  () => (props.usage?.no_result_queries ?? []).slice(0, DASHBOARD_NO_RESULT_LIMIT),
)
const trend = computed(() => usageTrendSeries(props.usage))
const paradigmData = computed(() => paradigmBars(props.usage?.paradigms))
const alertNoResult = computed(() => shouldAlertNoResult(props.usage))

function barHeight(count: number): string {
  return `${Math.max(140, count * 28 + 40)}px`
}
</script>

<style scoped>
.ops {
  background: var(--kb-bg-card);
  border: 1px solid var(--kb-border-light);
  border-radius: var(--kb-radius);
  box-shadow: var(--kb-shadow-card);
  padding: 18px 20px;
}

.ops__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 14px;
}

.ops__head-right {
  display: flex;
  align-items: center;
  gap: 8px;
}

.ops__title {
  font-size: 13px;
  font-weight: 600;
  color: var(--kb-text-secondary);
  letter-spacing: 0.5px;
  margin: 0;
}

.ops__subtitle {
  display: flex;
  align-items: baseline;
  gap: 8px;
  font-size: 12px;
  font-weight: 600;
  color: var(--kb-text-secondary);
  margin: 0 0 10px;
}

.ops__hint {
  font-weight: 400;
  font-size: 11px;
  color: var(--kb-text-tertiary);
}

.ops__scope {
  font-size: 11px;
  color: var(--kb-text-tertiary);
  padding: 1px 8px;
  border: 1px solid var(--kb-border-light);
  border-radius: 10px;
  white-space: nowrap;
}

.ops__tiles {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 12px;
}

.ops__alert {
  margin-top: 12px;
  padding: 10px 14px;
  border-radius: var(--kb-radius-sm);
  background: var(--kb-warning-soft);
  border: 1px solid var(--kb-warning);
  font-size: 13px;
  color: var(--kb-text-primary);
  line-height: 1.6;
}

.ops__block {
  margin-top: 18px;
}

.ops__charts {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
  gap: 16px;
}

/* ── 查询清单 ── */
.qlist {
  list-style: none;
  margin: 0;
  padding: 0;
}

.qlist__row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 7px 4px;
  font-size: 13px;
  border-bottom: 1px solid var(--kb-border-light);
}

.qlist__row:last-child { border-bottom: none; }

.qlist__text {
  flex: 1;
  color: var(--kb-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.qlist__count {
  color: var(--kb-text-tertiary);
  font-variant-numeric: tabular-nums;
  flex-shrink: 0;
}

.ops__muted {
  font-size: 12px;
  color: var(--kb-text-tertiary);
  margin: 0;
  padding: 16px 0;
  text-align: center;
}

.ops__notice {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--kb-text-tertiary);
  padding: 16px 0;
}

.ops__notice--error { color: var(--kb-danger); }

.ops__notice--info {
  flex-direction: column;
  align-items: flex-start;
  gap: 4px;
  padding: 14px 16px;
  background: var(--kb-accent-soft);
  border-radius: var(--kb-radius-sm);
  line-height: 1.6;
}

.ops__notice--info strong { color: var(--kb-text-primary); }

/* ── 骨架屏 ── */
.ops__skeleton-row {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 12px;
}

.ops__skeleton {
  border-radius: var(--kb-radius);
  background: linear-gradient(
    90deg,
    var(--kb-border-light) 25%,
    transparent 50%,
    var(--kb-border-light) 75%
  );
  background-size: 200% 100%;
  animation: ops-shimmer 1.4s infinite;
}

.ops__skeleton--tile { height: 66px; }

@keyframes ops-shimmer {
  0% { background-position: 200% 0; }
  100% { background-position: -200% 0; }
}
</style>
