<template>
  <div class="tbq" data-testid="table-query-panel">
    <!-- 查询构建区 -->
    <div class="tbq__builder">
      <div class="tbq__row">
        <span class="tbq__label">筛选</span>
        <template v-for="(f, i) in filters" :key="i">
          <el-select v-model="f.field" size="small" class="tbq__field" placeholder="字段"
                     @change="f.op = f.field ? defaultOp(f.field) : undefined">
            <el-option v-for="c in columns" :key="c.name" :value="c.name"
                       :label="`${c.name}（${typeLabel(c.value_type)}）`" />
          </el-select>
          <el-select v-model="f.op" size="small" class="tbq__op" placeholder="操作" data-testid="table-query-filter-op">
            <el-option v-for="op in opsOf(f.field)" :key="op" :value="op" :label="opLabel(op)" />
          </el-select>
          <el-input v-if="f.op !== 'is_null'" v-model="f.value" size="small"
                    class="tbq__value" data-testid="table-query-filter-value" :placeholder="f.op ? valueHint(f.field, f.op) : ''"
                    @keyup.enter="run" />
          <el-button link size="small" @click="filters.splice(i, 1)">✕</el-button>
        </template>
        <el-button size="small" plain @click="addFilter">+ 条件</el-button>
      </div>
      <div class="tbq__row">
        <span class="tbq__label">排序</span>
        <el-select v-model="orderField" size="small" class="tbq__field" clearable placeholder="（不排序）" data-testid="table-query-order-field">
          <el-option v-for="c in sortableColumns" :key="c.name" :value="c.name" :label="c.name" />
        </el-select>
        <el-select v-model="orderDir" size="small" class="tbq__op" :disabled="!orderField" data-testid="table-query-order-dir">
          <el-option value="asc" label="升序" />
          <el-option value="desc" label="降序" />
        </el-select>
        <span class="tbq__label">统计</span>
        <el-select v-model="aggOp" size="small" class="tbq__op" clearable placeholder="（不统计）">
          <el-option value="count" label="计数 count" />
          <el-option v-for="c in aggregableColumns" :key="c.name" :value="c.name" disabled
                     :label="`—— ${c.name} ——`" />
          <template v-for="c in aggregableColumns" :key="c.name">
            <el-option v-for="op in aggOpsOf(c)" :key="op + c.name" :value="`${op}:${c.name}`"
                       :label="`${op}(${c.name})`" />
          </template>
        </el-select>
        <el-button size="small" type="primary" :loading="loading" @click="run"
                   data-testid="table-query-run">查询</el-button>
      </div>
    </div>

    <!-- 错误（typed → 可修正提示；绝不退化模糊搜索） -->
    <el-alert v-if="error" type="error" :closable="false" class="tbq__error"
              :title="errorTitle" :description="error" data-testid="table-query-error" />

    <!-- 统计结果 -->
    <div v-if="aggregate" class="tbq__aggregate" data-testid="table-query-aggregate">
      {{ aggregate.op }}<template v-if="aggregate.field">({{ aggregate.field }})</template>
      = <b>{{ aggregate.value }}</b>
      <span class="tbq__muted">（符合条件 {{ aggregate.row_count }} 行）</span>
    </div>

    <!-- 选列（completeness-5：select 投影复选框） -->
    <div v-if="columns.length" class="tbq__row tbq__select-cols">
      <span class="tbq__label">显示列</span>
      <el-checkbox v-for="c in columns" :key="c.name" :model-value="selectedColumns.includes(c.name)"
                   size="small" @change="toggleColumn(c.name)" data-testid="table-query-col-check">
        {{ c.name }}
      </el-checkbox>
    </div>

    <!-- 结果 -->
    <div v-if="rows.length" class="tbq__result">
      <table class="tbq__table" data-testid="table-query-rows">
        <thead>
          <tr>
            <th v-for="c in resultColumns" :key="c">{{ c }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in rows" :key="String(r._row)" class="tbq__row-hit"
              data-testid="table-query-row" @click="$emit('row-click', r._row)">
            <td v-for="c in resultColumns" :key="c">{{ r[c] }}</td>
          </tr>
        </tbody>
      </table>
      <div class="tbq__pager">
        <span class="tbq__muted">{{ rows.length }} 行{{ hasMore ? '（更多未列）' : '' }}</span>
        <el-button v-if="hasMore" size="small" :loading="loading" :disabled="loading" data-testid="table-query-next" @click="runNext">{{ queryChanged ? '条件已修改，重新查询' : '下一页' }}</el-button>
      </div>
    </div>
    <p v-else-if="!loading && searched" class="tbq__muted">无符合条件的行——调整筛选条件试试。</p>

    <!-- 列能力面（schema 驱动的自描述） -->
    <details class="tbq__schema">
      <summary>列类型与可用操作</summary>
      <ul>
        <li v-for="c in columns" :key="c.name">
          <b>{{ c.name }}</b>（{{ typeLabel(c.value_type) }}）：{{ c.operations.join(' / ') }}
        </li>
      </ul>
    </details>
  </div>
</template>

<script setup lang="ts">
/**
 * A3 表格精确查询面板（34 号 P0-5；39 号 §3.3）。
 *
 * - 复用 serving StructuredQueryService（与 MCP get_knowledge 同源）；
 * - schema 驱动：字段类型（number/text/date）决定可用操作符与聚合；
 * - typed error 原样呈现（可修正），查询失败绝不退化为模糊搜索；
 * - 行点击回表格锚（_row = 预览行号，父组件高亮）。
 */
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { useServingApi, type TableFieldSchema, type TableQueryResult, type TableQuerySpec } from '@/api/serving'

const props = defineProps<{
  /** 表格资产 ref（内部 "{doc}#table:{t}" 或 st_） */
  assetRef: string
  kbId: string
  domain: string
  defaultColumns?: string[]
}>()
const emit = defineEmits<{ 'row-click': [row: number | undefined] }>()

const servingApi = useServingApi()
const columns = ref<TableFieldSchema[]>([])
const rows = ref<TableQueryResult['rows']>([])
const selectedColumns = ref<string[]>([])
const resultColumns = ref<string[]>([])
const executedSpec = ref<TableQuerySpec | null>(null)
/** completeness-5：列复选框开关（至少保留一列，空投影=全列语义不直观）。 */
function toggleColumn(name: string) {
  const has = selectedColumns.value.includes(name)
  if (!has) {
    selectedColumns.value = [...selectedColumns.value, name]
    return
  }
  if (selectedColumns.value.length <= 1) return // 保底一列
  selectedColumns.value = selectedColumns.value.filter(c => c !== name)
}
const hasMore = ref(false)
const cursor = ref<string | undefined>()
const aggregate = ref<TableQueryResult['aggregate']>(null)
const loading = ref(false)
const searched = ref(false)
const error = ref('')
const errorTitle = ref('查询失败')

type Filter = { field: string | undefined; op: string | undefined; value: string }
const filters = ref<Filter[]>([])
const orderField = ref<string | undefined>()
const orderDir = ref<'asc' | 'desc'>('asc')
const aggOp = ref<string | undefined>()

const OP_LABELS: Record<string, string> = {
  eq: '=', ne: '≠', lt: '<', lte: '≤', gt: '>', gte: '≥',
  in: '属于', contains: '包含', is_null: '为空',
}
const opLabel = (op: string) => OP_LABELS[op] ?? op
const typeLabel = (t: string) => t === 'number' ? '数值' : t === 'date' ? '日期' : '文本'

function schemaOf(field: string | undefined): TableFieldSchema | undefined {
  return columns.value.find(c => c.name === field)
}
function opsOf(field: string | undefined): string[] {
  return (schemaOf(field)?.operations ?? []).filter(op =>
    ['eq', 'ne', 'lt', 'lte', 'gt', 'gte', 'in', 'contains', 'is_null'].includes(op))
}
function defaultOp(field: string): string {
  const ops = opsOf(field)
  return ops.includes('eq') ? 'eq' : (ops[0] ?? 'eq')
}
function valueHint(field: string | undefined, op: string): string {
  const type = schemaOf(field)?.value_type
  if (op === 'in') return '多个值，逗号分隔'
  if (type === 'date') return 'YYYY-MM-DD'
  if (type === 'number') return '数值'
  return '文本'
}
const sortableColumns = computed(() => columns.value.filter(c => c.sortable))
const aggregableColumns = computed(() => columns.value.filter(c => c.can_aggregate))
function aggOpsOf(c: TableFieldSchema): string[] {
  return c.value_type === 'date' ? ['min', 'max'] : ['sum', 'min', 'max', 'avg']
}
function addFilter() {
  const field = columns.value[0]?.name
  filters.value = [...filters.value, { field, op: field ? defaultOp(field) : undefined, value: '' }]
}

function buildSpec(): TableQuerySpec {
  const where = filters.value
    .filter(f => f.field && f.op)
    .filter(f => f.op === 'is_null' || String(f.value).trim() !== '')
    .map(f => ({
      field: f.field!,
      op: f.op!,
      value: f.op === 'in'
        ? String(f.value).split(',').map(v => v.trim()).filter(Boolean)
        : schemaOf(f.field)?.value_type === 'number'
          ? Number(f.value)
          : f.value,
    }))
  const spec: TableQuerySpec = { where, limit: 20 }
  // completeness-5：选列 = select 投影（服务端裁列，传输与渲染同源）
  if (selectedColumns.value.length) spec.select = [...selectedColumns.value]
  if (orderField.value) spec.order_by = [{ field: orderField.value, direction: orderDir.value }]
  if (aggOp.value) {
    const split = aggOp.value.indexOf(':')
    spec.aggregate = split < 0 ? { op: aggOp.value }
      : { op: aggOp.value.slice(0, split), field: aggOp.value.slice(split + 1) }
  }
  return spec
}

function queryKey(spec: TableQuerySpec) {
  return JSON.stringify([spec.where, spec.select, spec.order_by, spec.aggregate, spec.limit])
}
const queryChanged = computed(() => !executedSpec.value
  || queryKey(buildSpec()) !== queryKey(executedSpec.value))

async function run() {
  if (loading.value) return
  await execute(buildSpec())
}
async function runNext() {
  if (loading.value) return
  if (queryChanged.value || !cursor.value || !executedSpec.value) return run()
  await execute({ ...executedSpec.value, cursor: cursor.value })
}

async function execute(spec: TableQuerySpec) {
  loading.value = true
  error.value = ''
  try {
    const out = await servingApi.queryStructure(props.assetRef, spec, {
      domain: props.domain, kbId: props.kbId,
    })
    if (out.columns?.length && !columns.value.length) {
      columns.value = out.columns
      selectedColumns.value = props.defaultColumns?.length
        ? props.defaultColumns.filter(c => out.columns.some(s => s.name === c))
        : out.columns.map(c => c.name)
    }
    resultColumns.value = spec.select?.length ? [...spec.select] : [...selectedColumns.value]
    if (!spec.cursor) executedSpec.value = { ...spec, select: [...resultColumns.value] }
    // 聚合态由响应声明（aggregate 出现即展示；行结果为空）
    aggregate.value = out.aggregate ?? null
    rows.value = spec.cursor
      ? [...rows.value, ...(out.rows ?? [])]
      : (out.rows ?? [])
    hasMore.value = out.has_more
    cursor.value = out.cursor ?? undefined
    searched.value = true
  } catch (e) {
    const err = e as { response?: { data?: { error?: string; message?: string } }; message?: string }
    const code = err.response?.data?.error ?? ''
    error.value = err.response?.data?.message || err.message || '查询失败'
    errorTitle.value = ({
      unknown_field: '字段不存在',
      type_mismatch: '类型不匹配',
      unsupported_operation: '不支持的操作',
      result_too_large: '结果过大',
      structured_query_unavailable: '该表格暂不可精确查询',
    } as Record<string, string>)[code] ?? '查询失败'
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  // 初始查询：拿列 schema + 首屏行（select 缺省全列）
  try {
    await run()
  } catch {
    ElMessage.error('表格查询面板初始化失败')
  }
})
</script>

<style scoped>
.tbq { border: 1px solid var(--el-border-color-lighter); border-radius: 6px; padding: 10px; margin-top: 8px; }
.tbq__builder { display: flex; flex-direction: column; gap: 6px; }
.tbq__row { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.tbq__label { color: var(--el-text-color-secondary); font-size: 13px; min-width: 32px; }
.tbq__field { width: 170px; }
.tbq__op { width: 100px; }
.tbq__value { width: 150px; }
.tbq__error { margin-top: 8px; }
.tbq__aggregate { margin-top: 8px; font-size: 14px; }
.tbq__result { margin-top: 10px; overflow-x: auto; }
.tbq__table { width: 100%; border-collapse: collapse; font-size: 13px; }
.tbq__table th, .tbq__table td { border: 1px solid var(--el-border-color-lighter); padding: 4px 8px; text-align: left; }
.tbq__table th { background: var(--el-fill-color-light); }
.tbq__row-hit { cursor: pointer; }
.tbq__row-hit:hover { background: var(--el-fill-color); }
.tbq__pager { display: flex; align-items: center; gap: 10px; margin-top: 6px; }
.tbq__muted { color: var(--el-text-color-secondary); font-size: 12px; }
.tbq__schema { margin-top: 8px; font-size: 12px; color: var(--el-text-color-secondary); }
</style>
