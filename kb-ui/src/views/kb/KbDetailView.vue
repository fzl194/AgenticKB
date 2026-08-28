<template>
  <div class="kb-detail-view">
    <!-- Header -->
    <div class="kb-detail-view__header">
      <div class="kb-detail-view__head-left">
        <router-link to="/kb" class="kb-back">
          <el-icon><ArrowLeft /></el-icon>
          <span>知识库</span>
        </router-link>
        <h2 class="kb-detail-view__title">{{ kb?.name || '加载中…' }}</h2>
        <el-tag
          v-if="kb"
          :type="visibilityTagType(kb.visibility)"
          size="small"
          effect="light"
        >
          {{ visibilityLabel(kb.visibility) }}
        </el-tag>
        <el-tag
          v-if="kb"
          :type="roleTagType(kb.my_role)"
          size="small"
          effect="plain"
        >
          我：{{ roleLabel(kb.my_role) }}
        </el-tag>
        <el-tag
          v-if="miningWorkflowId"
          size="small"
          type="success"
          effect="plain"
        >
          范式已绑定
        </el-tag>
      </div>
    </div>

    <p v-if="kb?.description" class="kb-detail-view__desc">{{ kb.description }}</p>

    <!-- 批次4：检索就绪度——这个库现在到底能搜什么，一眼可见 -->
    <div v-if="readiness" class="kb-detail-view__readiness">
      <div class="kb-detail-view__readiness-main">
        <span class="kb-detail-view__readiness-label">检索就绪度</span>
        <el-tag :type="readinessTagType(readiness.level)" effect="dark" size="small">
          {{ readinessLabel(readiness.level) }}
        </el-tag>
        <el-tooltip
          v-if="readiness.embedding_fallback"
          content="嵌入服务当时不可用，向量缺失已留痕；恢复后重新挖掘可补齐语义检索"
        >
          <el-tag type="warning" effect="plain" size="small">向量缺失已留痕</el-tag>
        </el-tooltip>
      </div>
      <div class="kb-detail-view__readiness-stats">
        <span>文档 {{ readiness.documents }}</span>
        <span>切片 {{ readiness.segments }}</span>
        <span>检索单元 {{ readiness.retrieval_units }}</span>
        <span>向量 {{ readiness.embeddings }}</span>
      </div>
    </div>

    <!-- Not found / no access -->
    <div v-if="!loading && !kb" class="kb-detail-view__missing">
      <EmptyState text="知识库不存在或无权访问" />
      <router-link to="/kb">
        <el-button type="primary">返回列表</el-button>
      </router-link>
    </div>

    <!-- Tabs -->
    <el-tabs v-if="kb" v-model="activeTab" class="kb-detail-view__tabs">
      <el-tab-pane label="文件" name="files">
        <KbFileManager
          :kb-id="kbId"
          :can-write="canWrite"
          :workflow-id="miningWorkflowId"
          :active="activeTab === 'files'"
          @mine-queued="onMineQueued"
        />
      </el-tab-pane>
      <el-tab-pane label="检索" name="search">
        <KbSearchPanel :kb="kb" :can-write="canWrite" :readiness="readiness" @updated="reload" />
      </el-tab-pane>
      <el-tab-pane label="成员" name="members">
        <KbMembersPanel :kb-id="kbId" :can-write="canWrite" :visibility="kb?.visibility ?? 'private'" />
      </el-tab-pane>
      <el-tab-pane label="挖掘" name="mining">
        <KbMiningPanel
          ref="miningPanelRef"
          :kb-id="kbId"
          v-model:selectedWorkflowId="miningWorkflowId"
          :can-write="canWrite"
        />
      </el-tab-pane>
      <el-tab-pane label="设置" name="settings">
        <KbSettingsPanel
          :kb="kb"
          :can-write="canWrite"
          @updated="reload"
          @deleted="onDeleted"
        />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ArrowLeft } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { useDomainStore } from '@/stores/domain'
import { useKbApi } from '@/api/kb'
import { apiErrorDetail } from '@/api/proxyClient'
import EmptyState from '@/components/common/EmptyState.vue'
import KbFileManager from '@/components/kb/KbFileManager.vue'
import KbSearchPanel from '@/components/kb/KbSearchPanel.vue'
import KbMembersPanel from '@/components/kb/KbMembersPanel.vue'
import KbMiningPanel from '@/components/kb/KbMiningPanel.vue'
import KbSettingsPanel from '@/components/kb/KbSettingsPanel.vue'
import { roleLabel, roleTagType, visibilityLabel, visibilityTagType } from '@/views/kb/kbMeta'
import type { KbReadiness, KbReadinessLevel, KbSummary } from '@/types/kb'

const props = defineProps<{ kbId: string }>()
const router = useRouter()
const domainStore = useDomainStore()
const kbApi = useKbApi()

const kb = ref<KbSummary | null>(null)
const readiness = ref<KbReadiness | null>(null)
const loading = ref(false)
const activeTab = ref<'files' | 'search' | 'members' | 'mining' | 'settings'>('files')
const miningPanelRef = ref<InstanceType<typeof KbMiningPanel> | null>(null)

/** 范式状态由父组件持有（单一真相源），修复旧版「按钮读列表快照 → 误报未选范式」的 Bug A。
 * KbMiningPanel 通过 v-model:selectedWorkflowId 双向同步；KbFileManager 批量挖掘前读它判断是否已设。 */
const miningWorkflowId = ref<string | null>(null)

const canWrite = computed(
  () => kb.value?.my_role === 'owner' || kb.value?.my_role === 'editor' || kb.value?.my_role === 'admin',
)

const READINESS_LABELS: Record<KbReadinessLevel, string> = {
  empty: '空库',
  parsed: '已解析',
  segmented: '已切片',
  lexical_ready: '关键词可搜',
  vector_ready: '语义可搜',
}

function readinessLabel(level: KbReadinessLevel): string {
  return READINESS_LABELS[level] ?? level
}

function readinessTagType(level: KbReadinessLevel): 'info' | 'warning' | 'success' | 'primary' {
  if (level === 'vector_ready') return 'success'
  if (level === 'lexical_ready') return 'primary'
  if (level === 'segmented') return 'warning'
  return 'info'
}

async function reload() {
  if (!domainStore.currentDomain) return
  loading.value = true
  try {
    // 列表保底（my_role/document_count 只在列表下发）；详情并行取 readiness。
    // 详情失败（如 404/网络抖动）不阻塞页面，readiness 静默置空。
    const [all, detail] = await Promise.all([
      kbApi.listKbs(domainStore.currentDomain),
      kbApi.getKb(props.kbId).catch(() => null),
    ])
    kb.value = all.find((k) => k.id === props.kbId) ?? null
    // 同步范式状态（列表快照 → 父组件权威 ref）
    miningWorkflowId.value = kb.value?.mining_workflow_id ?? null
    readiness.value = detail?.readiness ?? null
  } catch (e) {
    kb.value = null
    readiness.value = null
    ElMessage.error(await apiErrorDetail(e))
  } finally {
    loading.value = false
  }
}

/** 挖掘跑完后切回挖掘 tab 时刷新就绪度（不重拉整页）。 */
async function refreshReadiness() {
  try {
    const detail = await kbApi.getKb(props.kbId)
    readiness.value = detail?.readiness ?? null
  } catch {
    readiness.value = null
  }
}

/** 文件列表多选 → 批量挖掘成功后：切到挖掘 tab 并刷新任务列表/启动轮询。 */
function onMineQueued() {
  activeTab.value = 'mining'
  nextTick(() => miningPanelRef.value?.refresh())
}

function onDeleted() {
  ElMessage.success('知识库已删除')
  router.replace('/kb')
}

onMounted(reload)
watch(() => domainStore.currentDomain, reload)
watch(() => props.kbId, reload)
watch(activeTab, (tab) => {
  if (tab === 'mining') refreshReadiness()
})
</script>

<style scoped>
.kb-detail-view {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.kb-detail-view__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.kb-detail-view__head-left {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.kb-back {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: var(--kb-text-tertiary);
  text-decoration: none;
  font-size: 13px;
}
.kb-back:hover {
  color: var(--kb-accent);
}

.kb-detail-view__title {
  font-size: 16px;
  font-weight: 650;
  color: var(--kb-text-primary);
  margin: 0;
  letter-spacing: -0.2px;
}

.kb-detail-view__desc {
  margin: 0;
  color: var(--kb-text-secondary);
  font-size: 13px;
  line-height: 1.5;
}

.kb-detail-view__readiness {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  padding: 10px 14px;
  border: 1px solid var(--el-border-color-light);
  border-radius: 8px;
  background: var(--el-fill-color-extra-light);
}

.kb-detail-view__readiness-main {
  display: flex;
  align-items: center;
  gap: 8px;
}

.kb-detail-view__readiness-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--kb-text-secondary);
  letter-spacing: 0.5px;
}

.kb-detail-view__readiness-stats {
  display: flex;
  align-items: center;
  gap: 14px;
  font-size: 12px;
  color: var(--kb-text-tertiary);
  font-variant-numeric: tabular-nums;
}

.kb-detail-view__missing {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
}

.kb-detail-view__tabs :deep(.el-tabs__header) {
  margin-bottom: 12px;
}
</style>
