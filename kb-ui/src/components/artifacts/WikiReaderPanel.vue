<template>
  <div class="wiki-reader" data-test="wiki-reader">
    <!-- 加载中/清单失败/未发布（current_revision=0 → 清单为空即判）——单 EmptyState，不白屏 -->
    <EmptyState
      v-if="loading || pages.length === 0"
      :text="emptyText"
      data-test="wiki-reader-empty"
    />

    <!-- 三栏：左目录 / 中渲染页 / 右上下文（窄屏 media query 纵向堆叠，不引新依赖） -->
    <template v-else>
      <aside class="wiki-reader__toc">
        <!-- 检索区（批次4）：目录侧栏顶部——回车/按钮触发，结果渲染在侧栏；
             点击结果按 page_key 跳页（goToKey 复用：alias 兜底+未命中提示） -->
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
        <div class="wiki-reader__toc-head">目录 · 共 {{ sortedPages.length }} 页</div>
        <div class="wiki-reader__toc-body">
          <!-- D03 分节目录：runtime toc 有节 → 节序+节内键序分组（组容器=节锚点）；
               flat/无 toc → 单组无标题（title=null 不渲染组头）=原平铺口径 -->
          <section
            v-for="(group, gi) in tocGroups"
            :key="gi"
            class="wiki-reader__toc-group"
            :data-test="group.title !== null ? 'wiki-reader-toc-section' : undefined"
          >
            <div v-if="group.title" class="wiki-reader__toc-group-title">{{ group.title }}（{{ group.pages.length }}）</div>
            <button
              v-for="item in group.pages"
              :key="item.page_key"
              type="button"
              class="wiki-reader__toc-item"
              :class="{ 'is-active': item.page_key === selectedKey }"
              data-test="wiki-reader-toc-item"
              @click="selectPage(item.page_key)"
            >
              <el-tag size="small" effect="plain" :type="pageTypeMeta(item.page_type)?.tag ?? 'info'">
                {{ pageTypeMeta(item.page_type)?.label ?? item.page_type }}
              </el-tag>
              <span class="wiki-reader__toc-title">{{ item.title }}</span>
            </button>
          </section>
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
          </header>
          <!-- 点击委托：a[data-wiki-page] 切页 / sup.wiki-src 复制切片 ID（标记见 utils/markdown.ts） -->
          <div
            class="wiki-reader__body markdown-body"
            data-test="wiki-reader-body"
            v-html="pageHtml"
            @click="onContentClick"
          />
        </template>
        <EmptyState v-else text="在左侧目录选择页面" />
      </section>

      <aside v-if="page" class="wiki-reader__ctx">
        <section class="wiki-reader__ctx-block">
          <h4 class="wiki-reader__ctx-title">链接（{{ outLinks.length }}）</h4>
          <button
            v-for="(link, index) in outLinks"
            :key="`out-${index}`"
            type="button"
            class="wiki-reader__link"
            :class="{ 'is-broken': link.status === 'broken' }"
            data-test="wiki-reader-out-link"
            @click="goToKey(link.to_page)"
          >
            <span class="wiki-reader__link-text">{{ link.anchor_text || link.to_page }}</span>
            <span v-if="link.status === 'broken'" class="wiki-reader__link-flag">断链</span>
          </button>
          <p v-if="outLinks.length === 0" class="wiki-reader__none">无</p>
        </section>

        <section class="wiki-reader__ctx-block">
          <h4 class="wiki-reader__ctx-title">被链（{{ inLinks.length }}）</h4>
          <button
            v-for="(link, index) in inLinks"
            :key="`in-${index}`"
            type="button"
            class="wiki-reader__link"
            :class="{ 'is-broken': link.status === 'broken' }"
            data-test="wiki-reader-in-link"
            @click="goToKey(link.from_page)"
          >
            <!-- 被链主文案=来源页（谁链到我）；锚文本作次要提示 -->
            <span class="wiki-reader__link-text">
              {{ link.from_page }}<template v-if="link.anchor_text">（{{ link.anchor_text }}）</template>
            </span>
            <span v-if="link.status === 'broken'" class="wiki-reader__link-flag">断链</span>
          </button>
          <p v-if="inLinks.length === 0" class="wiki-reader__none">无</p>
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
            <!-- Q01（66号批次5 Task 5.3）：切片可点 → unit-source 原文截读弹层 -->
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
            <!-- 结构未知字段：容错为 k=v 一行展示 -->
            <p v-for="extra in item.extra" :key="extra" class="wiki-reader__prov-line">{{ extra }}</p>
          </div>
          <p v-if="provenanceViews.length === 0" class="wiki-reader__none">无溯源记录</p>
        </section>
      </aside>
    </template>

    <!-- Q01 溯源回原文弹层：unit-source 原文截读（≤500 字）+ kb/快照定位 +
         「在知识库中查看」跳 kb 详情（66号批次5 Task 5.3） -->
    <el-dialog v-model="unitSourceVisible" title="来源切片原文" width="540px">
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
import { ElMessage } from 'element-plus'
import { useArtifactsApi } from '@/api/artifacts'
import { apiErrorDetail } from '@/api/proxyClient'
import EmptyState from '@/components/common/EmptyState.vue'
import { extractWikiNavTarget, extractWikiSrcUnit, renderWikiMarkdown } from '@/utils/markdown'
import type {
  WikiLink,
  WikiPage,
  WikiPageMeta,
  WikiPageType,
  WikiSearchHit,
  WikiTocSnapshotResolved,
  WikiUnitSource,
} from '@/types/artifacts'

/** 四页型徽标表（63号 §4.3）——与 WikiEditorPanel 同表（纯展示映射，两处独立维护）。 */
const PAGE_TYPES = [
  { value: 'topic', label: '主题', tag: 'primary' },
  { value: 'glossary', label: '术语', tag: 'success' },
  { value: 'contradiction', label: '矛盾', tag: 'danger' },
  { value: 'open_question', label: '开放问题', tag: 'warning' },
] as const

/** 溯源条目视图模型（provenance 结构宽松——字段缺失/未知结构一律容错降级显示）。 */
interface ProvenanceView {
  kbId: string
  snapshotId: string
  unitIds: string[]
  extra: string[]
}

const props = defineProps<{ artifactId: string }>()

const api = useArtifactsApi()
const router = useRouter()

const loading = ref(false)
const pageLoading = ref(false)
const loadFailedText = ref('')
const pages = ref<WikiPageMeta[]>([])
const selectedKey = ref('')
const page = ref<WikiPage | null>(null)
const outLinks = ref<WikiLink[]>([])
const inLinks = ref<WikiLink[]>([])
/** 运行态分节目录（D03）：null=平铺（无 toc 行/flat/拉取失败一律回落）。 */
const tocSections = ref<WikiTocSnapshotResolved[] | null>(null)

// ── 检索（63号批次4：搜索框 → mining 转发 serving 三通道，fusion 默认）──
const searchQuery = ref('')
const searchResults = ref<WikiSearchHit[]>([])
const searchLoading = ref(false)
/** 最近一次搜索已完成（零命中时显示"无匹配结果"提示）。 */
const searchDone = ref(false)
/** 请求序号防护（CompilePanel epoch 同款坑）：连续提交时迟到旧响应
 * 一律丢弃不落地（发起时捕获旧值，落地前比对）。 */
let searchEpoch = 0

const sortedPages = computed(() =>
  [...pages.value].sort((a, b) => a.ordinal - b.ordinal || a.page_key.localeCompare(b.page_key)))

/** 目录分组视图（D03）：有节→节序+节内键序（页键不在已发布页滤除、跨节重复
 * 只取首见），未入节页归「其他」尾节；平铺→单组无标题=原 ordinal 口径。 */
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

/** 正文 HTML（renderWikiMarkdown：站内双链/溯源角标标记 → marked → DOMPurify）。 */
const pageHtml = computed(() => (page.value ? renderWikiMarkdown(page.value.body_md ?? '') : ''))

const provenanceViews = computed<ProvenanceView[]>(() => toProvenanceViews(page.value?.provenance))

const emptyText = computed(() => {
  if (loading.value) return 'Wiki 页面加载中…'
  if (loadFailedText.value) return `页面清单加载失败：${loadFailedText.value}`
  return '尚未发布版本——在设计态编辑并发布后，这里会显示 Wiki 页面'
})

onMounted(loadPages)

/** 页面清单（运行态：draft=false 读 current_revision 投影）+ 首选页/保持
 * 选中页。响应为 {revision, pages} 信封（D03.1）——清单只消费 pages。 */
async function loadPages(): Promise<void> {
  loading.value = true
  loadFailedText.value = ''
  try {
    const envelope = await api.listWikiPages(props.artifactId, { draft: false })
    pages.value = envelope.pages
    // 分节目录先于首选页落地（失败静默回落平铺）——目录序决定初始选中页，
    // 保证打开阅读器时目录首项高亮与正文一致（分节下≠ordinal 首页）。
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
      outLinks.value = []
      inLinks.value = []
    }
  } catch (error) {
    pages.value = []
    page.value = null
    loadFailedText.value = await apiErrorDetail(error)
  } finally {
    loading.value = false
  }
}

/** 运行态目录（缺省口径）：有节分节渲染；无行/flat/404（rev0 反探测）等
 * 一切失败形态一律回落平铺且不报错（Reader 已有的 rev0 容错口径）。 */
async function loadToc(): Promise<void> {
  try {
    const snap = await api.getWikiToc(props.artifactId)
    tocSections.value = snap.flat === true || (snap.sections ?? []).length === 0
      ? null
      : snap.sections
  } catch {
    tocSections.value = null
  }
}

/** 选中并加载一页（运行态）；过期响应（选中已被覆盖）丢弃。 */
async function selectPage(pageKey: string): Promise<void> {
  selectedKey.value = pageKey
  pageLoading.value = true
  outLinks.value = []
  inLinks.value = []
  try {
    const loaded = await api.getWikiPage(props.artifactId, pageKey, { draft: false })
    if (selectedKey.value !== pageKey) return
    page.value = loaded
    void loadLinks(pageKey)
  } catch (error) {
    if (selectedKey.value === pageKey) {
      ElMessage.error(`加载页面失败：${await apiErrorDetail(error)}`)
    }
  } finally {
    pageLoading.value = false
  }
}

/** 出入边并行拉取；仅当仍是当前页时落地（切页竞态防串台）。响应为
 * {revision, links} 信封（D03.1）——侧栏只消费 links。 */
async function loadLinks(pageKey: string): Promise<void> {
  try {
    const [outEnv, inEnv] = await Promise.all([
      api.listWikiLinks(props.artifactId, { page_key: pageKey, direction: 'out' }),
      api.listWikiLinks(props.artifactId, { page_key: pageKey, direction: 'in' }),
    ])
    if (selectedKey.value !== pageKey) return
    outLinks.value = outEnv.links
    inLinks.value = inEnv.links
  } catch (error) {
    if (selectedKey.value === pageKey) {
      ElMessage.error(`加载链接信息失败：${await apiErrorDetail(error)}`)
    }
  }
}

/** 提交检索（回车/按钮共用）：空输入=清空；迟到旧响应（epoch 不匹配）
 * 丢弃不落地。score 不展示——结果即排序后的命中清单。 */
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

/** 作废在飞检索并清空状态（清空输入/切换制品共用；幂等）。 */
function resetSearch(): void {
  searchEpoch += 1
  searchLoading.value = false
  searchResults.value = []
  searchDone.value = false
}

/** 清空输入=清结果（el-input clearable 图标与手输删空都走此处）。 */
watch(searchQuery, (value) => {
  if (!value) resetSearch()
})

/** 制品切换（路由复用组件不重挂）→ 检索状态整体重置（旧制品慢响应不落地）。 */
watch(() => props.artifactId, () => {
  searchQuery.value = ''
  resetSearch()
  tocSections.value = null   // 旧制品目录不残留（loadPages 会重拉）
})

/** 正文点击委托：双链 → 切页；溯源角标 → 复制切片 ID；其余点击不拦截。 */
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

/**
 * 双链目标解析：page_key 直配 → aliases 命中。aliases 唯一可信口径是
 * listWikiPages 的 WikiPageMeta.aliases（getWikiPage 返回体类型声明偏宽，
 * 运行态不含顶层 aliases——不依赖它）。未命中 → 提示未发布（断链可见）。
 */
function goToKey(key: string): void {
  const target = pages.value.find(p => p.page_key === key)
    ?? pages.value.find(p => p.aliases.includes(key))
  if (!target) {
    ElMessage.warning('目标页面未发布')
    return
  }
  if (target.page_key !== selectedKey.value) void selectPage(target.page_key)
}

/** 溯源角标：复制切片 ID（clipboard 不可用/失败 → 降级 info 直接展示）。 */
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

// ── Q01 溯源回原文（66号批次5 Task 5.3）：溯源切片点击 → unit-source 弹层 ──

const unitSourceVisible = ref(false)
const unitSourceLoading = ref(false)
const unitSource = ref<WikiUnitSource | null>(null)
/** 无权/失效（404 反探测单码）：直接透出后端 message，弹层内展示不崩。 */
const unitSourceError = ref('')
/** 请求序号防护：切换切片时迟到的旧响应不覆盖新内容（搜索 epoch 同款）。 */
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

/** 「在知识库中查看」：跳 kb 详情（router 先例 push(`/kb/${kbId}`)）。 */
function goKb(): void {
  const kbId = unitSource.value?.kb_id
  if (!kbId) return
  void router.push(`/kb/${kbId}`)
}

/** provenance 宽松归一：对象条目取 kb_id/snapshot_id/unit_ids，其余原始值键值行。 */
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
    // 非对象条目（结构漂移）：原样一行容错展示
    return { kbId: '—', snapshotId: '—', unitIds: [], extra: [String(item)] }
  })
}

function pageTypeMeta(pageType: WikiPageType | string) {
  return PAGE_TYPES.find(t => t.value === pageType)
}

/** origin 徽标中文化（原值落 title 提示；与 EditorPanel 同口径）。 */
function originLabel(origin: string): string {
  if (origin === 'manual') return '手工'
  if (origin === 'machine') return '编译'
  return origin
}

/** 更新时间：ISO → 「YYYY-MM-DD HH:mm:ss」（原始微秒精度对读者无意义）。 */
function formatTime(value: string | null | undefined): string {
  return value ? value.replace('T', ' ').slice(0, 19) : '—'
}

/** 命令式刷新（发布后父级 reloadAll 可不接线——面板自管理）；selectPage
 * 暴露给父级（D03.1：图谱「打开页面」回页面视图定位该页）。 */
defineExpose({ reload: loadPages, selectPage, currentPageKey: computed(() => selectedKey.value) })
</script>

<style scoped>
.wiki-reader { display: flex; gap: 14px; min-height: 420px; }
.wiki-reader__toc { width: 240px; flex-shrink: 0; }
/* 检索区（批次4）：目录侧栏顶部；结果列表限高滚动不挤压目录 */
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
.wiki-reader__search-none {
  margin: 6px 0 0; font-size: 12px; color: var(--kb-text-tertiary);
}
.wiki-reader__toc-head {
  font-size: 12.5px; color: var(--kb-text-tertiary); margin-bottom: 8px;
}
.wiki-reader__toc-body { display: flex; flex-direction: column; gap: 4px; }
/* D03 分节目录：组间留白，组标题弱化（页项仍是主交互面） */
.wiki-reader__toc-group { display: flex; flex-direction: column; gap: 4px; }
.wiki-reader__toc-group + .wiki-reader__toc-group { margin-top: 8px; }
.wiki-reader__toc-group-title {
  font-size: 12px; font-weight: 600; color: var(--kb-text-tertiary);
  padding: 2px 8px; border-bottom: 1px solid var(--el-border-color-lighter, #ebeef5);
}
.wiki-reader__toc-item {
  display: flex; align-items: center; gap: 6px; width: 100%; padding: 6px 8px;
  border: 1px solid transparent; border-radius: 6px; background: none;
  cursor: pointer; text-align: left; font-size: 13px;
}
.wiki-reader__toc-item:hover { background: var(--el-fill-color-light, #f5f7fa); }
.wiki-reader__toc-item.is-active {
  background: var(--el-color-primary-light-9, #ecf5ff);
  border-color: var(--el-color-primary-light-7, #d9ecff);
}
.wiki-reader__toc-title {
  flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  color: var(--kb-text-primary, inherit);
}
.wiki-reader__main { flex: 1; min-width: 0; }
.wiki-reader__page-head { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; flex-wrap: wrap; }
.wiki-reader__page-title { margin: 0; font-size: 17px; }
.wiki-reader__page-time { font-size: 12px; color: var(--kb-text-tertiary); }
.wiki-reader__body {
  border: 1px solid var(--el-border-color-lighter, #ebeef5); border-radius: 6px;
  padding: 12px 16px; line-height: 1.7; overflow-wrap: anywhere; min-height: 200px;
}
/* 站内双链/溯源角标（渲染标记由 utils/markdown.ts 预处理注入） */
.wiki-reader__body :deep(a[data-wiki-page]) { color: var(--kb-accent, #2563eb); cursor: pointer; }
.wiki-reader__body :deep(sup.wiki-src) {
  color: var(--kb-text-tertiary); cursor: copy; font-size: 11px; margin: 0 1px;
}
.wiki-reader__ctx { width: 240px; flex-shrink: 0; display: flex; flex-direction: column; gap: 14px; }
.wiki-reader__ctx-title { margin: 0 0 6px; font-size: 13px; }
.wiki-reader__link {
  display: flex; align-items: center; gap: 6px; width: 100%; padding: 4px 6px;
  border: none; border-radius: 4px; background: none; cursor: pointer;
  text-align: left; font-size: 12.5px; color: var(--kb-accent, #2563eb);
}
.wiki-reader__link:hover { background: var(--el-fill-color-light, #f5f7fa); }
.wiki-reader__link.is-broken { color: var(--el-color-danger, #f56c6c); }
.wiki-reader__link-text { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.wiki-reader__link-flag { font-size: 11px; flex-shrink: 0; }
.wiki-reader__none { margin: 0; font-size: 12.5px; color: var(--kb-text-tertiary); }
.wiki-reader__prov {
  border: 1px solid var(--el-border-color-lighter, #ebeef5); border-radius: 6px;
  padding: 8px 10px; margin-bottom: 8px; font-size: 12.5px;
}
.wiki-reader__prov-line { margin: 0 0 2px; overflow-wrap: anywhere; }
.wiki-reader__prov-units { margin: 4px 0 0; padding-left: 18px; color: var(--kb-text-tertiary); }
/* Q01 溯源切片可点（回原文弹层） */
.wiki-reader__prov-unit {
  border: none; padding: 0; background: none; cursor: pointer;
  color: var(--kb-accent, #2563eb); font-size: 12px;
  overflow-wrap: anywhere; text-align: left;
  text-decoration: underline dotted;
}
.wiki-reader__prov-unit:hover { opacity: 0.8; }
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
/* 窄屏：三栏纵向堆叠（简单折叠，不引新依赖） */
@media (max-width: 1100px) {
  .wiki-reader { flex-direction: column; }
  .wiki-reader__toc, .wiki-reader__ctx { width: auto; }
}
</style>
