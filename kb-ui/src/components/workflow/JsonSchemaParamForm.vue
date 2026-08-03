<template>
  <div class="param-form">
    <div v-if="fields.length === 0" class="param-form__empty">该算子无可配置参数</div>
    <div v-for="field in fields" :key="field.key" class="param-form__field">
      <label class="param-form__label">{{ field.title }}</label>
      <span v-if="field.description" class="param-form__description">{{ field.description }}</span>

      <el-select
        v-if="field.kind === 'enum'"
        :model-value="value(field.key)"
        size="small"
        style="width: 100%"
        @update:model-value="set(field.key, $event)"
      >
        <el-option v-for="option in field.enum" :key="String(option)" :label="String(option)" :value="option" />
      </el-select>

      <el-input-number
        v-else-if="field.kind === 'number'"
        :model-value="value(field.key) as number"
        :min="field.min"
        :max="field.max"
        :step="field.step"
        size="small"
        controls-position="right"
        style="width: 100%"
        @update:model-value="set(field.key, $event)"
      />

      <el-switch
        v-else-if="field.kind === 'boolean'"
        :model-value="value(field.key) as boolean"
        @update:model-value="set(field.key, $event)"
      />

      <!-- 声明了 x-widget 且调用方提供了对应候选项：渲染成选择器 -->
      <el-select
        v-else-if="field.kind === 'array' && optionsFor(field)"
        :model-value="(value(field.key) as unknown[]) ?? []"
        multiple
        collapse-tags
        collapse-tags-tooltip
        filterable
        clearable
        size="small"
        :placeholder="field.placeholder || '请选择'"
        style="width: 100%"
        @update:model-value="set(field.key, $event)"
      >
        <el-option
          v-for="option in optionsFor(field)"
          :key="option.value"
          :label="option.label"
          :value="option.value"
        >
          <span>{{ option.label }}</span>
          <span v-if="option.hint" class="param-form__option-hint">{{ option.hint }}</span>
        </el-option>
      </el-select>

      <el-select
        v-else-if="field.kind === 'array'"
        :model-value="(value(field.key) as unknown[]) ?? []"
        multiple
        filterable
        allow-create
        default-first-option
        size="small"
        placeholder="输入后回车添加"
        style="width: 100%"
        @update:model-value="set(field.key, $event)"
      />

      <div v-else-if="field.kind === 'map'" class="param-form__map">
        <div v-for="(row, index) in mapRows(field.key)" :key="index" class="param-form__map-row">
          <el-input
            :model-value="row.key"
            size="small"
            placeholder="key"
            @update:model-value="setMapKey(field.key, index, $event)"
          />
          <el-input-number
            :model-value="row.value"
            size="small"
            :step="0.1"
            controls-position="right"
            @update:model-value="setMapValue(field.key, index, $event)"
          />
          <el-button size="small" text @click="removeMapRow(field.key, index)">×</el-button>
        </div>
        <el-button size="small" text type="primary" @click="addMapRow(field.key)">+ 添加</el-button>
      </div>

      <el-input
        v-else
        :model-value="value(field.key) as string"
        size="small"
        @update:model-value="set(field.key, $event)"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, reactive } from 'vue'
import type { MiningJsonSchema, MiningJsonSchemaProperty } from '@/types/miningWorkflow'

/** 候选项由调用方注入——共享组件不知道也不该知道它们从哪个服务来。 */
export interface ParamOption {
  value: string
  label: string
  hint?: string
}

const props = defineProps<{
  schemaJson: string | MiningJsonSchema
  modelValue: Record<string, unknown>
  /**
   * 按 x-widget 名字提供候选项。缺省或缺对应键时，字段退化成默认控件——所以挖掘范式
   * 编辑器不传这个 prop 也完全正常，行为与改动前一致。
   */
  optionSources?: Record<string, ParamOption[]>
}>()
const emit = defineEmits<{ 'update:modelValue': [Record<string, unknown>] }>()

interface Field {
  key: string
  title: string
  description?: string
  kind: 'enum' | 'number' | 'boolean' | 'array' | 'map' | 'string'
  enum?: unknown[]
  min?: number
  max?: number
  step?: number
  default?: unknown
  widget?: string
  placeholder?: string
}

const schema = computed<MiningJsonSchema>(() => {
  if (typeof props.schemaJson !== 'string') return props.schemaJson ?? {}
  try {
    return JSON.parse(props.schemaJson || '{}') as MiningJsonSchema
  } catch {
    return {}
  }
})

const fields = computed<Field[]>(() => Object.entries(schema.value.properties ?? {})
  .map(([key, property]) => toField(key, property)))

function toField(key: string, property: MiningJsonSchemaProperty): Field {
  const title = property.title || key
  const base = {
    key, title, description: property.description, default: property.default,
    widget: property['x-widget'],
  }
  if (property.enum) return { ...base, kind: 'enum', enum: property.enum }
  if (property.type === 'integer' || property.type === 'number') {
    return {
      ...base,
      kind: 'number',
      min: property.minimum,
      max: property.maximum,
      step: property.type === 'integer' ? 1 : 0.1,
    }
  }
  if (property.type === 'boolean') return { ...base, kind: 'boolean' }
  if (property.type === 'array') return { ...base, kind: 'array' }
  if (property.type === 'object') return { ...base, kind: 'map' }
  return { ...base, kind: 'string' }
}

/**
 * 该字段的候选项；没有对应 source 则返回 null，模板据此回退到自由输入控件。
 *
 * 已保存但不在候选里的值（知识库被删、或范式是在别的域下建的）会补成一个标注项，
 * 而不是从选择器里消失——用户不该在打开表单时被静默改掉已保存的配置。
 */
function optionsFor(field: Field): ParamOption[] | null {
  if (!field.widget) return null
  const source = props.optionSources?.[field.widget]
  if (!source) return null
  const selected = (value(field.key) as unknown[]) ?? []
  const known = new Set(source.map(option => option.value))
  const orphans = selected
    .filter((v): v is string => typeof v === 'string' && !known.has(v))
    .map(v => ({ value: v, label: v, hint: '未知或不可见' }))
  return orphans.length > 0 ? [...source, ...orphans] : source
}

function value(key: string): unknown {
  const current = props.modelValue?.[key]
  if (current !== undefined) return current
  return fields.value.find(field => field.key === key)?.default
}

function set(key: string, nextValue: unknown) {
  const next = { ...props.modelValue }
  if (nextValue === null || nextValue === undefined) delete next[key]
  else next[key] = nextValue
  emit('update:modelValue', next)
}

interface MapRow { key: string; value: number }
const mapState = reactive<Record<string, MapRow[]>>({})

function mapRows(key: string): MapRow[] {
  if (!mapState[key]) {
    const current = (props.modelValue?.[key] as Record<string, number>) ?? {}
    mapState[key] = Object.entries(current).map(([rowKey, rowValue]) => ({ key: rowKey, value: rowValue }))
  }
  return mapState[key]
}

function emitMap(key: string) {
  const next: Record<string, number> = {}
  for (const row of mapRows(key)) if (row.key) next[row.key] = row.value
  set(key, next)
}

function setMapKey(key: string, index: number, rowKey: string) {
  mapRows(key)[index].key = rowKey
  emitMap(key)
}

function setMapValue(key: string, index: number, rowValue: number | undefined) {
  mapRows(key)[index].value = rowValue ?? 0
  emitMap(key)
}

function addMapRow(key: string) {
  mapRows(key).push({ key: '', value: 1 })
  emitMap(key)
}

function removeMapRow(key: string, index: number) {
  mapRows(key).splice(index, 1)
  emitMap(key)
}
</script>

<style scoped>
.param-form { display: flex; flex-direction: column; gap: 14px; }
.param-form__empty { color: var(--kb-text-tertiary); font-size: 13px; padding: 8px 0; }
.param-form__field { display: flex; flex-direction: column; gap: 6px; }
.param-form__label { font-size: 12px; font-weight: 600; color: var(--kb-text-secondary); }
.param-form__description { font-size: 11px; color: var(--kb-text-tertiary); line-height: 1.4; }
.param-form__map { display: flex; flex-direction: column; gap: 6px; }
.param-form__map-row { display: grid; grid-template-columns: 1fr 110px 28px; gap: 6px; align-items: center; }
.param-form__option-hint { float: right; margin-left: 12px; font-size: 11px; color: var(--kb-text-tertiary); }
</style>

