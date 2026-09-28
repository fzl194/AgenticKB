<template>
  <section class="ops">
    <div class="ops__head">
      <div>
        <h3 class="ops__title">检索概况</h3>
        <span class="ops__scope">近 {{ usage?.days ?? 7 }} 天 · 当前知识域</span>
      </div>
      <el-button text type="primary" size="small" @click="$emit('detail')">查看检索记录 →</el-button>
    </div>

    <div v-if="loading" class="ops__skeleton-row">
      <div v-for="i in 4" :key="i" class="ops__skeleton" />
    </div>
    <div v-else-if="error" class="ops__notice ops__notice--error">
      检索概况加载失败
      <el-button text type="primary" size="small" @click="$emit('retry')">重试</el-button>
    </div>
    <div v-else-if="!usage?.available" class="ops__notice ops__notice--info">
      <strong>尚无新检索记录</strong>
      <span>统一记录从本版本上线后开始产生，旧记录不会迁入。</span>
    </div>
    <template v-else>
      <div class="ops__tiles">
        <StatsCard label="调用量" :value="usage.summary.queries" icon="🔍" />
        <StatsCard label="零结果率" :value="formatRate(usage.summary.no_result_rate)" icon="🕳" />
        <StatsCard label="失败率" :value="formatRate(usage.summary.failed_rate ?? 0)" icon="⚠" />
        <StatsCard label="P95 延迟" :value="formatMs(usage.summary.p95_duration_ms)" icon="⏱" />
      </div>
      <div v-if="alertText" class="ops__alert">{{ alertText }}</div>
    </template>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { FAILURE_ALERT_THRESHOLD, formatMs, formatRate, shouldAlertNoResult } from '@/utils/opsStats'
import type { OpsUsage } from '@/types/ops'
import StatsCard from '@/components/common/StatsCard.vue'

const props = withDefaults(defineProps<{
  usage: OpsUsage | null
  loading?: boolean
  error?: boolean
}>(), { loading: false, error: false })

defineEmits<{ detail: []; retry: [] }>()

const alertText = computed(() => {
  const summary = props.usage?.summary
  if (!summary) return ''
  if ((summary.failed_rate ?? 0) >= FAILURE_ALERT_THRESHOLD) {
    return `失败率 ${formatRate(summary.failed_rate ?? 0)}，建议打开检索记录定位失败调用。`
  }
  if (shouldAlertNoResult(props.usage)) {
    return `零结果率 ${formatRate(summary.no_result_rate)}，建议打开检索记录查看需要补充的知识。`
  }
  return ''
})
</script>

<style scoped>
.ops { background: var(--kb-bg-card); border: 1px solid var(--kb-border-light); border-radius: var(--kb-radius); box-shadow: var(--kb-shadow-card); padding: 18px 20px; }
.ops__head { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 14px; }
.ops__title { margin: 0 0 4px; font-size: 13px; font-weight: 650; color: var(--kb-text-secondary); }
.ops__scope { font-size: 11px; color: var(--kb-text-tertiary); }
.ops__tiles, .ops__skeleton-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; }
.ops__alert { margin-top: 12px; padding: 10px 14px; border-radius: var(--kb-radius-sm); background: var(--kb-warning-soft); border: 1px solid var(--kb-warning); font-size: 13px; line-height: 1.6; }
.ops__notice { display: flex; align-items: center; gap: 6px; padding: 16px 0; font-size: 13px; color: var(--kb-text-tertiary); }
.ops__notice--error { color: var(--kb-danger); }
.ops__notice--info { flex-direction: column; align-items: flex-start; gap: 4px; padding: 14px 16px; background: var(--kb-accent-soft); border-radius: var(--kb-radius-sm); }
.ops__notice--info strong { color: var(--kb-text-primary); }
.ops__skeleton { height: 66px; border-radius: var(--kb-radius); background: var(--kb-border-light); }
</style>
