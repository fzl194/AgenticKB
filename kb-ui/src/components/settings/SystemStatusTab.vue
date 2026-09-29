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
      <!-- 重启完成后刷新上方的健康卡。仅 site-admin 渲染（组件内部判定）。 -->
      <ServiceRestartCard @restarted="loadHealth" />
    </section>
  </div>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from 'vue'
import { useDomainStore } from '@/stores/domain'
import { useMiningApi } from '@/api/mining'
import { useServingApi } from '@/api/serving'
import { useLlmApi } from '@/api/llm'
import type { HealthStatus } from '@/types'
import ServiceHealthCard from '@/components/common/ServiceHealthCard.vue'
import ServiceRestartCard from '@/components/settings/ServiceRestartCard.vue'

type Health = 'healthy' | 'degraded' | 'unhealthy' | 'unknown'

const domainStore = useDomainStore()
const miningApi = useMiningApi()
const servingApi = useServingApi()
const llmApi = useLlmApi()

const healthLoading = ref(false)

const services = ref([
  { key: 'mining', name: '挖掘服务', icon: '⚙', status: 'unknown' as Health, detail: '' },
  { key: 'serving', name: '检索服务', icon: '🔍', status: 'unknown' as Health, detail: '' },
  { key: 'llm', name: 'LLM服务', icon: '🤖', status: 'unknown' as Health, detail: '' },
])

// 切域竞态守卫：慢的旧域健康结果不得覆盖新域的卡片。
let healthGen = 0

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
  if (!domainStore.currentDomain) {
    healthGen++
    healthLoading.value = false
    return
  }
  loadHealth()
}

onMounted(loadAll)
onUnmounted(() => { healthGen++ })
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

.sys-status__health {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 14px;
}
</style>
