<template>
  <div class="kb-list">
    <!-- Header -->
    <div class="kb-list__header">
      <div class="kb-list__header-left">
        <span class="kb-list__count">{{ t('kbView.list.kbCount', { n: kbs.length }) }}</span>
        <span class="kb-list__domain">@ {{ domainStore.currentDomain }}</span>
      </div>
      <div class="kb-list__actions">
        <el-button :loading="loading" @click="load">
          <el-icon><Refresh /></el-icon>
        </el-button>
        <el-button type="primary" @click="showCreate = true">
          <el-icon class="el-icon--left"><Plus /></el-icon>
          {{ t('dashboard.action.newKb') }}
        </el-button>
      </div>
    </div>

    <!-- Cards -->
    <div v-loading="loading" class="kb-list__grid">
      <div
        v-for="kb in kbs"
        :key="kb.id"
        class="kb-card"
        :class="{ 'kb-card--ro': !canWrite(kb) }"
        @click="enter(kb)"
      >
        <div class="kb-card__top">
          <div class="kb-card__icon" :class="`kb-card__icon--${kb.visibility}`">
            <el-icon :size="20"><Collection /></el-icon>
          </div>
          <div class="kb-card__head">
            <div class="kb-card__name" :title="kb.name">{{ kb.name }}</div>
            <div class="kb-card__tags">
              <el-tag :type="visibilityTagType(kb.visibility)" size="small" effect="light">
                {{ visibilityLabel(kb.visibility) }}
              </el-tag>
              <el-tag :type="roleTagType(kb.my_role)" size="small" effect="plain">
                {{ t('kbView.role.my', { role: roleLabel(kb.my_role) }) }}
              </el-tag>
            </div>
          </div>
        </div>

        <p class="kb-card__desc">{{ kb.description || t('kbView.list.noDescription') }}</p>

        <div class="kb-card__meta">
          <span class="kb-card__meta-item">
            <el-icon><Document /></el-icon>{{ t('kbView.list.docCount', { n: kb.document_count }) }}
          </span>
          <span class="kb-card__meta-item">{{ t('kbView.list.creator', { name: kb.owner_name || '—' }) }}</span>
          <span class="kb-card__meta-item">{{ formatDate(kb.created_at) }}</span>
        </div>

        <div class="kb-card__footer" @click.stop>
          <el-button
            size="small"
            type="primary"
            :disabled="!canWrite(kb)"
            :loading="miningId === kb.id"
            @click="mine(kb)"
          >
            <el-icon class="el-icon--left"><Cpu /></el-icon>{{ t('kbView.list.mine') }}
          </el-button>
          <el-dropdown trigger="click" @click.stop>
            <el-button size="small" text><el-icon><MoreFilled /></el-icon></el-button>
            <template #dropdown>
              <el-dropdown-menu>
                <!-- v1.1 整库拷贝：read 即可拷（成员/公开库均可起步） -->
                <el-dropdown-item @click="copy(kb)">
                  <el-icon><CopyDocument /></el-icon>{{ t('kbView.list.copy') }}
                </el-dropdown-item>
                <!-- 整库导出下载：read 即可导（copy 超 5000 文档的互补通道） -->
                <el-dropdown-item @click="exportZip(kb)">{{ t('kbView.list.exportDownload') }}</el-dropdown-item>
                <el-dropdown-item v-if="canWrite(kb)" @click="rename(kb)">{{ t('kbView.list.rename') }}</el-dropdown-item>
                <el-dropdown-item v-if="canManageLifecycle(kb)" @click="remove(kb)" divided>{{ t('kbView.list.deleteKb') }}</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </div>

      <!-- 删除中的知识库（后台任务进度；owner/admin 可见） -->
      <div v-if="runningPurgeTasks.length" class="kb-purging">
        <div v-for="task in runningPurgeTasks" :key="task.id" class="kb-purging__row">
          <el-icon class="is-loading"><Loading /></el-icon>
          <span class="kb-purging__name">{{ task.kb_name }}</span>
          <span class="kb-purging__phase">{{ purgePhaseLabel(task) }}</span>
          <el-progress v-if="purgePercent(task) !== null" :percentage="purgePercent(task) ?? 0"
                       style="width: 180px" :stroke-width="8" />
          <span class="kb-purging__meta">{{ purgeProgressText(task) }}</span>
        </div>
      </div>
      <!-- 删除失败的任务（可重删） -->
      <el-alert v-if="failedPurgeTasks.length" type="error" :closable="false">
        <div v-for="task in failedPurgeTasks" :key="task.id">
          {{ t('kbView.list.purgeFailed', {
            name: task.kb_name,
            reason: task.error ?? t('kbView.list.unknownReason'),
          }) }}
        </div>
      </el-alert>

      <!-- 系统管理员或当前域管理员：恢复/清理本域软删除库。 -->
      <el-collapse v-if="canManageDomainKbs && deletedKbs.length" class="kb-deleted">
        <el-collapse-item :title="t('kbView.list.deletedTitle', { n: deletedKbs.length })">
          <div v-for="dkb in deletedKbs" :key="dkb.id" class="kb-deleted__row">
            <span class="kb-deleted__name">{{ dkb.name }}</span>
            <span class="kb-deleted__meta">{{ t('kbView.list.deletedAt', { date: formatDate(dkb.deleted_at ?? '') }) }}</span>
            <el-button size="small" plain
                       :loading="restoringId === dkb.id"
                       @click="restoreDeleted(dkb)">{{ t('kbView.list.restore') }}</el-button>
            <el-button size="small" type="danger" plain
                       :loading="purgingId === dkb.id"
                       @click="removeDeleted(dkb)">{{ t('kbView.list.purge') }}</el-button>
          </div>
        </el-collapse-item>
      </el-collapse>

      <EmptyState
        v-if="!loading && !loadError && !kbs.length"
        :text="t('kbView.list.empty')"
      />
      <div v-if="!loading && loadError" class="kb-list__error">
        <span>{{ loadError }}</span>
        <el-button size="small" @click="load">{{ t('common.retry') }}</el-button>
      </div>
    </div>

    <KbCreateDialog v-model="showCreate" :domain="domainStore.currentDomain" @created="load" />
    <KbCopyDialog v-model="showCopy" :kb="copyTarget" @copied="onCopied" />
    <KbExportDialog v-model="showExport" :kb="exportTarget" />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { Collection, CopyDocument, Cpu, Document, Loading, MoreFilled, Plus, Refresh } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useDomainStore } from '@/stores/domain'
import { useAuthStore } from '@/stores/auth'
import { canManageDomainKnowledgeBases } from '@/utils/domainPermissions'
import { useKbApi } from '@/api/kb'
import type { DeletedKbRow, KbPurgeTask } from '@/types/kb'
import { apiErrorDetail } from '@/api/proxyClient'
import { t } from '@/locales/t'
import EmptyState from '@/components/common/EmptyState.vue'
import KbCreateDialog from '@/components/kb/KbCreateDialog.vue'
import KbCopyDialog from '@/components/kb/KbCopyDialog.vue'
import KbExportDialog from '@/components/kb/KbExportDialog.vue'
import {
  canManageKbLifecycle, canWriteKb, roleLabel, roleTagType,
  visibilityLabel, visibilityTagType,
} from '@/views/kb/kbMeta'
import type { KbSummary } from '@/types/kb'

const router = useRouter()
const domainStore = useDomainStore()
const kbApi = useKbApi()

const kbs = ref<KbSummary[]>([])
const loading = ref(false)
const loadError = ref('')
const showCreate = ref(false)
const showCopy = ref(false)
const copyTarget = ref<KbSummary | null>(null)
const showExport = ref(false)
const exportTarget = ref<KbSummary | null>(null)
const miningId = ref<string | null>(null)
let loadGeneration = 0

function canWrite(kb: KbSummary): boolean {
  return canWriteKb(kb.my_role)
}

const authStore = useAuthStore()
const canManageDomainKbs = computed(() =>
  canManageDomainKnowledgeBases(authStore.siteRole, domainStore.currentDomainInfo),
)
const deletedKbs = ref<DeletedKbRow[]>([])
const purgingId = ref('')
const restoringId = ref('')
const purgeTasks = ref<KbPurgeTask[]>([])
let purgeTimer: ReturnType<typeof setInterval> | null = null

const runningPurgeTasks = computed(() =>
  purgeTasks.value.filter((task) => task.status === 'queued' || task.status === 'running'))
const failedPurgeTasks = computed(() =>
  purgeTasks.value.filter((task) => task.status === 'failed'))

/** 阶段 → 文案 key（表里只存 key，调用期经 t() 翻译） */
const PURGE_PHASE_LABEL_KEYS: Record<string, string> = {
  queued: 'common.runStatus.queued',
  prepare: 'kbView.list.purgePhase.prepare',
  documents: 'kbView.list.purgePhase.documents',
  snapshots: 'kbView.list.purgePhase.snapshots',
  objects: 'kbView.list.purgePhase.objects',
  finalize: 'kbView.list.purgePhase.finalize',
}

function purgePhaseLabel(task: KbPurgeTask): string {
  const key = PURGE_PHASE_LABEL_KEYS[task.phase]
  return key ? t(key) : task.phase
}

/** 快照阶段有 total/reclaimed 可算百分比；其余阶段显示计数 */
function purgePercent(task: KbPurgeTask): number | null {
  if (task.phase === 'snapshots' && task.progress.snapshots_total > 0) {
    return Math.min(99, Math.round(
      (task.progress.snapshots_reclaimed ?? 0) / task.progress.snapshots_total * 100))
  }
  return null
}

function purgeProgressText(task: KbPurgeTask): string {
  const p = task.progress ?? {}
  if (task.phase === 'snapshots') {
    return t('kbView.list.purgeProgress.snapshots', {
      done: p.snapshots_reclaimed ?? 0, total: p.snapshots_total ?? '?',
    })
  }
  if (task.phase === 'objects') {
    return t('kbView.list.purgeProgress.objects', {
      done: p.objects_reclaimed ?? 0, total: p.objects_total ?? '?',
    })
  }
  if (task.phase === 'finalize') return t('kbView.list.purgeFinishing')
  return ''
}

async function pollPurgeTasks() {
  const domain = domainStore.currentDomain
  if (!domain) return
  try {
    purgeTasks.value = await kbApi.purgeTasks(domain)
    if (purgeTasks.value.some((task) => task.status === 'done')
        && runningPurgeTasks.value.length === 0) {
      await load()
    }
  } catch { /* 轮询失败静默 */ }
}

function startPurgePolling() {
  if (purgeTimer) return
  purgeTimer = setInterval(() => {
    if (!runningPurgeTasks.value.length) return   // 无进行中任务不拉
    void pollPurgeTasks()
  }, 5000)
}

onUnmounted(() => { if (purgeTimer) clearInterval(purgeTimer) })

async function load() {
  const domain = domainStore.currentDomain
  if (!domain) return
  const generation = ++loadGeneration
  loading.value = true
  loadError.value = ''
  if (canManageDomainKbs.value) {
    try {
      const deleted = await kbApi.listDeletedKbs(domain)
      if (generation === loadGeneration) deletedKbs.value = deleted
    } catch { if (generation === loadGeneration) deletedKbs.value = [] }
  } else if (generation === loadGeneration) {
    deletedKbs.value = []
  }
  if (generation === loadGeneration) {
    try { purgeTasks.value = await kbApi.purgeTasks(domain) }
    catch { purgeTasks.value = [] }
    if (runningPurgeTasks.value.length) startPurgePolling()
  }
  try {
    const result = await kbApi.listKbs(domain)
    if (generation !== loadGeneration || domain !== domainStore.currentDomain) return
    kbs.value = result
  } catch (e) {
    if (generation !== loadGeneration || domain !== domainStore.currentDomain) return
    kbs.value = []
    loadError.value = await apiErrorDetail(e)
  } finally {
    if (generation === loadGeneration) loading.value = false
  }
}

function canManageLifecycle(kb: KbSummary): boolean {
  return canManageKbLifecycle(kb.my_role)
}

function enter(kb: KbSummary) {
  router.push(`/kb/${kb.id}`)
}

/** v1.1 整库拷贝：read 即可拷（快照血缘共享，拷完自选范式挖掘）。 */
function copy(kb: KbSummary) {
  copyTarget.value = kb
  showCopy.value = true
}

async function onCopied(result: { id: string }) {
  await load()
  // 跳新库详情——顶部引导条驱动选范式（next_step_hint 语义）
  router.push(`/kb/${result.id}`)
}

/** 整库导出下载（copy 超 5000 文档的互补通道）：read 即可导。 */
function exportZip(kb: KbSummary) {
  exportTarget.value = kb
  showExport.value = true
}

async function mine(kb: KbSummary) {
  miningId.value = kb.id
  try {
    const res = await kbApi.mineKb(kb.id)
    ElMessage.success(t('kbView.list.mineQueued', { id: res.run_id.slice(0, 8) }))
    await load()
  } catch (e) {
    ElMessage.error(await apiErrorDetail(e))
  } finally {
    miningId.value = null
  }
}

async function rename(kb: KbSummary) {
  let name: string
  try {
    const r = await ElMessageBox.prompt(t('kbView.list.newName'), t('kbView.list.renameTitle'), {
      inputValue: kb.name, confirmButtonText: t('common.save'), cancelButtonText: t('common.cancel'),
      inputValidator: (v) => !!v?.trim() || t('kbView.list.nameRequired'),
    })
    name = r.value.trim()
  } catch { return }
  try {
    await kbApi.updateKb(kb.id, { name })
    ElMessage.success(t('kbView.list.renamed'))
    await load()
  } catch (e) { ElMessage.error(await apiErrorDetail(e)) }
}

async function remove(kb: KbSummary) {
  // 硬删确认：输入库全名（GitHub 风格——整库不可逆操作的最高门槛）
  let name = ''
  try {
    const { value } = await ElMessageBox.prompt(
      t('kbView.list.deletePrompt', { name: kb.name }),
      t('kbView.list.deleteTitle'),
      {
        type: 'warning',
        confirmButtonText: t('common.deletePermanently'),
        cancelButtonText: t('common.cancel'),
        confirmButtonClass: 'el-button--danger',
        inputValidator: (v) => v?.trim() === kb.name || t('kbView.list.nameMismatch'),
      },
    )
    name = value.trim()
  } catch { return }
  try {
    await kbApi.deleteKb(kb.id, name)
    ElMessage.success(t('kbView.list.deleteStarted'))
    startPurgePolling()
    await load()
  } catch (e) { ElMessage.error(await apiErrorDetail(e)) }
}

/** 已删库（软删态存量）彻底清理：同样要求输入库全名 */
async function removeDeleted(dkb: DeletedKbRow) {
  let name = ''
  try {
    const { value } = await ElMessageBox.prompt(
      t('kbView.list.purgePrompt', { name: dkb.name }),
      t('kbView.list.purge'),
      {
        type: 'warning', confirmButtonText: t('kbView.list.purge'), cancelButtonText: t('common.cancel'),
        inputValidator: (v) => v?.trim() === dkb.name || t('kbView.list.nameMismatch'),
      },
    )
    name = value.trim()
  } catch { return }
  purgingId.value = dkb.id
  try {
    await kbApi.deleteKb(dkb.id, name)
    ElMessage.success(t('kbView.list.purgeStarted'))
    startPurgePolling()
    await load()
  } catch (e) { ElMessage.error(await apiErrorDetail(e)) }
  finally { purgingId.value = '' }
}

async function restoreDeleted(dkb: DeletedKbRow) {
  try {
    await ElMessageBox.confirm(
      t('kbView.list.restorePrompt', { name: dkb.name }),
      t('kbView.list.restoreTitle'),
      { type: 'warning', confirmButtonText: t('kbView.list.restore'), cancelButtonText: t('common.cancel') },
    )
  } catch { return }
  restoringId.value = dkb.id
  try {
    await kbApi.restoreKb(dkb.id)
    ElMessage.success(t('kbView.list.restored'))
    await load()
  } catch (e) { ElMessage.error(await apiErrorDetail(e)) }
  finally { restoringId.value = '' }
}

function formatDate(value: string): string {
  if (!value) return '-'
  return new Date(value).toLocaleDateString('zh-CN')
}

onMounted(load)
watch(() => domainStore.currentDomain, load)
</script>

<style scoped>
.kb-list { display: flex; flex-direction: column; gap: 14px; }

.kb-list__header {
  display: flex; align-items: center; justify-content: space-between;
}
.kb-list__header-left { display: flex; align-items: baseline; gap: 10px; }
.kb-list__title {
  font-size: 16px; font-weight: 650; color: var(--kb-text-primary);
  margin: 0; letter-spacing: -0.2px;
}
.kb-list__count { font-size: 12px; color: var(--kb-text-tertiary); }
.kb-list__domain {
  font-size: 12px; color: var(--kb-text-tertiary);
  font-family: 'SF Mono', 'Cascadia Code', monospace;
}
.kb-list__actions { display: flex; gap: 8px; }

/* Card grid */
.kb-list__grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(290px, 1fr));
  gap: 14px;
  min-height: 120px;
}

.kb-card {
  display: flex; flex-direction: column; gap: 10px;
  background: var(--kb-bg-card); border: 1px solid var(--kb-border-light);
  border-radius: var(--kb-radius); box-shadow: var(--kb-shadow-card);
  padding: 18px; cursor: pointer;
  transition: all var(--kb-duration) var(--kb-ease);
}
.kb-card:hover {
  border-color: var(--kb-accent-medium);
  transform: translateY(-2px);
  box-shadow: 0 6px 20px rgba(0, 0, 0, 0.08);
}
.kb-card--ro { cursor: pointer; } /* viewer 也能进入，只是没有写操作按钮 */

.kb-card__top { display: flex; gap: 12px; align-items: flex-start; }
.kb-card__icon {
  width: 40px; height: 40px; border-radius: 10px; flex-shrink: 0;
  display: flex; align-items: center; justify-content: center; color: #fff;
  background: var(--kb-accent);
}
.kb-card__icon--private { background: var(--kb-danger); }
.kb-card__icon--public { background: var(--kb-success); }

.kb-card__head { flex: 1; min-width: 0; }
.kb-card__name {
  font-size: 15px; font-weight: 650; color: var(--kb-text-primary);
  line-height: 1.3; margin-bottom: 6px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.kb-card__tags { display: flex; gap: 6px; flex-wrap: wrap; }

.kb-card__desc {
  margin: 0; font-size: 12.5px; color: var(--kb-text-secondary);
  line-height: 1.5; min-height: 38px;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
  overflow: hidden;
}

.kb-card__meta {
  display: flex; align-items: center; gap: 14px;
  font-size: 12px; color: var(--kb-text-tertiary);
  padding-top: 8px; border-top: 1px dashed var(--kb-border-light);
}
.kb-card__meta-item { display: inline-flex; align-items: center; gap: 4px; }

.kb-card__footer {
  display: flex; align-items: center; gap: 8px; margin-top: 2px;
}
</style>

.kb-purging { display: flex; flex-direction: column; gap: 6px;
  padding: 10px 14px; border: 1px dashed var(--el-color-warning);
  border-radius: 8px; background: var(--el-color-warning-light-9); }
.kb-purging__row { display: flex; align-items: center; gap: 10px; }
.kb-purging__name { font-weight: 600; }
.kb-purging__phase { color: var(--el-color-warning); font-size: 13px; }
.kb-purging__meta { color: var(--el-text-color-secondary); font-size: 12px; }
.kb-deleted__row { display: flex; align-items: center; gap: 10px; }
.kb-deleted__name { font-weight: 500; }
.kb-deleted__meta { color: var(--el-text-color-secondary); font-size: 12px; flex: 1; }
