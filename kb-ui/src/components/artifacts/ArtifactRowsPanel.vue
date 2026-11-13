<template>
  <div class="rows-panel">
    <div class="rows-panel__bar">
      <div class="rows-panel__bar-left">
        <el-switch v-model="draft" data-test="rows-draft" @change="onDraftChange" />
        <span class="rows-panel__bar-label">草稿视图（draft）</span>
        <span v-if="draft && !draftOpen" class="rows-panel__hint">
          当前与已发布版本一致；在此编辑将自动检出草稿
        </span>
      </div>
      <span class="rows-panel__count">共 {{ total }} 行</span>
      <div v-if="canWrite" class="rows-panel__bar-actions">
        <el-button size="small" data-test="rows-batch-assign" @click="openAssign">批量赋值</el-button>
        <el-button size="small" type="primary" plain data-test="rows-add" @click="openAdd">
          <el-icon class="rows-panel__add-icon"><Plus /></el-icon>新增行
        </el-button>
      </div>
    </div>

    <div v-loading="loading" class="rows-panel__table">
      <ArtifactTableView
        :columns="editableColumns"
        :rows="rows"
        :editable="canWrite"
        :index-offset="(page - 1) * pageSize"
        :footer-text="`${total} 行 · ${editableColumns.length} 列（${draft ? '草稿' : '已发布'}口径）`"
        @cell-dblclick="row => openEdit(row)"
        @provenance="showProvenance"
      >
        <template #empty>
          <EmptyState :text="draft ? '草稿无数据行（上传 Excel 或等待导入）' : '已发布版本无数据行'" />
        </template>
        <template #foot-hint>
          {{ canWrite ? '双击单元格编辑该行；来源列查看记账' : '只读' }}
        </template>
      </ArtifactTableView>
    </div>

    <el-pagination
      v-if="total > pageSize"
      class="rows-panel__pager"
      layout="total, prev, pager, next"
      :total="total"
      :page-size="pageSize"
      :current-page="page"
      @current-change="onPageChange"
    />

    <!-- 编辑行：按列类型分派控件（008 UI：长文本多行/数字/日期/布尔三态） -->
    <el-dialog v-model="editVisible" title="编辑行" width="600px">
      <el-form label-width="130px">
        <el-form-item label="行键">
          <span class="rows-panel__rowkey">{{ decodeRowKey(editForm.rowKey, pkColumns.length > 0) }}</span>
        </el-form-item>
        <el-form-item v-for="col in editableColumns" :key="col.column_id" :label="col.column_name">
          <CellEditor
            :column="col"
            :model-value="editForm.values[col.column_name]"
            @update:model-value="editForm.values[col.column_name] = $event"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button
          v-if="editingRow"
          type="danger"
          plain
          data-test="rows-delete"
          @click="removeRow(editingRow)"
        >删除行</el-button>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" data-test="rows-edit-submit" @click="submitEdit">保存</el-button>
      </template>
    </el-dialog>

    <!-- 来源记账/冲突明细（原 el-table 展开列迁移） -->
    <el-dialog v-model="provenanceVisible" title="数据来源" width="560px">
      <template v-if="provenanceRow">
        <el-descriptions v-if="provenanceOf(provenanceRow).length" :column="2" size="small" border>
          <el-descriptions-item
            v-for="(p, i) in provenanceOf(provenanceRow)"
            :key="i"
            :label="`来源 ${i + 1}`"
          >
            <el-tooltip :content="provenanceTitle(p)" placement="top">
              <span>{{ describeProvenance(p) }}</span>
            </el-tooltip>
          </el-descriptions-item>
        </el-descriptions>
        <p v-else class="rows-panel__hint">无来源记录（手工编辑或来源未记账）</p>
        <el-tag v-if="hasConflicts(provenanceRow)" type="danger" size="small" class="rows-panel__conflict-tag">
          冲突标记：{{ conflictKeys(provenanceRow).join('、') }}
        </el-tag>
      </template>
      <template #footer>
        <el-button @click="provenanceVisible = false">关闭</el-button>
      </template>
    </el-dialog>

    <!-- 批量赋值：目标列 + 值 + 条件行（可多行） -->
    <el-dialog v-model="assignVisible" title="批量赋值" width="620px">
      <el-form label-width="90px">
        <el-form-item label="目标列">
          <el-select v-model="assignForm.column" data-test="assign-column">
            <el-option
              v-for="col in editableColumns"
              :key="col.column_id"
              :value="col.column_name"
              :label="col.column_name"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="赋值">
          <el-input v-model="assignForm.value" style="max-width: 260px" data-test="assign-value" />
        </el-form-item>
        <el-form-item label="条件（可选）">
          <div class="rows-panel__conditions">
            <div v-for="(cond, i) in assignForm.conditions" :key="i" class="rows-panel__cond">
              <el-select v-model="cond.column" placeholder="列" style="width: 140px">
                <el-option
                  v-for="col in editableColumns"
                  :key="col.column_id"
                  :value="col.column_name"
                  :label="col.column_name"
                />
              </el-select>
              <el-select v-model="cond.op" style="width: 120px" data-test="assign-op">
                <el-option v-for="op in WHERE_OPS" :key="op.value" :value="op.value" :label="op.label" />
              </el-select>
              <el-input
                v-if="cond.op !== 'is_null' && cond.op !== 'not_null'"
                v-model="cond.value"
                placeholder="值（in 用逗号分隔）"
                style="flex: 1"
              />
              <el-button size="small" text type="danger" @click="assignForm.conditions.splice(i, 1)">删</el-button>
            </div>
            <el-button size="small" data-test="assign-add-cond" @click="addCondition">+ 条件</el-button>
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="assignVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" data-test="assign-submit" @click="submitAssign">执行</el-button>
      </template>
    </el-dialog>

    <!-- 新增行：按列生成表单（同编辑的类型化控件），接 POST /table/rows -->
    <el-dialog v-model="addVisible" title="新增行" width="600px">
      <el-alert
        v-if="pkColumns.length"
        type="info"
        :closable="false"
        :title="`主键列（${pkColumns.map(c => c.column_name).join('、')}）必须填写——同键已存在将被拒绝`"
      />
      <el-form label-width="130px" class="rows-panel__add-form">
        <el-form-item v-for="col in editableColumns" :key="col.column_id" :label="col.column_name">
          <CellEditor :column="col" v-model="addForm[col.column_name]" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="addVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" data-test="rows-add-submit" @click="submitAdd">提交</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Plus } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useArtifactsApi } from '@/api/artifacts'
import { apiErrorDetail } from '@/api/proxyClient'
import EmptyState from '@/components/common/EmptyState.vue'
import CellEditor from '@/components/artifacts/ArtifactCellEditor.vue'
import ArtifactTableView from '@/components/artifacts/ArtifactTableView.vue'
import { decodeRowKey } from '@/utils/rowKey'
import type { ArtifactColumn, ArtifactRow } from '@/types/artifacts'

const WHERE_OPS = [
  { value: 'eq', label: '等于' },
  { value: 'ne', label: '不等于' },
  { value: 'in', label: '在集合' },
  { value: 'contains', label: '包含' },
  { value: 'is_null', label: '为空' },
  { value: 'not_null', label: '非空' },
] as const

interface WhereCondition {
  column: string
  op: string
  value: string
}

const props = defineProps<{
  artifactId: string
  canWrite: boolean
  columns: ArtifactColumn[]
  /** 乐观锁令牌（artifact.updated_at）——编辑/删除/批量赋值随请求携带。 */
  expectedUpdatedAt?: string
  draftOpen?: boolean
}>()

const emit = defineEmits<{ changed: [] }>()

const api = useArtifactsApi()

const draft = ref(true)
const rows = ref<ArtifactRow[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = 50
const loading = ref(false)
const saving = ref(false)

const editVisible = ref(false)
const editingRow = ref<ArtifactRow | null>(null)
const editForm = ref<{ rowKey: string; values: Record<string, unknown> }>({ rowKey: '', values: {} })

const assignVisible = ref(false)
const assignForm = ref<{ column: string; value: string; conditions: WhereCondition[] }>({
  column: '', value: '', conditions: [],
})

const addVisible = ref(false)
const addForm = ref<Record<string, unknown>>({})

/** 来源记账弹窗（2026-11-13：展开列迁移为统一表格的 provenance 事件）。 */
const provenanceVisible = ref(false)
const provenanceRow = ref<ArtifactRow | null>(null)

function showProvenance(row: ArtifactRow): void {
  provenanceRow.value = row
  provenanceVisible.value = true
}

/** 可编辑列 = active 列（dropped 不进表单）。 */
const editableColumns = computed(() => props.columns.filter(c => c.status === 'active'))
/** 主键列（行增表单提示 + 前端预检必填）。 */
const pkColumns = computed(() => editableColumns.value.filter(c => c.is_primary_key))

onMounted(loadRows)

async function loadRows(): Promise<void> {
  loading.value = true
  try {
    const result = await api.listRows(props.artifactId, {
      draft: draft.value, page: page.value, size: pageSize,
    })
    rows.value = result.rows
    total.value = result.total
  } catch (error) {
    rows.value = []
    total.value = 0
    ElMessage.error(`加载行数据失败：${await apiErrorDetail(error)}`)
  } finally {
    loading.value = false
  }
}

function onDraftChange(): void {
  page.value = 1
  void loadRows()
}

function onPageChange(next: number): void {
  page.value = next
  void loadRows()
}





/** provenance 后端记账为数组（[{batch_id, file_name, sheet_name, row_index}]）。 */
function provenanceOf(row: ArtifactRow): Array<Record<string, unknown>> {
  return Array.isArray(row.provenance) ? (row.provenance as Array<Record<string, unknown>>) : []
}

/** 来源账主文案：文件/工作表/行号（人可读）；批次 id 收进 title 提示。 */
function describeProvenance(p: Record<string, unknown>): string {
  return `${p.file_name ?? '?'} / ${p.sheet_name ?? '?'} 第 ${p.row_index ?? '?'} 行`
}

function provenanceTitle(p: Record<string, unknown>): string {
  return `批次 ${String(p.batch_id ?? '?')}`
}

function hasConflicts(row: ArtifactRow): boolean {
  return !!row.conflicts && Object.keys(row.conflicts).length > 0
}

function conflictKeys(row: ArtifactRow): string[] {
  return row.conflicts ? Object.keys(row.conflicts) : []
}

/** 409 stale_artifact → 提示刷新（emit changed 触发父级 reload 刷新乐观锁令牌）。 */
function isStale(error: unknown): boolean {
  const resp = (error as { response?: { status?: number; data?: { detail?: { code?: string } } } })
    ?.response
  return resp?.status === 409 && resp.data?.detail?.code === 'stale_artifact'
}

function openEdit(row: ArtifactRow): void {
  editingRow.value = row
  const values: Record<string, unknown> = {}
  editableColumns.value.forEach(col => { values[col.column_name] = row.data[col.column_name] ?? null })
  editForm.value = { rowKey: row.row_key, values }
  editVisible.value = true
}

/** 按列类型把表单字符串归一为 jsonb 值（空串→null）。 */
function coerceValue(columnName: string, raw: unknown): unknown {
  if (raw === null || raw === undefined) return null
  if (typeof raw !== 'string') return raw
  const trimmed = raw.trim()
  if (!trimmed) return null
  const col = editableColumns.value.find(c => c.column_name === columnName)
  const type = col?.column_type
  if (type === 'int' || type === 'float') {
    const num = Number(trimmed)
    return Number.isFinite(num) ? num : trimmed
  }
  if (type === 'bool') {
    if (trimmed.toLowerCase() === 'true') return true
    if (trimmed.toLowerCase() === 'false') return false
  }
  return trimmed
}

async function submitEdit(): Promise<void> {
  const original = editingRow.value
  if (!original) return
  const changes = editableColumns.value
    .map(col => ({
      column: col.column_name,
      value: coerceValue(col.column_name, editForm.value.values[col.column_name]),
    }))
    .filter(({ column, value }) => !deepEqual(value, original.data[column] ?? null))
  if (changes.length === 0) {
    editVisible.value = false
    return
  }
  saving.value = true
  try {
    // 乐观锁令牌只在首格携带：后端每格 touch updated_at，同令牌第二格必 409。
    // 首格校验通过即代表动作起点不 stale；后续格省略令牌（后端跳过校验）。
    for (const [index, change] of changes.entries()) {
      await api.updateCell(props.artifactId, original.row_key, {
        ...change,
        ...(index === 0 ? { expected_updated_at: props.expectedUpdatedAt } : {}),
      })
    }
    ElMessage.success(`已更新 ${changes.length} 格`)
    editVisible.value = false
    emit('changed')
    await loadRows()
  } catch (error) {
    if (isStale(error)) {
      ElMessage.error('制品已被他人修改（409 stale_artifact），已为你刷新——请重试编辑')
      emit('changed')
    } else {
      ElMessage.error(`保存失败：${await apiErrorDetail(error)}`)
    }
  } finally {
    saving.value = false
  }
}

async function removeRow(row: ArtifactRow): Promise<void> {
  try {
    await ElMessageBox.confirm(`确定删除行「${row.row_key}」？（草稿态删除，发布前可丢弃草稿找回）`, '删除行', { type: 'warning' })
  } catch {
    return
  }
  try {
    await api.deleteRow(props.artifactId, row.row_key, props.expectedUpdatedAt)
    ElMessage.success('已删除')
    if (editingRow.value?.row_key === row.row_key) editVisible.value = false
    emit('changed')
    await loadRows()
  } catch (error) {
    if (isStale(error)) {
      ElMessage.error('制品已被他人修改（409 stale_artifact），已为你刷新——请重试删除')
      emit('changed')
    } else {
      ElMessage.error(`删除失败：${await apiErrorDetail(error)}`)
    }
  }
}

function openAssign(): void {
  assignForm.value = { column: '', value: '', conditions: [] }
  assignVisible.value = true
}

function addCondition(): void {
  assignForm.value.conditions.push({ column: '', op: 'eq', value: '' })
}

/** 组装 batch-assign 请求体：is_null/not_null 无值；in 拆数组；值按目标列类型归一。 */
function buildAssignPayload(): {
  column: string
  value: unknown
  where: Array<{ column: string; op: string; value?: unknown }>
} {
  const where = assignForm.value.conditions
    .filter(cond => cond.column)
    .map(cond => {
      if (cond.op === 'is_null' || cond.op === 'not_null') {
        return { column: cond.column, op: cond.op }
      }
      const raw = cond.value.trim()
      if (cond.op === 'in') {
        return {
          column: cond.column, op: cond.op,
          value: raw ? raw.split(/[，,]/).map(v => coerceValue(cond.column, v.trim())) : [],
        }
      }
      return { column: cond.column, op: cond.op, value: coerceValue(cond.column, raw) }
    })
  return { column: assignForm.value.column, value: coerceValue(assignForm.value.column, assignForm.value.value), where }
}

async function submitAssign(): Promise<void> {
  if (!assignForm.value.column) {
    ElMessage.warning('请选择目标列')
    return
  }
  saving.value = true
  try {
    const { updated } = await api.batchAssign(props.artifactId, {
      ...buildAssignPayload(), expected_updated_at: props.expectedUpdatedAt,
    })
    ElMessage.success(`已批量更新 ${updated} 行`)
    assignVisible.value = false
    emit('changed')
    await loadRows()
  } catch (error) {
    if (isStale(error)) {
      ElMessage.error('制品已被他人修改（409 stale_artifact），已为你刷新——请重试')
      emit('changed')
    } else {
      ElMessage.error(`批量赋值失败：${await apiErrorDetail(error)}`)
    }
  } finally {
    saving.value = false
  }
}

function openAdd(): void {
  const form: Record<string, unknown> = {}
  editableColumns.value.forEach(col => { form[col.column_name] = null })
  addForm.value = form
  addVisible.value = true
}

/** 手工行增：按列类型归一表单值 → POST /table/rows（乐观锁令牌随请求）。 */
async function submitAdd(): Promise<void> {
  const data: Record<string, unknown> = {}
  editableColumns.value.forEach(col => {
    data[col.column_name] = coerceValue(col.column_name, addForm.value[col.column_name])
  })
  const missingPk = pkColumns.value.filter(
    col => data[col.column_name] === null || data[col.column_name] === '')
  if (missingPk.length) {
    ElMessage.warning(`主键列必须填写：${missingPk.map(c => c.column_name).join('、')}`)
    return
  }
  saving.value = true
  try {
    const row = await api.insertRow(props.artifactId, {
      data, expected_updated_at: props.expectedUpdatedAt,
    })
    ElMessage.success(`已新增行「${row.row_key}」`)
    addVisible.value = false
    emit('changed')
    await loadRows()
  } catch (error) {
    if (isStale(error)) {
      ElMessage.error('制品已被他人修改（409 stale_artifact），已为你刷新——请重试新增')
      emit('changed')
    } else {
      ElMessage.error(`新增行失败：${await apiErrorDetail(error)}`)
    }
  } finally {
    saving.value = false
  }
}

function deepEqual(a: unknown, b: unknown): boolean {
  return JSON.stringify(a) === JSON.stringify(b)
}

defineExpose({
  loadRows, openEdit, submitEdit, removeRow, openAssign, submitAssign,
  openAdd, submitAdd, buildAssignPayload, onDraftChange,
})
</script>

<style scoped>
.rows-panel { display: flex; flex-direction: column; gap: 10px; }
.rows-panel__bar { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.rows-panel__bar-left { display: flex; align-items: center; gap: 8px; flex: 1; }
.rows-panel__bar-label { font-size: 13px; }
.rows-panel__hint { font-size: 12px; color: var(--el-color-warning, #e6a23c); }
.rows-panel__count { font-size: 12.5px; color: var(--kb-text-tertiary); }
.rows-panel__bar-actions { display: flex; gap: 8px; }
.rows-panel__bar-actions .el-button + .el-button { margin-left: 0; }
.rows-panel__add-icon { margin-right: 4px; }
.rows-panel__table { min-width: 0; }
.rows-panel__conflict-tag { margin-top: 10px; }
.rows-panel__rowkey { font-family: var(--el-font-family, inherit); cursor: default; }
.rows-panel__pager { justify-content: flex-end; }
.rows-panel__conditions { display: flex; flex-direction: column; gap: 6px; align-items: flex-start; }
.rows-panel__cond { display: flex; gap: 6px; align-items: center; width: 100%; }
.rows-panel__add-form { margin-top: 10px; }
</style>
