<template>
  <section class="workflow-trace">
    <div v-if="!trace.workflow" class="workflow-trace__legacy">
      <strong>Legacy Pipeline</strong>
      <span>该 Run 未绑定 Workflow Manifest，继续展示兼容阶段事件。</span>
    </div>
    <template v-else>
      <header class="workflow-trace__header">
        <div>
          <strong>{{ trace.workflow.name || trace.workflow.id }}</strong>
          <el-tag size="small" type="info">v{{ trace.workflow.version }}</el-tag>
        </div>
        <code>{{ trace.workflow.graph_hash }}</code>
      </header>

      <div v-if="trace.pause_step" class="workflow-trace__pause">
        暂停位置：<strong>{{ trace.pause_step }}</strong>
      </div>

      <div v-if="graph" class="workflow-trace__viewport">
        <div class="workflow-trace__canvas" :style="canvasStyle">
          <svg class="workflow-trace__edges" :width="canvasWidth" :height="canvasHeight" aria-hidden="true">
            <path v-for="edge in renderedEdges" :key="edge.key" :d="edge.path" />
          </svg>
          <article
            v-for="node in positionedNodes"
            :key="node.nodeId"
            class="trace-node"
            :class="nodeClasses(node.nodeId)"
            :data-node-id="node.nodeId"
            :style="{ left: `${node.x}px`, top: `${node.y}px` }"
          >
            <div class="trace-node__top">
              <strong>{{ miningOperatorLabel(node.operatorType) }}</strong>
              <span>{{ statusLabel(node.nodeId) }}</span>
            </div>
            <code>{{ node.nodeId }}</code>
            <div v-if="latestEvent(node.nodeId)" class="trace-node__attempt">
              attempt {{ latestEvent(node.nodeId)?.attempt_no }}
              <span v-if="latestEvent(node.nodeId)?.duration_ms != null"> · {{ formatDuration(latestEvent(node.nodeId)?.duration_ms) }}</span>
            </div>
            <p v-if="latestEvent(node.nodeId)?.error_message" class="trace-node__error">
              {{ latestEvent(node.nodeId)?.error_message }}
            </p>
            <ul v-if="warningsFor(node.nodeId).length" class="trace-node__warnings">
              <li v-for="warning in warningsFor(node.nodeId)" :key="`${warning.attempt_no}-${warning.code}`">
                {{ warning.message }}
              </li>
            </ul>
          </article>
        </div>
      </div>
      <p v-else class="workflow-trace__empty">冻结 Manifest 中没有可渲染的图。</p>

      <footer class="workflow-trace__footer">
        <span>节点事件 {{ trace.node_events.length }}</span>
        <span v-if="trace.build_id">最终 Build/Release：<strong>{{ trace.build_id }}</strong></span>
      </footer>
    </template>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { RunTrace } from '@/types'
import type { MiningWorkflowNodeEvent } from '@/types/miningWorkflow'
import { miningOperatorLabel } from '@/utils/miningWorkflowPresentation'

const props = defineProps<{ trace: RunTrace }>()
const NODE_WIDTH = 190
const NODE_HEIGHT = 116
const PADDING = 28

const graph = computed(() => props.trace.workflow?.graph ?? null)
const positionedNodes = computed(() => (graph.value?.nodes ?? []).map((node, index) => ({
  ...node,
  x: Number.isFinite(node.ui?.x) ? Number(node.ui?.x) + PADDING : PADDING + (index % 4) * 230,
  y: Number.isFinite(node.ui?.y) ? Number(node.ui?.y) + PADDING : PADDING + Math.floor(index / 4) * 155,
})))
const positions = computed(() => new Map(positionedNodes.value.map(node => [node.nodeId, node])))
const canvasWidth = computed(() => Math.max(620, ...positionedNodes.value.map(node => node.x + NODE_WIDTH + PADDING)))
const canvasHeight = computed(() => Math.max(220, ...positionedNodes.value.map(node => node.y + NODE_HEIGHT + PADDING)))
const canvasStyle = computed(() => ({ width: `${canvasWidth.value}px`, height: `${canvasHeight.value}px` }))
const renderedEdges = computed(() => (graph.value?.edges ?? []).flatMap(edge => {
  const source = positions.value.get(edge.fromNode)
  const target = positions.value.get(edge.toNode)
  if (!source || !target) return []
  const startX = source.x + NODE_WIDTH
  const startY = source.y + NODE_HEIGHT / 2
  const endX = target.x
  const endY = target.y + NODE_HEIGHT / 2
  const control = Math.max(45, Math.abs(endX - startX) / 2)
  return [{
    key: `${edge.fromNode}.${edge.fromSlot}->${edge.toNode}.${edge.toSlot}`,
    path: `M ${startX} ${startY} C ${startX + control} ${startY}, ${endX - control} ${endY}, ${endX} ${endY}`,
  }]
}))

function eventsFor(nodeId: string): MiningWorkflowNodeEvent[] {
  return props.trace.node_events.filter(event => event.node_id === nodeId)
}

function latestEvent(nodeId: string): MiningWorkflowNodeEvent | undefined {
  return [...eventsFor(nodeId)].sort((left, right) => {
    const byAttempt = right.attempt_no - left.attempt_no
    if (byAttempt) return byAttempt
    return String(right.started_at ?? '').localeCompare(String(left.started_at ?? ''))
  })[0]
}

function warningsFor(nodeId: string) {
  return props.trace.warnings.filter(warning => warning.node_id === nodeId)
}

function nodeStatus(nodeId: string): string {
  if (props.trace.active_node_id === nodeId) {
    return props.trace.status === 'awaiting_review' ? 'paused' : 'running'
  }
  const events = eventsFor(nodeId)
  if (events.some(event => event.status === 'failed')) return 'failed'
  const latest = latestEvent(nodeId)
  if (!latest) return 'pending'
  if (latest.status === 'completed' && warningsFor(nodeId).some(warning => warning.code.toLowerCase().includes('fallback'))) return 'fallback'
  return latest.status
}

function nodeClasses(nodeId: string): Record<string, boolean> {
  const status = nodeStatus(nodeId).replaceAll('_', '-')
  return {
    [`is-${status}`]: true,
    'is-active': props.trace.active_node_id === nodeId,
    'has-warning': warningsFor(nodeId).length > 0,
  }
}

function statusLabel(nodeId: string): string {
  return ({
    pending: '未运行', running: '运行中', paused: '已暂停', completed: '完成',
    success: '完成', failed: '失败', skipped: '跳过', fallback: '降级完成', not_applicable: '不适用',
  } as Record<string, string>)[nodeStatus(nodeId)] ?? nodeStatus(nodeId)
}

function formatDuration(value: number | null | undefined): string {
  if (value == null) return ''
  return value < 1000 ? `${value}ms` : `${(value / 1000).toFixed(1)}s`
}
</script>

<style scoped>
.workflow-trace { display: flex; flex-direction: column; gap: 12px; }
.workflow-trace__legacy { display: flex; flex-direction: column; gap: 4px; padding: 14px; border-radius: 8px; background: #f8fafc; color: var(--kb-text-secondary); }
.workflow-trace__legacy strong { color: var(--kb-text-primary); }
.workflow-trace__header { display: flex; justify-content: space-between; gap: 12px; align-items: center; }
.workflow-trace__header > div { display: flex; align-items: center; gap: 8px; }
.workflow-trace__header code { max-width: 45%; overflow: hidden; text-overflow: ellipsis; color: var(--kb-text-tertiary); font-size: 10px; }
.workflow-trace__pause { padding: 8px 10px; border-radius: 6px; color: #92400e; background: #fffbeb; font-size: 12px; }
.workflow-trace__viewport { overflow: auto; min-height: 240px; border: 1px solid var(--kb-border-light); border-radius: 8px; background: #f8fafc; }
.workflow-trace__canvas { position: relative; }
.workflow-trace__edges { position: absolute; inset: 0; pointer-events: none; }
.workflow-trace__edges path { fill: none; stroke: #94a3b8; stroke-width: 2; }
.trace-node { position: absolute; width: 190px; min-height: 116px; box-sizing: border-box; padding: 10px; border: 2px solid #cbd5e1; border-radius: 9px; background: #fff; box-shadow: 0 2px 7px rgba(15, 23, 42, .08); }
.trace-node__top { display: flex; justify-content: space-between; gap: 7px; }
.trace-node__top strong { overflow: hidden; text-overflow: ellipsis; font-size: 12px; }
.trace-node__top span { flex-shrink: 0; color: var(--kb-text-tertiary); font-size: 10px; }
.trace-node > code { display: block; margin-top: 2px; color: #94a3b8; font-size: 9px; }
.trace-node__attempt { margin-top: 7px; color: var(--kb-text-secondary); font-size: 10px; }
.trace-node__error { margin: 6px 0 0; color: #b91c1c; font-size: 10px; line-height: 1.35; }
.trace-node__warnings { margin: 5px 0 0; padding-left: 14px; color: #a16207; font-size: 10px; }
.trace-node.is-completed, .trace-node.is-success { border-color: #22c55e; }
.trace-node.is-running, .trace-node.is-active { border-color: #3b82f6; box-shadow: 0 0 0 3px rgba(59, 130, 246, .16); }
.trace-node.is-failed { border-color: #ef4444; background: #fff7f7; }
.trace-node.is-paused { border-color: #f59e0b; background: #fffbeb; }
.trace-node.is-fallback, .trace-node.has-warning { border-style: dashed; }
.trace-node.is-skipped, .trace-node.is-not-applicable { opacity: .62; }
.workflow-trace__footer { display: flex; flex-wrap: wrap; gap: 14px; color: var(--kb-text-secondary); font-size: 11px; }
.workflow-trace__empty { color: var(--kb-text-tertiary); }
</style>
