<template>
  <JsonSchemaParamForm
    :schema-json="schemaJson"
    :model-value="modelValue"
    :option-sources="optionSources"
    @update:model-value="emit('update:modelValue', $event)"
  />
</template>

<script setup lang="ts">
import JsonSchemaParamForm from '@/components/workflow/JsonSchemaParamForm.vue'
import type { ParamOption } from '@/components/workflow/JsonSchemaParamForm.vue'

defineProps<{
  schemaJson: string
  modelValue: Record<string, unknown>
  /** 按 x-widget 名提供候选项（如 kb-picker）。由编辑器加载后传入。 */
  optionSources?: Record<string, ParamOption[]>
}>()
const emit = defineEmits<{ 'update:modelValue': [Record<string, unknown>] }>()
</script>
