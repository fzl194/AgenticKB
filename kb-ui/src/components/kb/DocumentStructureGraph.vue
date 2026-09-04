<template>
  <section class="structure-graph" data-testid="document-structure-graph">
    <header class="structure-graph__header">
      <div>
        <div class="structure-graph__eyebrow">{{ versionLabel }}</div>
        <h3>文档结构图</h3>
      </div>
      <div class="structure-graph__stats" aria-label="结构化数据统计">
        <span>章节 {{ graph.summary.sections }}</span>
        <span>表格 {{ graph.summary.tables }}</span>
        <span>切片 {{ graph.summary.segments }}</span>
        <span>图中关系 {{ graph.edges.length }}</span>
      </div>
    </header>

    <p class="structure-graph__scope">
      由当前所选版本的解析结果生成，展示文档、章节和表格的确定性包含关系；不包含实体、本体或业务关系推断。
    </p>

    <div class="structure-graph__legend" aria-label="节点图例">
      <span><i class="is-document" />文档</span>
      <span><i class="is-section" />章节</span>
      <span><i class="is-table" />表格</span>
      <small>拖动画布查看 · 滚轮缩放</small>
    </div>

    <div class="structure-graph__canvas">
      <VueFlow
        :nodes="flowNodes"
        :edges="flowEdges"
        :nodes-draggable="false"
        :nodes-connectable="false"
        :edges-updatable="false"
        :elements-selectable="true"
        :delete-key-code="null"
        :min-zoom="0.2"
        :max-zoom="1.8"
        :fit-view-on-init="flowNodes.length <= 18"
        @node-click="selectNode"
      >
        <template #node-knowledge="nodeProps">
          <Handle
            v-if="nodeProps.data.kind !== 'document'"
            type="target"
            :position="Position.Left"
            :connectable="false"
          />
          <article
            class="structure-graph__node"
            :class="[
              `is-${nodeProps.data.kind}`,
              { 'is-selected': selectedNodeId === nodeProps.id },
            ]"
          >
            <span class="structure-graph__node-type">{{ kindName(nodeProps.data.kind) }}</span>
            <strong :title="nodeProps.data.label">{{ nodeProps.data.label }}</strong>
            <small>{{ nodeProps.data.subtitle }}</small>
          </article>
          <Handle
            v-if="nodeProps.data.kind !== 'table'"
            type="source"
            :position="Position.Right"
            :connectable="false"
          />
        </template>
        <Background pattern-color="#cbd5e1" :gap="20" />
        <Controls :show-interactive="false" />
      </VueFlow>
    </div>

    <div v-if="omittedText" class="structure-graph__notice">{{ omittedText }}</div>
    <div v-for="diagnostic in graph.diagnostics" :key="diagnostic" class="structure-graph__notice">
      {{ diagnostic }}
    </div>
    <div v-if="selectedNode" class="structure-graph__selection" aria-live="polite">
      <span>{{ kindLabel[selectedNode.kind] }}</span>
      <strong>{{ selectedNode.label }}</strong>
      <small>{{ selectedNode.subtitle }}</small>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { Handle, Position, VueFlow, type Edge, type Node } from '@vue-flow/core'
import { Background } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'
import '@vue-flow/core/dist/style.css'
import '@vue-flow/core/dist/theme-default.css'
import '@vue-flow/controls/dist/style.css'

import type { ParseResult } from '@/api/mining'
import {
  buildDocumentStructureGraph,
  type DocumentStructureNode,
  type DocumentStructureNodeKind,
} from '@/utils/documentStructureGraph'

const props = defineProps<{
  documentTitle: string
  result: ParseResult
}>()

const selectedNodeId = ref('')
const kindLabel: Record<DocumentStructureNodeKind, string> = {
  document: '文档',
  section: '章节',
  table: '表格',
}

const graph = computed(() => buildDocumentStructureGraph({
  documentTitle: props.documentTitle || props.result.snapshot.title || '未命名文档',
  outline: props.result.outline,
  tables: props.result.tables,
  segmentCount: props.result.segments.count,
  elementCount: props.result.elements.count,
  relationCount: props.result.diagnostics.relations,
}))

const versionLabel = computed(() => {
  const view = props.result.view ?? props.result.versioning?.view
  if (view === 'latest_revision') {
    return props.result.versioning?.latest_state === 'in_search'
      ? '最新解析 · 已进入搜索'
      : '最新解析 · 尚未进入搜索'
  }
  if (view === 'current_serving') return '当前可搜索版本 · 确定性结构层'
  return '当前所选解析结果 · 确定性结构层'
})

const flowNodes = computed<Node[]>(() => {
  const visibleCount = Math.max(1, graph.value.nodes.length - 1)
  const fitAll = graph.value.nodes.length <= 18

  return graph.value.nodes.map((node, index) => {
    if (node.kind === 'document') {
      return {
        id: node.id,
        type: 'knowledge',
        position: { x: 24, y: fitAll ? Math.max(24, (visibleCount - 1) * 42) : 24 },
        data: { ...node },
        draggable: false,
        connectable: false,
        selectable: true,
      }
    }

    const visualLevel = node.kind === 'table' ? 1 : Math.min(node.level, 6)
    return {
      id: node.id,
      type: 'knowledge',
      position: { x: 24 + visualLevel * 250, y: 24 + (index - 1) * 84 },
      data: { ...node },
      draggable: false,
      connectable: false,
      selectable: true,
    }
  })
})

const flowEdges = computed<Edge[]>(() => graph.value.edges.map(item => ({
  ...item,
  type: 'smoothstep',
  animated: false,
  style: { stroke: '#94a3b8', strokeWidth: 1.6 },
})))

const selectedNode = computed<DocumentStructureNode | null>(() => (
  graph.value.nodes.find(node => node.id === selectedNodeId.value) ?? null
))

const omittedText = computed(() => {
  const parts: string[] = []
  if (graph.value.omitted.sections) parts.push(`另有 ${graph.value.omitted.sections} 个章节未在画布展开`)
  if (graph.value.omitted.tables) parts.push(`另有 ${graph.value.omitted.tables} 张表格未在画布展开`)
  if (props.result.outline.some(item => item.level > 6)) parts.push('七级及更深标题合并显示在第六列')
  if (graph.value.nodes.length > 18) parts.push('结构较大，画布以原始比例展示，可拖动浏览')
  return parts.join('，')
})

function selectNode(event: { node: { id: string } }) {
  selectedNodeId.value = event.node.id
}

function kindName(value: unknown): string {
  return kindLabel[value as DocumentStructureNodeKind] ?? '结构'
}
</script>

<style scoped>
.structure-graph {
  border: 1px solid var(--kb-border-light);
  border-radius: 12px;
  background: linear-gradient(180deg, #f8fafc 0%, #ffffff 34%);
  overflow: hidden;
}
.structure-graph__header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 18px;
  padding: 16px 18px 8px;
}
.structure-graph__eyebrow {
  color: #047857;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: .08em;
}
.structure-graph h3 {
  margin: 3px 0 0;
  color: var(--kb-text-primary);
  font-size: 16px;
}
.structure-graph__stats {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 6px;
}
.structure-graph__stats span {
  border: 1px solid #dbe4ef;
  border-radius: 999px;
  background: rgba(255, 255, 255, .9);
  padding: 4px 9px;
  color: #475569;
  font-size: 11px;
}
.structure-graph__scope {
  margin: 0;
  padding: 0 18px 12px;
  color: var(--kb-text-secondary);
  font-size: 12px;
  line-height: 1.6;
}
.structure-graph__legend {
  display: flex;
  align-items: center;
  gap: 14px;
  border-top: 1px solid #e8eef5;
  border-bottom: 1px solid #e8eef5;
  padding: 8px 18px;
  background: rgba(248, 250, 252, .82);
  color: #64748b;
  font-size: 11px;
}
.structure-graph__legend span { display: inline-flex; align-items: center; gap: 5px; }
.structure-graph__legend i { width: 8px; height: 8px; border-radius: 50%; }
.structure-graph__legend i.is-document { background: #0f766e; }
.structure-graph__legend i.is-section { background: #2563eb; }
.structure-graph__legend i.is-table { background: #b45309; }
.structure-graph__legend small { margin-left: auto; }
.structure-graph__canvas { height: 460px; background: #fbfdff; }
.structure-graph__node {
  display: flex;
  width: 188px;
  min-height: 66px;
  flex-direction: column;
  justify-content: center;
  box-sizing: border-box;
  border: 1px solid #bfdbfe;
  border-left: 4px solid #2563eb;
  border-radius: 9px;
  background: #fff;
  box-shadow: 0 5px 16px rgba(15, 23, 42, .08);
  padding: 9px 12px;
  color: #0f172a;
}
.structure-graph__node.is-document { border-color: #99f6e4; border-left-color: #0f766e; background: #f0fdfa; }
.structure-graph__node.is-table { border-color: #fde68a; border-left-color: #b45309; background: #fffbeb; }
.structure-graph__node.is-selected { outline: 3px solid rgba(14, 165, 233, .2); }
.structure-graph__node-type { color: #64748b; font-size: 10px; font-weight: 700; letter-spacing: .08em; }
.structure-graph__node strong {
  margin-top: 2px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
}
.structure-graph__node small { margin-top: 3px; color: #64748b; font-size: 10px; }
.structure-graph__notice,
.structure-graph__selection {
  display: flex;
  align-items: center;
  gap: 8px;
  border-top: 1px solid #e8eef5;
  padding: 9px 18px;
  color: #64748b;
  font-size: 11px;
}
.structure-graph__notice { color: #92400e; background: #fffbeb; }
.structure-graph__selection span { color: #0369a1; font-weight: 700; }
.structure-graph__selection small { margin-left: auto; }
:deep(.vue-flow__handle) { width: 7px; height: 7px; border: 2px solid #fff; background: #64748b; }
:deep(.vue-flow__controls) { border: 1px solid #dbe4ef; box-shadow: 0 4px 12px rgba(15, 23, 42, .08); }
@media (max-width: 760px) {
  .structure-graph__header { flex-direction: column; }
  .structure-graph__stats { justify-content: flex-start; }
  .structure-graph__legend small { display: none; }
  .structure-graph__canvas { height: 380px; }
}
</style>
