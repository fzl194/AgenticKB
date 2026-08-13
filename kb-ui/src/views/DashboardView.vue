<template>
  <div class="dash">
    <!-- ── 区块 1：搜索 ───────────────────────────────────────────────── -->
    <section class="dash__search">
      <h2 class="dash__search-title">
        {{ kbs.length ? `在 ${kbs.length} 个知识库中搜索` : '知识检索' }}
      </h2>
      <div class="dash__search-bar">
        <el-input
          v-model="query"
          placeholder="搜索知识库内容…"
          size="large"
          clearable
          :disabled="!canSearch"
          @keyup.enter="goSearch"
        >
          <template #prefix><el-icon><Search /></el-icon></template>
        </el-input>
        <el-button type="primary" size="large" :disabled="!canSearch" @click="goSearch">
          搜索
        </el-button>
      </div>

      <div class="dash__search-scope">
        <template v-if="loading">
          <span class="dash__muted">正在准备检索范围…</span>
        </template>
        <template v-else-if="error">
          <span class="dash__warn">加载失败</span>
          <el-button text type="primary" size="small" @click="load">重试</el-button>
        </template>
        <template v-else-if="!kbs.length">
          <span class="dash__warn">你还没有可检索的知识库</span>
          <el-button text type="primary" size="small" @click="router.push('/kb')">
            去创建 →
          </el-button>
        </template>
        <template v-else>
          <span class="dash__muted">范围：我的全部知识库</span>
          <el-button text type="primary" size="small" @click="adjustScope">调整范围</el-button>
        </template>
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

    <!-- ── 区块 3：我的知识库 ────────────────────────────────────────── -->
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
      <BlockError v-else-if="error" @retry="load" />
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

    <!-- ── 区块 4：最近挖掘 ──────────────────────────────────────────── -->
    <section class="dash__block">
      <h3 class="dash__block-title">最近挖掘</h3>

      <div v-if="loading" class="dash__skeleton-list">
        <div v-for="i in 3" :key="i" class="dash__skeleton dash__skeleton--row" />
      </div>
      <BlockError v-else-if="error" @retry="load" />
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
          <!-- 「开始时间」用 started_at：mining_runs 根本没有 created_at 列（修 D2） -->
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
import { Search } from '@element-plus/icons-vue'
import { ElButton } from 'element-plus'
import { useDomainStore } from '@/stores/domain'
import { useKbApi } from '@/api/kb'
import { pendingTasks, searchTarget, visibleKbCards } from '@/utils/dashboard'
import { runStatusLabel } from '@/utils/runStatus'
import type { KbOverviewItem, KbOverviewRun } from '@/types/kb'
import KbCard from '@/components/dashboard/KbCard.vue'
import StatusBadge from '@/components/common/StatusBadge.vue'
import EmptyState from '@/components/common/EmptyState.vue'

/** 区块级失败提示。单块失败只在该块显示，不牵连其余（§4.4）。 */
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

const query = ref('')
const kbs = ref<KbOverviewItem[]>([])
const recentRuns = ref<KbOverviewRun[]>([])
const loading = ref(true)
const error = ref(false)

const cards = computed(() => visibleKbCards(kbs.value))
const tasks = computed(() => pendingTasks(kbs.value))
const canSearch = computed(() => !loading.value && !error.value && kbs.value.length > 0)

/**
 * 切域竞态守卫（修 D7）。
 *
 * 原来 loadData 对 stats 与 3 个 health 没有任何守卫，慢的旧域响应会覆盖新域的数字。
 * `alive` 只挡 unmount，挡不住切域——组件还活着，只是数据属于上一个域了。
 * 数据源已从 5 个降到 1 个（overview 聚合），竞态面本身也小了很多。
 */
let generation = 0

async function load() {
  const gen = ++generation
  const domain = domainStore.currentDomain
  if (!domain) return
  loading.value = true
  error.value = false
  try {
    const overview = await kbApi.getOverview(domain)
    if (gen !== generation) return   // 旧域的响应，丢弃
    kbs.value = overview.kbs
    recentRuns.value = overview.recent_runs
  } catch (e) {
    if (gen !== generation) return
    console.error('Failed to load overview:', e)
    // 以前 Promise.allSettled 把失败完全吞掉，用户看到的是一片空白——分不清
    // 「没有数据」和「没加载出来」。
    error.value = true
    kbs.value = []
    recentRuns.value = []
  } finally {
    if (gen === generation) loading.value = false
  }
}

function goSearch() {
  if (!canSearch.value || !query.value.trim()) return
  // 范围显式带上：/search 会照它还原，而不是自己再默认一遍
  router.push(searchTarget(query.value, kbs.value.map(kb => kb.id)))
}

/** 「调整范围」把人送到 /search，范围选择器在那儿（首页不复制一套）。 */
function adjustScope() {
  router.push(searchTarget(query.value, kbs.value.map(kb => kb.id)))
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

/* ── 搜索 ── */
.dash__search {
  background: var(--kb-bg-card);
  border: 1px solid var(--kb-border-light);
  border-radius: var(--kb-radius);
  box-shadow: var(--kb-shadow-card);
  padding: 36px 24px 28px;
  text-align: center;
}

.dash__search-title {
  font-size: 20px;
  font-weight: 600;
  color: var(--kb-text-primary);
  margin: 0 0 18px;
}

.dash__search-bar {
  display: flex;
  gap: 10px;
  max-width: 640px;
  margin: 0 auto;
}

.dash__search-bar :deep(.el-input) {
  flex: 1;
}

.dash__search-bar :deep(.el-input__wrapper) {
  border-radius: 10px;
  padding: 4px 16px;
}

.dash__search-scope {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  margin-top: 12px;
  font-size: 12px;
}

.dash__muted { color: var(--kb-text-tertiary); }
.dash__warn { color: var(--kb-warning); }

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
  gap: 4px;
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

@keyframes dash-shimmer {
  0% { background-position: 200% 0; }
  100% { background-position: -200% 0; }
}
</style>
