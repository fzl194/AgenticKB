<template>
  <div class="pd-list">
    <div class="pd-list__head">
      <div>
        <h2 class="pd-list__title">检索范式</h2>
        <p class="pd-list__sub">可插拔检索算子 DAG —— 拖拽编排、发布版本、按 id 调用</p>
      </div>
      <el-button type="primary" :icon="Plus" @click="openCreate">新建范式</el-button>
    </div>

    <el-tabs v-model="activeTab" class="pd-list__tabs">
      <el-tab-pane label="全部" name="all" />
      <el-tab-pane :label="`草稿 (${countBy('draft')})`" name="draft" />
      <el-tab-pane :label="`已发布 (${countBy('active')})`" name="active" />
      <el-tab-pane :label="`已归档 (${countBy('archived')})`" name="archived" />
    </el-tabs>

    <el-table v-loading="loading" :data="filteredParadigms" class="pd-list__table">
      <el-table-column prop="name" label="名称" min-width="160">
        <template #default="{ row }">
          <a class="pd-list__link" @click="edit(row.id)">{{ row.name }}</a>
        </template>
      </el-table-column>
      <el-table-column prop="description" label="描述" min-width="200" show-overflow-tooltip />
      <el-table-column label="状态" width="110">
        <template #default="{ row }">
          <el-tag size="small" :type="row.status === 'active' ? 'success' : 'info'">{{ statusLabel(row.status) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="当前版本" width="100">
        <template #default="{ row }">{{ row.currentVersion > 0 ? `v${row.currentVersion}` : '—' }}</template>
      </el-table-column>
      <el-table-column label="绑定域" width="200">
        <template #default="{ row }">
          <template v-if="row.boundDomain">
            <el-tag size="small" type="primary">{{ row.boundDomain }}</el-tag>
            <el-tag v-if="row.isDefault" size="small" type="success" effect="dark" class="pd-list__default-tag">
              自动匹配
            </el-tag>
            <span v-else class="pd-list__hint-inline">仅绑定，未自动匹配</span>
          </template>
          <span v-else class="pd-list__muted">—</span>
        </template>
      </el-table-column>
      <el-table-column prop="updatedAt" label="更新时间" width="180" />
      <el-table-column label="操作" width="250" fixed="right">
        <template #default="{ row }">
          <el-button size="small" text type="primary" @click="edit(row.id)">编辑</el-button>
          <el-tooltip
            :disabled="isBindable(row)"
            content="需先发布版本才能绑定到知识域"
            placement="top"
          >
            <span>
              <el-button
                size="small" text type="primary"
                :disabled="!isBindable(row)"
                @click="openBind(row)"
              >{{ row.boundDomain ? '改绑' : '绑定' }}</el-button>
            </span>
          </el-tooltip>
          <el-button v-if="row.boundDomain" size="small" text type="warning" @click="doUnbind(row)">解绑</el-button>
          <el-button v-if="row.status !== 'archived'" size="small" text type="info" @click="doArchive(row)">归档</el-button>
          <el-button v-else size="small" text type="danger" @click="doDelete(row)">删除</el-button>
        </template>
      </el-table-column>
      <template #empty>
        <EmptyState text="暂无范式，点击右上角新建" />
      </template>
    </el-table>

    <el-dialog v-model="bindVisible" title="绑定到知识域" width="500">
      <el-form label-width="92px">
        <el-form-item label="范式">
          <span class="pd-list__bind-name">{{ bindTarget?.name }}</span>
          <span class="pd-list__muted">（v{{ bindTarget?.currentVersion }}）</span>
        </el-form-item>
        <el-form-item label="知识域">
          <el-select v-model="bindForm.domain" placeholder="选择知识域" style="width: 100%">
            <el-option v-for="d in domainOptions" :key="d" :label="d" :value="d" />
          </el-select>
        </el-form-item>
        <el-form-item label="自动匹配">
          <el-switch v-model="bindForm.isDefault" />
          <div class="pd-list__field-hint">
            开启后，MCP 与其他只给 domain 的调用方检索该域时自动使用本范式。
            一个域同时只能有一个自动匹配范式，开启会替换掉原有的那个。
          </div>
        </el-form-item>
      </el-form>

      <el-alert
        v-if="draftNotServable"
        type="warning" :closable="false" show-icon
        title="当前草稿的终点不是 assemble"
        description="绑定校验的是已发布版本，不是草稿。若已发布版本同样以 collect 结尾，绑定会被拒绝——collect 输出的是供评测用的裸候选，不适合直接作为检索结果返回。"
        class="pd-list__bind-alert"
      />

      <template #footer>
        <el-button @click="bindVisible = false">取消</el-button>
        <el-button type="primary" :loading="binding" @click="doBind">确定绑定</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="createVisible" title="新建范式" width="460">
      <el-form label-width="72px">
        <el-form-item label="名称">
          <el-input v-model="form.name" placeholder="唯一名称，如 emb-only-baseline" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="form.description" type="textarea" :rows="2" />
        </el-form-item>
        <el-form-item label="模板">
          <el-select v-model="form.template" style="width: 100%">
            <el-option v-for="t in templates" :key="t.key" :label="t.label" :value="t.key" />
          </el-select>
        </el-form-item>
        <div v-if="selectedTemplateDesc" class="pd-list__tpl-desc">{{ selectedTemplateDesc }}</div>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="doCreate">创建并编辑</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { Plus } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import EmptyState from '@/components/common/EmptyState.vue'
import { useOperatorApi } from '@/api/operator'
import { useDomainStore } from '@/stores/domain'
import type { ParadigmView } from '@/types/operator'
import { PARADIGM_TEMPLATES } from './templates'

const router = useRouter()
const api = useOperatorApi()

const paradigms = ref<ParadigmView[]>([])
const loading = ref(false)
const createVisible = ref(false)
const creating = ref(false)
const form = ref({ name: '', description: '', template: 'blank' })
const templates = PARADIGM_TEMPLATES
const selectedTemplateDesc = computed(() =>
  PARADIGM_TEMPLATES.find(t => t.key === form.value.template)?.description ?? '')

const activeTab = ref('all')
const filteredParadigms = computed(() =>
  activeTab.value === 'all' ? paradigms.value : paradigms.value.filter(p => p.status === activeTab.value))
function countBy(status: string) {
  return paradigms.value.filter(p => p.status === status).length
}

async function load() {
  loading.value = true
  try {
    paradigms.value = await api.listParadigms()
  } catch (e) {
    ElMessage.error('加载范式列表失败：' + errMsg(e))
  } finally {
    loading.value = false
  }
}

function openCreate() {
  form.value = { name: '', description: '', template: 'blank' }
  createVisible.value = true
}

async function doCreate() {
  if (!form.value.name.trim()) { ElMessage.warning('请填写名称'); return }
  creating.value = true
  try {
    const tpl = PARADIGM_TEMPLATES.find(t => t.key === form.value.template)
    const pd = await api.createParadigm({
      name: form.value.name.trim(),
      description: form.value.description,
      graph: tpl?.graph ?? undefined,
    })
    createVisible.value = false
    router.push({ name: 'paradigm-edit', params: { id: pd.id } })
  } catch (e) {
    ElMessage.error('创建失败：' + errMsg(e))
  } finally {
    creating.value = false
  }
}

function edit(id: string) {
  router.push({ name: 'paradigm-edit', params: { id } })
}

async function doArchive(row: ParadigmView) {
  try {
    await ElMessageBox.confirm(`归档范式「${row.name}」？归档后可在「已归档」页签查看。`, '归档', { type: 'warning' })
  } catch { return }
  try {
    await api.archive(row.id)
    ElMessage.success('已归档')
    await load()
  } catch (e) {
    ElMessage.error('归档失败：' + errMsg(e))
  }
}

async function doDelete(row: ParadigmView) {
  try {
    await ElMessageBox.confirm(
      `永久删除范式「${row.name}」及其所有版本？此操作不可恢复。`,
      '删除', { type: 'error', confirmButtonText: '永久删除' })
  } catch { return }
  try {
    await api.deleteParadigm(row.id)
    ElMessage.success('已删除')
    await load()
  } catch (e) {
    ElMessage.error('删除失败：' + errMsg(e))
  }
}

function statusLabel(s: string): string {
  return ({ draft: '草稿', active: '已发布', archived: '已归档' } as Record<string, string>)[s] ?? s
}

// ---- domain binding (MCP auto-matching) ----

const domainStore = useDomainStore()
const domainOptions = computed(() => domainStore.enabledDomains.map(d => d.domain_id))

const bindVisible = ref(false)
const binding = ref(false)
const bindTarget = ref<ParadigmView | null>(null)
const bindForm = ref({ domain: '', isDefault: true })

/** Binding validates the published version, so an unpublished paradigm has nothing to bind. */
function isBindable(row: ParadigmView): boolean {
  return row.status === 'active' && row.currentVersion >= 1
}

/**
 * A hint, not a gate. The backend validates the *published* graph while all the list has is the
 * draft; the two can differ, so this warns without blocking and lets the real check decide.
 */
const draftNotServable = computed(() => {
  const slot = bindTarget.value?.draftGraph?.output?.slot
  return !!slot && slot !== 'contextPack'
})

function openBind(row: ParadigmView) {
  bindTarget.value = row
  bindForm.value = {
    domain: row.boundDomain || domainStore.currentDomain || domainOptions.value[0] || '',
    isDefault: row.boundDomain ? row.isDefault : true,
  }
  bindVisible.value = true
}

async function doBind() {
  const target = bindTarget.value
  if (!target) return
  if (!bindForm.value.domain) { ElMessage.warning('请选择知识域'); return }

  const prev = paradigms.value.find(
    p => p.boundDomain === bindForm.value.domain && p.isDefault && p.id !== target.id)
  if (bindForm.value.isDefault && prev) {
    try {
      await ElMessageBox.confirm(
        `知识域「${bindForm.value.domain}」当前由「${prev.name}」自动匹配，绑定后将改为「${target.name}」。`,
        '替换自动匹配范式', { type: 'warning' })
    } catch { return }
  }

  binding.value = true
  try {
    await api.bindParadigm(target.id, bindForm.value.domain, bindForm.value.isDefault)
    ElMessage.success(bindForm.value.isDefault
      ? `已绑定，${bindForm.value.domain} 的检索将自动使用该范式`
      : '已绑定（未设为自动匹配）')
    bindVisible.value = false
    await load()
  } catch (e) {
    ElMessage.error(bindErrMsg(e))
  } finally {
    binding.value = false
  }
}

async function doUnbind(row: ParadigmView) {
  try {
    await ElMessageBox.confirm(
      row.isDefault
        ? `解绑「${row.name}」？知识域「${row.boundDomain}」将回落到默认检索管线。`
        : `解绑「${row.name}」？它仍可按 id 调用，只是不再参与自动匹配。`,
      '解绑', { type: 'warning' })
  } catch { return }
  try {
    await api.unbindParadigm(row.id)
    ElMessage.success('已解绑')
    await load()
  } catch (e) {
    ElMessage.error('解绑失败：' + errMsg(e))
  }
}

/** Binding rejections carry a stable code; a raw code is useless to whoever is configuring this. */
function bindErrMsg(e: unknown): string {
  const data = (e as { response?: { data?: { error?: string; message?: string; details?: string[] } } })
    ?.response?.data
  const detail = data?.details?.length ? `：${data.details.join('、')}` : ''
  switch (data?.error) {
    case 'unknown_domain':
      return '绑定失败：知识域不存在或已停用'
    case 'paradigm_not_published':
      return '绑定失败：范式尚未发布，请先发布一个版本'
    case 'paradigm_not_servable':
      return '绑定失败：已发布版本的终点不是 assemble。只有产出 ContextPack 的范式能绑定到知识域'
    case 'paradigm_requires_identity':
      return `绑定失败：范式引用了非公开知识库${detail}。MCP 匿名调用读不到它们，请改为公开或从图中移除`
    default:
      return '绑定失败：' + errMsg(e)
  }
}

function errMsg(e: unknown): string {
  const anyE = e as { response?: { data?: { message?: string; error?: string } }; message?: string }
  return anyE?.response?.data?.message || anyE?.response?.data?.error || anyE?.message || '未知错误'
}

onMounted(async () => {
  await domainStore.fetchDomains()
  await load()
})
</script>

<style scoped>
.pd-list { padding: 4px; }
.pd-list__head { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 18px; }
.pd-list__title { font-size: 20px; font-weight: 700; margin: 0; }
.pd-list__sub { color: var(--kb-text-tertiary); font-size: 13px; margin: 4px 0 0; }
.pd-list__link { color: var(--kb-accent, #3b82f6); cursor: pointer; font-weight: 600; }
.pd-list__table { margin-top: 4px; }
.pd-list__tpl-desc { font-size: 12px; color: var(--kb-text-tertiary); line-height: 1.6; margin: -6px 0 4px; padding: 8px 10px; background: var(--kb-bg-subtle, #f8fafc); border-radius: 6px; }
.pd-list__muted { color: var(--kb-text-tertiary); }
.pd-list__default-tag { margin-left: 6px; }
.pd-list__hint-inline { margin-left: 6px; font-size: 12px; color: var(--kb-text-tertiary); }
.pd-list__bind-name { font-weight: 600; }
.pd-list__field-hint { font-size: 12px; color: var(--kb-text-tertiary); line-height: 1.6; margin-top: 4px; }
.pd-list__bind-alert { margin-top: 4px; }
</style>
