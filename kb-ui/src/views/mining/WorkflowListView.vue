<template>
  <div class="workflow-list">
    <header class="workflow-list__header">
      <div>
        <h2>挖掘 Workflow</h2>
        <p>全局共享，不随 Domain 切换；发布后可在上传文件挖掘时选择精确版本。</p>
      </div>
      <el-button type="primary" data-test="create-workflow" @click="openCreate">新建 Workflow</el-button>
    </header>

    <div class="workflow-list__notice">全局共享 · 草稿使用 revision 乐观锁 · 已发布版本不可变</div>

    <el-table v-loading="loading" :data="workflows">
      <el-table-column prop="name" label="名称" min-width="180">
        <template #default="{ row }">
          <button class="workflow-list__link" type="button" @click="edit(row.id)">{{ row.name }}</button>
          <el-tag v-if="row.is_system_default" size="small" type="success">默认 FULL</el-tag>
          <el-tag v-else-if="row.is_system" size="small" type="info">系统</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="description" label="描述" min-width="220" show-overflow-tooltip />
      <el-table-column label="草稿" width="100">
        <template #default="{ row }">r{{ row.draft_revision }}</template>
      </el-table-column>
      <el-table-column label="当前版本" width="110">
        <template #default="{ row }">{{ row.current_version ? `v${row.current_version}` : '未发布' }}</template>
      </el-table-column>
      <el-table-column label="状态" width="100">
        <template #default="{ row }">
          <el-tag size="small" :type="row.status === 'active' ? 'success' : 'info'">{{ row.status === 'active' ? '有效' : '已归档' }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="230" fixed="right">
        <template #default="{ row }">
          <el-button size="small" text type="primary" @click="edit(row.id)">编排/版本</el-button>
          <el-button size="small" text @click="openCopy(row)">复制</el-button>
          <el-button v-if="canArchive(row)" size="small" text type="warning" @click="archiveWorkflow(row)">归档</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="createVisible" title="新建挖掘 Workflow" width="520px">
      <el-form label-width="90px">
        <el-form-item label="名称"><el-input v-model="form.name" /></el-form-item>
        <el-form-item label="描述"><el-input v-model="form.description" type="textarea" /></el-form-item>
        <el-form-item label="解析链路">
          <el-radio-group v-model="form.schema_version">
            <el-radio value="2.0">新链路 v2（推荐）</el-radio>
            <el-radio value="1.0">传统 v1</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="模板">
          <el-select v-model="form.template_key" style="width: 100%">
            <el-option v-for="template in templates" :key="template.key" :label="template.label" :value="template.key" />
          </el-select>
        </el-form-item>
        <p class="workflow-list__template-desc">
          {{ form.schema_version === '2.0'
            ? 'v2：文档解析与切片编译分离（质量门控、知识快照、表格行带表头、证据可回原文），两个固定头部算子可在画布中点开调整参数。'
            : 'v1：传统解析与切分一体算子（历史兼容；既有 Workflow 不受影响）。' }}
          {{ selectedTemplateDescription }}
        </p>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="createWorkflow">创建并编排</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="copyVisible" title="复制 Workflow" width="460px">
      <el-form label-width="80px">
        <el-form-item label="新名称"><el-input v-model="copyName" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="copyVisible = false">取消</el-button>
        <el-button type="primary" @click="confirmCopy">复制草稿</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useMiningWorkflowApi } from '@/api/miningWorkflow'
import type { CreateMiningWorkflowRequest, MiningTemplateKey, MiningWorkflow } from '@/types/miningWorkflow'

const api = useMiningWorkflowApi()
const router = useRouter()
const workflows = ref<MiningWorkflow[]>([])
const loading = ref(false)
const creating = ref(false)
const createVisible = ref(false)
const copyVisible = ref(false)
const copySource = ref<MiningWorkflow | null>(null)
const copyName = ref('')
const form = ref<{
  name: string
  description: string
  template_key: MiningTemplateKey
  schema_version: '1.0' | '2.0'
}>({
  name: '', description: '', template_key: 'full', schema_version: '2.0',
})

const templates: Array<{ key: MiningTemplateKey; label: string; description: string }> = [
  { key: 'minimal', label: '基础文档入库', description: '仅解析、切分并持久化文档资产，适合低成本基础入库。' },
  { key: 'fast_retrieval', label: '快速向量检索', description: '直接生成检索单元与向量，适合快速构建基础 RAG 检索。' },
  { key: 'discourse_only', label: '篇章增强检索', description: '理解篇章关系并补充检索上下文，适合长文档的高质量语义检索。' },
  { key: 'entity_graph', label: '固定本体图谱构建', description: '按已有本体抽取实体和关系并写入图谱，不演化本体。' },
  { key: 'hybrid_knowledge', label: '检索与图谱联合构建', description: '同时构建篇章增强检索资产和固定本体实体图谱。' },
  { key: 'ontology_only', label: '本体演化专项', description: '抽取实体关系并归纳、审核本体候选，适合本体持续演化。' },
  { key: 'full', label: '全量知识构建', description: '执行检索、实体图谱和本体演化的完整挖掘链路。' },
]
const selectedTemplateDescription = computed(() => templates.find(item => item.key === form.value.template_key)?.description ?? '')

async function load() {
  loading.value = true
  try {
    workflows.value = await api.list({ include_archived: true })
  } catch (error) {
    ElMessage.error(`加载 Workflow 失败：${errorMessage(error)}`)
  } finally {
    loading.value = false
  }
}

function openCreate() {
  form.value = {
    name: '', description: '', template_key: 'full', schema_version: '2.0',
  }
  createVisible.value = true
}

async function createWorkflow() {
  const name = form.value.name.trim()
  if (!name) {
    ElMessage.warning('请填写名称')
    return
  }
  creating.value = true
  const payload: CreateMiningWorkflowRequest = {
    name,
    description: form.value.description.trim() || undefined,
    template_key: form.value.template_key,
    schema_version: form.value.schema_version,
  }
  try {
    const workflow = await api.create(payload)
    createVisible.value = false
    await router.push({ name: 'mining-workflow-editor', params: { id: workflow.id } })
  } catch (error) {
    ElMessage.error(isErrorCode(error, 'workflow_name_conflict') ? '名称已存在，请保留当前输入后修改' : `创建失败：${errorMessage(error)}`)
  } finally {
    creating.value = false
  }
}

function edit(id: string) {
  router.push({ name: 'mining-workflow-editor', params: { id } })
}

function openCopy(workflow: MiningWorkflow) {
  copySource.value = workflow
  copyName.value = `${workflow.name} copy`
  copyVisible.value = true
}

async function confirmCopy() {
  if (!copySource.value) return
  await copyWorkflow(copySource.value, copyName.value)
}

async function copyWorkflow(workflow: MiningWorkflow, requestedName?: string) {
  const name = (requestedName ?? `${workflow.name} copy`).trim()
  if (!name) {
    ElMessage.warning('请填写复制后的名称')
    return
  }
  try {
    const cloned = await api.clone(workflow.id, { name })
    copyVisible.value = false
    await router.push({ name: 'mining-workflow-editor', params: { id: cloned.id } })
  } catch (error) {
    ElMessage.error(isErrorCode(error, 'workflow_name_conflict') ? '名称已存在，请修改后重试' : `复制失败：${errorMessage(error)}`)
  }
}

function canArchive(workflow: MiningWorkflow): boolean {
  return workflow.status === 'active' && !workflow.is_system_default
}

async function archiveWorkflow(workflow: MiningWorkflow) {
  if (!canArchive(workflow)) return
  try {
    await ElMessageBox.confirm(`归档「${workflow.name}」？已发布版本仍可追溯。`, '归档 Workflow', { type: 'warning' })
    await api.archive(workflow.id)
    ElMessage.success('已归档')
    await load()
  } catch (error) {
    if (error === 'cancel' || error === 'close') return
    ElMessage.error(`归档失败：${errorMessage(error)}`)
  }
}

function isErrorCode(error: unknown, code: string): boolean {
  return (error as { response?: { data?: { detail?: { code?: string } } } })?.response?.data?.detail?.code === code
}

function errorMessage(error: unknown): string {
  const value = error as { response?: { data?: { detail?: { message?: string } | string } }; message?: string }
  const detail = value?.response?.data?.detail
  return (typeof detail === 'string' ? detail : detail?.message) || value?.message || '未知错误'
}

onMounted(load)
</script>

<style scoped>
.workflow-list { padding: 4px; }
.workflow-list__header { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 14px; }
.workflow-list__header h2 { margin: 0; font-size: 20px; }
.workflow-list__header p { margin: 5px 0 0; color: var(--kb-text-tertiary); font-size: 13px; }
.workflow-list__notice { margin-bottom: 14px; padding: 9px 12px; border-radius: 7px; color: #1d4ed8; background: #eff6ff; font-size: 12px; }
.workflow-list__link { border: 0; background: none; padding: 0; margin-right: 7px; color: var(--kb-accent, #2563eb); font-weight: 650; cursor: pointer; }
.workflow-list__template-desc { margin: -5px 0 0 90px; padding: 8px; background: #f8fafc; color: var(--kb-text-tertiary); font-size: 12px; }
</style>

