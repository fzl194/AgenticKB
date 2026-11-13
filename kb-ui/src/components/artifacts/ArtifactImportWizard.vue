<template>
  <div class="import-wizard">
    <!-- 卡① 选文件：上传 xlsx → 拿 preview_token + 预览（sheet 选择） -->
    <el-card class="import-wizard__step" shadow="never">
      <template #header>
        <b>① 选择文件</b>
        <span class="import-wizard__hint">（.xlsx，≤50MB；解析后 1 小时内有效）</span>
      </template>
      <el-upload
        accept=".xlsx"
        :show-file-list="false"
        :http-request="uploadRequest"
        class="import-wizard__upload"
      >
        <el-button type="primary" :loading="uploading" data-test="wizard-upload">
          {{ envelope ? '重新上传' : '上传 Excel' }}
        </el-button>
        <template #tip>
          <span v-if="envelope" class="import-wizard__file">
            已解析：{{ envelope.preview.file_name }}
          </span>
        </template>
      </el-upload>
      <div v-if="envelope" class="import-wizard__sheet-row">
        <span>工作表：</span>
        <el-select v-model="sheet" size="small" style="width: 200px" data-test="wizard-sheet">
          <el-option
            v-for="s in envelope.preview.sheets"
            :key="s.name"
            :value="s.name"
            :label="s.name"
          />
        </el-select>
        <span class="import-wizard__hint">（每个工作表独立推断列类型；切换会重建列映射）</span>
      </div>
    </el-card>

    <!-- 卡② 列映射：每个文件列与对应目标映射共用一行，长枚举值也不串行。 -->
    <el-card v-if="envelope" class="import-wizard__step" shadow="never">
      <template #header>
        <b>② 列映射</b>
        <span class="import-wizard__hint">（{{ hasExisting ? '追加模式：至少一列映射到已有列；文件新列可新建' : '建表模式：为每列选择目标类型，建议指定主键' }}）</span>
      </template>
      <div class="import-wizard__map">
        <div class="import-wizard__map-head">
          <div class="import-wizard__pane-title">文件列（类型推断：{{ sheet }}）</div>
          <div class="import-wizard__pane-title">目标映射（{{ mappings.length }} 列）</div>
        </div>
        <div
          v-for="(m, index) in mappings"
          :key="m.source"
          class="import-wizard__map-row"
          :class="{ 'import-wizard__map-row--drop': m.action === 'drop' }"
        >
          <div class="import-wizard__source">
            <span class="import-wizard__source-name" :title="m.source">{{ m.source }}</span>
            <el-tag size="small" effect="plain" :type="typeTagType(activeSheetColumns[index]?.inferred_type ?? '')">
              {{ typeLabel(activeSheetColumns[index]?.inferred_type ?? '') }}
            </el-tag>
          </div>
          <div class="import-wizard__map-controls">
            <el-select
              v-model="m.action"
              size="small"
              style="width: 120px"
              data-test="map-action"
              @change="onActionChange(m)"
            >
              <el-option value="new" label="新建列" />
              <el-option v-if="hasExisting" value="map_to" label="映射已有列" />
              <el-option value="drop" label="丢弃" />
            </el-select>
            <template v-if="m.action === 'map_to'">
              <el-select
                v-model="m.targetColumnId"
                size="small"
                style="width: 180px"
                placeholder="选择目标列"
                data-test="map-target"
              >
                <el-option
                  v-for="col in existingColumns"
                  :key="col.column_id"
                  :value="col.column_id"
                  :label="col.column_name"
                />
              </el-select>
              <el-tag v-if="mappedColumnType(m)" size="small" effect="plain" type="info">
                {{ mappedColumnType(m) }}
              </el-tag>
            </template>
            <template v-else-if="m.action === 'new'">
              <el-input v-model="m.targetName" size="small" style="width: 150px" placeholder="目标列名" />
              <el-select v-model="m.columnType" size="small" style="width: 96px" data-test="map-type">
                <el-option v-for="t in COLUMN_TYPES" :key="t.value" :value="t.value" :label="t.label" />
              </el-select>
              <template v-if="m.columnType === 'enum'">
                <span class="import-wizard__enum">
                  <el-tag
                    v-for="v in m.enumValues"
                    :key="v"
                    size="small"
                    closable
                    @close="removeEnumValue(m, v)"
                  >{{ v }}</el-tag>
                </span>
                <el-input
                  v-model="m.enumInput"
                  size="small"
                  style="width: 110px"
                  placeholder="+ 枚举值"
                  @keyup.enter="addEnumValue(m)"
                />
              </template>
              <!-- 主键多选（组合主键，后端本就支持多列）；仅建表模式有意义（追加
                   模式沿用 schema 既有主键，plan 标记被后端忽略） -->
              <label v-if="!hasExisting" class="import-wizard__check">
                <input
                  type="checkbox"
                  v-model="m.isPrimaryKey"
                  data-test="map-pk"
                />主键（可多选）
              </label>
              <label class="import-wizard__check">
                <input type="checkbox" v-model="m.isIndexed" />索引
              </label>
            </template>
            <span v-else class="import-wizard__hint">本列不导入</span>
          </div>
        </div>
      </div>
      <el-alert
        v-if="missingColumns.length"
        type="warning"
        :closable="false"
        class="import-wizard__missing"
        :title="`以下列本批将为空：${missingColumns.join('、')}`"
      />
      <div class="import-wizard__actions">
        <el-button
          type="primary"
          :disabled="!mappingComplete"
          data-test="wizard-confirm-mapping"
          @click="confirmMapping"
        >下一步：预览确认</el-button>
      </div>
    </el-card>

    <!-- 卡③ 预览确认：投影前 50 行 + 冲突策略 + 执行导入 → 报告 -->
    <el-card v-if="envelope && mappingConfirmed" class="import-wizard__step" shadow="never">
      <template #header>
        <b>③ 预览确认</b>
        <span class="import-wizard__hint">（按映射投影的前 {{ projectedRows.length }} 行样例）</span>
      </template>
      <template v-if="!report">
        <!-- 统一表格视图（与设计态/运行态同形态）：投影样例只读预览 -->
        <div class="import-wizard__preview">
          <ArtifactTableView
            :columns="previewColumns"
            :rows="previewRows"
            :editable="false"
            :show-sources="false"
            :footer-text="`样例 ${previewRows.length} 行 · ${previewColumns.length} 列（按映射投影）`"
          >
            <template #empty>文件无数据行</template>
          </ArtifactTableView>
        </div>
        <div class="import-wizard__policy">
          <span>类型错误：</span>
          <el-select v-model="conflictPolicy.type_error" size="small" style="width: 140px">
            <el-option value="strict" label="阻断（默认）" />
            <el-option value="tolerant" label="容错置空并标记" />
          </el-select>
          <span>主键冲突：</span>
          <el-select v-model="conflictPolicy.key_conflict" size="small" style="width: 140px">
            <el-option value="block" label="阻断（默认）" />
            <el-option value="keep_first" label="保留首行" />
          </el-select>
        </div>
        <div class="import-wizard__actions">
          <el-button type="primary" :loading="importing" data-test="wizard-import" @click="runImport">
            执行导入
          </el-button>
        </div>
      </template>
      <template v-else>
        <div class="import-wizard__report">
          <el-tag type="success">处理 {{ report.row_count ?? 0 }} 行</el-tag>
          <el-tag type="success">新增 {{ report.inserted ?? 0 }}</el-tag>
          <el-tag type="warning">更新 {{ report.updated ?? 0 }}</el-tag>
          <el-tag :type="(report.type_errors ?? 0) > 0 ? 'danger' : 'info'">
            类型错误 {{ report.type_errors ?? 0 }}
          </el-tag>
          <el-tag :type="(report.key_conflicts ?? 0) > 0 ? 'danger' : 'info'">
            键冲突 {{ report.key_conflicts ?? 0 }}
          </el-tag>
        </div>
        <el-alert
          v-if="(report.missing_columns ?? []).length"
          type="warning"
          :closable="false"
          class="import-wizard__missing"
          :title="`以下列本批为空：${(report.missing_columns ?? []).map(c => c.column_name).join('、')}`"
        />
        <el-collapse v-if="(report.conflicts_sample ?? []).length" class="import-wizard__conflicts">
          <el-collapse-item :title="`冲突样例（${(report.conflicts_sample ?? []).length} 条）`">
            <el-table :data="report.conflicts_sample ?? []" size="small">
              <el-table-column prop="row_index" label="行号" width="80" />
              <el-table-column prop="row_key" label="行键" min-width="140" show-overflow-tooltip />
              <el-table-column prop="column" label="列" width="120" />
              <el-table-column prop="message" label="说明" min-width="160" show-overflow-tooltip />
            </el-table>
          </el-collapse-item>
        </el-collapse>
        <div class="import-wizard__actions">
          <el-button data-test="wizard-reset" @click="resetWizard">再导入一批</el-button>
        </div>
      </template>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { UploadRequestOptions } from 'element-plus'
import { useArtifactsApi } from '@/api/artifacts'
import ArtifactTableView from '@/components/artifacts/ArtifactTableView.vue'
import { apiErrorDetail } from '@/api/proxyClient'
import type {
  ArtifactColumn, ArtifactRow, ImportConflictPolicy, ImportReport, PreviewColumn,
  PreviewEnvelope, SheetPreview,
} from '@/types/artifacts'

/** 后端 build_mapping 认可的列类型（§6.2 推断口径）。 */
const COLUMN_TYPES = [
  { value: 'text', label: '文本' },
  { value: 'int', label: '整数' },
  { value: 'float', label: '小数' },
  { value: 'date', label: '日期' },
  { value: 'enum', label: '枚举' },
  { value: 'bool', label: '布尔' },
] as const

/** 单个源列的映射决策（卡② 行状态）。 */
interface ColumnMapping {
  source: string
  action: 'new' | 'map_to' | 'drop'
  targetColumnId: string
  targetName: string
  columnType: string
  enumValues: string[]
  enumInput: string
  isPrimaryKey: boolean
  isIndexed: boolean
}

const props = withDefaults(defineProps<{
  artifactId: string
  /** 所在 tab 是否激活（懒加载已有列）。 */
  active?: boolean
}>(), { active: true })

const emit = defineEmits<{ imported: [report: ImportReport] }>()

const api = useArtifactsApi()

const uploading = ref(false)
const importing = ref(false)
const envelope = ref<PreviewEnvelope | null>(null)
const sheet = ref('')
const report = ref<ImportReport | null>(null)
const mappingConfirmed = ref(false)
const mappings = ref<ColumnMapping[]>([])
const conflictPolicy = ref<ImportConflictPolicy>({ type_error: 'strict', key_conflict: 'block' })
const existingColumns = ref<ArtifactColumn[]>([])
const columnsLoaded = ref(false)

const hasExisting = computed(() => existingColumns.value.length > 0)

/** 当前选中 sheet 的预览（codex二审功能项①：列/样例按 sheet 切换）。 */
const activeSheet = computed<SheetPreview | null>(() => {
  const sheets = envelope.value?.preview.sheets ?? []
  return sheets.find(s => s.name === sheet.value) ?? sheets[0] ?? null
})

const activeSheetColumns = computed<PreviewColumn[]>(() => activeSheet.value?.columns ?? [])

const mappedIds = computed(() => new Set(
  mappings.value
    .filter(m => m.action === 'map_to' && m.targetColumnId)
    .map(m => m.targetColumnId),
))

/** 追加模式下 schema 有、本批文件无的列（该批置 null 提示）。 */
const missingColumns = computed(() => {
  if (!hasExisting.value || mappedIds.value.size === 0) return []
  return existingColumns.value
    .filter(c => !mappedIds.value.has(c.column_id) && c.status === 'active')
    .map(c => c.column_name)
})

/** 映射行完备（map_to 有目标 / new 有列名）才允许进卡③。 */
const mappingComplete = computed(() => mappings.value.every(m => {
  if (m.action === 'drop') return true
  if (m.action === 'map_to') return !!m.targetColumnId
  return m.targetName.trim().length > 0
}))

/** 单行映射的目标列名（map_to 取已有列名）。 */
function targetNameOf(m: ColumnMapping): string {
  if (m.action === 'map_to') {
    return existingColumns.value
      .find(c => c.column_id === m.targetColumnId)?.column_name ?? m.targetColumnId
  }
  return m.targetName.trim()
}

/** 卡③ 投影列（映射顺序，drop 除外）。 */
const projectedColumns = computed(() => mappings.value
  .filter(m => m.action !== 'drop')
  .map(targetNameOf))

/** 卡③ 投影行：当前 sheet 的样例行按映射改写到目标列名。 */
const projectedRows = computed(() => {
  const sample = activeSheet.value?.sample_rows ?? []
  return sample.map(row => {
    const out: Record<string, unknown> = {}
    mappings.value.forEach(m => {
      if (m.action === 'drop') return
      out[targetNameOf(m)] = row[m.source] ?? null
    })
    return out
  })
})

/** 统一表格视图适配：投影列名 → 最小 ArtifactColumn 形态（纯文本预览）。 */
const previewColumns = computed<ArtifactColumn[]>(() =>
  projectedColumns.value.map((name, index) => ({
    artifact_id: props.artifactId,
    column_id: `preview-${index}`,
    column_name: name,
    column_type: 'text',
    enum_values: null,
    is_primary_key: false,
    is_indexed: false,
    alias: null,
    status: 'active',
    ordinal: index + 1,
  })))

/** 统一表格视图适配：投影行 → ArtifactRow 形态（预览占位字段）。 */
const previewRows = computed<ArtifactRow[]>(() =>
  projectedRows.value.map((data, index) => ({
    row_key: `preview-${index}`,
    data,
    provenance: null,
    conflicts: null,
    ordinal: index + 1,
  })))

watch(() => props.active, (active) => {
  if (active && !columnsLoaded.value) void loadColumns()
}, { immediate: true })

async function loadColumns(): Promise<void> {
  try {
    existingColumns.value = await api.listColumns(props.artifactId)
  } catch (error) {
    existingColumns.value = []
    ElMessage.error(`加载制品列定义失败：${await apiErrorDetail(error)}`)
  } finally {
    columnsLoaded.value = true
  }
}

/** el-upload 自定义请求 + 测试直调入口。 */
async function uploadRequest(options: UploadRequestOptions): Promise<void> {
  await doUpload(options.file as File)
}

async function doUpload(file: File): Promise<void> {
  uploading.value = true
  try {
    const data = await api.uploadTable(props.artifactId, file)
    // 制品列在两次导入间可能已被他人改动——先重取再初始化映射（保证 map_to 自动匹配新鲜）
    await loadColumns()
    envelope.value = data
    sheet.value = data.preview.default_sheet
    report.value = null
    mappingConfirmed.value = false
    initMappings(data.preview.columns)
  } catch (error) {
    ElMessage.error(`上传失败：${await apiErrorDetail(error)}`)
  } finally {
    uploading.value = false
  }
}

/**
 * 按预览列初始化映射行：已有列精确同名（含 alias）→ map_to，否则新建。
 * 切换 sheet 时用对应 sheet 的 columns 重建（主键/类型/枚举选择全部回到
 * 推断默认——旧 sheet 的手工选择对异构表头无意义，codex二审功能项①）。
 */
function initMappings(columns: PreviewColumn[]): void {
  mappings.value = columns.map(col => {
    const matched = existingColumns.value.find(c =>
      c.status === 'active'
      && (c.column_name === col.source_name || (c.alias ?? []).includes(col.source_name)))
    const inferred = COLUMN_TYPES.some(t => t.value === col.inferred_type)
      ? col.inferred_type : 'text'
    return {
      source: col.source_name,
      action: matched ? 'map_to' : 'new',
      targetColumnId: matched?.column_id ?? '',
      targetName: col.source_name,
      columnType: inferred,
      enumValues: [...col.enum_candidates],
      enumInput: '',
      isPrimaryKey: false,
      isIndexed: false,
    }
  })
}

/** 切换工作表：重建列映射（新 sheet 表头）+ 退回卡② 重新确认。 */
watch(sheet, (name) => {
  if (!envelope.value || !name) return
  const target = envelope.value.preview.sheets.find(s => s.name === name)
  if (!target) return
  initMappings(target.columns)
  mappingConfirmed.value = false
})

function onActionChange(m: ColumnMapping): void {
  if (m.action !== 'new') m.isPrimaryKey = false
}

function addEnumValue(m: ColumnMapping): void {
  const value = m.enumInput.trim()
  if (!value) return
  if (!m.enumValues.includes(value)) m.enumValues = [...m.enumValues, value]
  m.enumInput = ''
}

function removeEnumValue(m: ColumnMapping, value: string): void {
  m.enumValues = m.enumValues.filter(v => v !== value)
}

function confirmMapping(): void {
  if (!mappingComplete.value) {
    ElMessage.warning('请补全映射：映射已有列需选目标，新建列需填列名')
    return
  }
  if (hasExisting.value && mappedIds.value.size === 0) {
    ElMessage.warning('制品已有列定义：至少将一列映射到已有列（全部新建会被后端以 schema_already_exists 拒绝）')
    return
  }
  // 目标列名查重（后端不拦，同名会让 jsonb 数据键折叠）：
  // ① 计划内两列写同一目标名；② 新建列撞已有列名（map_to 指向已有名是合法填充，不算）
  const used = mappings.value.filter(m => m.action !== 'drop').map(targetNameOf)
  const duplicatedInPlan = used.filter((name, index) => used.indexOf(name) !== index)
  const newCollisions = mappings.value
    .filter(m => m.action === 'new')
    .map(m => m.targetName.trim())
    .filter(name => existingColumns.value.some(
      c => c.status === 'active' && c.column_name === name))
  const duplicated = [...new Set([...duplicatedInPlan, ...newCollisions])]
  if (duplicated.length) {
    ElMessage.warning(`目标列名重复：${duplicated.join('、')}——同名列数据会折叠到同一键`)
    return
  }
  mappingConfirmed.value = true
}

/** 组装 import plan 的 column_ops（原始形态，后端补跑 build_mapping）。 */
function buildColumnOps(): Array<Record<string, unknown>> {
  return mappings.value.map(m => {
    if (m.action === 'drop') return { source: m.source, action: 'drop' }
    if (m.action === 'map_to') {
      return { source: m.source, action: 'map_to', target: m.targetColumnId }
    }
    const op: Record<string, unknown> = {
      source: m.source,
      action: 'new',
      target_name: m.targetName.trim(),
      column_type: m.columnType,
      is_primary_key: m.isPrimaryKey,
      is_indexed: m.isIndexed,
    }
    if (m.columnType === 'enum') op.enum_values = m.enumValues
    return op
  })
}

async function runImport(): Promise<void> {
  if (!envelope.value) return
  importing.value = true
  try {
    const result = await api.importTable(props.artifactId, {
      preview_token: envelope.value.preview_token,
      plan: { sheet: sheet.value, header_row: 1, column_ops: buildColumnOps() },
      conflict_policy: { ...conflictPolicy.value },
    })
    report.value = result
    emit('imported', result)
    ElMessage.success(`导入完成：新增 ${result.inserted ?? 0} / 更新 ${result.updated ?? 0}`)
  } catch (error) {
    const response = (error as { response?: { status?: number; data?: { detail?: unknown } } })?.response
    const raw = await apiErrorDetail(error)
    const failure = describeImportFailure(response?.data?.detail, raw, response?.status, sheet.value)
    if (response?.status === 410) resetWizard()
    await ElMessageBox.alert(failure.message, failure.title, {
      type: 'warning', confirmButtonText: '知道了',
    }).catch(() => {})
  } finally {
    importing.value = false
  }
}

/** 执行导入的所有失败都使用可操作的中文说明；不把服务端错误码原样给用户。 */
function describeImportFailure(detail: unknown, raw: string, status: number | undefined, sheetName: string): {
  title: string; message: string
} {
  const info = detail && typeof detail === 'object' ? detail as Record<string, unknown> : null
  const serverMessage = typeof info?.message === 'string' ? info.message : ''
  const serverCode = typeof info?.reason === 'string' ? info.reason
    : typeof info?.code === 'string' ? info.code : ''
  const legacyCode = (typeof detail === 'string' ? detail : raw).split(':', 1)[0].trim()
  const code = serverCode || legacyCode
  const suffix = typeof detail === 'string' && detail.includes(':')
    ? detail.slice(detail.indexOf(':') + 1).trim() : ''
  const sheetLabel = sheetName ? `工作表「${sheetName}」` : '所选工作表'

  if (serverMessage && typeof info?.cell === 'string' && info.cell) {
    return { title: '无法导入：请检查指定单元格', message: serverMessage }
  }
  if (code === 'formula_not_allowed' || raw.includes('formula_not_allowed')) {
    return {
      title: '无法导入：发现 Excel 公式',
      message: `${sheetLabel}的待导入列包含 Excel 公式。公式可能在表格软件中执行，也可能使导入结果与看到的值不一致。请将公式结果粘贴为值后重新上传。当前服务未返回具体单元格位置，请确认挖掘服务已更新。`,
    }
  }
  if (code === 'type_error' || raw.includes('type_error:')) {
    return {
      title: '无法导入：列类型不匹配',
      message: `${sheetLabel}中有值无法按目标列类型导入。请检查列映射与预览数据，或把「类型错误」改为「容错置空」。当前服务未返回具体单元格位置，请确认挖掘服务已更新。`,
    }
  }
  if (code === 'duplicate_key' || raw.includes('duplicate_key:')) {
    return {
      title: '无法导入：主键值重复',
      message: `${sheetLabel}中有两行使用了相同的主键值。请修改重复行，或把「主键冲突」改为「保留首行」。当前服务未返回具体单元格位置，请确认挖掘服务已更新。`,
    }
  }
  if (code === 'unknown_source_column' || code === 'duplicate_source_column') {
    const column = suffix ? `「${suffix}」` : ''
    return { title: '无法导入：文件列映射有误',
      message: `表头${column}${code === 'unknown_source_column' ? '在当前工作表中不存在' : '被重复映射'}。请重新选择工作表并检查列映射后重试。` }
  }
  if (code === 'missing_target_name' || code === 'invalid_column_type' || code === 'invalid_plan_column') {
    return { title: '无法导入：目标列设置不完整',
      message: `请检查${suffix ? `文件列「${suffix}」` : '列映射'}的目标列名和类型，补全后重新预览确认。` }
  }
  const globalReasons: Record<string, string> = {
    preview_expired: '上传文件的预览已过期。请重新上传文件并确认列映射。',
    schema_already_exists: '制品已有列定义，不能按首次建表方式导入。请将至少一列映射到已有列。',
    no_schema: '当前制品还没有列定义，不能按追加模式导入。请先创建列定义。',
    unknown_target_column: '目标列已不存在或发生变化。请刷新页面后重新选择目标列。',
    invalid_conflict_policy: '类型错误或主键冲突的处理方式无效。请重新选择后再导入。',
    unknown_sheet: '选择的工作表已不存在。请重新上传文件并选择工作表。',
    empty_sheet: '选择的工作表没有可导入的表头或数据。请检查 Excel 文件。',
    too_many_rows: '工作表行数超过导入上限。请拆分文件后重试。',
    too_many_columns: '工作表列数超过导入上限。请删减列后重试。',
    empty_column_ops: '没有设置文件列的导入方式。请返回列映射步骤重新确认。',
    empty_plan_columns: '列映射为空。请返回列映射步骤重新确认。',
    invalid_header_row: '表头位置无效。请重新上传文件并检查第一行是否为表头。',
    stale_artifact: '制品已被其他操作修改。请刷新页面，重新上传并确认列映射。',
  }
  if (Object.prototype.hasOwnProperty.call(globalReasons, code)) {
    return { title: '无法导入', message: `${globalReasons[code]}此问题不对应单个表头或单元格。` }
  }
  if (status === 401 || status === 403) {
    return { title: '无法导入：没有操作权限', message: '当前账号没有导入权限或登录已过期。请重新登录或联系制品负责人；此问题不对应单个单元格。' }
  }
  if (status === 410) {
    return { title: '无法导入：预览已过期', message: '预览有效期已结束，请重新上传文件并确认列映射；此问题不对应单个单元格。' }
  }
  if (status === 404) {
    return { title: '无法导入：文件或制品已失效', message: '上传预览或制品已不存在。请刷新页面后重新上传文件；此问题不对应单个单元格。' }
  }
  return { title: '无法导入', message: '本次导入未完成，服务未提供可定位的表头或单元格信息。请刷新页面后重试；若仍失败，请联系管理员检查导入服务。' }
}

function resetWizard(): void {
  envelope.value = null
  sheet.value = ''
  report.value = null
  mappingConfirmed.value = false
  mappings.value = []
}

function mappedColumnType(m: ColumnMapping): string {
  return existingColumns.value
    .find(c => c.column_id === m.targetColumnId)?.column_type ?? ''
}

function typeLabel(value: string): string {
  return COLUMN_TYPES.find(t => t.value === value)?.label ?? value
}

function typeTagType(value: string): 'primary' | 'success' | 'warning' | 'info' {
  if (value === 'enum') return 'warning'
  if (value === 'int' || value === 'float') return 'success'
  if (value === 'date' || value === 'bool') return 'primary'
  return 'info'
}

defineExpose({
  doUpload, runImport, confirmMapping,
  buildColumnOps, resetWizard, initMappings,
})
</script>

<style scoped>
.import-wizard { display: flex; flex-direction: column; gap: 12px; }
.import-wizard__step :deep(.el-card__header) { padding: 10px 16px; }
.import-wizard__hint { font-size: 12px; color: var(--kb-text-tertiary); font-weight: normal; }
.import-wizard__file { margin-left: 10px; font-size: 12.5px; color: var(--kb-text-tertiary); }
.import-wizard__sheet-row { margin-top: 10px; display: flex; align-items: center; gap: 6px; }
.import-wizard__map { min-width: 0; }
.import-wizard__map-head, .import-wizard__map-row { display: grid; grid-template-columns: 220px minmax(0, 1fr); column-gap: 14px; }
.import-wizard__pane-title { font-size: 12.5px; color: var(--kb-text-tertiary); margin-bottom: 6px; }
.import-wizard__map-row { align-items: start; padding: 6px 0; border-bottom: 1px dashed var(--kb-border-light, #eee); }
.import-wizard__source { display: flex; align-items: center; justify-content: space-between; gap: 8px; min-width: 0; min-height: 24px; }
.import-wizard__source-name { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 13px; }
.import-wizard__source :deep(.el-tag) { flex-shrink: 0; }
.import-wizard__map-controls { display: flex; align-items: center; gap: 8px; min-width: 0; min-height: 24px; flex-wrap: wrap; }
.import-wizard__map-row--drop { opacity: 0.5; }
.import-wizard__enum { display: inline-flex; gap: 4px; flex-wrap: wrap; }
.import-wizard__check { display: inline-flex; align-items: center; gap: 3px; font-size: 12.5px; color: var(--kb-text-tertiary); }
.import-wizard__missing { margin-top: 10px; }
.import-wizard__actions { margin-top: 12px; display: flex; justify-content: flex-end; gap: 8px; }
.import-wizard__preview { margin-bottom: 10px; max-height: 340px; overflow-y: auto; border: 1px solid var(--kb-border, #e2e8f0); border-radius: 8px; padding: 4px 0; }
.import-wizard__policy { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; font-size: 13px; flex-wrap: wrap; }
.import-wizard__report { display: flex; gap: 8px; flex-wrap: wrap; }
.import-wizard__conflicts { margin-top: 10px; }
</style>
