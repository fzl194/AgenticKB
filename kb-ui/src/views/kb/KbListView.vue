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
import { onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { Collection, Cpu, Document, MoreFilled, Plus, Refresh } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useDomainStore } from '@/stores/domain'
import { useKbApi } from '@/api/kb'
import { apiErrorDetail } from '@/api/proxyClient'
import EmptyState from '@/components/common/EmptyState.vue'
import KbCreateDialog from '@/components/kb/KbCreateDialog.vue'
import { roleLabel, roleTagType, visibilityLabel, visibilityTagType } from '@/views/kb/kbMeta'
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
  return kb.my_role === 'owner' || kb.my_role === 'editor' || kb.my_role === 'admin'
}

async function load() {
  const domain = domainStore.currentDomain
  if (!domain) return
  const generation = ++loadGeneration
  loading.value = true
  loadError.value = ''
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
  return kb.my_role === 'owner' || kb.my_role === 'admin'
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
  try {
    await ElMessageBox.confirm(
      `确定删除知识库「${kb.name}」？软删除后对所有人不可见，历史数据保留，原名称可重新使用。`,
      '删除知识库',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch { return }
  try {
    await kbApi.deleteKb(kb.id)
    ElMessage.success('已删除')
    await load()
  } catch (e) { ElMessage.error(await apiErrorDetail(e)) }
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
