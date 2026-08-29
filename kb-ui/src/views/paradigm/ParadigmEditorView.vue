<template>
  <div class="editor">
    <!-- toolbar -->
    <div class="editor__toolbar">
      <div class="editor__tb-left">
        <el-button text :icon="Back" @click="goBack">返回</el-button>
        <span class="editor__name">{{ paradigm?.name ?? '...' }}</span>
        <el-tag v-if="paradigm" size="small" :type="paradigm.status === 'active' ? 'success' : 'info'">
          {{ paradigm.currentVersion > 0 ? `v${paradigm.currentVersion} 已发布` : '草稿' }}
        </el-tag>
      </div>
      <div class="editor__tb-right">
        <el-button :icon="Check" :loading="saving" :disabled="previewMode" @click="save">保存草稿</el-button>
        <el-button :icon="Finished" :loading="validating" :disabled="previewMode" @click="validate">校验</el-button>
        <el-button type="primary" :icon="Promotion" :loading="publishing" :disabled="previewMode" @click="publish">发布</el-button>
      </div>
    </div>

    <div class="editor__body">
      <!-- palette -->
      <aside class="editor__palette" :class="{ 'editor__palette--locked': previewMode }">
        <OperatorPalette :operators="operators" />
      </aside>

      <!-- canvas -->
      <div class="editor__canvas" @drop="onDrop" @dragover.prevent @dragenter.prevent>
        <VueFlow
          v-model:nodes="nodes"
          v-model:edges="edges"
          :default-viewport="{ zoom: 0.9 }"
          :delete-key-code="previewMode ? null : 'Delete'"
          :is-valid-connection="isValidConnection"
          :nodes-draggable="!previewMode"
          fit-view-on-init
          @connect="onConnect"
          @connect-start="onConnectStart"
          @connect-end="onConnectEnd"
          @node-click="onNodeClick"
          @pane-click="selectedNodeId = ''"
        >
          <template #node-operator="nodeProps">
            <OperatorNode :id="nodeProps.id" :data="nodeProps.data" :selected="nodeProps.id === selectedNodeId" />
          </template>
          <Background pattern-color="#cbd5e1" :gap="16" />
          <Controls />
        </VueFlow>
        <div v-if="nodes.length === 0" class="editor__canvas-hint">从左侧拖拽算子到此处开始编排</div>
        <div class="editor__canvas-legend">连线规则：相同 slot 类型才能连（候选可接入融合多入口）；类型不符会被拒绝</div>

        <button class="editor__help-btn" type="button" @click="helpVisible = !helpVisible">
          {{ helpVisible ? '✕ 关闭' : '？ 连线帮助' }}
        </button>
        <div v-if="helpVisible" class="editor__help-panel">
          <div class="editor__help-title">连线规则</div>
          <ul class="editor__help-list">
            <li><b>方向</b>：从节点右侧「输出口」拖到下游左侧「输入口」</li>
            <li><b>类型一致</b>：同类型才能连（向量→向量、范围→范围、候选→候选…）；唯一例外：<b>候选 → 融合算子的多入口</b></li>
            <li><b>单入 / 多入</b>：普通输入口只接一条（再连会替换旧线）；融合 <code>candidates</code> 可接多条</li>
            <li><b>不能成环</b>：整图必须是有向无环图（DAG）</li>
            <li><b>入口</b>：<code>query</code> 输入口可不连（自动用请求查询）；检索的 <b><code>scope</code> 必须连 scope_resolve</b></li>
            <li><b>终点</b>：生产范式用 <code>assemble</code>（EvidenceResponse）作输出；评测图可直接把召回算子的 <code>candidates</code> 声明为输出</li>
            <li><b>删除</b>：选中节点/连线按 <kbd>Delete</kbd>；节点也可在右侧「删除节点」</li>
          </ul>
        </div>

        <div v-if="previewMode" class="editor__preview-banner">
          <span>正在查看已发布 <strong>v{{ previewVersion }}</strong>（只读）</span>
          <el-button size="small" type="primary" @click="exitPreview">退出预览，回到草稿</el-button>
        </div>
      </div>

      <!-- inspector -->
      <aside class="editor__inspector">
        <!-- selected node params -->
        <section class="editor__sec">
          <div class="editor__sec-title">
            节点参数
            <el-button v-if="selectedNode" size="small" text type="danger" @click="deleteSelected">删除节点</el-button>
          </div>
          <div v-if="!selectedNode" class="editor__sec-empty">选中画布上的节点以编辑参数</div>
          <template v-else>
            <div class="editor__node-head">
              <strong>{{ selectedDef?.displayName }}</strong>
              <code>{{ selectedNode.id }}</code>
            </div>
            <p class="editor__node-desc">{{ selectedDef?.description }}</p>
            <ParamForm
              v-if="selectedDef"
              :key="selectedNode.id"
              :schema-json="selectedDef.paramSchemaJson"
              :model-value="selectedNode.data.params"
              :option-sources="paramOptionSources"
              @update:model-value="updateSelectedParams"
            />
          </template>
        </section>

        <!-- output node -->
        <section class="editor__sec">
          <div class="editor__sec-title">输出（终点算子）</div>
          <el-select v-model="outputKey" size="small" style="width: 100%" :disabled="previewMode" placeholder="选择终点节点的输出 slot" @change="onOutputChange">
            <el-option v-for="o in outputOptions" :key="o.value" :label="o.label" :value="o.value" />
          </el-select>
        </section>

        <!-- version history -->
        <section v-if="versions.length" class="editor__sec">
          <div class="editor__sec-title">版本历史</div>
          <div v-for="v in versions" :key="v.version" class="editor__ver-row">
            <span class="editor__ver-tag" :class="{ 'editor__ver-tag--cur': v.version === paradigm?.currentVersion }">
              v{{ v.version }}<span v-if="v.version === paradigm?.currentVersion"> · 当前</span>
            </span>
            <span class="editor__ver-by">{{ v.createdBy || '—' }}</span>
            <el-button size="small" text @click="viewVersion(v.version)">查看</el-button>
            <el-button
              size="small" text type="warning"
              :disabled="v.version === paradigm?.currentVersion"
              @click="rollbackTo(v.version)"
            >回滚</el-button>
          </div>
        </section>

        <!-- run -->
        <section class="editor__sec">
          <div class="editor__sec-title">运行</div>
          <el-input v-model="runQuery" size="small" placeholder="查询，如 aa-interface" @keyup.enter="dryRun" />
          <div class="editor__run-row">
            <el-button size="small" :loading="running" @click="dryRun">试运行草稿（不落库）</el-button>
          </div>
          <div class="editor__run-row">
            <el-select v-model="runVersion" size="small" style="width: 124px" :disabled="!hasPublished">
              <el-option label="当前版本" :value="0" />
              <el-option v-for="v in versions" :key="v.version" :label="`v${v.version}`" :value="v.version" />
            </el-select>
            <el-button size="small" type="primary" :loading="runningPub" :disabled="!hasPublished" @click="runPublished">
              运行已发布
            </el-button>
          </div>
          <div v-if="!hasPublished" class="editor__run-hint">尚未发布，先点上方「发布」</div>
          <RunResultPanel :result="runResult" />
        </section>

        <!-- published API call info -->
        <section v-if="hasPublished" class="editor__sec">
          <div class="editor__sec-title">接口调用（测试系统）</div>
          <div class="editor__api-row">
            <span class="editor__api-k">范式 ID</span>
            <code class="editor__api-v">{{ id }}</code>
            <el-button size="small" text type="primary" @click="copyText(id)">复制</el-button>
          </div>
          <div class="editor__api-row">
            <span class="editor__api-k">端点</span>
            <code class="editor__api-v">POST {{ apiPath }}</code>
            <el-button size="small" text type="primary" @click="copyText(apiPath)">复制</el-button>
          </div>
          <div class="editor__api-note">
            当前版本 <b>v{{ paradigm?.currentVersion }}</b>；调用加 <code>?version=N</code> 锁定版本，不加用最新。
            body：<code>{"query":"...","domain":"{{ domainStore.currentDomain }}"}</code>
          </div>
          <el-button size="small" type="primary" plain style="width: 100%; margin-top: 8px" @click="copyText(curlExample)">
            复制 curl 调用示例
          </el-button>
        </section>
      </aside>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { VueFlow, useVueFlow, MarkerType, type Connection } from '@vue-flow/core'

// Loosely-typed node/edge shapes for our own state. Vue Flow's Node/Edge generics are
// extremely deep and trigger TS2589 ("excessively deep") when used in our computeds/refs;
// VueFlow applies its own types internally at the component boundary.
interface FlowNode { id: string; type: string; position: { x: number; y: number }; data: any }
interface FlowEdge { id: string; source: string; target: string; sourceHandle: string; targetHandle: string; markerEnd?: string }
import { Background } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'
import '@vue-flow/core/dist/style.css'
import '@vue-flow/core/dist/theme-default.css'
import '@vue-flow/controls/dist/style.css'
import { Back, Check, Finished, Promotion } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import OperatorPalette from '@/components/paradigm/OperatorPalette.vue'
import OperatorNode from '@/components/paradigm/OperatorNode.vue'
import ParamForm from '@/components/paradigm/ParamForm.vue'
import RunResultPanel from '@/components/paradigm/RunResultPanel.vue'
import { useOperatorApi } from '@/api/operator'
import { useKbApi } from '@/api/kb'
import { useDomainStore } from '@/stores/domain'
import { copyToClipboard } from '@/utils/clipboard'
import type { ParamOption } from '@/components/workflow/JsonSchemaParamForm.vue'
import type { OperatorDef, ParadigmGraph, ParadigmView, ParadigmVersionView, RunResult } from '@/types/operator'

const props = defineProps<{ id: string }>()
const router = useRouter()
const api = useOperatorApi()
const kbApi = useKbApi()
const { screenToFlowCoordinate } = useVueFlow()

const operators = ref<OperatorDef[]>([])
const defMap = computed<Record<string, OperatorDef>>(() =>
  Object.fromEntries(operators.value.map(o => [o.type, o])))

const paradigm = ref<ParadigmView | null>(null)
const nodes = ref<FlowNode[]>([])
const edges = ref<FlowEdge[]>([])
const selectedNodeId = ref('')
const output = ref<{ nodeId: string; slot: string } | null>(null)
const outputKey = ref('')

const runQuery = ref('aa-interface')
const runResult = ref<RunResult | null>(null)

const versions = ref<ParadigmVersionView[]>([])
const runVersion = ref(0) // 0 = current/latest
const hasPublished = computed(() => (paradigm.value?.currentVersion ?? 0) > 0)

// ---- kb-picker options for scope_resolve.kbIds ----
const kbOptions = ref<ParamOption[]>([])
// 空数组也要下发：optionSources 里有这个键，控件才会渲染成选择器而不是自由输入框。
const paramOptionSources = computed<Record<string, ParamOption[]>>(() => ({
  'kb-picker': kbOptions.value,
}))

// ---- published API call info ----
const domainStore = useDomainStore()
const apiPath = computed(() => `/api/v1/paradigm/${props.id}/search`)
/** Full URL via the main_control proxy (reachable from this UI's origin); the test system can also call serving directly. */
const apiProxyUrl = computed(() =>
  `${window.location.origin}/api/control-plane/api/v1/proxy/${domainStore.currentDomain}/serving${apiPath.value}`)
const curlExample = computed(() =>
  `curl -X POST '${apiProxyUrl.value}?version=${paradigm.value?.currentVersion ?? 1}' \\\n`
  + `  -H 'Content-Type: application/json' \\\n`
  + `  -d '{"query":"aa-interface","domain":"${domainStore.currentDomain}"}'`)

async function copyText(text: string) {
  if (await copyToClipboard(text)) ElMessage.success('已复制')
  else ElMessage.warning('复制失败，请手动选择文本')
}

const saving = ref(false)
const validating = ref(false)
const publishing = ref(false)
const running = ref(false)
const runningPub = ref(false)

const previewMode = ref(false)
const previewVersion = ref(0)
const helpVisible = ref(false)

let seq = 0

const selectedNode = computed(() => nodes.value.find(n => n.id === selectedNodeId.value) || null)
const selectedDef = computed(() => selectedNode.value ? defMap.value[selectedNode.value.data.operatorType] : undefined)

const outputOptions = computed(() => {
  const opts: { label: string; value: string }[] = []
  for (const n of nodes.value) {
    const def = defMap.value[n.data.operatorType]
    for (const s of def?.outputSlots ?? []) {
      opts.push({ label: `${n.id} · ${def?.displayName} → ${s.name}`, value: `${n.id}:${s.name}` })
    }
  }
  return opts
})

// ---- load ----
onMounted(async () => {
  try {
    operators.value = await api.getCatalog()
    paradigm.value = await api.getParadigm(props.id)
    const g = paradigm.value.draftGraph
    if (g) loadGraph(g)
    await loadVersions()
  } catch (e) {
    ElMessage.error('加载失败：' + errMsg(e))
  }
  loadKbOptions()
})

/**
 * scope_resolve 的 kbIds 参数（x-widget: kb-picker）的候选项。
 *
 * 知识库归 mining 管，所以这里跨服务取——检索范式编辑器唯一一次调 mining。取不到就
 * 让参数退化成手填 id 的输入框（JsonSchemaParamForm 的默认数组控件），不挡编辑。
 * 列表本身已按 X-KB-User 过滤，因此下拉里只会出现当前用户可见的库。
 */
async function loadKbOptions() {
  const domain = domainStore.currentDomain
  if (!domain) return
  try {
    const kbs = await kbApi.listKbs(domain)
    kbOptions.value = kbs.map(kb => ({
      value: kb.id,
      label: kb.name,
      hint: `${kb.document_count} 篇`,
    }))
  } catch (e) {
    console.error('Failed to load knowledge bases for kb-picker:', e)
    kbOptions.value = []
  }
}

async function loadVersions() {
  if ((paradigm.value?.currentVersion ?? 0) <= 0) return
  try {
    versions.value = await api.listVersions(props.id)
    runVersion.value = paradigm.value?.currentVersion ?? 0
  } catch { /* ignore */ }
}

function loadGraph(g: ParadigmGraph) {
  let maxSeq = 0
  nodes.value = g.nodes.map((gn, i) => {
    const m = /(\d+)$/.exec(gn.nodeId)
    if (m) maxSeq = Math.max(maxSeq, Number(m[1]))
    return {
      id: gn.nodeId,
      type: 'operator',
      position: gn.ui ?? { x: 60 + (i % 4) * 220, y: 60 + Math.floor(i / 4) * 160 },
      data: { operatorType: gn.operatorType, def: defMap.value[gn.operatorType], params: gn.params ?? {} },
    }
  })
  edges.value = g.edges.map(ge => mkEdge(ge.fromNode, ge.fromSlot, ge.toNode, ge.toSlot))
  seq = maxSeq
  if (g.output) { output.value = g.output; outputKey.value = `${g.output.nodeId}:${g.output.slot}` }
  syncOutputFlag()
}

// ---- canvas interactions ----
// ---- connect-time slot-type validation (acceptance #1) ----
function outSlotType(nodeId?: string | null, slot?: string | null): string | undefined {
  const n = nodes.value.find(x => x.id === nodeId)
  return defMap.value[n?.data.operatorType]?.outputSlots.find(s => s.name === slot)?.type
}
function inSlotType(nodeId?: string | null, slot?: string | null): string | undefined {
  const n = nodes.value.find(x => x.id === nodeId)
  return defMap.value[n?.data.operatorType]?.inputSlots.find(s => s.name === slot)?.type
}
/** Mirror of backend SlotType.isAssignable: same type, or CANDIDATE_LIST → CANDIDATE_LIST_MULTI. */
function isAssignable(from?: string, to?: string): boolean {
  if (!from || !to) return true // unknown — let the server compiler decide
  if (from === to) return true
  return to === 'CANDIDATE_LIST_MULTI' && from === 'CANDIDATE_LIST'
}
const TYPE_LABELS: Record<string, string> = {
  STRING: '文本', INT: '整数', DOUBLE: '小数', BOOL: '布尔', VECTOR: '向量',
  STRING_LIST: '字符串列表', CANDIDATE_LIST: '候选', CANDIDATE_LIST_MULTI: '候选(多入)',
  SCOPE: '范围', QUERY_UNDERSTANDING: '查询理解', HYDRATED_EVIDENCE_LIST: '水合证据', EVIDENCE_RESPONSE: '证据响应',
}
function typeLabel(t?: string): string { return t ? (TYPE_LABELS[t] ?? t) : '?' }

// Track the latest invalid hover so connect-end can surface a clear reason.
let invalidHint = ''
let connectedThisDrag = false

/** Real-time guard during a connection drag — incompatible target handles won't connect. */
function isValidConnection(c: Connection): boolean {
  const from = outSlotType(c.source, c.sourceHandle)
  const to = inSlotType(c.target, c.targetHandle)
  const ok = isAssignable(from, to)
  if (!ok && from && to) {
    invalidHint = `连接有误：${typeLabel(from)}（输出）不能接到 ${typeLabel(to)}（输入）—— slot 类型需一致`
  }
  return ok
}

function onConnectStart() { connectedThisDrag = false; invalidHint = '' }

/** A drag that ended without a successful connect, after hovering an incompatible handle. */
function onConnectEnd() {
  if (!connectedThisDrag && invalidHint) ElMessage.warning(invalidHint)
  invalidHint = ''
}

function onConnect(c: Connection) {
  if (previewMode.value) return
  if (!c.source || !c.target) return
  // Backstop type check (is-valid-connection already blocks during drag; this also surfaces a hint).
  const from = outSlotType(c.source, c.sourceHandle)
  const to = inSlotType(c.target, c.targetHandle)
  if (!isAssignable(from, to)) {
    ElMessage.warning(`类型不匹配：${from} 不能接到 ${to}`)
    return
  }
  // one incoming edge per non-variadic target slot: drop existing edge to same target slot
  const slot = defMap.value[nodes.value.find(n => n.id === c.target)?.data.operatorType]
    ?.inputSlots.find(s => s.name === c.targetHandle)
  if (slot && slot.type !== 'CANDIDATE_LIST_MULTI') {
    edges.value = edges.value.filter(e => !(e.target === c.target && e.targetHandle === c.targetHandle))
  }
  // Dedup: the same source→target/handle pair (e.g. re-dragged onto a MULTI slot) yields an
  // identical edge id; skip it to avoid duplicate-keyed edges.
  const edge = mkEdge(c.source, c.sourceHandle || '', c.target, c.targetHandle || '')
  if (edges.value.some(e => e.id === edge.id)) { connectedThisDrag = true; return }
  edges.value.push(edge)
  connectedThisDrag = true
}

function onDrop(e: DragEvent) {
  if (previewMode.value) return
  const type = e.dataTransfer?.getData('application/operator-type')
  if (!type) return
  const def = defMap.value[type]
  if (!def) return
  const pos = screenToFlowCoordinate({ x: e.clientX, y: e.clientY })
  const id = `${type}_${++seq}`
  nodes.value.push({
    id, type: 'operator', position: pos,
    data: { operatorType: type, def, params: defaultParams(def) },
  })
  selectedNodeId.value = id
}

function onNodeClick(e: { node: FlowNode }) { selectedNodeId.value = e.node.id }

function deleteSelected() {
  if (previewMode.value) return
  const id = selectedNodeId.value
  nodes.value = nodes.value.filter(n => n.id !== id)
  edges.value = edges.value.filter(e => e.source !== id && e.target !== id)
  if (output.value?.nodeId === id) { output.value = null; outputKey.value = '' }
  selectedNodeId.value = ''
}

function updateSelectedParams(params: Record<string, unknown>) {
  if (previewMode.value) return
  const n = selectedNode.value
  // Reassign data (not just .params) so VueFlow propagates the change to the on-canvas node
  // and its param summary refreshes (same pattern as syncOutputFlag).
  if (n) n.data = { ...n.data, params }
}

// ---- version history: read-only preview + rollback ----
function viewVersion(v: number) {
  const ve = versions.value.find(x => x.version === v)
  if (!ve?.graph) { ElMessage.warning('该版本无图数据'); return }
  loadGraph(ve.graph)
  previewVersion.value = v
  previewMode.value = true
  selectedNodeId.value = ''
}

function exitPreview() {
  previewMode.value = false
  selectedNodeId.value = ''
  if (paradigm.value?.draftGraph) {
    loadGraph(paradigm.value.draftGraph)
  } else {
    nodes.value = []
    edges.value = []
    output.value = null
    outputKey.value = ''
  }
}

async function rollbackTo(v: number) {
  try {
    await ElMessageBox.confirm(`将「当前版本」指回 v${v}（历史版本内容不变，可再次回滚）。`, '回滚版本', { type: 'warning' })
  } catch { return }
  try {
    paradigm.value = await api.rollback(props.id, v)
    await loadVersions()
    runVersion.value = v
    ElMessage.success(`已回滚，当前版本 = v${v}`)
  } catch (e) {
    ElMessage.error('回滚失败：' + errMsg(e))
  }
}

function onOutputChange(val: string) {
  const [nodeId, slot] = val.split(':')
  output.value = { nodeId, slot }
  syncOutputFlag()
}

function syncOutputFlag() {
  for (const n of nodes.value) n.data = { ...n.data, isOutput: n.id === output.value?.nodeId }
}

// ---- actions ----
function serialize(): ParadigmGraph {
  return {
    schemaVersion: '1.0',
    nodes: nodes.value.map(n => ({
      nodeId: n.id,
      operatorType: n.data.operatorType,
      params: n.data.params ?? {},
      ui: { x: Math.round(n.position.x), y: Math.round(n.position.y) },
    })),
    edges: edges.value.map(e => ({
      fromNode: e.source, fromSlot: e.sourceHandle || '',
      toNode: e.target, toSlot: e.targetHandle || '',
    })),
    output: output.value ?? { nodeId: '', slot: '' },
  }
}

async function save() {
  saving.value = true
  try {
    paradigm.value = await api.updateDraft(props.id, serialize())
    ElMessage.success('草稿已保存')
  } catch (e) { ElMessage.error('保存失败：' + errMsg(e)) } finally { saving.value = false }
}

async function validate() {
  validating.value = true
  try {
    const r = await api.validateInline(serialize())
    runResult.value = null
    if (r.errors && r.errors.length) {
      runResult.value = { error: 'paradigm_compile_failed', errors: r.errors }
      ElMessage.warning(`校验未通过：${r.errors.length} 个问题`)
    } else {
      ElMessage.success('校验通过 ✓')
    }
  } catch (e) {
    runResult.value = compileErrorOf(e)
    ElMessage.warning('校验未通过')
  } finally { validating.value = false }
}

async function publish() {
  try {
    await ElMessageBox.confirm('将先保存草稿、编译校验，然后发布为不可变版本。', '发布范式', { type: 'info' })
  } catch { return }
  publishing.value = true
  try {
    await api.updateDraft(props.id, serialize())
    const v = await api.publish(props.id)
    paradigm.value = await api.getParadigm(props.id)
    await loadVersions()
    runVersion.value = v.version
    ElMessage.success(`已发布 v${v.version}`)
  } catch (e) {
    runResult.value = compileErrorOf(e)
    ElMessage.error('发布失败：' + errMsg(e))
  } finally { publishing.value = false }
}

async function dryRun() {
  if (!runQuery.value.trim()) { ElMessage.warning('请输入查询'); return }
  running.value = true
  runResult.value = null
  try {
    runResult.value = await api.runInline(serialize(), runQuery.value.trim(), { debug: true })
  } catch (e) {
    runResult.value = compileErrorOf(e)
    ElMessage.error('运行失败：' + errMsg(e))
  } finally { running.value = false }
}

async function runPublished() {
  if (!runQuery.value.trim()) { ElMessage.warning('请输入查询'); return }
  runningPub.value = true
  runResult.value = null
  try {
    runResult.value = await api.search(props.id, runQuery.value.trim(), {
      debug: true,
      version: runVersion.value > 0 ? runVersion.value : undefined,
    })
  } catch (e) {
    runResult.value = compileErrorOf(e)
    ElMessage.error('运行失败：' + errMsg(e))
  } finally { runningPub.value = false }
}

// ---- helpers ----
function mkEdge(source: string, sourceHandle: string, target: string, targetHandle: string): FlowEdge {
  return {
    id: `e_${source}.${sourceHandle}__${target}.${targetHandle}`,
    source, target, sourceHandle, targetHandle,
    markerEnd: MarkerType.ArrowClosed,
  }
}

function defaultParams(def: OperatorDef): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  try {
    const schema = JSON.parse(def.paramSchemaJson || '{}')
    for (const [k, p] of Object.entries(schema.properties ?? {})) {
      const def_ = (p as { default?: unknown }).default
      if (def_ !== undefined) out[k] = def_
    }
  } catch { /* ignore */ }
  return out
}

function compileErrorOf(e: unknown): RunResult {
  const data = (e as { response?: { data?: RunResult } })?.response?.data
  if (data?.errors) return { error: data.error, errors: data.errors }
  return { error: errMsg(e) }
}

function errMsg(e: unknown): string {
  const a = e as { response?: { data?: { message?: string; error?: string } }; message?: string }
  return a?.response?.data?.message || a?.response?.data?.error || a?.message || '未知错误'
}

function goBack() { router.push({ name: 'paradigm' }) }
</script>

<style scoped>
.editor { display: flex; flex-direction: column; height: calc(100vh - var(--kb-header-height, 56px) - 32px); }
.editor__toolbar { display: flex; justify-content: space-between; align-items: center; padding: 0 4px 12px; }
.editor__tb-left { display: flex; align-items: center; gap: 12px; }
.editor__name { font-size: 16px; font-weight: 700; }
.editor__body { flex: 1; display: flex; gap: 12px; min-height: 0; }
.editor__palette { width: 196px; overflow-y: auto; background: var(--kb-bg-subtle, #f8fafc); border: 1px solid var(--kb-border, #e5e7eb); border-radius: 10px; padding: 10px; }
.editor__canvas { position: relative; flex: 1; border: 1px solid var(--kb-border, #e5e7eb); border-radius: 10px; overflow: hidden; background: #fbfcfe; }
.editor__canvas-hint { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; color: var(--kb-text-tertiary); pointer-events: none; font-size: 14px; }
.editor__canvas-legend { position: absolute; left: 10px; bottom: 8px; font-size: 11px; color: var(--kb-text-tertiary); background: rgba(255,255,255,.75); padding: 3px 8px; border-radius: 6px; pointer-events: none; }
.editor__help-btn { position: absolute; top: 10px; right: 10px; z-index: 5; font-size: 12px; padding: 5px 10px; border: 1px solid var(--kb-border, #e5e7eb); border-radius: 7px; background: #fff; color: var(--kb-text-secondary); cursor: pointer; box-shadow: 0 1px 4px rgba(0,0,0,.08); }
.editor__help-btn:hover { background: var(--kb-bg-subtle, #f8fafc); }
.editor__help-panel { position: absolute; top: 46px; right: 10px; z-index: 5; width: 320px; background: #fff; border: 1px solid var(--kb-border, #e5e7eb); border-radius: 10px; box-shadow: 0 6px 24px rgba(0,0,0,.12); padding: 12px 14px; }
.editor__help-title { font-size: 13px; font-weight: 700; color: var(--kb-text-secondary); margin-bottom: 8px; }
.editor__help-list { margin: 0; padding-left: 18px; font-size: 12px; line-height: 1.7; color: var(--kb-text-secondary); }
.editor__help-list code { font-family: monospace; font-size: 11px; background: var(--kb-bg-subtle, #f1f5f9); padding: 0 4px; border-radius: 4px; }
.editor__help-list kbd { font-family: monospace; font-size: 11px; background: #1e293b; color: #fff; padding: 0 5px; border-radius: 4px; }
/* invalid connection line turns red in real time */
:deep(.vue-flow__connection-path) { stroke: #3b82f6; }
:deep(.vue-flow__edge.invalid .vue-flow__edge-path) { stroke: #ef4444; }
.editor__preview-banner { position: absolute; top: 8px; left: 50%; transform: translateX(-50%); display: flex; align-items: center; gap: 12px; background: #fffbeb; border: 1px solid #fcd34d; color: #92400e; font-size: 12px; padding: 5px 12px; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,.08); }
.editor__palette--locked { opacity: .5; pointer-events: none; }
.editor__ver-row { display: grid; grid-template-columns: auto 1fr auto auto; gap: 6px; align-items: center; padding: 4px 0; border-bottom: 1px dashed var(--kb-border, #eee); }
.editor__ver-tag { font-size: 12px; font-weight: 600; color: var(--kb-text-secondary); }
.editor__ver-tag--cur { color: #22c55e; }
.editor__ver-by { font-size: 11px; color: var(--kb-text-tertiary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.editor__api-row { display: grid; grid-template-columns: 52px 1fr auto; gap: 6px; align-items: center; margin-bottom: 6px; }
.editor__api-k { font-size: 11px; color: var(--kb-text-tertiary); }
.editor__api-v { font-size: 11px; font-family: monospace; background: var(--kb-bg-subtle, #f1f5f9); padding: 2px 6px; border-radius: 5px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.editor__api-note { font-size: 11px; color: var(--kb-text-tertiary); line-height: 1.6; margin-top: 4px; }
.editor__api-note code { font-family: monospace; background: var(--kb-bg-subtle, #f1f5f9); padding: 0 4px; border-radius: 4px; }
.editor__inspector { width: 332px; overflow-y: auto; display: flex; flex-direction: column; gap: 14px; }
.editor__sec { background: #fff; border: 1px solid var(--kb-border, #e5e7eb); border-radius: 10px; padding: 12px 14px; }
.editor__sec-title { display: flex; justify-content: space-between; align-items: center; font-size: 13px; font-weight: 700; color: var(--kb-text-secondary); margin-bottom: 10px; }
.editor__sec-empty { color: var(--kb-text-tertiary); font-size: 13px; }
.editor__node-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px; }
.editor__node-head code { font-size: 11px; color: #94a3b8; }
.editor__node-desc { font-size: 12px; color: var(--kb-text-tertiary); margin: 0 0 12px; line-height: 1.5; }
.editor__run-row { display: flex; gap: 6px; margin-top: 8px; }
.editor__run-hint { font-size: 12px; color: var(--kb-text-tertiary); margin-top: 6px; }
</style>
