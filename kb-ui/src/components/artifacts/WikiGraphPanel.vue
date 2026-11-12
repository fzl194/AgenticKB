<template>
  <section class="wiki-graph" data-test="wiki-graph-panel">
    <!-- 错误/混版态：非空白（EmptyState 文案 + 重试入口） -->
    <div v-if="errorText" class="wiki-graph__state" data-test="wiki-graph-error">
      <EmptyState :text="errorText" />
      <el-button type="primary" data-test="wiki-graph-retry" @click="reload">重试</el-button>
    </div>

    <!-- 空态：0 页 Wiki（66号 D03.1 验收明文） -->
    <EmptyState
      v-else-if="!loading && pages.length === 0"
      text="该 Wiki 暂无页面——编译或新建页面后，这里展示页面与引用关系"
      data-test="wiki-graph-empty"
    />

    <template v-else>
      <div class="wiki-graph__toolbar">
        <el-input
          v-model="searchQuery"
          size="small"
          clearable
          class="wiki-graph__search"
          placeholder="定位页面（输入标题，回车居中）"
          data-test="wiki-graph-search"
          @keyup.enter="locateSearch"
        />
        <span class="wiki-graph__stats" data-test="wiki-graph-stats">
          一阶输入 {{ model.totals.input1 }} · 一阶输出 {{ model.totals.output1 }}
          <template v-if="model.totals.broken > 0"> · 断链 {{ model.totals.broken }}</template>
          · r{{ revision }}
        </span>
      </div>

      <div class="wiki-graph__body">
        <div class="wiki-graph__canvas-shell">
          <WikiGraphCanvas :option="chartOption" @node-click="onNodeClick" @node-dblclick="key => recenter(key)" />
        </div>

        <aside class="wiki-graph__detail" data-test="wiki-graph-detail" aria-live="polite">
          <template v-if="selectedNode">
            <div class="wiki-graph__detail-head">
              <span class="wiki-graph__detail-type">{{ selectedNode.typeLabel }}</span>
              <h4 :title="selectedNode.label">{{ selectedNode.label }}</h4>
            </div>
            <dl class="wiki-graph__detail-meta">
              <div><dt>来源</dt><dd :title="selectedNode.origin">{{ originLabel(selectedNode.origin) }}</dd></div>
              <div><dt>页键</dt><dd :title="selectedNode.id">{{ selectedNode.id }}</dd></div>
              <div><dt>出链</dt><dd>{{ selectedNode.outCount }}</dd></div>
              <div><dt>被引用</dt><dd>{{ selectedNode.inCount }}</dd></div>
            </dl>

            <p v-if="selectedNode.isBroken" class="wiki-graph__detail-note">
              该引用目标不存在或别名不唯一，请在设计态检查并修正。
            </p>
            <template v-else>
              <div v-if="expandInfo" class="wiki-graph__detail-actions">
                <el-button
                  v-if="!isExpanded(selectedNode.id)"
                  size="small"
                  plain
                  data-test="wiki-graph-expand"
                  @click="toggleExpand(selectedNode.id)"
                >展开二阶 +{{ expandInfo.count }}</el-button>
                <el-button
                  v-else
                  size="small"
                  plain
                  data-test="wiki-graph-collapse"
                  @click="toggleExpand(selectedNode.id)"
                >收起二阶</el-button>
              </div>
              <div class="wiki-graph__detail-actions">
                <el-button
                  v-if="selectedNode.id !== model.centerKey"
                  size="small"
                  data-test="wiki-graph-recenter"
                  @click="recenter(selectedNode.id)"
                >设为中心</el-button>
                <el-button
                  type="primary"
                  class="wiki-graph__open"
                  data-test="wiki-graph-open"
                  @click="emit('open-page', selectedNode.id)"
                >打开页面</el-button>
              </div>

              <section v-if="selectedOutgoing.length" class="wiki-graph__detail-links">
                <h5>引用了 {{ selectedOutgoing.length }} 页</h5>
                <button
                  v-for="item in selectedOutgoing"
                  :key="`o-${item.key}`"
                  type="button"
                  class="wiki-graph__detail-link"
                  :title="item.label"
                  @click="recenter(item.key)"
                >→ {{ item.label }}<span v-if="item.broken"> · 断链</span></button>
              </section>
              <section v-if="selectedIncoming.length" class="wiki-graph__detail-links">
                <h5>被 {{ selectedIncoming.length }} 页引用</h5>
                <button
                  v-for="item in selectedIncoming"
                  :key="`i-${item.key}`"
                  type="button"
                  class="wiki-graph__detail-link"
                  :title="item.label"
                  @click="recenter(item.key)"
                >← {{ item.label }}</button>
              </section>
            </template>
          </template>
          <p v-else class="wiki-graph__detail-hint">点击节点查看页面详情；双击节点切换中心</p>
        </aside>
      </div>

      <p class="wiki-graph__legend">
        <span>节点＝Wiki 页面</span><span>箭头＝引用方向</span><span>虚线＝断链</span>
        <span>拖拽＝整理布局（松手回落）</span><span class="wiki-graph__legend-tail">仅本制品内知识关系</span>
      </p>
    </template>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import type { EChartsOption } from 'echarts'

import { useArtifactsApi } from '@/api/artifacts'
import { apiErrorDetail } from '@/api/proxyClient'
import EmptyState from '@/components/common/EmptyState.vue'
import WikiGraphCanvas from './WikiGraphCanvas.vue'
import {
  buildWikiNeighborhood,
  type WikiNeighborhoodNode,
} from '@/utils/wikiGraph'
import type {
  WikiLink,
  WikiLinksEnvelope,
  WikiPageMeta,
  WikiPagesEnvelope,
} from '@/types/artifacts'

/**
 * Wiki 图谱视图——邻域自我中心图（2026-11-12 拍板重做）：中心=当前阅读/
 * 编辑页（centerPageKey 由父级从阅读器/编辑器带入），左侧一阶输入、右侧
 * 一阶输出，二阶默认折叠（一阶节点计数徽章 + 详情面板展开），echarts
 * 力导向布局（可拖拽、松手回落）。设计态读 draft（editor+ 由入口守卫保
 * 证）；运行态读正式 revision。只读——出口仅「打开页面」导航事件与视图
 * 内定位。引用解析以服务端 links 为准；断链伪节点只出现在输出侧。
 */
const props = defineProps<{
  artifactId: string
  /** true=设计态草稿视图；false=运行态正式版。 */
  draft: boolean
  /** 锚定页（阅读器/编辑器当前页）——变化即重定中心；无效则回落首页。 */
  centerPageKey?: string
}>()

const emit = defineEmits<{ 'open-page': [pageKey: string] }>()

const api = useArtifactsApi()

const loading = ref(false)
const loadError = ref('')
const revisionMismatch = ref(false)
const pages = ref<WikiPageMeta[]>([])
const links = ref<WikiLink[]>([])
const revision = ref(0)

const searchQuery = ref('')
const centerKey = ref('')
const selectedKey = ref('')
/** 已展开二阶的一阶节点键。 */
const expandedKeys = ref<string[]>([])

const model = computed(() => (
  buildWikiNeighborhood(pages.value, links.value, centerKey.value || undefined, new Set(expandedKeys.value))))

const selectedNode = computed<WikiNeighborhoodNode | null>(() => (
  model.value.nodes.find(node => node.id === selectedKey.value) ?? null))

const errorText = computed(() => {
  if (revisionMismatch.value) return '版本切换中，请重试（页面与引用数据版本不一致，可能刚发生发布）'
  if (loadError.value) return `图谱数据加载失败：${loadError.value}`
  return ''
})

/** 选中一阶节点的折叠信息（二阶计数>0 才有展开入口；中心/二阶/断链节点无）。 */
const expandInfo = computed(() => {
  const node = selectedNode.value
  if (!node || node.isBroken) return null
  if (node.role !== 'input1' && node.role !== 'output1') return null
  return node.folded && node.folded.count > 0 ? node.folded : null
})

/** 全图口径的选中页出入清单（详情面板导航，不限于当前邻域）。 */
const labelOf = (key: string): { key: string; label: string; broken: boolean } => {
  const page = pages.value.find(p => p.page_key === key)
  return { key, label: page?.title || key, broken: !page }
}
const selectedOutgoing = computed(() => {
  const node = selectedNode.value
  if (!node || node.isBroken) return []
  return [...new Set(links.value
    .filter(link => link.from_page === node.id)
    .map(link => link.to_page))].slice(0, 8).map(labelOf)
})
const selectedIncoming = computed(() => {
  const node = selectedNode.value
  if (!node || node.isBroken) return []
  return [...new Set(links.value
    .filter(link => link.to_page === node.id)
    .map(link => link.from_page))].slice(0, 8).map(labelOf)
})

function isExpanded(key: string): boolean {
  return expandedKeys.value.includes(key)
}

// ── echarts option（颜色取 variables.css 同值字面量——canvas 内不可读 CSS 变量）──

const TYPE_COLORS: Record<string, string> = {
  topic: '#0891b2',
  glossary: '#10b981',
  contradiction: '#ef4444',
  open_question: '#f59e0b',
}
const BROKEN_COLOR = '#b45309'
const EDGE_COLOR = '#94a3b8'
const TEXT_PRIMARY = '#0f172a'
const TEXT_SECONDARY = '#64748b'
const TEXT_TERTIARY = '#94a3b8'
const ACCENT = '#0891b2'
const ACCENT_WASH = 'rgba(8, 145, 178, 0.10)'

const chartOption = computed<EChartsOption>(() => ({
  tooltip: {
    trigger: 'item',
    backgroundColor: '#ffffff',
    borderColor: '#e2e8f0',
    borderWidth: 1,
    padding: [8, 10],
    textStyle: { color: TEXT_PRIMARY, fontSize: 12 },
    formatter: (params: unknown) => {
      const p = params as { dataType?: string; data?: Record<string, unknown> }
      if (p.dataType === 'edge') {
        const edge = p.data as { refCount?: number; isBroken?: boolean }
        return `引用${(edge.refCount ?? 1) > 1 ? ` ×${edge.refCount}` : ''}${edge.isBroken ? ' · 断链' : ''}`
      }
      const node = p.data as { pageTitle?: string; typeLabel?: string; origin?: string; outCount?: number; inCount?: number; isBroken?: boolean }
      if (!node?.pageTitle) return ''
      const origin = node.origin === 'manual' ? '手工' : node.origin === 'machine' ? '编译' : '—'
      return [
        `<b>${node.pageTitle}</b>`,
        `${node.typeLabel} · ${origin}`,
        node.isBroken ? '目标页不存在' : `出链 ${node.outCount} · 被引用 ${node.inCount}`,
      ].join('<br/>')
    },
  },
  series: [{
    type: 'graph',
    layout: 'force',
    roam: true,
    draggable: true,
    force: {
      repulsion: 340,
      gravity: 0.1,
      edgeLength: [70, 150],
      friction: 0.06,
    },
    label: {
      // 角色级默认在 data 项上覆盖（输入侧标签向左、输出侧向右，背向中心）
      position: 'right',
      fontSize: 12,
      color: TEXT_PRIMARY,
      backgroundColor: '#ffffff',
      borderWidth: 1,
      borderRadius: 6,
      padding: [6, 9],
      rich: {
        tag: { fontSize: 10, color: TEXT_TERTIARY, fontWeight: 700 as const, lineHeight: 16, align: 'left' },
        title: { fontSize: 12.5, color: TEXT_PRIMARY, fontWeight: 600 as const, lineHeight: 18, align: 'left' },
        meta: { fontSize: 10.5, color: TEXT_SECONDARY, lineHeight: 15, align: 'left' },
        badge: {
          fontSize: 10, color: ACCENT, backgroundColor: ACCENT_WASH,
          padding: [1, 4], borderRadius: 3, lineHeight: 16, align: 'left',
        },
        badgeOn: {
          fontSize: 10, color: ACCENT, backgroundColor: 'rgba(8, 145, 178, 0.22)',
          padding: [1, 4], borderRadius: 3, lineHeight: 16, align: 'left',
        },
      },
    },
    emphasis: {
      focus: 'adjacency',
      itemStyle: { shadowBlur: 12, shadowColor: 'rgba(15, 23, 42, 0.18)' },
      label: { borderColor: ACCENT, borderWidth: 2 },
    },
    edgeSymbol: ['none', 'arrow'],
    edgeSymbolSize: 7,
    data: model.value.nodes.map(node => {
      const color = node.isBroken ? BROKEN_COLOR : TYPE_COLORS[node.pageType] ?? ACCENT
      const isCenter = node.role === 'center'
      const badge = node.folded && node.folded.count > 0
        ? (isExpanded(node.id)
          ? `{badgeOn|二阶已展}`
          : `{badge|二阶 +${node.folded.count}}`)
        : ''
      return {
        id: node.id,
        key: node.id,
        name: node.label,
        x: node.position.x,
        y: node.position.y,
        fixed: isCenter,
        symbolSize: isCenter ? 18 : 11,
        itemStyle: {
          color: isCenter ? ACCENT : '#ffffff',
          borderColor: node.id === selectedKey.value ? ACCENT : color,
          borderWidth: isCenter ? 5 : 2,
          borderOpacity: isCenter ? 0.28 : 1,
          shadowBlur: isCenter ? 10 : 0,
          shadowColor: 'rgba(8, 145, 178, 0.25)',
        },
        label: {
          position: isCenter ? 'bottom' : node.role.startsWith('input') ? 'left' : 'right',
          borderColor: node.id === selectedKey.value ? ACCENT : color,
          borderType: node.isBroken ? 'dashed' : 'solid',
          formatter: node.isBroken
            ? `{tag|断链}\n{title|${node.label}}\n{meta|目标页不存在}`
            : `{tag|${node.typeLabel}}${badge}\n{title|${node.label}}\n{meta|出链 ${node.outCount} · 被引用 ${node.inCount}}`,
        },
        // 业务载荷（tooltip 取用；label 名被 echarts 标签配置占用）
        pageTitle: node.label,
        typeLabel: node.typeLabel,
        origin: node.origin,
        outCount: node.outCount,
        inCount: node.inCount,
        isBroken: node.isBroken,
      }
    }),
    links: model.value.edges.map(edge => ({
      source: edge.source,
      target: edge.target,
      lineStyle: {
        color: edge.isBroken ? '#d97706' : EDGE_COLOR,
        width: 1.4,
        opacity: 0.75,
        curveness: 0.18,
        type: edge.isBroken ? 'dashed' : 'solid',
      },
      refCount: edge.refCount,
      isBroken: edge.isBroken,
    })),
  }],
}))

// ── 交互 ──

/** 画布节点点击：选中（断链伪节点不选中——不可打开，虚线已区分）。 */
function onNodeClick(key: string): void {
  const node = model.value.nodes.find(item => item.id === key)
  if (!node || node.isBroken) return
  selectedKey.value = key
}

/** 重定中心：切换自我中心图 + 收起全部二阶（新中心的新邻域）；选中保留。 */
function recenter(key: string): void {
  if (!pages.value.some(page => page.page_key === key)) return
  if (key === centerKey.value) return
  centerKey.value = key
  expandedKeys.value = []
}

/** 展开/收起某一阶节点的二阶邻居（输入节点展上游、输出节点展下游）。 */
function toggleExpand(key: string): void {
  expandedKeys.value = isExpanded(key)
    ? expandedKeys.value.filter(item => item !== key)
    : [...expandedKeys.value, key]
}

/** 定位搜索：标题/键首个匹配页 → 设为中心并选中。 */
function locateSearch(): void {
  const query = searchQuery.value.trim().toLocaleLowerCase()
  if (!query) return
  const hit = pages.value.find(page => (
    page.title.toLocaleLowerCase().includes(query)
      || page.page_key.toLocaleLowerCase().includes(query)))
  if (!hit) {
    ElMessage.info('没有标题或页键匹配的页面')
    return
  }
  recenter(hit.page_key)
  selectedKey.value = hit.page_key
}

/** 来源中文化（原值落 title 提示；与 Editor/Reader 面板同口径）。 */
function originLabel(origin: string): string {
  if (origin === 'manual') return '手工'
  if (origin === 'machine') return '编译'
  return origin || '—'
}

// ── 数据加载（守卫逻辑与旧版逐字对齐：混版防护/请求序号守卫） ──

/** 锚定页变化（父级阅读器/编辑器切页）→ 重定中心（无效键不动）。 */
watch(() => props.centerPageKey, key => {
  if (key && key !== centerKey.value && pages.value.some(page => page.page_key === key)) {
    centerKey.value = key
    expandedKeys.value = []
  }
})

/** 制品切换（路由复用组件不重挂）→ 整体重置。 */
watch(() => props.artifactId, () => {
  searchQuery.value = ''
  centerKey.value = ''
  selectedKey.value = ''
  expandedKeys.value = []
  void reload()
})

/**
 * 加载 pages/links 两信封 + 混版防护（66号 D03.1：两请求之间发生发布/
 * 回滚须可检出）：revision 不一致 → 整对重拉一次；仍不一致 → 错误态
 * （不用半新半旧数据拼图）。
 *
 * 请求序号守卫（审查 #2）：reload 并发（artifactId 快切/重试双击）时，
 * 迟到的旧响应在任一落地前被丢弃，不串台覆盖新制品数据——先例=
 * Reader 的 selectedKey 竞态守卫。
 */
let loadEpoch = 0

async function reload(): Promise<void> {
  const epoch = ++loadEpoch
  loading.value = true
  loadError.value = ''
  revisionMismatch.value = false
  try {
    let [pagesEnv, linksEnv] = await fetchPair()
    if (epoch !== loadEpoch) return
    if (pagesEnv.revision !== linksEnv.revision) {
      [pagesEnv, linksEnv] = await fetchPair()
      if (epoch !== loadEpoch) return
      if (pagesEnv.revision !== linksEnv.revision) {
        revisionMismatch.value = true
        return
      }
    }
    pages.value = pagesEnv.pages
    links.value = linksEnv.links
    revision.value = pagesEnv.revision
    if (centerKey.value && !pagesEnv.pages.some(page => page.page_key === centerKey.value)) {
      centerKey.value = ''
    }
    if (!centerKey.value) {
      // 默认中心：优先父级锚定页（阅读器/编辑器当前页），回落首页
      centerKey.value = pagesEnv.pages.some(page => page.page_key === props.centerPageKey)
        ? props.centerPageKey as string
        : pagesEnv.pages[0]?.page_key ?? ''
    }
    if (selectedKey.value && !pagesEnv.pages.some(page => page.page_key === selectedKey.value)) {
      selectedKey.value = ''
    }
  } catch (error) {
    const message = await apiErrorDetail(error)
    if (epoch !== loadEpoch) return
    pages.value = []
    links.value = []
    loadError.value = message
  } finally {
    if (epoch === loadEpoch) loading.value = false
  }
}

/** 一对请求（同一 draft 口径；links 不带 page_key=全量——图谱视图口径）。 */
async function fetchPair(): Promise<[WikiPagesEnvelope, WikiLinksEnvelope]> {
  return Promise.all([
    api.listWikiPages(props.artifactId, { draft: props.draft }),
    api.listWikiLinks(props.artifactId, { draft: props.draft }),
  ])
}

onMounted(reload)

defineExpose({ reload, locateSearch })
</script>

<style scoped>
.wiki-graph { display: flex; flex-direction: column; gap: 10px; min-height: 420px; }
.wiki-graph__state { display: flex; flex-direction: column; align-items: center; gap: 8px; }
.wiki-graph__toolbar { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }
.wiki-graph__search { width: min(260px, 60vw); }
.wiki-graph__stats { margin-left: auto; color: var(--kb-text-tertiary, #909399); font-size: 12px; }
.wiki-graph__body { display: flex; gap: 12px; min-height: 460px; }
.wiki-graph__canvas-shell {
  position: relative;
  flex: 1;
  min-width: 0;
  border: 1px solid var(--kb-border, #ebeef5);
  border-radius: 8px;
  background: var(--kb-bg-card, #fff);
  overflow: hidden;
}
.wiki-graph__canvas-shell :deep(.wiki-graph-canvas) { height: 460px; }
.wiki-graph__legend {
  display: flex; flex-wrap: wrap; gap: 14px; align-items: center;
  margin: 0; padding: 0 2px;
  color: var(--kb-text-tertiary, #909399); font-size: 11.5px;
}
.wiki-graph__legend-tail { margin-left: auto; }
.wiki-graph__detail {
  width: 250px;
  flex-shrink: 0;
  border: 1px solid var(--kb-border, #ebeef5);
  border-radius: 8px;
  padding: 14px;
  background: var(--kb-bg-card, #fff);
}
.wiki-graph__detail-head h4 {
  margin: 4px 0 10px;
  font-size: 15px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.wiki-graph__detail-type { color: var(--kb-accent, #0891b2); font-size: 11px; font-weight: 700; }
.wiki-graph__detail-meta { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin: 0; }
.wiki-graph__detail-meta > div { border: 1px solid var(--kb-border-light, #f2f6fc); border-radius: 4px; padding: 7px 8px; min-width: 0; }
.wiki-graph__detail-meta dt { color: var(--kb-text-tertiary, #909399); font-size: 10.5px; }
.wiki-graph__detail-meta dd {
  margin: 2px 0 0;
  font-size: 13px;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.wiki-graph__detail-note {
  margin: 10px 0 0; padding: 8px 10px; border-radius: 6px;
  background: var(--kb-warning-soft, #fdf6ec);
  color: var(--kb-text-secondary, #64748b);
  font-size: 12px;
}
.wiki-graph__detail-actions { display: flex; gap: 8px; margin-top: 12px; flex-wrap: wrap; }
.wiki-graph__detail-actions .el-button { margin: 0; }
.wiki-graph__open { flex: 1; }
.wiki-graph__detail-links { margin-top: 14px; padding-top: 10px; border-top: 1px solid var(--kb-border-light, #f1f5f9); }
.wiki-graph__detail-links h5 {
  margin: 0 0 6px; font-size: 11px; font-weight: 600;
  color: var(--kb-text-tertiary, #909399);
}
.wiki-graph__detail-link {
  display: block; width: 100%; text-align: left;
  padding: 4px 2px; border: 0; background: none; cursor: pointer;
  font-size: 12px; color: var(--kb-accent, #0891b2);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.wiki-graph__detail-link:hover { text-decoration: underline; }
.wiki-graph__detail-link span { color: var(--kb-warning, #f59e0b); }
.wiki-graph__detail-hint { margin: 20px 0; color: var(--kb-text-tertiary, #909399); font-size: 12.5px; text-align: center; }
@media (max-width: 1100px) {
  .wiki-graph__body { flex-direction: column; }
  .wiki-graph__detail { width: auto; }
}
</style>
