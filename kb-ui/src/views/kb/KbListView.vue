<template>
  <div class="kb-list">
    <!-- Header -->
    <div class="kb-list__header">
      <div class="kb-list__header-left">
        <span class="kb-list__count">{{ kbs.length }} 个</span>
        <span class="kb-list__domain">@ {{ domainStore.currentDomain }}</span>
      </div>
      <div class="kb-list__actions">
        <el-button :loading="loading" @click="load">
          <el-icon><Refresh /></el-icon>
        </el-button>
        <el-button type="primary" @click="showCreate = true">
          <el-icon class="el-icon--left"><Plus /></el-icon>
          新建知识库
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
                我：{{ roleLabel(kb.my_role) }}
              </el-tag>
            </div>
          </div>
        </div>

        <p class="kb-card__desc">{{ kb.description || '暂无描述' }}</p>

        <div class="kb-card__meta">
          <span class="kb-card__meta-item">
            <el-icon><Document /></el-icon>{{ kb.document_count }} 个文档
          </span>
          <span class="kb-card__meta-item">创建者：{{ kb.owner_name || '—' }}</span>
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
            <el-icon class="el-icon--left"><Cpu /></el-icon>挖掘
          </el-button>
          <el-dropdown v-if="canWrite(kb)" trigger="click" @click.stop>
            <el-button size="small" text><el-icon><MoreFilled /></el-icon></el-button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item @click="rename(kb)">改名</el-dropdown-item>
                <el-dropdown-item v-if="canManageLifecycle(kb)" @click="remove(kb)" divided>删除知识库</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </div>

      <!-- 删除中的知识库（后台任务进度；owner/admin 可见） -->
      <div v-if="runningPurgeTasks.length" class="kb-purging">
        <div v-for="t in runningPurgeTasks" :key="t.id" class="kb-purging__row">
          <el-icon class="is-loading"><Loading /></el-icon>
          <span class="kb-purging__name">{{ t.kb_name }}</span>
          <span class="kb-purging__phase">{{ purgePhaseLabel(t) }}</span>
          <el-progress v-if="purgePercent(t) !== null" :percentage="purgePercent(t) ?? 0"
                       style="width: 180px" :stroke-width="8" />
          <span class="kb-purging__meta">{{ purgeProgressText(t) }}</span>
        </div>
      </div>
      <!-- 删除失败的任务（可重删） -->
      <el-alert v-if="failedPurgeTasks.length" type="error" :closable="false">
        <div v-for="t in failedPurgeTasks" :key="t.id">
          「{{ t.kb_name }}」删除失败：{{ t.error ?? '未知原因' }}——可重新发起删除
        </div>
      </el-alert>

      <!-- 系统管理员或当前域管理员：恢复/清理本域软删除库。 -->
      <el-collapse v-if="canManageDomainKbs && deletedKbs.length" class="kb-deleted">
        <el-collapse-item :title="`已删除的知识库（${deletedKbs.length}）`">
          <div v-for="dkb in deletedKbs" :key="dkb.id" class="kb-deleted__row">
            <span class="kb-deleted__name">{{ dkb.name }}</span>
            <span class="kb-deleted__meta">删除于 {{ formatDate(dkb.deleted_at ?? '') }}</span>
            <el-button size="small" plain
                       :loading="restoringId === dkb.id"
                       @click="restoreDeleted(dkb)">恢复</el-button>
            <el-button size="small" type="danger" plain
                       :loading="purgingId === dkb.id"
                       @click="removeDeleted(dkb)">彻底删除</el-button>
          </div>
        </el-collapse-item>
      </el-collapse>

      <EmptyState
        v-if="!loading && !loadError && !kbs.length"
        text="当前域还没有知识库，点击右上角「新建知识库」开始"
      />
      <div v-if="!loading && loadError" class="kb-list__error">
        <span>{{ loadError }}</span>
        <el-button size="small" @click="load">重试</el-button>
      </div>
    </div>

    <KbCreateDialog v-model="showCreate" :domain="domainStore.currentDomain" @created="load" />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { Collection, Cpu, Document, Loading, MoreFilled, Plus, Refresh } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useDomainStore } from '@/stores/domain'
import { useAuthStore } from '@/stores/auth'
import { canManageDomainKnowledgeBases } from '@/utils/domainPermissions'
import { useKbApi } from '@/api/kb'
import type { DeletedKbRow, KbPurgeTask } from '@/types/kb'
import { apiErrorDetail } from '@/api/proxyClient'
import EmptyState from '@/components/common/EmptyState.vue'
import KbCreateDialog from '@/components/kb/KbCreateDialog.vue'
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
  purgeTasks.value.filter((t) => t.status === 'queued' || t.status === 'running'))
const failedPurgeTasks = computed(() =>
  purgeTasks.value.filter((t) => t.status === 'failed'))

function purgePhaseLabel(t: KbPurgeTask): string {
  const labels: Record<string, string> = {
    queued: '排队中', prepare: '准备', documents: '删除文档',
    snapshots: '回收知识快照', objects: '回收存储对象', finalize: '收尾',
  }
  return labels[t.phase] ?? t.phase
}

/** 快照阶段有 total/reclaimed 可算百分比；其余阶段显示计数 */
function purgePercent(t: KbPurgeTask): number | null {
  if (t.phase === 'snapshots' && t.progress.snapshots_total > 0) {
    return Math.min(99, Math.round(
      (t.progress.snapshots_reclaimed ?? 0) / t.progress.snapshots_total * 100))
  }
  return null
}

function purgeProgressText(t: KbPurgeTask): string {
  const p = t.progress ?? {}
  if (t.phase === 'snapshots') {
    return `${p.snapshots_reclaimed ?? 0}/${p.snapshots_total ?? '?'} 快照`
  }
  if (t.phase === 'objects') {
    return `${p.objects_reclaimed ?? 0}/${p.objects_total ?? '?'} 对象`
  }
  if (t.phase === 'finalize') return '完成中'
  return ''
}

async function pollPurgeTasks() {
  const domain = domainStore.currentDomain
  if (!domain) return
  try {
    purgeTasks.value = await kbApi.purgeTasks(domain)
    if (purgeTasks.value.some((t) => t.status === 'done')
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

async function mine(kb: KbSummary) {
  miningId.value = kb.id
  try {
    const res = await kbApi.mineKb(kb.id)
    ElMessage.success(`挖掘已排队（run ${res.run_id.slice(0, 8)}）`)
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
    const r = await ElMessageBox.prompt('新名称', '重命名知识库', {
      inputValue: kb.name, confirmButtonText: '保存', cancelButtonText: '取消',
      inputValidator: (v) => !!v?.trim() || '名称不能为空',
    })
    name = r.value.trim()
  } catch { return }
  try {
    await kbApi.updateKb(kb.id, { name })
    ElMessage.success('已改名')
    await load()
  } catch (e) { ElMessage.error(await apiErrorDetail(e)) }
}

async function remove(kb: KbSummary) {
  // 硬删确认：输入库全名（GitHub 风格——整库不可逆操作的最高门槛）
  let name = ''
  try {
    const { value } = await ElMessageBox.prompt(
      `此操作将永久删除知识库「${kb.name}」及其全部文档、挖掘知识与历史记录，不可恢复。请输入库全名确认：`,
      '永久删除知识库',
      {
        type: 'warning', confirmButtonText: '永久删除', cancelButtonText: '取消',
        confirmButtonClass: 'el-button--danger',
        inputValidator: (v) => v?.trim() === kb.name || '名称不一致',
      },
    )
    name = value.trim()
  } catch { return }
  try {
    await kbApi.deleteKb(kb.id, name)
    ElMessage.success('已开始后台删除：库即刻停用（成员不可见），进度见列表下方')
    startPurgePolling()
    await load()
  } catch (e) { ElMessage.error(await apiErrorDetail(e)) }
}

/** 已删库（软删态存量）彻底清理：同样要求输入库全名 */
async function removeDeleted(dkb: DeletedKbRow) {
  let name = ''
  try {
    const { value } = await ElMessageBox.prompt(
      `将彻底清除「${dkb.name}」的全部残留数据（文档/知识/存储），不可恢复。请输入库全名确认：`,
      '彻底删除',
      {
        type: 'warning', confirmButtonText: '彻底删除', cancelButtonText: '取消',
        inputValidator: (v) => v?.trim() === dkb.name || '名称不一致',
      },
    )
    name = value.trim()
  } catch { return }
  purgingId.value = dkb.id
  try {
    await kbApi.deleteKb(dkb.id, name)
    ElMessage.success('已开始后台彻底清除，进度见列表下方')
    startPurgePolling()
    await load()
  } catch (e) { ElMessage.error(await apiErrorDetail(e)) }
  finally { purgingId.value = '' }
}

async function restoreDeleted(dkb: DeletedKbRow) {
  try {
    await ElMessageBox.confirm(
      `确认恢复知识库「${dkb.name}」？若当前域已有同名知识库，恢复会被拒绝。`,
      '恢复知识库',
      { type: 'warning', confirmButtonText: '恢复', cancelButtonText: '取消' },
    )
  } catch { return }
  restoringId.value = dkb.id
  try {
    await kbApi.restoreKb(dkb.id)
    ElMessage.success('知识库已恢复')
    await load()
  } catch (e) { ElMessage.error(await apiErrorDetail(e)) }
  finally { restoringId.value = '' }
}

function formatDate(t: string): string {
  if (!t) return '-'
  return new Date(t).toLocaleDateString('zh-CN')
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
