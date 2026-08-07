<template>
  <div class="workflow-editor">
    <header class="workflow-editor__toolbar">
      <div class="workflow-editor__identity">
        <el-button text @click="goBack">返回</el-button>
        <strong>{{ workflow?.name ?? '加载中…' }}</strong>
        <el-tag v-if="workflow" size="small">draft r{{ workflow.draft_revision }}</el-tag>
        <el-tag v-if="workflow?.current_version" size="small" type="success">v{{ workflow.current_version }}</el-tag>
        <el-tag v-if="readOnly" size="small" type="info">历史版本只读</el-tag>
        <span v-if="dirty" class="workflow-editor__dirty">未保存</span>
      </div>
      <div class="workflow-editor__actions">
        <el-button :disabled="readOnly || !canUndo" @click="undo">撤销</el-button>
        <el-button :disabled="readOnly || !canRedo" @click="redo">重做</el-button>
        <el-button data-test="save-draft" :loading="saving" :disabled="readOnly" @click="saveDraft">保存草稿</el-button>
        <el-button :loading="validating" @click="validateOnServer">服务端校验</el-button>
        <el-button type="primary" :loading="publishing" :disabled="readOnly" @click="publishWorkflow">发布新版本</el-button>
      </div>
    </header>

    <div v-if="requiresRepublish" class="workflow-editor__restore-banner">
      历史版本已恢复为新的草稿 revision；当前发布版本未改变，请确认后显式重新发布。
    </div>

    <div v-if="recoveryVisible" class="workflow-editor__conflict">
      <strong>草稿 revision 冲突</strong>
      <span>远端草稿已变化，本地内容没有被覆盖。</span>
      <el-button data-test="copy-local-json" size="small" @click="copyLocalJson">复制本地 JSON</el-button>
      <el-button data-test="reload-remote" size="small" type="warning" @click="reloadRemote">放弃本地并加载远端</el-button>
      <pre data-test="local-json">{{ localJson }}</pre>
    </div>

    <div class="workflow-editor__body">
      <aside class="workflow-editor__palette" :class="{ 'is-readonly': readOnly }">
        <MiningOperatorPalette :operators="catalog" :nodes="graph.nodes" @add="addOperator" />
      </aside>

      <main class="workflow-editor__canvas" @drop="onDrop" @dragover.prevent>
        <VueFlow
          v-model:nodes="flowNodes"
          v-model:edges="flowEdges"
          :nodes-draggable="!readOnly"
          :nodes-connectable="!readOnly"
          :edges-updatable="!readOnly"
          :delete-key-code="null"
          :is-valid-connection="isValidConnection"
          fit-view-on-init
          @connect="onConnect"
          @edge-update="onEdgeUpdate"
          @edge-click="selectEdge"
          @node-drag-start="startFlowMutation"
          @node-drag-stop="finishFlowMutation"
          @node-click="selectNode"
          @pane-click="clearSelection"
        >
          <template #node-miningOperator="nodeProps">
            <MiningOperatorNode
              :id="nodeProps.id"
              :operator-type="nodeProps.data.operatorType"
              :definition="nodeProps.data.definition"
              :params="nodeProps.data.params"
              :selected="nodeProps.id === selectedNodeId"
              :disabled="nodeProps.data.disabled"
              :is-output="nodeProps.data.isOutput"
              :edit-state="nodeProps.data.definition ? effectiveEditState(nodeProps.data.definition, graph.nodes) : undefined"
              :edit-reason="nodeProps.data.definition ? effectiveEditReason(nodeProps.data.definition, graph.nodes) : undefined"
            />
          </template>
          <Background pattern-color="#cbd5e1" :gap="18" />
          <Controls />
        </VueFlow>
      </main>

      <aside class="workflow-editor__inspector">
        <section class="workflow-editor__section">
          <h3>节点</h3>
          <p v-if="!selectedNode" class="workflow-editor__muted">选择节点查看参数与编辑策略。</p>
          <template v-else>
            <div class="workflow-editor__node-title">
              <strong>{{ selectedDefinition?.displayName ?? selectedNode.data.operatorType }}</strong>
              <code>{{ selectedNode.id }}</code>
            </div>
            <p class="workflow-editor__muted">{{ selectedDefinition?.description }}</p>
            <p v-if="selectedDefinition" class="workflow-editor__policy" :title="selectedEditReason">
              {{ editStateLabel(selectedEditState) }} · {{ selectedEditReason }}
            </p>
            <JsonSchemaParamForm
              v-if="selectedDefinition"
              :key="selectedNode.id"
              :schema-json="selectedDefinition.paramSchemaJson"
              :model-value="selectedNode.data.params"
              @update:model-value="updateNodeParams(selectedNode.id, $event)"
            />
            <div class="workflow-editor__node-actions">
              <el-button
                size="small"
                :disabled="readOnly || !selectedDefinition || !canDisableNode(selectedDefinition)"
                @click="toggleSelectedDisabled"
              >{{ selectedNode.data.disabled ? '启用' : '禁用' }}</el-button>
              <el-button
                size="small"
                type="danger"
                :disabled="readOnly || !selectedDefinition || !canDeleteNodeInGraph(selectedDefinition, graph.nodes)"
                @click="deleteSelected"
              >删除</el-button>
            </div>
          </template>
        </section>

        <section class="workflow-editor__section">
          <template v-if="selectedEdge">
            <div data-test="selected-edge">
              <h3>连接</h3>
              <div class="workflow-editor__edge-route">
                <code>{{ selectedEdge.source }}.{{ selectedEdge.sourceHandle }}</code>
                <span>→</span>
                <code>{{ selectedEdge.target }}.{{ selectedEdge.targetHandle }}</code>
              </div>
              <p class="workflow-editor__muted">拖动连线任一端可重新连接；无效目标不会改变原连接。</p>
              <div class="workflow-editor__node-actions">
                <el-button
                  data-test="delete-edge"
                  size="small"
                  type="danger"
                  :disabled="readOnly"
                  @click="deleteSelectedEdge"
                >删除连接</el-button>
              </div>
            </div>
          </template>
          <template v-else>
            <h3>连接</h3>
            <p class="workflow-editor__muted">选择连线后可查看、删除或拖动端点重新连接。</p>
          </template>
        </section>

        <section class="workflow-editor__section">
          <WorkflowValidationPanel :local-issues="localIssues" :server-result="serverResult" />
        </section>

        <section class="workflow-editor__section">
          <h3>版本历史</h3>
          <p v-if="!versions.length" class="workflow-editor__muted">尚无发布版本。</p>
          <button
            v-for="version in versions"
            :key="version.version"
            class="workflow-editor__version-row"
            type="button"
            @click="previewVersion(version.version)"
          >
            <span>v{{ version.version }}</span>
            <small>{{ version.release_notes || '无发布说明' }}</small>
          </button>
          <WorkflowVersionPreview
            v-if="previewedVersion"
            :version="previewedVersion"
            @restore="restoreVersion"
          />
          <el-button v-if="readOnly" size="small" @click="exitPreview">返回当前草稿</el-button>
        </section>
      </aside>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { onBeforeRouteLeave, useRouter } from 'vue-router'
import { VueFlow, useVueFlow, type Connection, type EdgeMouseEvent, type EdgeUpdateEvent } from '@vue-flow/core'
import { Background } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'
import '@vue-flow/core/dist/style.css'
import '@vue-flow/core/dist/theme-default.css'
import '@vue-flow/controls/dist/style.css'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useMiningWorkflowApi } from '@/api/miningWorkflow'
import JsonSchemaParamForm from '@/components/workflow/JsonSchemaParamForm.vue'
import MiningOperatorPalette from '@/components/mining/workflow/MiningOperatorPalette.vue'
import MiningOperatorNode from '@/components/mining/workflow/MiningOperatorNode.vue'
import WorkflowValidationPanel from '@/components/mining/workflow/WorkflowValidationPanel.vue'
import WorkflowVersionPreview from '@/components/mining/workflow/WorkflowVersionPreview.vue'
import {
  canDeleteNodeInGraph,
  canDisableNode,
  canEditNodeParams,
  canMoveNode,
  effectiveEditReason,
  effectiveEditState,
  fromVueFlowElements,
  stableGraphJson,
  toVueFlowElements,
  validateLocalGraph,
  type MiningVueFlowEdge,
  type MiningVueFlowNode,
} from '@/utils/miningWorkflowGraph'
import { copyToClipboard } from '@/utils/clipboard'
import type {
  MiningOperatorDef,
  MiningWorkflow,
  MiningWorkflowGraph,
  MiningWorkflowValidationResult,
  MiningWorkflowVersion,
} from '@/types/miningWorkflow'

interface EditorFlowNode extends MiningVueFlowNode {
  draggable?: boolean
  deletable?: boolean
  connectable?: boolean
}

const props = defineProps<{ id: string }>()
const api = useMiningWorkflowApi()
const router = useRouter()
const { screenToFlowCoordinate } = useVueFlow()

const catalog = ref<MiningOperatorDef[]>([])
const workflow = ref<MiningWorkflow | null>(null)
const versions = ref<MiningWorkflowVersion[]>([])
const graph = ref<MiningWorkflowGraph>(emptyGraph())
const savedGraph = ref(emptyGraph())
const flowNodes = ref<EditorFlowNode[]>([])
const flowEdges = ref<MiningVueFlowEdge[]>([])
const selectedNodeId = ref('')
const selectedEdgeId = ref('')
const serverResult = ref<MiningWorkflowValidationResult | null>(null)
const readOnly = ref(false)
const previewedVersion = ref<MiningWorkflowVersion | null>(null)
const recoveryVisible = ref(false)
const requiresRepublish = ref(false)
const saving = ref(false)
const validating = ref(false)
const publishing = ref(false)
const undoStack = ref<MiningWorkflowGraph[]>([])
const redoStack = ref<MiningWorkflowGraph[]>([])
let flowMutationStart: MiningWorkflowGraph | null = null
let previewReturnGraph: MiningWorkflowGraph | null = null
let sequence = 0

const definitionMap = computed(() => new Map(catalog.value.map(definition => [definition.type, definition])))
const selectedNode = computed(() => flowNodes.value.find(node => node.id === selectedNodeId.value) ?? null)
const selectedEdge = computed(() => flowEdges.value.find(edge => edge.id === selectedEdgeId.value) ?? null)
const selectedDefinition = computed(() => selectedNode.value?.data.definition)
const localIssues = computed(() => validateLocalGraph(graph.value, catalog.value))
const localJson = computed(() => stableGraphJson(graph.value))
const dirty = computed(() => localJson.value !== stableGraphJson(savedGraph.value))
const canUndo = computed(() => undoStack.value.length > 0)
const canRedo = computed(() => redoStack.value.length > 0)
const selectedEditState = computed(() => selectedDefinition.value
  ? effectiveEditState(selectedDefinition.value, graph.value.nodes)
  : undefined)
const selectedEditReason = computed(() => selectedDefinition.value
  ? effectiveEditReason(selectedDefinition.value, graph.value.nodes)
  : '')

function emptyGraph(): MiningWorkflowGraph {
  return { schemaVersion: '1.0', nodes: [], edges: [], output: { nodeId: '', slot: '' } }
}

function cloneGraph(value: MiningWorkflowGraph): MiningWorkflowGraph {
  return JSON.parse(JSON.stringify(value)) as MiningWorkflowGraph
}

function applyGraph(value: MiningWorkflowGraph) {
  graph.value = cloneGraph(value)
  const mapped = toVueFlowElements(graph.value, catalog.value)
  flowNodes.value = mapped.nodes.map(node => ({
    ...node,
    draggable: node.data.definition ? canMoveNode(node.data.definition) : false,
    deletable: node.data.definition ? canDeleteNodeInGraph(node.data.definition, graph.value.nodes) : false,
    connectable: Boolean(node.data.definition) && !readOnly.value,
  }))
  const writable = !readOnly.value
  flowEdges.value = mapped.edges.map(edge => ({
    ...edge,
    selected: edge.id === selectedEdgeId.value,
    selectable: true,
    deletable: writable,
    updatable: writable,
    interactionWidth: 20,
  }))
  if (selectedEdgeId.value && !flowEdges.value.some(edge => edge.id === selectedEdgeId.value)) selectedEdgeId.value = ''
  sequence = Math.max(0, ...graph.value.nodes.map(node => Number(/(\d+)$/.exec(node.nodeId)?.[1] ?? 0)))
}

function syncGraphFromFlow() {
  graph.value = fromVueFlowElements(flowNodes.value, flowEdges.value, graph.value.output)
}

async function load() {
  try {
    const catalogResponse = await api.getCatalog()
    catalog.value = catalogResponse.items
    const loaded = await api.get(props.id)
    workflow.value = loaded
    savedGraph.value = cloneGraph(loaded.draft_graph_json)
    applyGraph(loaded.draft_graph_json)
    versions.value = await api.listVersions(props.id)
    undoStack.value = []
    redoStack.value = []
    recoveryVisible.value = false
  } catch (error) {
    ElMessage.error(`加载 Workflow 失败：${errorMessage(error)}`)
  }
}

function recordMutation(mutator: () => void) {
  if (readOnly.value) return
  const previous = cloneGraph(graph.value)
  mutator()
  if (stableGraphJson(previous) === stableGraphJson(graph.value)) return
  undoStack.value.push(previous)
  redoStack.value = []
  applyGraph(graph.value)
  serverResult.value = null
}

function updateNodeParams(nodeId: string, params: Record<string, unknown>) {
  const definition = definitionMap.value.get(graph.value.nodes.find(node => node.nodeId === nodeId)?.operatorType ?? '')
  if (!definition || !canEditNodeParams(definition) || readOnly.value) return
  recordMutation(() => {
    const node = graph.value.nodes.find(item => item.nodeId === nodeId)
    if (node) node.params = { ...params }
  })
  selectedNodeId.value = nodeId
}

function undo() {
  const previous = undoStack.value.pop()
  if (!previous || readOnly.value) return
  redoStack.value.push(cloneGraph(graph.value))
  applyGraph(previous)
}

function redo() {
  const next = redoStack.value.pop()
  if (!next || readOnly.value) return
  undoStack.value.push(cloneGraph(graph.value))
  applyGraph(next)
}

function defaultParams(definition: MiningOperatorDef): Record<string, unknown> {
  return Object.fromEntries(Object.entries(definition.paramSchemaJson.properties ?? {})
    .filter(([, schema]) => schema.default !== undefined)
    .map(([key, schema]) => [key, schema.default]))
}

function addOperator(operatorType: string, position = { x: 100, y: 100 }) {
  const definition = definitionMap.value.get(operatorType)
  if (!definition || definition.editPolicy === 'fixed' || readOnly.value) return
  if (definition.unique && graph.value.nodes.some(node => node.operatorType === operatorType)) {
    ElMessage.warning(`${definition.displayName} 只能出现一次`)
    return
  }
  recordMutation(() => {
    const nodeId = `${operatorType}_${++sequence}`
    graph.value.nodes.push({
      nodeId,
      operatorType,
      operatorVersion: definition.version,
      params: defaultParams(definition),
      ui: position,
    })
  })
}

function onDrop(event: DragEvent) {
  const operatorType = event.dataTransfer?.getData('application/mining-operator-type')
  if (!operatorType) return
  addOperator(operatorType, screenToFlowCoordinate({ x: event.clientX, y: event.clientY }))
}

function slotType(nodeId: string | null | undefined, slotName: string | null | undefined, output: boolean): string | undefined {
  const node = flowNodes.value.find(item => item.id === nodeId)
  const slots = output ? node?.data.definition?.outputSlots : node?.data.definition?.inputSlots
  return slots?.find(slot => slot.name === slotName)?.type
}

function isValidConnection(connection: Connection): boolean {
  if (!connection.source || !connection.sourceHandle || !connection.target || !connection.targetHandle) return false
  const source = flowNodes.value.find(node => node.id === connection.source)?.data.definition
  const target = flowNodes.value.find(node => node.id === connection.target)?.data.definition
  if (!source || !target) return false
  const sourceType = slotType(connection.source, connection.sourceHandle, true)
  const targetType = slotType(connection.target, connection.targetHandle, false)
  return sourceType !== undefined && targetType !== undefined && sourceType === targetType
}

function editStateLabel(state: ReturnType<typeof effectiveEditState> | undefined): string {
  if (!state) return ''
  return ({ fixed: '固定骨架', required: '当前必需', optional: '可选' })[state]
}

function canApplyConnection(connection: Connection): boolean {
  if (readOnly.value || !isValidConnection(connection) || !connection.source || !connection.target) return false
  const source = flowNodes.value.find(node => node.id === connection.source)?.data.definition
  const target = flowNodes.value.find(node => node.id === connection.target)?.data.definition
  if (!source || !target) return false
  return true
}

function onConnect(connection: Connection) {
  if (!canApplyConnection(connection) || !connection.source || !connection.target) return
  const targetDefinition = flowNodes.value.find(node => node.id === connection.target)?.data.definition
  const targetSlot = targetDefinition?.inputSlots.find(slot => slot.name === connection.targetHandle)
  recordMutation(() => {
    if (targetSlot && !targetSlot.variadic) {
      graph.value.edges = graph.value.edges.filter(edge => !(edge.toNode === connection.target && edge.toSlot === connection.targetHandle))
    }
    const edge = {
      fromNode: connection.source!, fromSlot: connection.sourceHandle ?? '',
      toNode: connection.target!, toSlot: connection.targetHandle ?? '',
    }
    if (!graph.value.edges.some(item => JSON.stringify(item) === JSON.stringify(edge))) graph.value.edges.push(edge)
  })
}

function onEdgeUpdate(event: EdgeUpdateEvent) {
  if (
    !canApplyConnection(event.connection)
    || !event.connection.source || !event.connection.target
  ) return
  const targetDefinition = flowNodes.value.find(node => node.id === event.connection.target)?.data.definition
  const targetSlot = targetDefinition?.inputSlots.find(slot => slot.name === event.connection.targetHandle)
  recordMutation(() => {
    graph.value.edges = graph.value.edges.filter(edge => !(
      edge.fromNode === event.edge.source && edge.fromSlot === (event.edge.sourceHandle ?? '')
      && edge.toNode === event.edge.target && edge.toSlot === (event.edge.targetHandle ?? '')
    ))
    if (targetSlot && !targetSlot.variadic) {
      graph.value.edges = graph.value.edges.filter(edge => !(
        edge.toNode === event.connection.target && edge.toSlot === event.connection.targetHandle
      ))
    }
    const replacement = {
      fromNode: event.connection.source!,
      fromSlot: event.connection.sourceHandle ?? '',
      toNode: event.connection.target!,
      toSlot: event.connection.targetHandle ?? '',
    }
    if (!graph.value.edges.some(edge => (
      edge.fromNode === replacement.fromNode && edge.fromSlot === replacement.fromSlot
      && edge.toNode === replacement.toNode && edge.toSlot === replacement.toSlot
    ))) graph.value.edges.push(replacement)
  })
  selectedEdgeId.value = `${event.connection.source}.${event.connection.sourceHandle ?? ''}->${event.connection.target}.${event.connection.targetHandle ?? ''}`
  flowEdges.value = flowEdges.value.map(edge => ({ ...edge, selected: edge.id === selectedEdgeId.value }))
}

function startFlowMutation() {
  if (!readOnly.value) flowMutationStart = cloneGraph(graph.value)
}

function finishFlowMutation() {
  if (!flowMutationStart || readOnly.value) return
  syncGraphFromFlow()
  if (stableGraphJson(flowMutationStart) !== stableGraphJson(graph.value)) {
    undoStack.value.push(flowMutationStart)
    redoStack.value = []
  }
  flowMutationStart = null
}

function selectNode(event: { node: { id: string } }) {
  selectedNodeId.value = event.node.id
  selectedEdgeId.value = ''
}

function selectEdge(event: EdgeMouseEvent) {
  selectedNodeId.value = ''
  selectedEdgeId.value = event.edge.id
  flowEdges.value = flowEdges.value.map(edge => ({ ...edge, selected: edge.id === event.edge.id }))
}

function clearSelection() {
  selectedNodeId.value = ''
  selectedEdgeId.value = ''
  flowEdges.value = flowEdges.value.map(edge => ({ ...edge, selected: false }))
}

function deleteSelectedEdge() {
  const edge = selectedEdge.value
  if (!edge || readOnly.value) return
  recordMutation(() => {
    graph.value.edges = graph.value.edges.filter(item => !(
      item.fromNode === edge.source && item.fromSlot === edge.sourceHandle
      && item.toNode === edge.target && item.toSlot === edge.targetHandle
    ))
  })
  selectedEdgeId.value = ''
}

function isTextInput(target: EventTarget | null): boolean {
  if (!(target instanceof Element)) return false
  if (target instanceof HTMLElement && target.isContentEditable) return true
  return Boolean(target.closest('input, textarea, select, [contenteditable]:not([contenteditable="false"])'))
}

function onEditorKeydown(event: KeyboardEvent) {
  if (!selectedEdge.value || readOnly.value || isTextInput(event.target)) return
  if (event.key !== 'Delete' && event.key !== 'Backspace') return
  event.preventDefault()
  deleteSelectedEdge()
}

function deleteSelected() {
  const node = graph.value.nodes.find(item => item.nodeId === selectedNodeId.value)
  const definition = node ? definitionMap.value.get(node.operatorType) : undefined
  if (!node || !definition || !canDeleteNodeInGraph(definition, graph.value.nodes)) return
  recordMutation(() => {
    graph.value.nodes = graph.value.nodes.filter(item => item.nodeId !== node.nodeId)
    graph.value.edges = graph.value.edges.filter(edge => edge.fromNode !== node.nodeId && edge.toNode !== node.nodeId)
  })
  selectedNodeId.value = ''
}

function toggleSelectedDisabled() {
  const node = graph.value.nodes.find(item => item.nodeId === selectedNodeId.value)
  const definition = node ? definitionMap.value.get(node.operatorType) : undefined
  if (!node || !definition || !canDisableNode(definition)) return
  recordMutation(() => { node.disabled = !node.disabled })
  selectedNodeId.value = node.nodeId
}

async function saveDraft(): Promise<boolean> {
  if (!workflow.value || readOnly.value) return false
  saving.value = true
  syncGraphFromFlow()
  try {
    const updated = await api.saveDraft(props.id, {
      graph: cloneGraph(graph.value),
      expected_revision: workflow.value.draft_revision,
    })
    workflow.value = updated
    savedGraph.value = cloneGraph(graph.value)
    undoStack.value = []
    redoStack.value = []
    recoveryVisible.value = false
    ElMessage.success('草稿已保存')
    return true
  } catch (error) {
    if (isErrorCode(error, 'draft_revision_conflict')) recoveryVisible.value = true
    else ElMessage.error(`保存失败：${errorMessage(error)}`)
    return false
  } finally {
    saving.value = false
  }
}

async function validateOnServer(): Promise<MiningWorkflowValidationResult | null> {
  validating.value = true
  syncGraphFromFlow()
  try {
    serverResult.value = await api.validate(props.id, cloneGraph(graph.value))
    return serverResult.value
  } catch (error) {
    ElMessage.error(`校验失败：${errorMessage(error)}`)
    return null
  } finally {
    validating.value = false
  }
}

async function publishWorkflow() {
  if (!workflow.value || readOnly.value) return
  publishing.value = true
  try {
    if (dirty.value && !(await saveDraft())) return
    const validation = await validateOnServer()
    if (!validation?.valid) {
      ElMessage.warning('服务端校验未通过，不能发布')
      return
    }
    await api.publish(props.id, { expected_revision: workflow.value.draft_revision })
    workflow.value = await api.get(props.id)
    versions.value = await api.listVersions(props.id)
    savedGraph.value = cloneGraph(graph.value)
    requiresRepublish.value = false
    ElMessage.success('已发布不可变新版本')
  } catch (error) {
    if (isErrorCode(error, 'draft_revision_conflict')) recoveryVisible.value = true
    else ElMessage.error(`发布失败：${errorMessage(error)}`)
  } finally {
    publishing.value = false
  }
}

async function previewVersion(version: number) {
  try {
    const loaded = await api.getVersion(props.id, version)
    if (!readOnly.value) previewReturnGraph = cloneGraph(graph.value)
    previewedVersion.value = loaded
    readOnly.value = true
    selectedNodeId.value = ''
    selectedEdgeId.value = ''
    applyGraph(loaded.graph_json)
  } catch (error) {
    ElMessage.error(`加载版本失败：${errorMessage(error)}`)
  }
}

function exitPreview() {
  readOnly.value = false
  previewedVersion.value = null
  applyGraph(previewReturnGraph ?? savedGraph.value)
  previewReturnGraph = null
}

async function restoreVersion(version: number) {
  if (!workflow.value) return
  try {
    const restored = await api.restoreDraft(props.id, version, { expected_revision: workflow.value.draft_revision })
    workflow.value = restored
    savedGraph.value = cloneGraph(restored.draft_graph_json)
    applyGraph(restored.draft_graph_json)
    readOnly.value = false
    previewedVersion.value = null
    previewReturnGraph = null
    requiresRepublish.value = true
    undoStack.value = []
    redoStack.value = []
    ElMessage.success('已恢复为新草稿，尚未发布')
  } catch (error) {
    if (isErrorCode(error, 'draft_revision_conflict')) recoveryVisible.value = true
    else ElMessage.error(`恢复失败：${errorMessage(error)}`)
  }
}

async function copyLocalJson() {
  if (await copyToClipboard(localJson.value)) ElMessage.success('本地 JSON 已复制')
  else ElMessage.warning('复制失败，请从下方 JSON 手动复制')
}

async function reloadRemote() {
  recoveryVisible.value = false
  requiresRepublish.value = false
  readOnly.value = false
  previewReturnGraph = null
  await load()
}

function goBack() {
  router.push({ name: 'mining-workflows' })
}

function isErrorCode(error: unknown, code: string): boolean {
  return (error as { response?: { data?: { detail?: { code?: string } } } })?.response?.data?.detail?.code === code
}

function errorMessage(error: unknown): string {
  const value = error as { response?: { data?: { detail?: { message?: string } | string } }; message?: string }
  const detail = value?.response?.data?.detail
  return (typeof detail === 'string' ? detail : detail?.message) || value?.message || '未知错误'
}

onBeforeRouteLeave((_to, _from, next) => {
  if (!dirty.value) {
    next()
    return
  }
  ElMessageBox.confirm('存在未保存的 Workflow 草稿改动，确认离开？', '未保存改动', { type: 'warning' })
    .then(() => next())
    .catch(() => next(false))
})

function beforeUnload(event: BeforeUnloadEvent) {
  if (!dirty.value) return
  event.preventDefault()
  event.returnValue = ''
}

onMounted(() => {
  window.addEventListener('beforeunload', beforeUnload)
  window.addEventListener('keydown', onEditorKeydown)
  load()
})
onBeforeUnmount(() => {
  window.removeEventListener('beforeunload', beforeUnload)
  window.removeEventListener('keydown', onEditorKeydown)
})
</script>

<style scoped>
.workflow-editor { height: calc(100vh - 88px); display: flex; flex-direction: column; }
.workflow-editor__toolbar { min-height: 48px; display: flex; justify-content: space-between; align-items: center; gap: 16px; border-bottom: 1px solid var(--kb-border, #e5e7eb); }
.workflow-editor__identity, .workflow-editor__actions { display: flex; align-items: center; gap: 8px; }
.workflow-editor__dirty { color: #d97706; font-size: 12px; }
.workflow-editor__restore-banner { padding: 9px 14px; color: #92400e; background: #fffbeb; border-bottom: 1px solid #fde68a; font-size: 12px; }
.workflow-editor__conflict { display: grid; grid-template-columns: auto 1fr auto auto; align-items: center; gap: 8px; padding: 10px 14px; color: #991b1b; background: #fef2f2; }
.workflow-editor__conflict pre { grid-column: 1 / -1; max-height: 120px; overflow: auto; margin: 0; padding: 8px; background: #fff; font-size: 10px; }
.workflow-editor__body { flex: 1; min-height: 0; display: grid; grid-template-columns: 220px minmax(460px, 1fr) 310px; }
.workflow-editor__palette, .workflow-editor__inspector { overflow: auto; padding: 12px; background: var(--kb-bg-subtle, #f8fafc); }
.workflow-editor__palette { border-right: 1px solid var(--kb-border, #e5e7eb); }
.workflow-editor__palette.is-readonly { pointer-events: none; opacity: .55; }
.workflow-editor__canvas { min-width: 0; position: relative; }
.workflow-editor__inspector { border-left: 1px solid var(--kb-border, #e5e7eb); display: flex; flex-direction: column; gap: 12px; }
.workflow-editor__section { padding: 10px; border-radius: 8px; background: #fff; border: 1px solid var(--kb-border, #e5e7eb); }
.workflow-editor__section h3 { margin: 0 0 9px; font-size: 13px; }
.workflow-editor__muted { color: var(--kb-text-tertiary); font-size: 12px; line-height: 1.5; }
.workflow-editor__policy { margin: 8px 0; padding: 6px 8px; border-radius: 6px; background: #f8fafc; color: #475569; font-size: 11px; line-height: 1.5; }
.workflow-editor__node-title { display: flex; flex-direction: column; gap: 2px; }
.workflow-editor__node-title code { color: var(--kb-text-tertiary); font-size: 10px; }
.workflow-editor__edge-route { display: flex; align-items: center; gap: 6px; min-width: 0; }
.workflow-editor__edge-route code { min-width: 0; overflow: hidden; text-overflow: ellipsis; color: #334155; font-size: 10px; }
.workflow-editor__node-actions { display: flex; justify-content: flex-end; gap: 6px; margin-top: 12px; }
.workflow-editor__version-row { width: 100%; display: flex; justify-content: space-between; gap: 6px; padding: 7px; border: 0; border-bottom: 1px solid #eef2f7; background: transparent; cursor: pointer; }
.workflow-editor__version-row small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--kb-text-tertiary); }
@media (max-width: 1100px) { .workflow-editor__body { grid-template-columns: 180px 1fr 270px; } }
</style>
