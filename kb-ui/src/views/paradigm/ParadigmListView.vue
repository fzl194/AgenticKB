<template>
  <div class="pd-list">
    <div class="pd-list__head">
      <div>
        <h2 class="pd-list__title">检索范式</h2>
        <p class="pd-list__sub">可插拔检索算子 DAG —— 拖拽编排、发布版本；范式跨域通用，用哪个库检索由知识库自己绑定</p>
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
      <el-table-column prop="description" label="描述" min-width="220" show-overflow-tooltip />
      <el-table-column label="状态" width="110">
        <template #default="{ row }">
          <el-tag size="small" :type="row.status === 'active' ? 'success' : 'info'">{{ statusLabel(row.status) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="当前版本" width="100">
        <template #default="{ row }">{{ row.currentVersion > 0 ? `v${row.currentVersion}` : '—' }}</template>
      </el-table-column>
      <el-table-column prop="updatedAt" label="更新时间" width="180" />
      <el-table-column label="操作" width="180" fixed="right">
        <template #default="{ row }">
          <el-button size="small" text type="primary" @click="edit(row.id)">编辑</el-button>
          <el-button v-if="row.status !== 'archived'" size="small" text type="info" @click="doArchive(row)">归档</el-button>
          <el-button v-else size="small" text type="danger" @click="doDelete(row)">删除</el-button>
        </template>
      </el-table-column>
      <template #empty>
        <EmptyState text="暂无范式，点击右上角新建" />
      </template>
    </el-table>

    <el-dialog v-model="createVisible" title="新建范式" width="460">
      <el-form label-width="72px">
        <el-form-item label="名称">
          <el-input v-model="form.name" placeholder="唯一名称，如 emb-only-baseline" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input
            v-model="form.description"
            type="textarea"
            :rows="3"
            placeholder="例：查 ODN 拓扑、端口占用与光路走向；不适合查参数表格"
          />
          <div class="pd-list__field-hint">
            <strong>这段是给 AI 看的</strong>：说清什么问题该用这个范式。Agent 就是靠它决定选不选，
            写成给人看的介绍会让它选不准。建议一两句话、200 字以内。
          </div>
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

function errMsg(e: unknown): string {
  const anyE = e as { response?: { data?: { message?: string; error?: string } }; message?: string }
  return anyE?.response?.data?.message || anyE?.response?.data?.error || anyE?.message || '未知错误'
}

onMounted(async () => {
  await useDomainStore().fetchDomains()
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
.pd-list__field-hint { font-size: 12px; color: var(--kb-text-tertiary); line-height: 1.6; margin-top: 4px; }
</style>
