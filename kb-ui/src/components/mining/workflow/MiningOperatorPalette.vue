<template>
  <div class="mining-palette">
    <p class="mining-palette__hint">算子目录由服务端 Catalog 提供</p>
    <section v-for="group in groups" :key="group.key" class="mining-palette__group">
      <h4>{{ group.label }}</h4>
      <button
        v-for="operator in group.items"
        :key="operator.type"
        class="mining-palette__item"
        :class="`mining-palette__item--${operator.category}`"
        type="button"
        :data-operator="operator.type"
        :draggable="operator.editPolicy !== 'fixed'"
        :disabled="operator.editPolicy === 'fixed'"
        @dragstart="startDrag($event, operator)"
        @dblclick="emit('add', operator.type)"
      >
        <span>{{ operator.displayName }}</span>
        <code>{{ operator.type }}</code>
        <span class="mining-palette__meta">
          <small class="mining-palette__scope">{{ zoneLabel(operator) }}</small>
          <small :title="effectiveEditReason(operator, nodes)">{{ policyLabel(operator) }}</small>
        </span>
      </button>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { effectiveEditReason, effectiveEditState } from '@/utils/miningWorkflowGraph'
import { groupMiningOperators, miningZoneLabel } from '@/utils/miningWorkflowPresentation'
import type { MiningOperatorDef, MiningWorkflowNode } from '@/types/miningWorkflow'

const props = withDefaults(defineProps<{
  operators: MiningOperatorDef[]
  nodes?: MiningWorkflowNode[]
}>(), { nodes: () => [] })
const emit = defineEmits<{ add: [operatorType: string] }>()

const groups = computed(() => groupMiningOperators(props.operators))

function policyLabel(operator: MiningOperatorDef): string {
  return ({ fixed: '固定骨架', required: '当前必需', optional: '可选' })[
    effectiveEditState(operator, props.nodes)
  ]
}

function zoneLabel(operator: MiningOperatorDef): string {
  return miningZoneLabel(operator.zone)
}

function startDrag(event: DragEvent, operator: MiningOperatorDef) {
  if (operator.editPolicy === 'fixed') {
    event.preventDefault()
    return
  }
  event.dataTransfer?.setData('application/mining-operator-type', operator.type)
  if (event.dataTransfer) event.dataTransfer.effectAllowed = 'move'
}
</script>

<style scoped>
.mining-palette { display: flex; flex-direction: column; gap: 14px; padding: 4px; }
.mining-palette__hint { margin: 0; font-size: 11px; color: var(--kb-text-tertiary); }
.mining-palette__group { display: flex; flex-direction: column; gap: 6px; }
.mining-palette__group h4 { margin: 0; font-size: 12px; color: var(--kb-text-secondary); }
.mining-palette__item { display: grid; grid-template-columns: 1fr auto; gap: 2px 8px; text-align: left; padding: 8px 10px; border: 1px solid var(--kb-border, #e5e7eb); border-top: 3px solid var(--cat, #94a3b8); border-radius: 7px; background: #fff; cursor: grab; transition: border-color .15s ease, box-shadow .15s ease; }
.mining-palette__item--output, .mining-palette__item--publish { --cat: #22c55e; }
.mining-palette__item--input { --cat: #0ea5e9; }
.mining-palette__item--query { --cat: #6366f1; }
.mining-palette__item--scope { --cat: #14b8a6; }
.mining-palette__item--retrieve, .mining-palette__item--document, .mining-palette__item--discourse { --cat: #3b82f6; }
.mining-palette__item--fuse, .mining-palette__item--storage { --cat: #f59e0b; }
.mining-palette__item--rerank, .mining-palette__item--ontology { --cat: #ec4899; }
.mining-palette__item--review { --cat: #8b5cf6; }
.mining-palette__item:not(:disabled):hover { border-color: var(--cat, #94a3b8); box-shadow: 0 2px 8px rgba(15, 23, 42, .08); }
.mining-palette__item:disabled { cursor: not-allowed; opacity: .58; }
.mining-palette__item span { font-weight: 600; color: var(--kb-text-primary); }
.mining-palette__item code { grid-column: 1; font-size: 10px; color: var(--kb-text-tertiary); }
.mining-palette__meta { grid-column: 2; grid-row: 1 / span 2; align-self: center; display: flex; flex-direction: column; align-items: flex-end; gap: 3px; }
.mining-palette__meta small { color: var(--kb-text-tertiary); }
.mining-palette__scope { padding: 1px 5px; border-radius: 999px; background: #f1f5f9; color: #475569 !important; white-space: nowrap; }
</style>

