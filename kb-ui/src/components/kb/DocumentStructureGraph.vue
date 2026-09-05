<template>
  <section class="structure-workspace" data-testid="document-structure-graph">
    <header class="structure-workspace__header">
      <div>
        <div class="structure-workspace__eyebrow">{{ versionLabel }}</div>
        <h3>文档结构浏览</h3>
        <p>点击章节或表格直接查看当前版本返回的真实内容；不包含实体、本体或业务关系推断。</p>
      </div>
      <div class="structure-workspace__stats" aria-label="结构化数据统计">
        <span>章节 {{ graph.summary.sections }}</span>
        <span>表格 {{ graph.summary.tables }}</span>
        <span>切片 {{ graph.summary.segments }}</span>
      </div>
    </header>

    <div
      v-if="graph.partial"
      class="structure-workspace__notice is-warning"
      data-testid="structure-partial-notice"
    >
      当前工作台仅展示接口已返回的内容：切片 {{ graph.completeness.returnedSegments }}/{{ graph.completeness.totalSegments }}，
      元素 {{ graph.completeness.returnedElements }}/{{ graph.completeness.totalElements }}。完整列表请查看高级信息。
    </div>
    <div v-if="navigationTruncated" class="structure-workspace__notice is-warning">
      文档结构较大，左侧仅展示前 {{ MAX_NAVIGATION_NODES }} 个入口；可通过高级信息查看完整清单。
    </div>
    <div
      v-for="(diagnostic, diagnosticIndex) in graph.diagnostics"
      :key="diagnosticIndex"
      class="structure-workspace__notice"
    >
      {{ diagnostic }}
    </div>

    <div class="structure-workspace__toolbar">
      <label class="structure-workspace__search">
        <span>筛选结构</span>
        <input
          v-model="searchQuery"
          data-testid="structure-search"
          type="search"
          placeholder="搜索章节标题或表格字段"
        >
      </label>
      <span class="structure-workspace__local-hint">中间仅展示当前节点的上下文，避免大文档一次铺满</span>
    </div>

    <div class="structure-workspace__grid">
      <nav
        class="structure-workspace__outline"
        data-testid="structure-outline-pane"
        aria-label="文档结构大纲"
      >
        <button
          class="structure-outline__item is-document"
          :class="{ 'is-selected': selectedNodeId === DOCUMENT_ROOT_ID }"
          type="button"
          data-testid="outline-document:root"
          @click="selectNodeById(DOCUMENT_ROOT_ID)"
        >
          <span class="structure-outline__marker">文</span>
          <span class="structure-outline__copy">
            <strong>{{ documentTitle }}</strong>
            <small>文档概览</small>
          </span>
        </button>

        <div v-if="filteredNavigationNodes.length" class="structure-outline__list">
          <button
            v-for="node in filteredNavigationNodes"
            :key="node.id"
            class="structure-outline__item"
            :class="[`is-${node.kind}`, { 'is-selected': selectedNodeId === node.id }]"
            :style="outlineIndent(node)"
            type="button"
            :data-testid="`outline-${node.id}`"
            @click="selectNodeById(node.id)"
          >
            <span class="structure-outline__marker">{{ compactKindName(node) }}</span>
            <span class="structure-outline__copy">
              <strong :title="node.label">{{ node.label }}</strong>
              <small>{{ node.subtitle }}</small>
            </span>
          </button>
        </div>
        <p v-else class="structure-outline__empty">
          {{ navigationTruncated
            ? `未在已展示的前 ${MAX_NAVIGATION_NODES} 个入口中匹配到`
            : '没有匹配的章节或表格' }}
        </p>
      </nav>

      <div
        class="structure-workspace__graph"
        data-testid="structure-local-graph"
        aria-label="当前结构上下文"
      >
        <div class="structure-workspace__pane-title">
          <span>局部结构</span>
          <small>{{ localNodes.length }} 个节点</small>
        </div>
        <VueFlow
          :key="`${result.snapshot.id}:${selectedNodeId}`"
          :nodes="flowNodes"
          :edges="flowEdges"
          :nodes-draggable="false"
          :nodes-connectable="false"
          :edges-updatable="false"
          :elements-selectable="true"
          :delete-key-code="null"
          :min-zoom="0.45"
          :max-zoom="1.5"
          fit-view-on-init
          @node-click="selectNode"
        >
          <template #node-knowledge="nodeProps">
            <Handle
              v-if="nodeProps.data.parentId"
              type="target"
              :position="Position.Left"
              :connectable="false"
            />
            <article
              class="structure-node"
              :class="[
                `is-${nodeProps.data.kind}`,
                { 'is-selected': selectedNodeId === nodeProps.id },
              ]"
            >
              <span>{{ compactKindName(nodeProps.data) }}</span>
              <strong :title="nodeProps.data.label">{{ nodeProps.data.label }}</strong>
              <small>{{ nodeProps.data.subtitle }}</small>
            </article>
            <Handle type="source" :position="Position.Right" :connectable="false" />
          </template>
          <Background pattern-color="var(--kb-border)" :gap="22" />
          <Controls :show-interactive="false" />
        </VueFlow>
        <p
          v-if="localChildrenTruncated"
          class="structure-workspace__local-truncated"
          data-testid="structure-local-truncated"
        >
          当前节点共 {{ selectedChildCount }} 个直接下级，画布仅展示前 {{ MAX_LOCAL_CHILDREN }} 个，完整层级见左侧导航
        </p>
      </div>

      <aside
        class="structure-workspace__inspector"
        data-testid="structure-inspector"
        aria-live="polite"
      >
        <div class="structure-workspace__pane-title">
          <span>内容详情</span>
          <small>{{ selectedKindName }}</small>
        </div>

        <template v-if="selectedNode?.kind === 'document'">
          <div class="structure-inspector__heading">
            <span class="structure-inspector__kind">文档</span>
            <h4>{{ selectedNode.label }}</h4>
            <p>选择左侧章节或表格，在这里查看当前所选版本的真实内容。</p>
          </div>
          <dl class="structure-inspector__summary">
            <div><dt>章节</dt><dd>{{ graph.summary.sections }}</dd></div>
            <div><dt>表格</dt><dd>{{ graph.summary.tables }}</dd></div>
            <div><dt>内容组</dt><dd>{{ graph.summary.contentGroups }}</dd></div>
            <div><dt>切片</dt><dd>{{ graph.summary.segments }}</dd></div>
          </dl>
        </template>

        <template v-else-if="selectedNode?.kind === 'table' && selectedNode.table">
          <div class="structure-inspector__heading">
            <span class="structure-inspector__kind">表格</span>
            <h4>{{ selectedNode.label }}</h4>
            <p>{{ selectedNode.subtitle }}</p>
          </div>
          <div class="structure-inspector__table-wrap">
            <table class="structure-inspector__table">
              <thead>
                <tr>
                  <th v-for="(heading, index) in selectedNode.table.header" :key="index">
                    {{ heading || `列 ${index + 1}` }}
                  </th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(row, rowIndex) in selectedNode.table.preview" :key="rowIndex">
                  <td v-for="(cell, cellIndex) in row" :key="cellIndex">{{ cell }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <p v-if="selectedNode.table.preview_truncated" class="structure-inspector__partial">
            当前仅显示预览数据，共 {{ selectedNode.table.rows }} 行。
          </p>
          <p v-if="selectedNode.parentId === UNASSIGNED_GROUP_ID" class="structure-inspector__partial">
            该表格的所属章节暂不可用，未根据标题猜测归属。
          </p>
        </template>

        <template v-else-if="selectedNode">
          <div class="structure-inspector__heading">
            <span class="structure-inspector__kind">{{ selectedKindName }}</span>
            <h4>{{ selectedNode.label }}</h4>
            <p>{{ selectedNode.subtitle }}</p>
          </div>
          <div v-if="selectedNode.segments.length" class="structure-inspector__segments">
            <article v-for="segment in selectedNode.segments" :key="segment.segment_index">
              <div>
                <span>#{{ segment.segment_index }}</span>
                <b>{{ contentTypeName(segment.block_type) }}</b>
              </div>
              <pre>{{ segment.text }}</pre>
            </article>
          </div>
          <p v-else class="structure-inspector__empty">当前返回数据中没有该节的正文切片。</p>
          <p v-if="graph.partial" class="structure-inspector__partial">
            这是当前接口已返回的内容，不代表完整章节。
          </p>
        </template>
      </aside>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Handle, Position, VueFlow, type Edge, type Node } from '@vue-flow/core'
import { Background } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'
import '@vue-flow/core/dist/style.css'
import '@vue-flow/core/dist/theme-default.css'
import '@vue-flow/controls/dist/style.css'

import type { ParseResult } from '@/api/mining'
import {
  buildDocumentStructureGraph,
  DOCUMENT_ROOT_ID,
  UNASSIGNED_GROUP_ID,
  type DocumentStructureNode,
} from '@/utils/documentStructureGraph'

const props = defineProps<{
  documentTitle: string
  result: ParseResult
}>()

const selectedNodeId = ref(DOCUMENT_ROOT_ID)
const searchQuery = ref('')
const MAX_NAVIGATION_NODES = 240
const MAX_LOCAL_CHILDREN = 24
const MAX_SEARCH_TABLE_CELLS = 200

const graph = computed(() => buildDocumentStructureGraph({
  documentTitle: props.documentTitle || props.result.snapshot.title || '未命名文档',
  outline: props.result.outline,
  tables: props.result.tables,
  segments: props.result.segments.items,
  segmentCount: props.result.segments.count,
  elementCount: props.result.elements.count,
  returnedElementCount: props.result.elements.items.length,
  relationCount: props.result.diagnostics.relations,
  sectionCount: props.result.diagnostics.outline_total,
  tableCount: props.result.diagnostics.tables_total,
}))

const documentTitle = computed(() => graph.value.nodes[0]?.label ?? '未命名文档')
const selectedNode = computed(() => (
  graph.value.nodes.find(node => node.id === selectedNodeId.value) ?? graph.value.nodes[0] ?? null
))

const orderedNavigationNodes = computed(() => {
  const children = new Map<string, DocumentStructureNode[]>()
  for (const node of graph.value.nodes) {
    if (!node.parentId) continue
    const siblings = children.get(node.parentId) ?? []
    children.set(node.parentId, [...siblings, node])
  }
  const result: DocumentStructureNode[] = []
  const visit = (parentId: string) => {
    const items = [...(children.get(parentId) ?? [])].sort((left, right) => (
      (left.orderIndex ?? Number.MAX_SAFE_INTEGER)
      - (right.orderIndex ?? Number.MAX_SAFE_INTEGER)
      || left.label.localeCompare(right.label)
    ))
    for (const node of items) {
      const navigable = node.kind === 'section'
        || node.kind === 'table'
        || (node.kind === 'content_group' && node.contentType === 'unassigned')
      if (navigable) result.push(node)
      if (node.kind === 'section' || node.contentType === 'unassigned') visit(node.id)
    }
  }
  visit(DOCUMENT_ROOT_ID)
  return result
})

const navigationTruncated = computed(() => (
  orderedNavigationNodes.value.length > MAX_NAVIGATION_NODES
))
const navigationNodes = computed(() => (
  orderedNavigationNodes.value.slice(0, MAX_NAVIGATION_NODES)
))

const filteredNavigationNodes = computed(() => {
  const query = searchQuery.value.trim().toLocaleLowerCase()
  if (!query) return navigationNodes.value
  return navigationNodes.value.filter(node => {
    const tableSearch = node.table
      ? [
          ...node.table.header,
          ...node.table.preview.slice(0, 20).flat().slice(0, MAX_SEARCH_TABLE_CELLS),
        ].join(' ')
      : ''
    return `${node.label} ${node.subtitle} ${tableSearch}`.toLocaleLowerCase().includes(query)
  })
})

const localNodes = computed(() => {
  const byId = new Map(graph.value.nodes.map(node => [node.id, node]))
  const ids = new Set<string>([DOCUMENT_ROOT_ID, selectedNode.value?.id ?? DOCUMENT_ROOT_ID])
  let cursor: DocumentStructureNode | undefined = selectedNode.value ?? undefined
  while (cursor?.parentId) {
    ids.add(cursor.parentId)
    cursor = byId.get(cursor.parentId)
  }
  let addedChildren = 0
  for (const node of graph.value.nodes) {
    if (node.parentId === selectedNode.value?.id && addedChildren < MAX_LOCAL_CHILDREN) {
      ids.add(node.id)
      addedChildren += 1
    }
  }
  return graph.value.nodes.filter(node => ids.has(node.id))
})

const localNodeIds = computed(() => new Set(localNodes.value.map(node => node.id)))
const selectedChildCount = computed(() => graph.value.nodes.filter(
  node => node.parentId === selectedNode.value?.id,
).length)
const localChildrenTruncated = computed(() => (
  selectedChildCount.value > MAX_LOCAL_CHILDREN
))
const localEdges = computed(() => graph.value.edges.filter(edge => (
  localNodeIds.value.has(edge.source) && localNodeIds.value.has(edge.target)
)))

const flowNodes = computed<Node[]>(() => localNodes.value.map((node, index) => ({
  id: node.id,
  type: 'knowledge',
  position: {
    x: node.id === DOCUMENT_ROOT_ID ? 20 : 220 + Math.max(0, node.level - 1) * 210,
    y: 26 + index * 92,
  },
  data: { ...node },
  draggable: false,
  connectable: false,
  selectable: true,
})))

const flowEdges = computed<Edge[]>(() => localEdges.value.map(item => ({
  ...item,
  type: 'smoothstep',
  animated: false,
  style: { stroke: 'var(--kb-text-tertiary)', strokeWidth: 1.5 },
})))

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

const selectedKindName = computed(() => kindName(selectedNode.value?.kind))

watch(() => props.result.snapshot.id, () => {
  selectedNodeId.value = DOCUMENT_ROOT_ID
  searchQuery.value = ''
})

watch(graph, () => {
  if (!graph.value.nodes.some(node => node.id === selectedNodeId.value)) {
    selectedNodeId.value = DOCUMENT_ROOT_ID
  }
})

function selectNode(event: { node: { id: string } }) {
  selectNodeById(event.node.id)
}

function selectNodeById(id: string) {
  if (graph.value.nodes.some(node => node.id === id)) selectedNodeId.value = id
}

function outlineIndent(node: DocumentStructureNode): Record<string, string> {
  return { '--outline-depth': String(Math.max(0, node.level - 1)) }
}

function kindName(kind: unknown): string {
  if (kind === 'document') return '文档'
  if (kind === 'section') return '章节'
  if (kind === 'table') return '表格'
  if (kind === 'content_group') return '内容组'
  return '结构'
}

function compactKindName(node: Pick<DocumentStructureNode, 'kind' | 'contentType'>): string {
  if (node.kind === 'document') return '文'
  if (node.kind === 'section') return '节'
  if (node.kind === 'table') return '表'
  if (node.contentType === 'unassigned') return '未'
  return '组'
}

function contentTypeName(value: string): string {
  const labels: Record<string, string> = {
    prose: '正文', paragraph: '正文', list: '列表', list_item: '列表',
    code: '代码', code_block: '代码', formula: '公式', figure_caption: '图片说明',
  }
  return labels[value] ?? value
}
</script>

<style scoped>
.structure-workspace {
  overflow: hidden;
  border: 1px solid var(--kb-border);
  border-radius: var(--kb-radius-lg);
  background: var(--kb-bg-card);
  box-shadow: var(--kb-shadow-xs);
}
.structure-workspace__header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 18px;
  padding: 18px 20px 14px;
  border-bottom: 1px solid var(--kb-border-light);
}
.structure-workspace__eyebrow {
  color: var(--kb-accent);
  font-size: 11px;
  font-weight: 750;
  letter-spacing: .08em;
  text-transform: uppercase;
}
.structure-workspace h3 { margin: 4px 0 0; color: var(--kb-text-primary); font-size: 17px; }
.structure-workspace__header p {
  max-width: 660px;
  margin: 5px 0 0;
  color: var(--kb-text-secondary);
  font-size: 12px;
  line-height: 1.55;
}
.structure-workspace__stats { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 6px; }
.structure-workspace__stats span {
  border: 1px solid var(--kb-border);
  border-radius: 999px;
  background: var(--kb-bg-card-hover);
  padding: 4px 9px;
  color: var(--kb-text-secondary);
  font-size: 11px;
}
.structure-workspace__notice {
  border-bottom: 1px solid var(--kb-border-light);
  padding: 9px 20px;
  background: var(--kb-bg-card-hover);
  color: var(--kb-text-secondary);
  font-size: 12px;
}
.structure-workspace__notice.is-warning { background: var(--kb-warning-soft); color: var(--kb-warning); }
.structure-workspace__toolbar {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
  padding: 10px 14px;
  border-bottom: 1px solid var(--kb-border);
  background: var(--kb-bg-card-hover);
}
.structure-workspace__search { display: grid; gap: 4px; color: var(--kb-text-secondary); font-size: 11px; }
.structure-workspace__search input {
  width: min(320px, 70vw);
  border: 1px solid var(--kb-border);
  border-radius: var(--kb-radius-sm);
  outline: none;
  background: var(--kb-bg-card);
  padding: 7px 10px;
  color: var(--kb-text-primary);
  font: inherit;
  font-size: 12px;
}
.structure-workspace__search input:focus { border-color: var(--kb-accent); box-shadow: 0 0 0 3px var(--kb-accent-soft); }
.structure-workspace__local-hint { color: var(--kb-text-tertiary); font-size: 11px; }
.structure-workspace__grid {
  display: grid;
  grid-template-columns: minmax(210px, 260px) minmax(340px, 1fr) minmax(320px, 390px);
  min-height: 520px;
}
.structure-workspace__outline,
.structure-workspace__inspector { min-width: 0; overflow: auto; }
.structure-workspace__outline { border-right: 1px solid var(--kb-border); padding: 10px; background: var(--kb-bg-card); }
.structure-workspace__graph { min-width: 0; border-right: 1px solid var(--kb-border); background: var(--kb-bg-card-hover); }
.structure-workspace__graph :deep(.vue-flow) { height: 476px; }
.structure-workspace__local-truncated {
  margin: 0;
  border-top: 1px solid var(--kb-border-light);
  padding: 8px 14px;
  background: var(--kb-warning-soft);
  color: var(--kb-warning);
  font-size: 11px;
}
.structure-workspace__inspector { padding: 0 16px 18px; background: var(--kb-bg-card); }
.structure-workspace__pane-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 44px;
  border-bottom: 1px solid var(--kb-border-light);
  padding: 0 14px;
  color: var(--kb-text-primary);
  font-size: 12px;
  font-weight: 700;
}
.structure-workspace__pane-title small { color: var(--kb-text-tertiary); font-weight: 500; }
.structure-workspace__inspector > .structure-workspace__pane-title { margin: 0 -16px 14px; }
.structure-outline__list { display: grid; gap: 3px; }
.structure-outline__item {
  display: flex;
  width: 100%;
  align-items: center;
  gap: 8px;
  border: 0;
  border-radius: var(--kb-radius-sm);
  background: transparent;
  padding: 7px 8px 7px calc(8px + var(--outline-depth, 0) * 13px);
  color: var(--kb-text-primary);
  text-align: left;
  cursor: pointer;
}
.structure-outline__item:hover { background: var(--kb-accent-soft); }
.structure-outline__item.is-selected { background: var(--kb-accent-medium); color: var(--kb-accent); }
.structure-outline__item.is-document { margin-bottom: 7px; border-bottom: 1px solid var(--kb-border-light); border-radius: 0; }
.structure-outline__marker {
  display: inline-flex;
  width: 24px;
  height: 24px;
  flex: 0 0 24px;
  align-items: center;
  justify-content: center;
  border-radius: 7px;
  background: var(--kb-bg-card-hover);
  color: var(--kb-text-secondary);
  font-size: 10px;
  font-weight: 800;
}
.is-selected .structure-outline__marker { background: var(--kb-bg-card); color: var(--kb-accent); }
.structure-outline__copy { display: grid; min-width: 0; gap: 2px; }
.structure-outline__copy strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 12px; }
.structure-outline__copy small { color: var(--kb-text-tertiary); font-size: 10px; }
.structure-outline__empty { padding: 24px 8px; color: var(--kb-text-tertiary); font-size: 12px; text-align: center; }
.structure-node {
  display: grid;
  width: 178px;
  min-height: 64px;
  align-content: center;
  gap: 3px;
  box-sizing: border-box;
  border: 1px solid var(--kb-border);
  border-left: 4px solid var(--kb-accent);
  border-radius: var(--kb-radius);
  background: var(--kb-bg-card);
  box-shadow: var(--kb-shadow-sm);
  padding: 9px 11px;
  color: var(--kb-text-primary);
}
.structure-node.is-table { border-left-color: var(--kb-warning); }
.structure-node.is-content_group { border-left-color: var(--kb-info); }
.structure-node.is-selected { box-shadow: 0 0 0 3px var(--kb-accent-medium), var(--kb-shadow-sm); }
.structure-node span { color: var(--kb-text-tertiary); font-size: 9px; font-weight: 800; }
.structure-node strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 12px; }
.structure-node small { color: var(--kb-text-secondary); font-size: 10px; }
.structure-inspector__heading h4 { margin: 4px 0; color: var(--kb-text-primary); font-size: 16px; }
.structure-inspector__heading p { margin: 0; color: var(--kb-text-secondary); font-size: 12px; line-height: 1.55; }
.structure-inspector__kind { color: var(--kb-accent); font-size: 10px; font-weight: 800; letter-spacing: .08em; }
.structure-inspector__summary { display: grid; grid-template-columns: repeat(2, 1fr); gap: 8px; margin: 18px 0 0; }
.structure-inspector__summary div { border: 1px solid var(--kb-border-light); border-radius: var(--kb-radius-sm); padding: 9px; }
.structure-inspector__summary dt { color: var(--kb-text-tertiary); font-size: 10px; }
.structure-inspector__summary dd { margin: 2px 0 0; color: var(--kb-text-primary); font-size: 16px; font-weight: 700; }
.structure-inspector__segments { display: grid; gap: 10px; margin-top: 14px; }
.structure-inspector__segments article { border: 1px solid var(--kb-border-light); border-radius: var(--kb-radius-sm); overflow: hidden; }
.structure-inspector__segments article > div { display: flex; gap: 8px; padding: 6px 9px; background: var(--kb-bg-card-hover); color: var(--kb-text-tertiary); font-size: 10px; }
.structure-inspector__segments article b { color: var(--kb-text-secondary); }
.structure-inspector__segments pre {
  max-height: 220px;
  margin: 0;
  overflow: auto;
  padding: 10px;
  color: var(--kb-text-primary);
  font: 12px/1.6 'SF Mono', 'Cascadia Code', monospace;
  white-space: pre-wrap;
  word-break: break-word;
}
.structure-inspector__table-wrap { margin-top: 14px; overflow: auto; border: 1px solid var(--kb-border); border-radius: var(--kb-radius-sm); }
.structure-inspector__table { width: 100%; border-collapse: collapse; font-size: 12px; }
.structure-inspector__table th,
.structure-inspector__table td { min-width: 90px; border-bottom: 1px solid var(--kb-border-light); padding: 7px 9px; text-align: left; }
.structure-inspector__table th { background: var(--kb-bg-card-hover); color: var(--kb-text-secondary); font-weight: 700; }
.structure-inspector__empty,
.structure-inspector__partial { margin: 14px 0 0; color: var(--kb-text-tertiary); font-size: 11px; line-height: 1.55; }
.structure-inspector__partial { border-left: 3px solid var(--kb-warning); background: var(--kb-warning-soft); padding: 8px 10px; color: var(--kb-text-secondary); }
:deep(.vue-flow__handle) { width: 7px; height: 7px; border: 2px solid var(--kb-bg-card); background: var(--kb-text-tertiary); }
:deep(.vue-flow__controls) { border: 1px solid var(--kb-border); box-shadow: var(--kb-shadow-sm); }
@media (max-width: 1180px) {
  .structure-workspace__grid { grid-template-columns: minmax(210px, 260px) minmax(360px, 1fr); }
  .structure-workspace__inspector { grid-column: 1 / -1; max-height: 440px; border-top: 1px solid var(--kb-border); }
  .structure-workspace__graph { border-right: 0; }
}
@media (max-width: 760px) {
  .structure-workspace__header,
  .structure-workspace__toolbar { align-items: stretch; flex-direction: column; }
  .structure-workspace__stats { justify-content: flex-start; }
  .structure-workspace__local-hint { display: none; }
  .structure-workspace__search input { width: 100%; }
  .structure-workspace__grid { display: block; }
  .structure-workspace__outline { max-height: 300px; border-right: 0; border-bottom: 1px solid var(--kb-border); }
  .structure-workspace__graph { display: none; }
  .structure-workspace__inspector { max-height: none; }
}
</style>
