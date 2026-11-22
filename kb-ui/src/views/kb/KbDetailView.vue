<template>
  <div class="kb-detail-view">
    <!-- Header -->
    <div class="kb-detail-view__header">
      <div class="kb-detail-view__head-left">
        <router-link to="/kb" class="kb-back">
          <el-icon><ArrowLeft /></el-icon>
          <span>{{ t('title.kb') }}</span>
        </router-link>
        <h2 class="kb-detail-view__title">{{ kb?.name || t('common.loading') }}</h2>
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
          {{ t('kbView.role.my', { role: roleLabel(kb.my_role) }) }}
        </el-tag>
        <el-tag
          v-if="miningWorkflowId"
          size="small"
          type="success"
          effect="plain"
        >
          {{ t('kbView.detail.paradigmBound') }}
        </el-tag>
        <el-tooltip v-if="copySource" :content="t('kbView.detail.copiedAt', { time: formatCopyTime(copySource.copied_at) })">
          <el-tag size="small" type="info" effect="plain">{{ t('kbView.detail.copiedFrom', { name: copySource.kb_name }) }}</el-tag>
        </el-tooltip>
      </div>
    </div>

    <p v-if="kb?.description" class="kb-detail-view__desc">{{ kb.description }}</p>

    <!-- 批次4：检索就绪度——这个库现在到底能搜什么，一眼可见 -->
    <div v-if="readiness" class="kb-detail-view__readiness">
      <div class="kb-detail-view__readiness-main">
        <span class="kb-detail-view__readiness-label">{{ t('kbView.detail.readiness') }}</span>
        <el-tag :type="readinessTagType(readiness.level)" effect="dark" size="small">
          {{ readinessLabel(readiness.level) }}
        </el-tag>
        <el-tooltip
          v-if="readiness.embedding_fallback"
          :content="t('kbView.detail.embeddingFallbackTip')"
        >
          <el-tag type="warning" effect="plain" size="small">{{ t('kbView.detail.embeddingFallback') }}</el-tag>
        </el-tooltip>
      </div>
      <div class="kb-detail-view__readiness-stats">
        <span>{{ t('kbView.detail.readinessDocs', { n: readiness.documents }) }}</span>
        <span>{{ t('kbView.detail.readinessSegments', { n: readiness.segments }) }}</span>
        <span>{{ t('kbView.detail.readinessUnits', { n: readiness.retrieval_units }) }}</span>
        <span>{{ t('kbView.detail.readinessEmbeddings', { n: readiness.embeddings }) }}</span>
      </div>
    </div>

    <!-- v1.1 拷贝引导条：血缘已就位但范式未选——next_step_hint 语义的常驻化 -->
    <el-alert
      v-if="copySource && !miningWorkflowId"
      type="success"
      :closable="false"
      class="kb-detail-view__copy-banner"
    >
      <template #title>
        {{ t('kbView.detail.copyBanner', { name: copySource.kb_name }) }}
      </template>
      <el-button size="small" type="primary" @click="activeTab = 'mining'">
        {{ t('kbView.detail.chooseParadigm') }}
      </el-button>
    </el-alert>

    <!-- Not found / no access -->
    <div v-if="!loading && loadError" class="kb-detail-view__missing">
      <EmptyState :text="loadError" />
      <el-button type="primary" @click="reload">{{ t('common.retry') }}</el-button>
    </div>
    <div v-else-if="!loading && !kb" class="kb-detail-view__missing">
      <EmptyState :text="t('kbView.detail.notFound')" />
      <router-link to="/kb">
        <el-button type="primary">{{ t('kbView.detail.backToList') }}</el-button>
      </router-link>
    </div>

    <!-- Tabs -->
    <el-tabs v-if="kb" v-model="activeTab" class="kb-detail-view__tabs">
      <el-tab-pane :label="t('kbView.detail.tab.files')" name="files">
        <KbFileManager
          :kb-id="kbId"
          :can-write="canWrite"
          :workflow-id="miningWorkflowId"
          :active="activeTab === 'files'"
          @mine-queued="onMineQueued"
        />
      </el-tab-pane>
      <el-tab-pane :label="t('kbView.detail.tab.search')" name="search">
        <KbSearchWorkspace
          :kb="kb" :can-write="canWrite" :readiness="readiness"
          @updated="reload" @go-mining="activeTab = 'mining'"
        />
      </el-tab-pane>
      <el-tab-pane :label="t('kbView.detail.tab.members')" name="members">
        <KbMembersPanel :kb-id="kbId" :can-write="canWrite" :visibility="kb?.visibility ?? 'private'" />
      </el-tab-pane>
      <el-tab-pane :label="t('kbView.detail.tab.mining')" name="mining">
        <KbMiningPanel
          ref="miningPanelRef"
          :kb-id="kbId"
          v-model:selectedWorkflowId="miningWorkflowId"
          :can-write="canWrite"
        />
      </el-tab-pane>
      <el-tab-pane :label="t('kbView.detail.tab.quality')" name="quality">
        <KbQualityPanel :kb-id="kbId" :active="activeTab === 'quality'" />
      </el-tab-pane>
      <el-tab-pane :label="t('kbView.detail.tab.onenet')" name="onenet">
        <OnenetRefPanel :kb-id="kbId" :can-write="canWrite" :active="activeTab === 'onenet'" />
      </el-tab-pane>
      <el-tab-pane :label="t('kbView.detail.tab.settings')" name="settings">
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
import { useRoute, useRouter } from 'vue-router'
import { ArrowLeft } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { useDomainStore } from '@/stores/domain'
import { useKbApi } from '@/api/kb'
import { apiErrorDetail } from '@/api/proxyClient'
import { t } from '@/locales/t'
import EmptyState from '@/components/common/EmptyState.vue'
import KbFileManager from '@/components/kb/KbFileManager.vue'
import KbSearchWorkspace from '@/components/kb/KbSearchWorkspace.vue'
import KbMembersPanel from '@/components/kb/KbMembersPanel.vue'
import KbMiningPanel from '@/components/kb/KbMiningPanel.vue'
import KbQualityPanel from '@/components/kb/KbQualityPanel.vue'
import OnenetRefPanel from '@/components/kb/OnenetRefPanel.vue'
import KbSettingsPanel from '@/components/kb/KbSettingsPanel.vue'
import { canWriteKb, roleLabel, roleTagType, visibilityLabel, visibilityTagType } from '@/views/kb/kbMeta'
import type { KbReadiness, KbReadinessLevel, KbSummary } from '@/types/kb'

const props = defineProps<{ kbId: string }>()
const router = useRouter()
const route = useRoute()
const domainStore = useDomainStore()
const kbApi = useKbApi()

const kb = ref<KbSummary | null>(null)
const readiness = ref<KbReadiness | null>(null)
const loading = ref(false)
const loadError = ref('')
const activeTab = ref<'files' | 'search' | 'members' | 'mining' | 'quality' | 'onenet' | 'settings'>('files')
// A2：文档页「本节搜索」跳转带 tab=search + 范围参数（面板自行吸收）
{
  const tab = (route.query as Record<string, string | undefined>).tab
  const allowed = ['search', 'mining', 'members', 'quality', 'onenet', 'settings', 'files'] as const
  if (tab && (allowed as readonly string[]).includes(tab)) {
    activeTab.value = tab as typeof allowed[number]
  }
}
const miningPanelRef = ref<InstanceType<typeof KbMiningPanel> | null>(null)

/** 范式状态由父组件持有（单一真相源），修复旧版「按钮读列表快照 → 误报未选范式」的 Bug A。
 * KbMiningPanel 通过 v-model:selectedWorkflowId 双向同步；KbFileManager 批量挖掘前读它判断是否已设。 */
const miningWorkflowId = ref<string | null>(null)
let reloadGeneration = 0

const canWrite = computed(
  () => kb.value ? canWriteKb(kb.value.my_role) : false,
)

/** v1.1 拷贝溯源（详情 metadata_json.copy_source；非拷贝库为 null）。 */
const copySource = computed(() => kb.value?.metadata_json?.copy_source ?? null)

function formatCopyTime(time: string): string {
  return time ? new Date(time).toLocaleString('zh-CN') : ''
}

/** 就绪度档位 → 文案 key（表里只存 key，调用期经 t() 翻译——R8） */
const READINESS_LABEL_KEYS: Record<KbReadinessLevel, string> = {
  empty: 'kbView.detail.readinessLevel.empty',
  parsed: 'kbView.detail.readinessLevel.parsed',
  segmented: 'kbView.detail.readinessLevel.segmented',
  lexical_ready: 'kbView.detail.readinessLevel.lexical_ready',
  vector_ready: 'kbView.detail.readinessLevel.vector_ready',
}

function readinessLabel(level: KbReadinessLevel): string {
  return t(READINESS_LABEL_KEYS[level] ?? level)
}

function readinessTagType(level: KbReadinessLevel): 'info' | 'warning' | 'success' | 'primary' {
  if (level === 'vector_ready') return 'success'
  if (level === 'lexical_ready') return 'primary'
  if (level === 'segmented') return 'warning'
  return 'info'
}

async function reload() {
  const domain = domainStore.currentDomain
  const kbId = props.kbId
  if (!domain) return
  const generation = ++reloadGeneration
  loading.value = true
  loadError.value = ''
  try {
    // 列表提供 my_role/document_count，详情提供默认检索范式和 readiness；
    // 两者合并后再渲染，避免使用过期的列表快照。
    const [all, detail] = await Promise.all([
      kbApi.listKbs(domain),
      kbApi.getKb(kbId),
    ])
    if (generation !== reloadGeneration || domain !== domainStore.currentDomain || kbId !== props.kbId) return
    const summary = all.find((k) => k.id === kbId) ?? null
    kb.value = summary ? { ...summary, ...detail, my_role: summary.my_role } : null
    // 同步范式状态（列表快照 → 父组件权威 ref）
    miningWorkflowId.value = kb.value?.mining_workflow_id ?? null
    readiness.value = detail?.readiness ?? null
  } catch (e) {
    if (generation !== reloadGeneration || domain !== domainStore.currentDomain || kbId !== props.kbId) return
    kb.value = null
    readiness.value = null
    loadError.value = await apiErrorDetail(e)
  } finally {
    if (generation === reloadGeneration) loading.value = false
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
  ElMessage.success(t('kbView.detail.deleted'))
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

.kb-detail-view__copy-banner :deep(.el-alert__content) {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.kb-detail-view__copy-banner :deep(.el-alert__title) {
  flex: 1;
  min-width: 260px;
  font-size: 12.5px;
  line-height: 1.6;
  font-weight: 500;
}

.kb-detail-view__tabs :deep(.el-tabs__header) {
  margin-bottom: 12px;
}
</style>
