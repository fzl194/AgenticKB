<template>
  <div class="artifact-list">
    <header class="artifact-list__header">
      <div>
        <p class="artifact-list__eyebrow">KNOWLEDGE ARTIFACTS</p>
        <h1 class="artifact-list__title">知识制品</h1>
        <p class="artifact-list__subtitle">上传 Excel 建表发布，供 Agent 结构化查询；列表=我参与的 + 本域公开制品（管理员见本域全部）。</p>
      </div>
      <el-button type="primary" data-test="create-artifact" @click="openCreate">新建制品</el-button>
    </header>

    <!-- L01 筛选组（66号 §4）：类型/状态/标题关键字，三项组合本地过滤 -->
    <div class="artifact-list__filters">
      <el-select v-model="filterKind" class="artifact-list__filter" data-test="filter-kind">
        <el-option value="all" label="类型：全部" />
        <el-option value="table" label="表格" />
        <el-option value="wiki" label="Wiki" />
      </el-select>
      <el-select v-model="filterStatus" class="artifact-list__filter" data-test="filter-status">
        <el-option value="all" label="状态：全部" />
        <el-option value="draft" label="草稿" />
        <el-option value="published" label="已发布" />
        <el-option value="deprecated" label="已停用" />
      </el-select>
      <el-input
        v-model="filterTitle"
        class="artifact-list__filter artifact-list__filter-title"
        data-test="filter-title"
        placeholder="标题关键字"
        clearable
      />
      <span class="artifact-list__count">共 {{ filteredArtifacts.length }} 份</span>
    </div>

    <el-table v-loading="loading" :data="filteredArtifacts" class="artifact-list__table">
      <el-table-column prop="title" label="知识制品" min-width="260">
        <template #default="{ row }">
          <button class="artifact-list__name" type="button" @click="open(row.artifact_id)">
            <span class="artifact-list__kindicon" :class="`is-${row.kind}`" aria-hidden="true">
              {{ row.kind === 'wiki' ? 'W' : 'T' }}
            </span>
            <span class="artifact-list__name-text">
              <span class="artifact-list__link">{{ row.title }}</span>
              <span v-if="row.description" class="artifact-list__desc">{{ row.description }}</span>
            </span>
          </button>
        </template>
      </el-table-column>
      <el-table-column prop="domain_id" label="归属域" min-width="150" show-overflow-tooltip />
      <el-table-column label="类型" width="80">
        <template #default="{ row }">
          <el-tag size="small" effect="plain">{{ row.kind === 'wiki' ? 'Wiki' : '表格' }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="可见性" width="80">
        <template #default="{ row }">
          <el-tag size="small" :type="row.visibility === 'public' ? 'success' : 'info'" effect="plain">
            {{ row.visibility === 'public' ? '公开' : '私有' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-tag size="small" :type="statusTagType(row.status)" :effect="row.status === 'published' ? 'light' : 'plain'">{{ statusLabel(row.status) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="当前版本" width="90" align="center">
        <template #default="{ row }">{{ row.current_revision ? `r${row.current_revision}` : '未发布' }}</template>
      </el-table-column>
      <el-table-column label="我的角色" width="90" align="center">
        <template #default="{ row }">{{ row.my_role ? roleLabel(row.my_role) : '—' }}</template>
      </el-table-column>
      <el-table-column label="更新时间" width="150">
        <template #default="{ row }">{{ formatTime(row.updated_at) }}</template>
      </el-table-column>
      <template #empty>
        <EmptyState :text="emptyText" />
      </template>
    </el-table>

    <el-dialog v-model="createVisible" title="新建知识制品" width="520px">
      <el-form label-width="70px">
        <el-form-item label="类型">
          <el-radio-group v-model="form.kind" data-test="create-artifact-kind">
            <el-radio value="table">表格制品</el-radio>
            <el-radio value="wiki">Wiki 制品</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="标题">
          <el-input v-model="form.title" placeholder="如：MOP 责任域表" maxlength="200" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="form.description" type="textarea" :rows="3" placeholder="这份表给谁用、解决什么问题（可选）" />
        </el-form-item>
        <el-form-item label="归属域">
          <el-select
            v-model="form.domainId"
            placeholder="选择知识域"
            style="width: 100%"
            data-test="create-artifact-domain"
          >
            <el-option
              v-for="d in domainStore.enabledDomains"
              :key="d.domain_id"
              :value="d.domain_id"
              :label="d.domain_id"
            />
          </el-select>
          <div class="artifact-list__domain-hint">制品将创建到该知识域（默认当前域：{{ domainStore.currentDomain || '未知' }}）</div>
        </el-form-item>
        <p class="artifact-list__hint">{{ KIND_CREATE_HINTS[form.kind] }}</p>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="creating" data-test="submit-create-artifact" @click="createArtifact">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useArtifactsApi } from '@/api/artifacts'
import { apiErrorDetail } from '@/api/proxyClient'
import { useDomainStore } from '@/stores/domain'
import { useDocumentTitle } from '@/composables/useDocumentTitle'
import EmptyState from '@/components/common/EmptyState.vue'
import type { Artifact, ArtifactKind, ArtifactStatus } from '@/types/artifacts'

const api = useArtifactsApi()
const router = useRouter()
const domainStore = useDomainStore()

useDocumentTitle(() => '知识制品')

const artifacts = ref<Artifact[]>([])
const loading = ref(false)
let loadGeneration = 0
const createVisible = ref(false)
const creating = ref(false)
const form = ref({
  title: '', description: '', domainId: '',
  /** 60号四形态：创建时选类型（默认 table；wiki 详情页暂为占位守卫）。 */
  kind: 'table' as ArtifactKind,
})

// ── L01 筛选组（66号 §4）──────────────────────────────────────────────
// 三项组合对当前页数据做本地过滤——列表量级 <1000 前端过滤足够，不加后端
// 筛选参数（YAGNI；66号原型同口径，筛选变更不触发任何重取）。
const filterKind = ref<'all' | ArtifactKind>('all')
const filterStatus = ref<'all' | ArtifactStatus>('all')
const filterTitle = ref('')

const filteredArtifacts = computed(() => {
  const keyword = filterTitle.value.trim().toLowerCase()
  return artifacts.value.filter(a =>
    (filterKind.value === 'all' || a.kind === filterKind.value)
    && (filterStatus.value === 'all' || a.status === filterStatus.value)
    && (keyword === '' || a.title.toLowerCase().includes(keyword)),
  )
})

const hasActiveFilter = computed(() =>
  filterKind.value !== 'all' || filterStatus.value !== 'all' || filterTitle.value.trim() !== '')

/** 空态双口径：无筛选=真没有制品（指路新建入口）；有筛选无命中=让用户调条件。 */
const emptyText = computed(() => hasActiveFilter.value
  ? '无符合筛选条件的制品——试试调整类型/状态或清空标题关键字'
  : '还没有知识制品，点右上角「新建制品」从上传 Excel 开始')

/** 创建对话框底部流程提示（Record 键钉死 ArtifactKind 全集——四形态路线
 *  下新增 kind 时 TS 强制补齐文案）。 */
const KIND_CREATE_HINTS: Record<ArtifactKind, string> = {
  table: '创建后进入设计态：上传 Excel → 列映射 → 导入 → 填使用说明 → 发布。',
  wiki: '创建后进入设计态：绑定知识库目录 → 编译生成页面 → 人工修订 → 填使用说明 → 发布。',
}

async function load() {
  const generation = ++loadGeneration
  const domainId = domainStore.currentDomain
  artifacts.value = []
  if (!domainId) {
    loading.value = false
    return
  }
  loading.value = true
  try {
    const result = await api.list({ domain_id: domainId })
    if (generation === loadGeneration && domainId === domainStore.currentDomain) {
      artifacts.value = result
    }
  } catch (error) {
    if (generation === loadGeneration) {
      ElMessage.error(`加载知识制品失败：${await apiErrorDetail(error)}`)
    }
  } finally {
    if (generation === loadGeneration) loading.value = false
  }
}

function openCreate() {
  // codex二审功能项③：显式带当前域创建——不再依赖后端 first_domain_for_user
  // 兜底（多域用户在 B 域页面会落到 A 域）。域选择对齐 Header/McpAccessView
  // 的 domain store 机制（enabledDomains），不新造状态源。
  form.value = { title: '', description: '', domainId: domainStore.currentDomain, kind: 'table' }
  createVisible.value = true
}

async function createArtifact() {
  const title = form.value.title.trim()
  if (!title) {
    ElMessage.warning('请填写标题')
    return
  }
  if (!form.value.domainId) {
    ElMessage.warning('无法确定归属知识域，请在新建窗口选择目标域')
    return
  }
  creating.value = true
  try {
    const artifact = await api.create({
      title,
      description: form.value.description.trim() || undefined,
      domain_id: form.value.domainId,
      kind: form.value.kind,
    })
    createVisible.value = false
    await router.push({ name: 'artifact-detail', params: { artifactId: artifact.artifact_id } })
  } catch (error) {
    ElMessage.error(`创建失败：${await apiErrorDetail(error)}`)
  } finally {
    creating.value = false
  }
}

function open(artifactId: string) {
  router.push({ name: 'artifact-detail', params: { artifactId } })
}

/** 状态 tag 映射：draft 灰 / published 绿 / deprecated 红（测试钉死）。 */
function statusTagType(status: string): 'info' | 'success' | 'danger' {
  if (status === 'published') return 'success'
  if (status === 'deprecated') return 'danger'
  return 'info'
}

function statusLabel(status: string): string {
  if (status === 'published') return '已发布'
  if (status === 'deprecated') return '已停用'
  return '草稿'
}

function roleLabel(role: string): string {
  return role === 'owner' ? '拥有者' : role === 'editor' ? '编辑者' : '查看者'
}

function formatTime(value: string | null): string {
  return value ? value.replace('T', ' ').slice(0, 19) : '—'
}

watch(() => domainStore.currentDomain, load, { immediate: true })
</script>

<style scoped>
.artifact-list { padding: 4px 2px; }

/* ── 页头：eyebrow + 大标题 + 副题（高保真 ar-eyebrow/h1 排版） ── */
.artifact-list__header { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; margin-bottom: 18px; }
.artifact-list__eyebrow {
  margin: 0 0 6px;
  font-size: 10.5px; font-weight: 600; letter-spacing: 1.6px;
  color: var(--kb-accent, #0891b2);
}
.artifact-list__title { margin: 0; font-size: 22px; font-weight: 600; letter-spacing: -.4px; color: var(--kb-text-primary, inherit); }
.artifact-list__subtitle { margin: 7px 0 0; color: var(--kb-text-tertiary); font-size: 12.5px; }

/* ── 筛选行 ── */
.artifact-list__filters { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; flex-wrap: wrap; }
.artifact-list__filter { width: 140px; }
.artifact-list__filter-title { width: 220px; }
.artifact-list__count { margin-left: auto; font-size: 12px; color: var(--kb-text-tertiary); }

/* ── 表格：发丝线 + 软表头 + 名称双行（高保真 ar-list-table 质感） ── */
.artifact-list__table :deep(th.el-table__cell) {
  background: var(--kb-bg-card-hover, #f8fafc);
  color: var(--kb-text-secondary, #64748b);
  font-weight: 500; font-size: 11.5px;
}
.artifact-list__table :deep(td.el-table__cell) { padding: 10px 0; }
.artifact-list__table :deep(.el-table__row:hover td.el-table__cell) { background: var(--kb-bg-card-hover, #f8fafc); }
.artifact-list__name {
  display: flex; align-items: center; gap: 10px;
  border: 0; background: none; padding: 2px 4px;
  cursor: pointer; text-align: left; min-width: 0;
}
.artifact-list__kindicon {
  display: inline-grid; place-items: center; flex-shrink: 0;
  width: 32px; height: 32px; border-radius: 8px;
  border: 1px solid var(--kb-border, #e2e8f0);
  background: var(--kb-accent-soft, rgba(8, 145, 178, 0.08));
  color: var(--kb-accent, #0891b2);
  font-size: 12px; font-weight: 700;
}
.artifact-list__name-text { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.artifact-list__link {
  border: 0; background: none; padding: 0; text-align: left;
  color: var(--kb-text-primary, inherit); font-weight: 600; font-size: 13.5px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.artifact-list__name:hover .artifact-list__link { color: var(--kb-accent, #0891b2); }
.artifact-list__desc {
  color: var(--kb-text-tertiary); font-size: 11.5px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.artifact-list__hint { margin: -5px 0 0 70px; color: var(--kb-text-tertiary); font-size: 12px; }
.artifact-list__domain-hint { width: 100%; color: var(--kb-text-tertiary); font-size: 12px; }
</style>
