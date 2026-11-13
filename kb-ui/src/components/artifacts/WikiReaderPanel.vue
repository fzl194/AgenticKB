<template>
  <div class="wiki-reader" data-test="wiki-reader">
    <!-- 加载中/清单失败/未发布（current_revision=0 → 清单为空即判）——单 EmptyState，不白屏 -->
    <EmptyState
      v-if="loading || pages.length === 0"
      :text="emptyText"
      data-test="wiki-reader-empty"
    />

    <!-- 统一三栏（2026-11-13 拍板）：左目录（可拖拽调整）｜中页面渲染（默认阅读，
         编辑正文/页面属性按钮化）｜右近邻力导向图+溯源。设计态=canWrite 开写入口，
         运行态=同款只读。 -->
    <template v-else>
      <aside class="wiki-reader__toc">
        <!-- 检索区（批次4）：目录侧栏顶部——回车/按钮触发，结果渲染在侧栏 -->
        <div class="wiki-reader__search">
          <el-input
            v-model="searchQuery"
            size="small"
            clearable
            placeholder="搜索 Wiki 内容"
            data-test="wiki-reader-search-input"
            @keyup.enter="runSearch"
          >
            <template #append>
              <el-button
                :loading="searchLoading"
                data-test="wiki-reader-search-btn"
                @click="runSearch"
              >搜索</el-button>
            </template>
          </el-input>
          <div
            v-if="searchResults.length"
            class="wiki-reader__search-list"
            data-test="wiki-reader-search-list"
          >
            <button
              v-for="(hit, index) in searchResults"
              :key="`${hit.unit_key}-${index}`"
              type="button"
              class="wiki-reader__search-hit"
              data-test="wiki-reader-search-hit"
              @click="goToKey(hit.page_key)"
            >
              <span class="wiki-reader__search-hit-title">{{ hit.title || hit.page_key }}</span>
              <span class="wiki-reader__search-hit-snippet">{{ hit.snippet }}</span>
            </button>
          </div>
          <p
            v-else-if="searchDone"
            class="wiki-reader__search-none"
            data-test="wiki-reader-search-none"
          >无匹配结果</p>
        </div>

        <div class="wiki-reader__toc-head">
          <span>目录 · 共 {{ sortedPages.length }} 页</span>
          <button
            v-if="canWrite"
            type="button"
            class="wiki-reader__toc-new"
            data-test="wiki-new-page"
            @click="startNewPage"
          >＋ 新建页</button>
          <button
            v-if="canWrite && tocDirty"
            type="button"
            class="wiki-reader__toc-save"
            data-test="wiki-toc-save"
            :disabled="tocSaving"
            @click="saveToc"
          >保存目录</button>
        </div>

        <div class="wiki-reader__toc-body">
          <!-- 分节目录（编译自动分节；设计态可拖拽调整=换节/排序，保存后生效于发布） -->
          <section
            v-for="(group, gi) in displayGroups"
            :key="gi"
            class="wiki-reader__toc-group"
            :data-test="group.title !== null ? 'wiki-reader-toc-section' : undefined"
            @drop="editable && onDropSection(groupIndex(gi))"
          >
            <div v-if="groupTitle(group)" class="wiki-reader__toc-group-title">
              <template v-if="editable && group.title !== null">
                <el-input
                  :model-value="group.title"
                  size="small"
                  class="wiki-reader__toc-group-edit"
                  @update:model-value="(value: string) => renameSection(sectionIndex(gi), value)"
                />
                <span
                  class="wiki-reader__toc-group-del"
                  role="button"
                  aria-label="删除本节"
                  title="删除本节（节内页回落未分节）"
                  @click="removeSection(sectionIndex(gi))"
                >✕</span>
              </template>
              <template v-else>{{ groupTitle(group) }}（{{ group.pages.length }}）</template>
            </div>
            <button
              v-for="(item, ii) in group.pages"
              :key="item.page_key"
              type="button"
              class="wiki-reader__toc-item"
              :class="{ 'is-active': item.page_key === selectedKey, 'is-dragover': dragOverKey === item.page_key }"
              data-test="wiki-reader-toc-item"
              :draggable="editable"
              @click="selectPage(item.page_key)"
              @dragstart="editable && onDragStart(item.page_key)"
              @dragover.prevent="editable && (dragOverKey = item.page_key)"
              @dragleave="editable && (dragOverKey = '')"
              @drop.stop.prevent="editable && onDropItem(groupIndex(gi), ii)"
            >
              <el-tag size="small" effect="plain" :type="pageTypeMeta(item.page_type)?.tag ?? 'info'">
                {{ pageTypeMeta(item.page_type)?.label ?? item.page_type }}
              </el-tag>
              <span class="wiki-reader__toc-title">{{ item.title }}</span>
              <span
                v-if="canWrite"
                class="wiki-reader__toc-del"
                role="button"
                aria-label="删除页面"
                title="删除页面"
                data-test="wiki-page-delete"
                @click.stop="removePage(item)"
              >✕</span>
            </button>
            <p v-if="editable && group.pages.length === 0" class="wiki-reader__toc-empty-hint">
              拖入页面到本节
            </p>
          </section>

          <button
            v-if="editable"
            type="button"
            class="wiki-reader__toc-add-section"
            data-test="wiki-toc-add-section"
            @click="addSection"
          >＋ 新增节</button>
        </div>
      </aside>

      <section v-loading="pageLoading" class="wiki-reader__main">
        <template v-if="page">
          <header class="wiki-reader__page-head">
            <h3 class="wiki-reader__page-title">{{ page.title }}</h3>
            <el-tag size="small" effect="plain" :type="pageTypeMeta(page.page_type)?.tag ?? 'info'">
              {{ pageTypeMeta(page.page_type)?.label ?? page.page_type }}
            </el-tag>
            <el-tag
              size="small"
              effect="plain"
              :type="page.origin === 'manual' ? 'primary' : 'info'"
              :title="`来源：${page.origin}`"
            >
              {{ originLabel(page.origin) }}
            </el-tag>
            <span class="wiki-reader__page-time">更新于 {{ formatTime(page.updated_at) }}</span>
            <span v-if="canWrite" class="wiki-reader__page-actions">
              <el-button
                v-if="!editing"
                size="small"
                data-test="wiki-edit-body"
                @click="startEditBody"
              >编辑正文</el-button>
              <el-button
                v-if="!editing"
                size="small"
                data-test="wiki-page-props"
                @click="openProps"
              >页面属性</el-button>
            </span>
          </header>

          <!-- 编辑模式：markdown 源文 textarea；保存后整页标记人工修订 -->
          <template v-if="editing">
            <div class="wiki-reader__edit-bar">
              <span class="wiki-reader__edit-addr">page_key：{{ selectedKey }} · 保存后记录为人工修订页</span>
              <el-button size="small" :disabled="saving" data-test="wiki-cancel-edit" @click="editing = false">取消</el-button>
              <el-button
                type="primary"
                size="small"
                :loading="saving"
                data-test="wiki-save-body"
                @click="saveBody"
              >保存正文</el-button>
            </div>
            <el-input
              v-model="editBody"
              type="textarea"
              :rows="18"
              class="wiki-reader__editor"
              data-test="wiki-body-editor"
            />
          </template>
          <!-- 阅读模式（默认）：点击委托切页/复制切片 ID -->
          <div
            v-else
            class="wiki-reader__body markdown-body"
            data-test="wiki-reader-body"
            v-html="pageHtml"
            @click="onContentClick"
          />
        </template>
        <EmptyState v-else :text="canWrite ? '在左侧目录选择页面，或新建一页' : '在左侧目录选择页面'" />
      </section>

      <aside v-if="page" class="wiki-reader__ctx">
        <!-- 近邻：以当前页为中心的力导向图（一阶出入；点击节点切页） -->
        <section class="wiki-reader__ctx-block">
          <h4 class="wiki-reader__ctx-title">近邻（一阶出入 {{ neighborhood.totals.input1 + neighborhood.totals.output1 }}）</h4>
          <div class="wiki-reader__graph" data-test="wiki-reader-graph">
            <WikiGraphCanvas
              v-if="neighborhood.nodes.length > 1"
              :option="graphOption"
              @node-click="key => goToKey(key)"
            />
            <p v-else class="wiki-reader__none">本页暂无引用关联</p>
          </div>
        </section>

        <section class="wiki-reader__ctx-block">
          <h4 class="wiki-reader__ctx-title">溯源（{{ provenanceViews.length }}）</h4>
          <div
            v-for="(item, index) in provenanceViews"
            :key="`prov-${index}`"
            class="wiki-reader__prov"
            data-test="wiki-reader-provenance"
          >
            <p class="wiki-reader__prov-line">kb：{{ item.kbId }}</p>
            <p class="wiki-reader__prov-line">快照：{{ item.snapshotId }}</p>
            <p class="wiki-reader__prov-line">切片 {{ item.unitIds.length }} 条</p>
            <ul v-if="item.unitIds.length" class="wiki-reader__prov-units">
              <li v-for="unitId in item.unitIds" :key="unitId">
                <button
                  type="button"
                  class="wiki-reader__prov-unit"
                  data-test="wiki-reader-prov-unit"
                  :title="`查看切片 ${unitId} 原文`"
                  @click="openUnitSource(unitId)"
                >{{ unitId }}</button>
              </li>
            </ul>
            <p v-for="extra in item.extra" :key="extra" class="wiki-reader__prov-line">{{ extra }}</p>
          </div>
          <p v-if="provenanceViews.length === 0" class="wiki-reader__none">无溯源记录</p>
        </section>
      </aside>
    </template>

    <!-- 页面属性（结构化字段归口：标题/页型/序号/别名/标签/frontmatter JSON） -->
    <el-dialog v-model="propsVisible" :title="propsIsNew ? '新建页面' : '页面属性'" width="620px">
      <el-form label-width="130px">
        <el-form-item label="标题">
          <el-input v-model="propsForm.title" data-test="wiki-title" />
        </el-form-item>
        <el-form-item label="页面类型">
          <el-select v-model="propsForm.pageType" data-test="wiki-page-type">
            <el-option v-for="t in PAGE_TYPES" :key="t.value" :value="t.value" :label="t.label" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="propsIsNew" label="page_key（可选）">
          <el-input v-model="propsForm.pageKey" placeholder="缺省取标题原文，后端归一定稿" data-test="wiki-page-key" />
        </el-form-item>
        <el-form-item label="序号 ordinal">
          <el-input-number v-model="propsForm.ordinal" :min="0" data-test="wiki-ordinal" />
        </el-form-item>
        <el-form-item label="别名（逗号分隔）">
          <el-input v-model="propsForm.aliases" data-test="wiki-aliases" />
        </el-form-item>
        <el-form-item label="标签（逗号分隔）">
          <el-input v-model="propsForm.tags" data-test="wiki-tags" />
        </el-form-item>
        <el-alert
          v-if="requiredKeys.length > 0"
          type="warning"
          :closable="false"
          :title="`「${propsForm.pageType}」页必填 frontmatter 键：${requiredKeys.join('、')}——在下方 JSON 区填写`"
        />
        <el-form-item label="frontmatter JSON">
          <el-input
            v-model="frontmatterJson"
            type="textarea"
            :rows="6"
            class="wiki-reader__json"
            data-test="wiki-frontmatter-json"
          />
          <p v-if="!frontmatterValid" class="wiki-reader__json-error" data-test="wiki-json-error">
            frontmatter JSON 解析失败——修正后才能保存
          </p>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="propsVisible = false">取消</el-button>
        <el-button
          type="primary"
          :loading="saving"
          :disabled="!frontmatterValid"
          data-test="wiki-props-save"
          @click="saveProps"
        >{{ propsIsNew ? '创建页面' : '保存属性' }}</el-button>
      </template>
    </el-dialog>

    <!-- Q01 溯源回原文弹层：unit-source 原文截读（≤500 字） -->
    <el-dialog
      v-model="unitSourceVisible"
      title="来源切片原文"
      width="540px"
      data-test="wiki-unit-source-dialog"
    >
      <div v-loading="unitSourceLoading" class="wiki-reader__src-body">
        <template v-if="unitSource">
          <p class="wiki-reader__src-meta">
            切片 {{ unitSource.unit_id }} · 知识库 {{ unitSource.kb_id }} · 快照
            {{ unitSource.snapshot_id }}<template v-if="unitSource.section_ref">
              · 定位 {{ unitSource.section_ref }}</template>
          </p>
          <p class="wiki-reader__src-text">{{ unitSource.content_text }}</p>
          <p class="wiki-reader__src-hint">原文截读（前 500 字），完整内容请进入来源知识库查看。</p>
        </template>
        <p
          v-else-if="unitSourceError"
          class="wiki-reader__src-error"
          data-test="wiki-unit-source-error"
        >{{ unitSourceError }}</p>
        <p v-else class="wiki-reader__src-hint">加载中…</p>
      </div>
      <template #footer>
        <el-button
          v-if="unitSource?.kb_id"
          data-test="wiki-unit-source-kb-link"
          @click="goKb"
        >在知识库中查看</el-button>
        <el-button @click="unitSourceVisible = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { EChartsOption } from 'echarts'

import { useArtifactsApi } from '@/api/artifacts'
import { apiErrorDetail } from '@/api/proxyClient'
import EmptyState from '@/components/common/EmptyState.vue'
import WikiGraphCanvas from './WikiGraphCanvas.vue'
import { extractWikiNavTarget, extractWikiSrcUnit, renderWikiMarkdown } from '@/utils/markdown'
import { buildWikiNeighborhood } from '@/utils/wikiGraph'
import type {
  WikiLink,
  WikiPage,
  WikiPageMeta,
  WikiPageType,
  WikiSearchHit,
  WikiTocSnapshotResolved,
  WikiUnitSource,
} from '@/types/artifacts'

/** 四页型徽标表（63号 §4.3）。 */
const PAGE_TYPES = [
  { value: 'topic', label: '主题', tag: 'primary' },
  { value: 'glossary', label: '术语', tag: 'success' },
  { value: 'contradiction', label: '矛盾', tag: 'danger' },
  { value: 'open_question', label: '开放问题', tag: 'warning' },
] as const

/** 专页必填 frontmatter 键（63号 §4.3，与后端 _validate_frontmatter 同表）。 */
const REQUIRED_FM: Partial<Record<WikiPageType, string[]>> = {
  contradiction: ['claim_a', 'claim_b', 'severity（low|medium|high）'],
  open_question: ['question（以 ? 或 ？ 结尾）', 'priority'],
  glossary: ['term'],
}

/** 溯源条目视图模型（provenance 结构宽松——字段缺失/未知结构一律容错降级显示）。 */
interface ProvenanceView {
  kbId: string
  snapshotId: string
  unitIds: string[]
  extra: string[]
}

/** 可拖拽编辑节（设计态目录调整；口径=WikiTocDesignPanel 的 EditableSection）。 */
interface EditableSection {
  sectionTitle: string
  pageKeys: string[]
}

const props = withDefaults(defineProps<{
  artifactId: string
  /** 设计态（owner/editor）：读 draft + 开写入口（编辑正文/页面属性/新建/删除/目录拖拽）。 */
  canWrite?: boolean
  /** 乐观锁令牌（artifact.updated_at）——页面/目录写操作随请求携带。 */
  expectedUpdatedAt?: string
}>(), { canWrite: false })

const emit = defineEmits<{ changed: [] }>()

const api = useArtifactsApi()
const router = useRouter()

const loading = ref(false)
const pageLoading = ref(false)
const loadFailedText = ref('')
const pages = ref<WikiPageMeta[]>([])
const selectedKey = ref('')
const page = ref<WikiPage | null>(null)
/** 全量引用（近邻图数据源；draft 口径随 canWrite）。 */
const allLinks = ref<WikiLink[]>([])
/** 分节目录快照（运行态展示/设计态编辑起点）：null=平铺。 */
const tocSections = ref<WikiTocSnapshotResolved[] | null>(null)
/** 设计态目录编辑模型（拖拽调整的落地目标）。 */
const editableSections = ref<EditableSection[]>([])
/** 目录乐观锁令牌（GET draft 的 updated_at；PUT 携带）。 */
let tocUpdatedAt: string | undefined
const tocDirty = ref(false)
const tocSaving = ref(false)

// ── 检索（63号批次4）──
const searchQuery = ref('')
const searchResults = ref<WikiSearchHit[]>([])
const searchLoading = ref(false)
const searchDone = ref(false)
let searchEpoch = 0

// ── 正文编辑（设计态）──
const editing = ref(false)
const editBody = ref('')
const saving = ref(false)

// ── 页面属性弹窗（设计态）──
const propsVisible = ref(false)
const propsIsNew = ref(false)
const propsForm = ref({ title: '', pageType: 'topic' as WikiPageType, pageKey: '', aliases: '', tags: '', ordinal: 1 })
const frontmatterJson = ref('{}')

// ── 目录拖拽 ──
let draggingKey = ''
const dragOverKey = ref('')

const editable = computed(() => props.canWrite)

const sortedPages = computed(() =>
  [...pages.value].sort((a, b) => a.ordinal - b.ordinal || a.page_key.localeCompare(b.page_key)))

/** 运行态分组视图：有节→节序+节内键序，未入节归「其他」；平铺→单组无标题。 */
interface TocGroup { title: string | null; pages: WikiPageMeta[] }
const tocGroups = computed<TocGroup[]>(() => {
  const secs = tocSections.value
  if (!secs || secs.length === 0) return [{ title: null, pages: sortedPages.value }]
  const byKey = new Map(sortedPages.value.map(p => [p.page_key, p]))
  const seen = new Set<string>()
  const groups: TocGroup[] = []
  for (const sec of secs) {
    const groupPages: WikiPageMeta[] = []
    for (const key of sec.page_keys) {
      const meta = byKey.get(key)
      if (meta && !seen.has(key)) {
        seen.add(key)
        groupPages.push(meta)
      }
    }
    groups.push({ title: sec.section_title, pages: groupPages })
  }
  const rest = sortedPages.value.filter(p => !seen.has(p.page_key))
  if (rest.length > 0) groups.push({ title: '其他', pages: rest })
  return groups
})

/** 设计态分组视图：editableSections + 未分节尾组（title=null 渲染为「未分节」）。 */
const editableGroups = computed<TocGroup[]>(() => {
  const byKey = new Map(sortedPages.value.map(p => [p.page_key, p]))
  const groups: TocGroup[] = editableSections.value.map(sec => ({
    title: sec.sectionTitle,
    pages: sec.pageKeys.map(k => byKey.get(k)).filter((p): p is WikiPageMeta => !!p),
  }))
  const assigned = new Set(editableSections.value.flatMap(s => s.pageKeys))
  const rest = sortedPages.value.filter(p => !assigned.has(p.page_key))
  groups.push({ title: null, pages: rest })
  return groups
})

// 模板展示统一出口：设计态走 editableGroups，运行态走 tocGroups
const displayGroups = computed(() => (editable.value ? editableGroups.value : tocGroups.value))

/** 展示组索引 → 编辑节索引（未分节尾组=-1：移出所有节）。 */
function sectionIndex(groupIdx: number): number {
  return groupIdx < editableSections.value.length ? groupIdx : -1
}

/** 展示组索引 → 拖拽落地组标识（统一用 sectionIndex；未分节组也可作为落点）。 */
function groupIndex(groupIdx: number): number {
  return sectionIndex(groupIdx)
}

/** 正文 HTML（renderWikiMarkdown：站内双链/溯源角标标记 → marked → DOMPurify）。 */
const pageHtml = computed(() => (page.value ? renderWikiMarkdown(page.value.body_md ?? '') : ''))

const provenanceViews = computed<ProvenanceView[]>(() => toProvenanceViews(page.value?.provenance))

/** 近邻模型：当前页为中心的一阶出入（二阶计数折在节点标签上）。 */
const neighborhood = computed(() =>
  buildWikiNeighborhood(pages.value, allLinks.value, selectedKey.value || undefined))

const emptyText = computed(() => {
  if (loading.value) return 'Wiki 页面加载中…'
  if (loadFailedText.value) return `页面清单加载失败：${loadFailedText.value}`
  if (editable.value) return '暂无页面——点左上「新建页」手工创建，或从上方编译生成'
  return '尚未发布版本——在设计态编辑并发布后，这里会显示 Wiki 页面'
})

/** JSON 区解析：空串视为 {}；非对象/数组/解析失败 → null（禁保存）。 */
const parsedFrontmatter = computed<Record<string, unknown> | null>(() => {
  if (!frontmatterJson.value.trim()) return {}
  try {
    const parsed: unknown = JSON.parse(frontmatterJson.value)
    if (parsed !== null && typeof parsed === 'object' && !Array.isArray(parsed)) {
      return parsed as Record<string, unknown>
    }
    return null
  } catch {
    return null
  }
})

const frontmatterValid = computed(() => parsedFrontmatter.value !== null)
const requiredKeys = computed(() => REQUIRED_FM[propsForm.value.pageType] ?? [])

// ── 近邻图 option（紧凑变体：标题标签卡+类型描边；色值与图谱面板同源） ──

const TYPE_COLORS: Record<string, string> = {
  topic: '#0891b2',
  glossary: '#10b981',
  contradiction: '#ef4444',
  open_question: '#f59e0b',
}
const BROKEN_COLOR = '#b45309'
const ACCENT = '#0891b2'
const TEXT_PRIMARY = '#0f172a'
const TEXT_TERTIARY = '#94a3b8'

const graphOption = computed<EChartsOption>(() => ({
  tooltip: {
    trigger: 'item',
    backgroundColor: '#ffffff',
    borderColor: '#e2e8f0',
    borderWidth: 1,
    padding: [6, 9],
    textStyle: { color: TEXT_PRIMARY, fontSize: 11.5 },
    formatter: (params: unknown) => {
      const p = params as { dataType?: string; data?: { pageTitle?: string; typeLabel?: string; outCount?: number; inCount?: number; isBroken?: boolean } }
      if (p.dataType !== 'node' || !p.data?.pageTitle) return ''
      return p.data.isBroken
        ? `${p.data.pageTitle}<br/>断链目标`
        : `<b>${p.data.pageTitle}</b><br/>${p.data.typeLabel} · 出链 ${p.data.outCount} · 被引用 ${p.data.inCount}`
    },
  },
  series: [{
    type: 'graph',
    layout: 'force',
    roam: true,
    draggable: true,
    force: { repulsion: 230, gravity: 0.14, edgeLength: [46, 100], friction: 0.06 },
    label: {
      position: 'right',
      fontSize: 11,
      color: TEXT_PRIMARY,
      backgroundColor: '#ffffff',
      borderWidth: 1,
      borderRadius: 5,
      padding: [3, 6],
      rich: {
        title: { fontSize: 11, color: TEXT_PRIMARY, lineHeight: 15, align: 'left' },
        badge: {
          fontSize: 9.5, color: ACCENT, backgroundColor: 'rgba(8, 145, 178, 0.10)',
          padding: [0, 3], borderRadius: 3, lineHeight: 15, align: 'left',
        },
      },
    },
    emphasis: {
      focus: 'adjacency',
      itemStyle: { shadowBlur: 8, shadowColor: 'rgba(15, 23, 42, 0.16)' },
      label: { borderColor: ACCENT },
    },
    edgeSymbol: ['none', 'arrow'],
    edgeSymbolSize: 6,
    data: neighborhood.value.nodes.map(node => {
      const color = node.isBroken ? BROKEN_COLOR : TYPE_COLORS[node.pageType] ?? ACCENT
      const isCenter = node.role === 'center'
      const badge = node.folded && node.folded.count > 0 ? ` {badge|+${node.folded.count}}` : ''
      return {
        id: node.id,
        key: node.id,
        name: node.label,
        x: node.position.x / 1.6,   // 紧凑列位（窄栏缩距）
        y: node.position.y / 1.6,
        fixed: isCenter,
        symbolSize: isCenter ? 13 : 8,
        itemStyle: {
          color: isCenter ? ACCENT : '#ffffff',
          borderColor: color,
          borderWidth: isCenter ? 4 : 2,
          borderOpacity: isCenter ? 0.3 : 1,
        },
        label: {
          position: isCenter ? 'bottom' : node.role.startsWith('input') ? 'left' : 'right',
          borderColor: color,
          borderType: node.isBroken ? 'dashed' : 'solid',
          formatter: `{title|${node.label}}${badge}`,
        },
        pageTitle: node.label,
        typeLabel: node.typeLabel,
        outCount: node.outCount,
        inCount: node.inCount,
        isBroken: node.isBroken,
      }
    }),
    links: neighborhood.value.edges.map(edge => ({
      source: edge.source,
      target: edge.target,
      lineStyle: {
        color: edge.isBroken ? '#d97706' : TEXT_TERTIARY,
        width: 1.2,
        opacity: 0.7,
        curveness: 0.18,
        type: edge.isBroken ? 'dashed' : 'solid',
      },
    })),
  }],
}))

onMounted(loadPages)

/** 页面清单 + 全量引用 + 目录 + 首选页（draft 口径随 canWrite）。 */
async function loadPages(): Promise<void> {
  loading.value = true
  loadFailedText.value = ''
  try {
    const draft = editable.value
    const [envelope, linksEnv] = await Promise.all([
      api.listWikiPages(props.artifactId, { draft }),
      api.listWikiLinks(props.artifactId, { draft }),
    ])
    pages.value = envelope.pages
    allLinks.value = linksEnv.links
    await loadToc()
    const keepKey = selectedKey.value &&
      pages.value.some(p => p.page_key === selectedKey.value)
    if (keepKey) {
      await selectPage(selectedKey.value)
    } else if (tocGroups.value[0]?.pages[0]) {
      await selectPage(tocGroups.value[0].pages[0].page_key)
    } else {
      selectedKey.value = ''
      page.value = null
    }
  } catch (error) {
    pages.value = []
    page.value = null
    loadFailedText.value = await apiErrorDetail(error)
  } finally {
    loading.value = false
  }
}

/** 目录加载：运行态（draft=false）展示用；设计态（draft=true）另存编辑模型。 */
async function loadToc(): Promise<void> {
  try {
    const snap = await api.getWikiToc(props.artifactId, { draft: editable.value })
    const sections = snap.flat === true || (snap.sections ?? []).length === 0 ? null : snap.sections
    tocSections.value = sections
    if (editable.value) {
      tocUpdatedAt = snap.updated_at
      editableSections.value = (sections ?? []).map(s => ({
        sectionTitle: s.section_title,
        pageKeys: [...s.page_keys],
      }))
      resetTocBaseline()
    }
  } catch {
    tocSections.value = null
    if (editable.value) {
      editableSections.value = []
      resetTocBaseline()
    }
  }
}

// ── 目录拖拽调整（设计态）──

/** 归一序列化（与 PUT 载荷同形态）——dirty 对比基线。 */
function serializeToc(): string {
  return JSON.stringify(editableSections.value.map(s => ({
    section_title: s.sectionTitle.trim(), page_keys: [...s.pageKeys],
  })))
}

let tocBaseline = ''
function resetTocBaseline(): void {
  tocBaseline = serializeToc()
  tocDirty.value = false
}

function markTocDirty(): void {
  tocDirty.value = serializeToc() !== tocBaseline
}

function onDragStart(key: string): void {
  draggingKey = key
}

/** 拖到某节内某页上 → 插到该页前；拖到节容器空白 → 追加到该节尾。 */
function onDropItem(sectionIndex: number, itemIndex: number): void {
  dragOverKey.value = ''
  moveDraggedTo(sectionIndex, itemIndex)
}

function onDropSection(sectionIndex: number): void {
  dragOverKey.value = ''
  moveDraggedTo(sectionIndex, editableSections.value[sectionIndex]?.pageKeys.length ?? 0)
}

/** 移动拖拽页到目标节目标位（未分节尾组=移出所有节）。 */
function moveDraggedTo(sectionIndex: number, insertAt: number): void {
  if (!draggingKey) return
  editableSections.value = editableSections.value.map(s =>
    ({ ...s, pageKeys: s.pageKeys.filter(k => k !== draggingKey) }))
  if (sectionIndex >= 0 && sectionIndex < editableSections.value.length) {
    const target = editableSections.value[sectionIndex]
    const at = Math.min(Math.max(insertAt, 0), target.pageKeys.length)
    target.pageKeys = [...target.pageKeys.slice(0, at), draggingKey, ...target.pageKeys.slice(at)]
  }
  draggingKey = ''
  markTocDirty()
}

function addSection(): void {
  editableSections.value = [...editableSections.value, {
    sectionTitle: `第 ${editableSections.value.length + 1} 节`,
    pageKeys: [],
  }]
  markTocDirty()
}

function removeSection(index: number): void {
  editableSections.value = editableSections.value.filter((_, i) => i !== index)
  markTocDirty()
}

function renameSection(index: number, title: string): void {
  editableSections.value = editableSections.value.map((s, i) =>
    i === index ? { ...s, sectionTitle: title } : s)
  markTocDirty()
}

/** 目录保存：全量 PUT + 乐观锁（口径=原 WikiTocDesignPanel）。 */
async function saveToc(): Promise<void> {
  const payload = editableSections.value.map(s => ({
    section_title: s.sectionTitle.trim(),
    page_keys: [...s.pageKeys],
  }))
  if (payload.some(s => !s.section_title)) {
    ElMessage.warning('节名不能为空——请补全或删除空名节')
    return
  }
  tocSaving.value = true
  try {
    const resp = await api.saveWikiToc(props.artifactId, {
      sections: payload,
      ...(tocUpdatedAt ? { expected_updated_at: tocUpdatedAt } : {}),
    })
    tocUpdatedAt = resp.updated_at
    resetTocBaseline()
    ElMessage.success('目录已保存（发布后生效于运行态）')
  } catch (error) {
    if (isStale(error)) {
      ElMessage.error('目录已被他人修改——请放弃本次调整并重试')
      await loadToc()
    } else {
      ElMessage.error(`保存目录失败：${await apiErrorDetail(error)}`)
    }
  } finally {
    tocSaving.value = false
  }
}

// ── 页面选择/加载 ──

/** 选中并加载一页；过期响应（选中已被覆盖）丢弃。 */
async function selectPage(pageKey: string): Promise<void> {
  selectedKey.value = pageKey
  editing.value = false
  pageLoading.value = true
  try {
    const loaded = await api.getWikiPage(props.artifactId, pageKey, { draft: editable.value })
    if (selectedKey.value !== pageKey) return
    page.value = loaded
  } catch (error) {
    if (selectedKey.value === pageKey) {
      ElMessage.error(`加载页面失败：${await apiErrorDetail(error)}`)
    }
  } finally {
    pageLoading.value = false
  }
}

// ── 正文编辑（设计态）──

function startEditBody(): void {
  editBody.value = page.value?.body_md ?? ''
  editing.value = true
}

/** 保存正文：整页 upsert（属性沿用当前值）——保存后后端记人工修订。 */
async function saveBody(): Promise<void> {
  if (!page.value) return
  saving.value = true
  try {
    await api.upsertWikiPage(props.artifactId, selectedKey.value, {
      title: page.value.title,
      page_type: page.value.page_type,
      frontmatter: page.value.frontmatter ?? {},
      body_md: editBody.value,
      ordinal: page.value.ordinal,
      expected_updated_at: props.expectedUpdatedAt,
    })
    ElMessage.success('正文已保存（人工修订页）')
    editing.value = false
    emit('changed')
    await loadPages()
  } catch (error) {
    staleOrError(error, '保存正文失败')
  } finally {
    saving.value = false
  }
}

// ── 页面属性/新建/删除（设计态；口径=原 WikiEditorPanel） ──

function openProps(): void {
  if (!page.value) return
  propsIsNew.value = false
  const fm = page.value.frontmatter ?? {}
  propsForm.value = {
    title: page.value.title,
    pageType: page.value.page_type,
    pageKey: page.value.page_key,
    aliases: toCsv(fm.aliases),
    tags: toCsv(fm.tags),
    ordinal: page.value.ordinal,
  }
  frontmatterJson.value = JSON.stringify(fm, null, 2)
  propsVisible.value = true
}

function startNewPage(): void {
  propsIsNew.value = true
  propsForm.value = { title: '', pageType: 'topic', pageKey: '', aliases: '', tags: '', ordinal: sortedPages.value.length + 1 }
  frontmatterJson.value = '{}'
  propsVisible.value = true
}

async function removePage(meta: WikiPageMeta): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `确定删除页面「${meta.title}」？（草稿态删除，发布前可丢弃草稿找回）`,
      '删除页面', { type: 'warning' })
  } catch {
    return
  }
  try {
    await api.deleteWikiPage(props.artifactId, meta.page_key, props.expectedUpdatedAt)
    ElMessage.success('已删除')
    if (selectedKey.value === meta.page_key) selectedKey.value = ''
    emit('changed')
    await loadPages()
  } catch (error) {
    staleOrError(error, '删除失败')
  }
}

/** 提交 frontmatter：JSON 区解析结果打底，aliases/tags 专属输入框覆盖同名键。 */
function buildFrontmatter(): Record<string, unknown> | null {
  const base = parsedFrontmatter.value
  if (base === null) return null
  return { ...base, aliases: splitCsv(propsForm.value.aliases), tags: splitCsv(propsForm.value.tags) }
}

async function saveProps(): Promise<void> {
  const title = propsForm.value.title.trim()
  if (!title) {
    ElMessage.warning('标题必填')
    return
  }
  const frontmatter = buildFrontmatter()
  if (frontmatter === null) {
    ElMessage.warning('frontmatter JSON 解析失败，请修正后再保存')
    return
  }
  const bodyMd = propsIsNew.value ? `# ${title}\n\n请填写知识正文。` : page.value?.body_md ?? ''
  const addressKey = propsIsNew.value ? (propsForm.value.pageKey.trim() || title) : selectedKey.value
  const explicitKey = propsForm.value.pageKey.trim()
  saving.value = true
  try {
    const saved = await api.upsertWikiPage(props.artifactId, addressKey, {
      title,
      page_type: propsForm.value.pageType,
      frontmatter,
      body_md: bodyMd,
      ordinal: propsForm.value.ordinal,
      ...(explicitKey ? { page_key: explicitKey } : {}),
      expected_updated_at: props.expectedUpdatedAt,
    })
    ElMessage.success(`已保存「${saved.title}」`)
    propsVisible.value = false
    emit('changed')
    await loadPages()
    await selectPage(saved.page_key)
  } catch (error) {
    staleOrError(error, '保存失败', true)
  } finally {
    saving.value = false
  }
}

/** 409 stale → 提示刷新（emit changed 推进乐观锁）；frontmatter 422 转人读。 */
async function staleOrError(error: unknown, where: string, frontmatterAware = false): Promise<void> {
  if (isStale(error)) {
    ElMessage.error('页面已被他人修改，已刷新最新状态')
    emit('changed')
    await loadPages()
    return
  }
  let message = await apiErrorDetail(error)
  if (frontmatterAware && message.startsWith('invalid_frontmatter:')) {
    message = `frontmatter 校验失败：${message.slice('invalid_frontmatter:'.length) || '结构非法'}`
  }
  ElMessage.error(`${where}：${message}`)
}

function isStale(error: unknown): boolean {
  const resp = (error as { response?: { status?: number; data?: { detail?: { code?: string } } } })
    ?.response
  return resp?.status === 409 && resp.data?.detail?.code === 'stale_artifact'
}

// ── 检索 ──

async function runSearch(): Promise<void> {
  const query = searchQuery.value.trim()
  if (!query) {
    resetSearch()
    return
  }
  const epoch = ++searchEpoch
  searchLoading.value = true
  try {
    const res = await api.searchWiki(props.artifactId, { q: query })
    if (epoch !== searchEpoch) return
    searchResults.value = res.results ?? []
    searchDone.value = true
    if (res.degraded_dense) {
      ElMessage.warning('向量检索暂不可用，结果基于关键词匹配')
    }
  } catch (error) {
    if (epoch !== searchEpoch) return
    searchResults.value = []
    searchDone.value = false
    ElMessage.error(`检索失败：${await apiErrorDetail(error)}`)
  } finally {
    if (epoch === searchEpoch) searchLoading.value = false
  }
}

function resetSearch(): void {
  searchEpoch += 1
  searchLoading.value = false
  searchResults.value = []
  searchDone.value = false
}

watch(searchQuery, (value) => {
  if (!value) resetSearch()
})

/** 制品切换 → 检索/目录整体重置。 */
watch(() => props.artifactId, () => {
  searchQuery.value = ''
  resetSearch()
  tocSections.value = null
  editableSections.value = []
})

/** 正文点击委托：双链 → 切页；溯源角标 → 复制切片 ID。 */
function onContentClick(event: MouseEvent): void {
  const navKey = extractWikiNavTarget(event.target)
  if (navKey !== null) {
    event.preventDefault()
    goToKey(navKey)
    return
  }
  const unitId = extractWikiSrcUnit(event.target)
  if (unitId !== null) {
    event.preventDefault()
    void copyUnitId(unitId)
  }
}

/** 双链目标解析：page_key 直配 → aliases 命中；未命中提示。 */
function goToKey(key: string): void {
  const target = pages.value.find(p => p.page_key === key)
    ?? pages.value.find(p => p.aliases.includes(key))
  if (!target) {
    if (neighborhood.value.nodes.some(n => n.id === key && n.isBroken)) {
      ElMessage.warning('该引用目标不存在（断链）——请在设计态检查修正')
    } else {
      ElMessage.warning('目标页面不存在')
    }
    return
  }
  if (target.page_key !== selectedKey.value) void selectPage(target.page_key)
}

/** 溯源角标：复制切片 ID。 */
async function copyUnitId(unitId: string): Promise<void> {
  const clipboard = (navigator as Navigator & {
    clipboard?: { writeText(text: string): Promise<void> }
  }).clipboard
  if (!clipboard?.writeText) {
    ElMessage.info(`切片 ID：${unitId}`)
    return
  }
  try {
    await clipboard.writeText(unitId)
    ElMessage.success('已复制切片 ID')
  } catch {
    ElMessage.info(`切片 ID：${unitId}`)
  }
}

// ── Q01 溯源回原文 ──

const unitSourceVisible = ref(false)
const unitSourceLoading = ref(false)
const unitSource = ref<WikiUnitSource | null>(null)
const unitSourceError = ref('')
let unitSourceEpoch = 0

async function openUnitSource(unitId: string): Promise<void> {
  const epoch = ++unitSourceEpoch
  unitSourceVisible.value = true
  unitSourceLoading.value = true
  unitSource.value = null
  unitSourceError.value = ''
  try {
    const loaded = await api.getWikiUnitSource(props.artifactId, unitId)
    if (epoch !== unitSourceEpoch) return
    unitSource.value = loaded
  } catch (error) {
    if (epoch !== unitSourceEpoch) return
    unitSourceError.value = await apiErrorDetail(error)
  } finally {
    if (epoch === unitSourceEpoch) unitSourceLoading.value = false
  }
}

function goKb(): void {
  const kbId = unitSource.value?.kb_id
  if (!kbId) return
  void router.push(`/kb/${kbId}`)
}

// ── 视图辅助 ──

/** 分组标题（未分节尾组：设计态显式标注，运行态平铺=无标题）。 */
function groupTitle(group: TocGroup): string {
  if (group.title === null) return editable.value ? '未分节' : ''
  return group.title
}

/** provenance 宽松归一。 */
function toProvenanceViews(provenance: unknown): ProvenanceView[] {
  if (!Array.isArray(provenance)) return []
  return provenance.map(item => {
    if (item !== null && typeof item === 'object' && !Array.isArray(item)) {
      const record = item as Record<string, unknown>
      const unitIds = Array.isArray(record.unit_ids) ? record.unit_ids.map(String) : []
      const known = new Set(['kb_id', 'snapshot_id', 'unit_ids'])
      const extra = Object.entries(record)
        .filter(([key, value]) => !known.has(key) && (value === null || typeof value !== 'object'))
        .map(([key, value]) => `${key}=${String(value)}`)
      return {
        kbId: record.kb_id === undefined || record.kb_id === null ? '—' : String(record.kb_id),
        snapshotId: record.snapshot_id === undefined || record.snapshot_id === null
          ? '—' : String(record.snapshot_id),
        unitIds,
        extra,
      }
    }
    return { kbId: '—', snapshotId: '—', unitIds: [], extra: [String(item)] }
  })
}

function pageTypeMeta(pageType: WikiPageType | string) {
  return PAGE_TYPES.find(t => t.value === pageType)
}

function originLabel(origin: string): string {
  if (origin === 'manual') return '手工'
  if (origin === 'machine') return '编译'
  return origin
}

function formatTime(value: string | null | undefined): string {
  return value ? value.replace('T', ' ').slice(0, 19) : '—'
}

function toCsv(value: unknown): string {
  return Array.isArray(value) ? value.map(String).join(', ') : ''
}

function splitCsv(value: string): string[] {
  return value.split(/[，,]/).map(v => v.trim()).filter(Boolean)
}

defineExpose({ reload: loadPages, selectPage, currentPageKey: computed(() => selectedKey.value) })
</script>

<style scoped>
.wiki-reader { display: flex; gap: 14px; min-height: 420px; }
.wiki-reader__toc { width: 250px; flex-shrink: 0; }

.wiki-reader__search { margin-bottom: 10px; }
.wiki-reader__search-list {
  display: flex; flex-direction: column; gap: 4px; margin-top: 8px;
  max-height: 280px; overflow-y: auto;
}
.wiki-reader__search-hit {
  display: flex; flex-direction: column; gap: 2px; width: 100%;
  padding: 5px 8px; border: 1px solid transparent; border-radius: 6px;
  background: none; cursor: pointer; text-align: left;
}
.wiki-reader__search-hit:hover { background: var(--el-fill-color-light, #f5f7fa); }
.wiki-reader__search-hit-title {
  font-size: 12.5px; color: var(--kb-text-primary, inherit);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.wiki-reader__search-hit-snippet {
  font-size: 12px; color: var(--kb-text-tertiary);
  display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2;
  overflow: hidden;
}
.wiki-reader__search-none { margin: 6px 0 0; font-size: 12px; color: var(--kb-text-tertiary); }

.wiki-reader__toc-head {
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  font-size: 12.5px; color: var(--kb-text-tertiary); margin-bottom: 8px;
}
.wiki-reader__toc-new {
  border: 0; background: none; padding: 0; cursor: pointer;
  color: var(--kb-accent, #0891b2); font-size: 12px; font-weight: 600;
}
.wiki-reader__toc-new:hover { text-decoration: underline; }
.wiki-reader__toc-save {
  border: 1px solid var(--kb-accent-medium, rgba(8, 145, 178, 0.15)); background: none;
  border-radius: 4px; padding: 1px 8px; cursor: pointer;
  color: var(--kb-accent, #0891b2); font-size: 12px; font-weight: 600;
}
.wiki-reader__toc-body { display: flex; flex-direction: column; gap: 4px; }

.wiki-reader__toc-group { display: flex; flex-direction: column; gap: 4px; min-height: 24px; }
.wiki-reader__toc-group + .wiki-reader__toc-group { margin-top: 8px; }
.wiki-reader__toc-group-title {
  display: flex; align-items: center; gap: 6px;
  font-size: 12px; font-weight: 600; color: var(--kb-text-tertiary);
  padding: 2px 8px; border-bottom: 1px solid var(--el-border-color-lighter, #ebeef5);
}
.wiki-reader__toc-group-edit :deep(.el-input__wrapper) {
  background: transparent; box-shadow: none; font-weight: 600; font-size: 12px;
  padding: 0 4px;
}
.wiki-reader__toc-group-edit :deep(.el-input__wrapper:focus-within) {
  box-shadow: 0 0 0 1px var(--kb-accent, #0891b2) inset;
}
.wiki-reader__toc-group-del {
  margin-left: auto; cursor: pointer; font-size: 11px;
  color: var(--kb-text-tertiary, #94a3b8);
}
.wiki-reader__toc-group-del:hover { color: var(--el-color-danger, #f56c6c); }

.wiki-reader__toc-item {
  display: flex; align-items: center; gap: 6px; width: 100%; padding: 6px 8px;
  border: 1px solid transparent; border-radius: 6px; background: none;
  cursor: pointer; text-align: left; font-size: 13px;
}
.wiki-reader__toc-item:hover { background: var(--el-fill-color-light, #f5f7fa); }
.wiki-reader__toc-item.is-active {
  background: var(--kb-accent-soft, rgba(8, 145, 178, 0.08));
  border-color: var(--kb-accent-medium, rgba(8, 145, 178, 0.15));
}
.wiki-reader__toc-item.is-dragover { border-color: var(--kb-accent, #0891b2); border-style: dashed; }
.wiki-reader__toc-item[draggable='true'] { cursor: grab; }
.wiki-reader__toc-title {
  flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  color: var(--kb-text-primary, inherit);
}
.wiki-reader__toc-del {
  display: none; font-size: 11px; color: var(--kb-text-tertiary, #94a3b8); flex-shrink: 0;
}
.wiki-reader__toc-item:hover .wiki-reader__toc-del { display: inline; }
.wiki-reader__toc-del:hover { color: var(--el-color-danger, #f56c6c); }
.wiki-reader__toc-empty-hint {
  margin: 0; padding: 4px 8px; font-size: 11.5px;
  color: var(--kb-text-tertiary, #94a3b8); border: 1px dashed var(--kb-border, #cbd5e1);
  border-radius: 6px; text-align: center;
}
.wiki-reader__toc-add-section {
  align-self: flex-start; border: 0; background: none; padding: 2px 8px;
  color: var(--kb-text-tertiary, #94a3b8); font-size: 12px; cursor: pointer;
}
.wiki-reader__toc-add-section:hover { color: var(--kb-accent, #0891b2); }

.wiki-reader__main { flex: 1; min-width: 0; }
.wiki-reader__page-head { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; flex-wrap: wrap; }
.wiki-reader__page-title { margin: 0; font-size: 17px; }
.wiki-reader__page-time { font-size: 12px; color: var(--kb-text-tertiary); }
.wiki-reader__page-actions { margin-left: auto; display: inline-flex; gap: 8px; }
.wiki-reader__page-actions .el-button + .el-button { margin-left: 0; }

.wiki-reader__edit-bar {
  display: flex; align-items: center; gap: 8px; margin-bottom: 8px;
  padding: 8px 12px; border-radius: 8px;
  background: var(--kb-bg-card-hover, #f8fafc);
  border: 1px solid var(--kb-border-light, #f1f5f9);
}
.wiki-reader__edit-addr { flex: 1; font-size: 12px; color: var(--kb-text-tertiary); }
.wiki-reader__edit-bar .el-button + .el-button { margin-left: 0; }
.wiki-reader__editor :deep(textarea) { font-family: Consolas, 'Microsoft YaHei', monospace; font-size: 12.5px; line-height: 1.8; }

.wiki-reader__body {
  border: 1px solid var(--el-border-color-lighter, #ebeef5); border-radius: 6px;
  padding: 12px 16px; line-height: 1.7; overflow-wrap: anywhere; min-height: 200px;
}
.wiki-reader__body :deep(a[data-wiki-page]) { color: var(--kb-accent, #0891b2); cursor: pointer; }
.wiki-reader__body :deep(sup.wiki-src) {
  color: var(--kb-text-tertiary); cursor: copy; font-size: 11px; margin: 0 1px;
}

.wiki-reader__ctx { width: 262px; flex-shrink: 0; display: flex; flex-direction: column; gap: 14px; }
.wiki-reader__ctx-title { margin: 0 0 6px; font-size: 13px; }
.wiki-reader__graph {
  border: 1px solid var(--kb-border, #e2e8f0); border-radius: 8px;
  height: 300px; overflow: hidden; background: var(--kb-bg-card, #fff);
}
.wiki-reader__graph :deep(.wiki-graph-canvas) { height: 300px; }
.wiki-reader__none { margin: 0; font-size: 12.5px; color: var(--kb-text-tertiary); }
.wiki-reader__prov {
  border: 1px solid var(--el-border-color-lighter, #ebeef5); border-radius: 6px;
  padding: 8px 10px; margin-bottom: 8px; font-size: 12.5px;
}
.wiki-reader__prov-line { margin: 0 0 2px; overflow-wrap: anywhere; }
.wiki-reader__prov-units { margin: 4px 0 0; padding-left: 18px; color: var(--kb-text-tertiary); }
.wiki-reader__prov-unit {
  border: none; padding: 0; background: none; cursor: pointer;
  color: var(--kb-accent, #0891b2); font-size: 12px;
  overflow-wrap: anywhere; text-align: left;
  text-decoration: underline dotted;
}
.wiki-reader__prov-unit:hover { opacity: 0.8; }
.wiki-reader__json :deep(textarea) { font-family: monospace; }
.wiki-reader__json-error { margin: 6px 0 0; font-size: 12px; color: var(--el-color-danger, #f56c6c); }
.wiki-reader__src-body { min-height: 80px; }
.wiki-reader__src-meta {
  margin: 0 0 8px; font-size: 12px; color: var(--kb-text-tertiary);
  overflow-wrap: anywhere;
}
.wiki-reader__src-text {
  margin: 0; font-size: 13.5px; line-height: 1.7; overflow-wrap: anywhere;
  white-space: pre-wrap;
}
.wiki-reader__src-hint { margin: 8px 0 0; font-size: 12px; color: var(--kb-text-tertiary); }
.wiki-reader__src-error { margin: 0; font-size: 13px; color: var(--el-color-danger, #f56c6c); }

@media (max-width: 1100px) {
  .wiki-reader { flex-direction: column; }
  .wiki-reader__toc, .wiki-reader__ctx { width: auto; }
}
</style>
