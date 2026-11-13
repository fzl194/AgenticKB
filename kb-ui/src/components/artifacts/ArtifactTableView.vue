<template>
  <div class="artifact-table" :class="{ 'is-editable': editable }">
    <div class="artifact-table__wrap">
      <table class="artifact-table__table">
        <thead>
          <tr>
            <th v-if="showIndex" class="artifact-table__idx" aria-label="序号">#</th>
            <th
              v-for="col in columns"
              :key="col.column_id"
              :class="{ 'is-numeric': isNumeric(col) }"
              :title="`column_id: ${col.column_id}`"
            >
              <span class="artifact-table__th-inner">
                <el-icon v-if="col.is_primary_key" class="artifact-table__th-icon is-pk" title="主键"><Key /></el-icon>
                <el-icon v-else :class="['artifact-table__th-icon', `is-${col.column_type}`]" :title="typeLabel(col)">
                  <component :is="typeIcon(col)" />
                </el-icon>
                <span class="artifact-table__th-name">{{ col.column_name }}</span>
              </span>
            </th>
            <th v-if="showSources" class="artifact-table__th-src">来源</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(row, i) in rows" :key="row.row_key || i">
            <td v-if="showIndex" class="artifact-table__idx">{{ indexOffset + i + 1 }}</td>
            <td
              v-for="col in columns"
              :key="col.column_id"
              :class="{ 'is-pk': col.is_primary_key, 'is-numeric': isNumeric(col) }"
            >
              <component
                :is="cellTag(col, row)"
                :type="cellTag(col, row) === 'button' ? 'button' : undefined"
                class="artifact-table__cell"
                :class="cellClass(col, row)"
                :title="cellTitle(col, row)"
                :data-column="col.column_name"
                @dblclick="editable && emit('cell-dblclick', row, col)"
              >
                <template v-if="isEnum(col) && hasValue(col, row)">
                  <span class="artifact-table__chip">{{ cellText(col, row) }}</span>
                </template>
                <template v-else-if="isBool(col) && hasValue(col, row)">
                  <span class="artifact-table__chip" :class="boolValue(col, row) ? 'is-true' : 'is-false'">
                    {{ boolValue(col, row) ? '是' : '否' }}
                  </span>
                </template>
                <template v-else>{{ cellText(col, row) }}</template>
              </component>
            </td>
            <td v-if="showSources" class="artifact-table__src">
              <button
                type="button"
                class="artifact-table__cell artifact-table__src-btn"
                :disabled="provenanceOf(row).length === 0 && !hasConflicts(row)"
                :title="provenanceTitle(row)"
                :data-test="`row-source-${indexOffset + i}`"
                @click="emit('provenance', row)"
              >
                <el-icon v-if="provenanceOf(row).length"><Link /></el-icon>
                {{ provenanceOf(row).length ? `${provenanceOf(row).length} 处` : '—' }}
                <span v-if="hasConflicts(row)" class="artifact-table__conflict" title="有冲突标记">⚠</span>
              </button>
            </td>
          </tr>
          <tr v-if="rows.length === 0">
            <td :colspan="columns.length + 1 + (showSources ? 1 : 0)" class="artifact-table__empty">
              <slot name="empty">暂无数据行</slot>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <p class="artifact-table__foot">
      {{ footerText }}
      <span class="artifact-table__foot-hint">
        <slot name="foot-hint">{{ editable ? '双击单元格编辑该行；来源列查看记账' : '只读正式版' }}</slot>
      </span>
    </p>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { Calendar, Check, Coin, Key, Link, Tickets, CollectionTag } from '@element-plus/icons-vue'
import type { Component } from 'vue'
import type { ArtifactColumn, ArtifactRow } from '@/types/artifacts'

/**
 * 表格制品统一表格视图（2026-11-13 用户拍板：设计态/运行态/导入预览共用
 * 同一形态，区别仅 editable）。语义化 table 复刻高保真 st-table：# 序号列、
 * 软底小写表头、发丝线、单元格截断+title 全文、枚举/布尔 soft 章、来源
 * 列计数徽章——不引新依赖，样式完全走系统令牌。
 *
 * 交互出口（全 emit，业务决策在父级）：
 * - cell-dblclick(row, col)：editable 时双击格 → 父级开该行编辑（聚焦列）
 * - provenance(row)：来源格 → 父级展开来源记账/冲突明细
 */
const props = withDefaults(defineProps<{
  columns: ArtifactColumn[]
  rows: ArtifactRow[]
  /** 设计态（可编辑）：开双击编辑；false=运行态只读。 */
  editable?: boolean
  showIndex?: boolean
  showSources?: boolean
  /** 序号偏移（分页第 N 页续号）。 */
  indexOffset?: number
  footerText?: string
}>(), {
  editable: false,
  showIndex: true,
  showSources: true,
  indexOffset: 0,
})

const emit = defineEmits<{
  'cell-dblclick': [row: ArtifactRow, column: ArtifactColumn]
  provenance: [row: ArtifactRow]
}>()

const isEnum = (col: ArtifactColumn): boolean => col.column_type === 'enum'
const isBool = (col: ArtifactColumn): boolean => col.column_type === 'bool'
const isNumeric = (col: ArtifactColumn): boolean =>
  col.column_type === 'int' || col.column_type === 'float'

/** 表头类型图标（文本=票据/整数小数=硬币/日期=日历/布尔=勾/枚举=标签）。 */
const TYPE_ICONS: Record<string, Component> = {
  text: Tickets,
  int: Coin,
  float: Coin,
  date: Calendar,
  bool: Check,
  enum: CollectionTag,
}
function typeIcon(col: ArtifactColumn): Component {
  return TYPE_ICONS[col.column_type] ?? Tickets
}
function typeLabel(col: ArtifactColumn): string {
  const labels: Record<string, string> = {
    text: '文本', int: '整数', float: '小数',
    date: '日期', bool: '布尔', enum: '枚举',
  }
  return col.is_primary_key ? `主键 · ${labels[col.column_type] ?? col.column_type}` : (labels[col.column_type] ?? col.column_type)
}

const rawValue = (col: ArtifactColumn, row: ArtifactRow): unknown => row.data?.[col.column_name]

const hasValue = (col: ArtifactColumn, row: ArtifactRow): boolean => {
  const value = rawValue(col, row)
  return value !== null && value !== undefined
}

const boolValue = (col: ArtifactColumn, row: ArtifactRow): boolean => Boolean(rawValue(col, row))

function cellText(col: ArtifactColumn, row: ArtifactRow): string {
  const value = rawValue(col, row)
  if (value === null || value === undefined) return '—'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

function cellTitle(col: ArtifactColumn, row: ArtifactRow): string {
  return `${col.column_name}：${cellText(col, row)}`
}

/** 可交互面：editable=button（双击编辑）；运行态=span（无交互语义）。 */
function cellTag(col: ArtifactColumn, row: ArtifactRow): string {
  void col
  void row
  return props.editable ? 'button' : 'span'
}

function cellClass(col: ArtifactColumn, row: ArtifactRow): string[] {
  const classes: string[] = []
  if (col.column_type === 'text' && hasValue(col, row) && String(rawValue(col, row)).length > 40) {
    classes.push('is-long')
  }
  return classes
}

function provenanceOf(row: ArtifactRow): Array<Record<string, unknown>> {
  return Array.isArray(row.provenance) ? (row.provenance as Array<Record<string, unknown>>) : []
}

function hasConflicts(row: ArtifactRow): boolean {
  return !!row.conflicts && Object.keys(row.conflicts).length > 0
}

function provenanceTitle(row: ArtifactRow): string {
  const sources = provenanceOf(row).map(p => `${p.file_name ?? '?'} / ${p.sheet_name ?? '?'} 第 ${p.row_index ?? '?'} 行`)
  return sources.length ? sources.join('\n') : (hasConflicts(row) ? '查看冲突标记' : '无来源记录')
}

const footerText = computed(() =>
  props.footerText ?? `${props.rows.length} 行 · ${props.columns.length} 列`)
</script>

<style scoped>
.artifact-table { display: flex; flex-direction: column; min-width: 0; }
.artifact-table__wrap { overflow: auto; width: 100%; max-height: 560px; }

.artifact-table__table thead th {
  position: sticky;
  top: 0;
  z-index: 2;
  box-shadow: inset 0 -1px 0 var(--kb-border, #e2e8f0);
}
.artifact-table__th-inner {
  display: inline-flex; align-items: center; gap: 5px;
  min-width: 0;
}
.artifact-table__th-name {
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.artifact-table__th-icon { font-size: 12px; color: var(--kb-text-tertiary, #94a3b8); flex-shrink: 0; }
.artifact-table__th-icon.is-pk { color: var(--kb-accent, #0891b2); }
.artifact-table__th-icon.is-enum { color: var(--kb-warning, #f59e0b); }
.artifact-table__th-icon.is-int, .artifact-table__th-icon.is-float { color: var(--kb-success, #10b981); }
th.is-numeric, td.is-numeric { text-align: right; }
td.is-numeric .artifact-table__cell {
  font-variant-numeric: tabular-nums;
  text-align: right;
  font-feature-settings: 'tnum';
}

.artifact-table__table {
  border-collapse: collapse;
  text-align: left;
  width: 100%;
  font-size: 12.5px;
}

.artifact-table__table th {
  font-weight: 500;
  font-size: 11.5px;
  color: var(--kb-text-secondary, #64748b);
  background: var(--kb-bg-card-hover, #f8fafc);
  border-bottom: 1px solid var(--kb-border, #e2e8f0);
  padding: 10px 11px;
  white-space: nowrap;
}
.artifact-table__th-icon {
  font-size: 11px;
  color: var(--kb-accent, #0891b2);
  vertical-align: -1px;
  margin-right: 3px;
}
.artifact-table__th-src { width: 86px; }

.artifact-table__table td {
  padding: 0;
  border-bottom: 1px solid var(--kb-border-light, #f1f5f9);
  height: 44px;
  vertical-align: middle;
  max-width: 240px;
}
.artifact-table__table tbody tr { transition: background .12s var(--kb-ease, ease); }
.artifact-table__table tbody tr:hover td { background: var(--kb-bg-card-hover, #f8fafc); }

.artifact-table__idx {
  width: 38px; min-width: 38px;
  text-align: center;
  color: var(--kb-text-tertiary, #94a3b8);
  font-size: 10.5px;
}
.artifact-table__table td.artifact-table__idx { padding: 0 8px; }

.artifact-table__cell {
  display: block;
  width: 100%;
  box-sizing: border-box;
  min-width: 96px;
  max-width: 240px;
  padding: 10px 11px;
  text-align: left;
  font-size: 12.5px;
  font-family: inherit;
  color: var(--kb-text-primary, inherit);
  background: none;
  border: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.artifact-table.is-editable .artifact-table__cell { cursor: pointer; }
.artifact-table.is-editable .artifact-table__cell:hover {
  color: var(--kb-accent, #0891b2);
  background: var(--kb-accent-soft, rgba(8, 145, 178, 0.06));
  border-radius: 4px;
}
.artifact-table__cell.is-long { color: var(--kb-text-secondary, #64748b); }
td.is-pk .artifact-table__cell { font-weight: 600; }

.artifact-table__chip {
  display: inline-block;
  border-radius: 4px;
  padding: 1px 7px;
  font-size: 11px;
  background: var(--kb-bg-card-hover, #f1f5f9);
  color: var(--kb-text-secondary, #64748b);
}
.artifact-table__chip.is-true { background: var(--kb-success-soft, rgba(16, 185, 129, 0.1)); color: var(--kb-success, #10b981); }
.artifact-table__chip.is-false { background: var(--kb-bg-card-hover, #f1f5f9); color: var(--kb-text-tertiary, #94a3b8); }

.artifact-table__src-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  max-width: none;
  color: var(--kb-text-tertiary, #94a3b8);
  font-size: 11.5px;
  cursor: pointer;
}
.artifact-table__src-btn:not(:disabled):hover { color: var(--kb-accent, #0891b2); }
.artifact-table__src-btn:disabled { cursor: default; }
.artifact-table__src-btn .el-icon { font-size: 12px; }
.artifact-table__conflict { color: var(--kb-warning, #f59e0b); margin-left: 2px; }

.artifact-table__empty {
  padding: 42px 16px;
  text-align: center;
  color: var(--kb-text-tertiary, #94a3b8);
  font-size: 12.5px;
}

.artifact-table__foot {
  display: flex;
  align-items: center;
  gap: 9px;
  margin: 0;
  padding: 9px 2px 0;
  color: var(--kb-text-tertiary, #94a3b8);
  font-size: 11.5px;
}
.artifact-table__foot-hint { margin-left: auto; }
</style>
