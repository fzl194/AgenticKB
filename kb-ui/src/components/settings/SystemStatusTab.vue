<template>
  <div class="sys-status">
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
import type { HealthStatus, KnowledgeStats } from '@/types'
import StatsCard from '@/components/common/StatsCard.vue'
import ServiceHealthCard from '@/components/common/ServiceHealthCard.vue'
import PieChart from '@/components/charts/PieChart.vue'

type Health = 'healthy' | 'degraded' | 'unhealthy' | 'unknown'

const domainStore = useDomainStore()
const miningApi = useMiningApi()
const servingApi = useServingApi()
const llmApi = useLlmApi()

const stats = ref<KnowledgeStats | null>(null)
const statsLoading = ref(false)
const statsError = ref(false)
const healthLoading = ref(false)

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
}

onMounted(loadAll)
onUnmounted(() => { statsGen++; healthGen++ })
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
</style>
