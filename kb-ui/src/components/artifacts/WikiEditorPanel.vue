<template>
  <div class="wiki-editor">
    <!-- 左栏：页面清单（ordinal 排序；徽标=页型色 + origin）｜目录编辑入口（D03） -->
    <aside class="wiki-editor__list">
      <div class="wiki-editor__list-head">
        <div class="wiki-editor__list-meta">
          <span class="wiki-editor__list-label">页面内容</span>
          <span class="wiki-editor__count">共 {{ sortedPages.length }} 页</span>
        </div>
        <div v-if="canWrite" class="wiki-editor__views" role="tablist" aria-label="Wiki 编辑视图">
          <button
            type="button"
            role="tab"
            :aria-selected="leftView === 'pages'"
            :class="{ active: leftView === 'pages' }"
            data-test="wiki-left-pages"
            @click="switchLeftView('pages')"
          >页面清单</button>
          <button
            type="button"
            role="tab"
            :aria-selected="leftView === 'toc'"
            :class="{ active: leftView === 'toc' }"
            data-test="wiki-left-toc"
            @click="switchLeftView('toc')"
          >目录</button>
        </div>
      </div>
      <el-button
        v-if="canWrite && leftView === 'pages'"
        type="primary"
        plain
        size="small"
        class="wiki-editor__new"
        data-test="wiki-new-page"
        @click="startNewPage"
      >
        <el-icon class="wiki-editor__new-icon"><Plus /></el-icon>新建页
      </el-button>
      <div v-loading="loading" class="wiki-editor__list-body">
        <button
          v-for="page in sortedPages"
          :key="page.page_key"
          type="button"
          class="wiki-editor__item"
          :class="{ 'is-active': leftView === 'pages' && !isNew && selectedKey === page.page_key }"
          data-test="wiki-page-item"
          @click="selectPage(page.page_key)"
        >
          <el-tag size="small" effect="plain" :type="pageTypeMeta(page.page_type)?.tag ?? 'info'">
            {{ pageTypeMeta(page.page_type)?.label ?? page.page_type }}
          </el-tag>
          <span class="wiki-editor__item-title">{{ page.title }}</span>
          <el-tag
            size="small"
            effect="plain"
            :type="page.origin === 'manual' ? 'primary' : 'info'"
            :title="`来源：${page.origin}`"
          >
            {{ originLabel(page.origin) }}
          </el-tag>
          <span
            v-if="canWrite"
            class="wiki-editor__item-del"
            role="button"
            aria-label="删除页面"
            title="删除页面"
            data-test="wiki-page-delete"
            @click.stop="removePage(page)"
          >
            <el-icon><Delete /></el-icon>
          </span>
        </button>
        <EmptyState
          v-if="!loading && sortedPages.length === 0"
          :text="canWrite ? '暂无页面，点击新建开始' : '暂无页面'"
        />
      </div>
    </aside>

    <!-- 右栏：目录编辑（D03，左栏切到「目录」时占据）｜编辑表单（viewer 全禁用但可预览） -->
    <section class="wiki-editor__main">
      <WikiTocDesignPanel
        v-if="leftView === 'toc'"
        :artifact-id="artifactId"
        :pages="sortedPages"
        @dirty="onTocDirty"
      />
      <template v-else-if="formVisible">
        <div class="wiki-editor__toolbar">
          <span class="wiki-editor__addr">
            {{ isNew ? '新建页（保存后按后端归一定稿 page_key）' : `page_key：${selectedKey}` }}
          </span>
          <el-switch v-model="preview" data-test="wiki-preview-toggle" active-text="预览" />
          <el-button
            v-if="canWrite"
            type="primary"
            size="small"
            :loading="saving"
            :disabled="!frontmatterValid"
            data-test="wiki-save"
            @click="save"
          >保存</el-button>
        </div>
        <el-alert
          v-if="canWrite && !draftOpen"
          type="info"
          :closable="false"
          title="当前与已发布版本一致；保存页面将自动检出草稿"
        />
        <el-form label-position="top" class="wiki-editor__form" :disabled="!canWrite">
          <div class="wiki-editor__grid">
            <el-form-item label="标题">
              <el-input v-model="form.title" data-test="wiki-title" />
            </el-form-item>
            <el-form-item label="页面类型">
              <el-select v-model="form.pageType" data-test="wiki-page-type">
                <el-option v-for="t in PAGE_TYPES" :key="t.value" :value="t.value" :label="t.label" />
              </el-select>
            </el-form-item>
            <el-form-item v-if="isNew" label="page_key（可选，缺省取标题原文）">
              <el-input v-model="form.pageKey" data-test="wiki-page-key" />
            </el-form-item>
            <el-form-item label="序号 ordinal">
              <el-input-number v-model="form.ordinal" :min="0" data-test="wiki-ordinal" />
            </el-form-item>
          </div>
          <el-form-item label="别名 aliases（逗号分隔 → frontmatter.aliases）">
            <el-input v-model="form.aliases" data-test="wiki-aliases" />
          </el-form-item>
          <el-form-item label="标签 tags（逗号分隔 → frontmatter.tags）">
            <el-input v-model="form.tags" data-test="wiki-tags" />
          </el-form-item>
          <el-alert
            v-if="requiredKeys.length > 0"
            type="warning"
            :closable="false"
            :title="`「${form.pageType}」页必填 frontmatter 键：${requiredKeys.join('、')}——在下方 frontmatter JSON 区填写`"
          />
          <el-collapse class="wiki-editor__fm">
            <el-collapse-item name="frontmatter">
              <template #title>
                frontmatter JSON（专页必填键 + 其他自定义键；aliases/tags 以后方专属输入框为准）
              </template>
              <el-input
                v-model="frontmatterJson"
                type="textarea"
                :rows="6"
                class="wiki-editor__json"
                data-test="wiki-frontmatter-json"
              />
              <p v-if="!frontmatterValid" class="wiki-editor__json-error" data-test="wiki-json-error">frontmatter JSON 解析失败——修正后才能保存</p>
            </el-collapse-item>
          </el-collapse>
          <el-form-item label="正文 body_md（markdown，[[页面键]] 为站内链接）">
            <el-input
              v-if="!preview"
              v-model="form.bodyMd"
              type="textarea"
              :rows="16"
              class="wiki-editor__body"
              data-test="wiki-body"
            />
            <!-- 预览：marked 渲染 + DOMPurify 消毒（utils/markdown.ts） -->
            <div
              v-else
              class="wiki-editor__preview markdown-body"
              data-test="wiki-preview-body"
              v-html="previewHtml"
            />
          </el-form-item>
        </el-form>
      </template>
      <EmptyState v-else :text="canWrite ? '在左侧选择页面，或新建一页' : '在左侧选择页面查看'" />
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Delete, Plus } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useArtifactsApi } from '@/api/artifacts'
import { apiErrorDetail } from '@/api/proxyClient'
import EmptyState from '@/components/common/EmptyState.vue'
import WikiTocDesignPanel from './WikiTocDesignPanel.vue'
import { renderMarkdown } from '@/utils/markdown'
import type { WikiPage, WikiPageMeta, WikiPageType } from '@/types/artifacts'

/** 四页型（63号 §4.3）——表单下拉与列表徽标共用（label=徽标文案，tag=徽标色）。 */
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

interface WikiForm {
  title: string
  pageType: WikiPageType
  pageKey: string
  aliases: string
  tags: string
  ordinal?: number
  bodyMd: string
}

const props = defineProps<{
  artifactId: string
  canWrite: boolean
  /** 乐观锁令牌（artifact.updated_at）——保存/删除随请求携带。 */
  expectedUpdatedAt?: string
  draftOpen?: boolean
}>()

const emit = defineEmits<{ changed: [] }>()

const api = useArtifactsApi()

const loading = ref(false)
const saving = ref(false)
const pages = ref<WikiPageMeta[]>([])
const selectedKey = ref('')
const isNew = ref(false)
const preview = ref(false)
/** 左栏视图（D03）：页面清单｜目录编辑（目录编辑=editor+ 能力，viewer 无入口）。 */
const leftView = ref<'pages' | 'toc'>('pages')
/** 目录面板是否有未保存修改（子组件 dirty 事件维护）——离开目录视图的守卫依据。 */
const tocDirty = ref(false)

const emptyForm = (): WikiForm => ({ title: '', pageType: 'topic', pageKey: '', aliases: '', tags: '', ordinal: 1, bodyMd: '' })
const form = ref<WikiForm>(emptyForm())
/** frontmatter 原始 JSON 编辑区（aliases/tags 专属输入框提交时覆盖同名键）。 */
const frontmatterJson = ref('{}')

const sortedPages = computed(() =>
  [...pages.value].sort((a, b) => a.ordinal - b.ordinal || a.page_key.localeCompare(b.page_key)))

const formVisible = computed(() => isNew.value || selectedKey.value !== '')

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
const requiredKeys = computed(() => REQUIRED_FM[form.value.pageType] ?? [])
const previewHtml = computed(() => renderMarkdown(form.value.bodyMd))

onMounted(loadPages)

/** 页面清单：编辑者读设计态 draft 视图（rev0 工作区），viewer 读运行态。
 * 响应为 {revision, pages} 信封（D03.1）——清单只消费 pages。 */
async function loadPages(): Promise<void> {
  loading.value = true
  try {
    const envelope = await api.listWikiPages(props.artifactId, { draft: props.canWrite })
    pages.value = envelope.pages
    if (selectedKey.value && !pages.value.some(p => p.page_key === selectedKey.value)) {
      selectedKey.value = ''   // 选中页已被删（并发）→ 收起表单
    }
  } catch (error) {
    pages.value = []
    ElMessage.error(`加载页面清单失败：${await apiErrorDetail(error)}`)
  } finally {
    loading.value = false
  }
}

/** 切换左栏视图（D03）。目录有未保存修改时离开需用户明确放弃（66号 §6：
 * 未完成的编辑在切换前必须保存或由用户明确放弃）。返回是否完成切换。 */
async function switchLeftView(view: 'pages' | 'toc'): Promise<boolean> {
  if (view === leftView.value) return true
  if (view === 'pages' && tocDirty.value) {
    try {
      await ElMessageBox.confirm(
        '目录有未保存的修改，离开将放弃这些修改——确定离开吗？',
        '未保存的目录修改',
        { type: 'warning', confirmButtonText: '放弃修改并离开', cancelButtonText: '继续编辑目录' })
    } catch {
      return false   // 用户选择继续编辑——视图不动，编辑不丢
    }
    tocDirty.value = false
  }
  leftView.value = view
  return true
}

/** 目录面板 dirty 事件落地（保存成功也会发 false 复位）。 */
function onTocDirty(isDirty: boolean): void {
  tocDirty.value = isDirty
}

async function selectPage(pageKey: string): Promise<void> {
  // 目录视图下点左栏页项=离开目录编辑——同走放弃确认守卫
  if (leftView.value === 'toc') {
    const switched = await switchLeftView('pages')
    if (!switched) return
  }
  isNew.value = false
  selectedKey.value = pageKey
  try {
    fillForm(await api.getWikiPage(props.artifactId, pageKey, { draft: props.canWrite }))
  } catch (error) {
    ElMessage.error(`加载页面失败：${await apiErrorDetail(error)}`)
  }
}

function fillForm(page: WikiPage): void {
  const fm = page.frontmatter ?? {}
  form.value = {
    title: page.title,
    pageType: page.page_type,
    pageKey: page.page_key,
    aliases: toCsv(fm.aliases),
    tags: toCsv(fm.tags),
    ordinal: page.ordinal,
    bodyMd: page.body_md ?? '',
  }
  frontmatterJson.value = JSON.stringify(fm, null, 2)
}

/** 新建页：本地暂存空表单，保存时才落 PUT（URL 键=page_key 字段或缺省 title）。 */
function startNewPage(): void {
  isNew.value = true
  selectedKey.value = ''
  form.value = { ...emptyForm(), ordinal: sortedPages.value.length + 1 }
  frontmatterJson.value = '{}'
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
    if (isStale(error)) {
      ElMessage.error('页面已被他人修改，已刷新最新状态')
      emit('changed')
      await loadPages()
    } else {
      ElMessage.error(`删除失败：${await apiErrorDetail(error)}`)
    }
  }
}

/** 提交 frontmatter：JSON 区解析结果打底，aliases/tags 专属输入框覆盖同名键。 */
function buildFrontmatter(): Record<string, unknown> | null {
  const base = parsedFrontmatter.value
  if (base === null) return null
  return { ...base, aliases: splitCsv(form.value.aliases), tags: splitCsv(form.value.tags) }
}

async function save(): Promise<void> {
  const title = form.value.title.trim()
  if (!title) {
    ElMessage.warning('标题必填')
    return
  }
  const frontmatter = buildFrontmatter()
  if (frontmatter === null) {
    ElMessage.warning('frontmatter JSON 解析失败，请修正后再保存')
    return
  }
  // URL 寻址键：新建=page_key 字段（缺省 title 原文，后端归一定稿）；编辑=原
  // page_key（title 改名不漂移；改 page_key 字段=改名流，后端做占用校验）。
  const addressKey = isNew.value ? (form.value.pageKey.trim() || title) : selectedKey.value
  const explicitKey = form.value.pageKey.trim()
  saving.value = true
  try {
    const page = await api.upsertWikiPage(props.artifactId, addressKey, {
      title,
      page_type: form.value.pageType,
      frontmatter,
      body_md: form.value.bodyMd,
      ...(form.value.ordinal === undefined ? {} : { ordinal: form.value.ordinal }),
      ...(explicitKey ? { page_key: explicitKey } : {}),
      expected_updated_at: props.expectedUpdatedAt,
    })
    ElMessage.success(`已保存「${page.title}」`)
    isNew.value = false
    emit('changed')
    await loadPages()
    // 后端归一后的 key 可能 ≠ title 原文——按返回值刷新选中
    await selectPage(page.page_key)
  } catch (error) {
    if (isStale(error)) {
      ElMessage.error('页面已被他人修改，已刷新最新状态')
      emit('changed')
      await loadPages()
    } else {
      ElMessage.error(`保存失败：${frontmatterErrorText(await apiErrorDetail(error))}`)
    }
  } finally {
    saving.value = false
  }
}

/** 409 stale_artifact → 提示刷新（emit changed 触发父级 reload 刷新乐观锁令牌）。 */
function isStale(error: unknown): boolean {
  const resp = (error as { response?: { status?: number; data?: { detail?: { code?: string } } } })
    ?.response
  return resp?.status === 409 && resp.data?.detail?.code === 'stale_artifact'
}

/** 422 invalid_frontmatter:字段 → 人读文案（后端 detail 是 code 字符串，含缺失键名）。 */
function frontmatterErrorText(message: string): string {
  if (message.startsWith('invalid_frontmatter:')) {
    return `frontmatter 校验失败：${message.slice('invalid_frontmatter:'.length) || '结构非法'}`
  }
  return message
}

function pageTypeMeta(pageType: string) {
  return PAGE_TYPES.find(t => t.value === pageType)
}

/** origin 徽标中文化（原值落 title 提示，便于排查数据）。 */
function originLabel(origin: string): string {
  if (origin === 'manual') return '手工'
  if (origin === 'machine') return '编译'
  return origin
}

function toCsv(value: unknown): string {
  return Array.isArray(value) ? value.map(String).join(', ') : ''
}

/** 逗号拆分（中英文逗号均可）→ 去空白 → 滤空。 */
function splitCsv(value: string): string[] {
  return value.split(/[，,]/).map(v => v.trim()).filter(Boolean)
}

/** selectPage 暴露给父级（D03.1：图谱「打开页面」回页面视图定位该页）。 */
defineExpose({ loadPages, selectPage, currentPageKey: computed(() => selectedKey.value) })
</script>

<style scoped>
.wiki-editor {
  display: flex; gap: 14px; min-height: 420px; padding: 14px;
  border: 1px solid var(--kb-border-light); border-radius: 8px;
  background: var(--kb-bg-card);
}
.wiki-editor__list { width: 280px; flex-shrink: 0; display: flex; flex-direction: column; gap: 8px; padding-right: 14px; border-right: 1px solid var(--kb-border-light); }
.wiki-editor__list-head { display: flex; flex-direction: column; gap: 10px; }
.wiki-editor__list-meta { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.wiki-editor__list-label { color: var(--kb-text-primary); font-size: 13px; font-weight: 600; }
.wiki-editor__views { display: flex; gap: 2px; padding: 3px; border-radius: 6px; background: var(--el-fill-color-light, #f5f7fa); }
.wiki-editor__views button {
  flex: 1; padding: 6px 10px;
  border: 1px solid transparent; border-radius: 4px;
  color: var(--kb-text-secondary);
  background: transparent;
  cursor: pointer;
}
.wiki-editor__views button.active {
  color: var(--kb-accent);
  border-color: var(--kb-border-light);
  background: var(--kb-bg-card);
  font-weight: 600;
}
.wiki-editor__views button:focus-visible { outline: 2px solid var(--kb-accent); outline-offset: 1px; }
.wiki-editor__new { align-self: stretch; }
.wiki-editor__new-icon { margin-right: 4px; }
.wiki-editor__count { font-size: 12.5px; color: var(--kb-text-tertiary); }
.wiki-editor__list-body { display: flex; flex-direction: column; gap: 4px; }
.wiki-editor__item {
  display: flex; align-items: center; gap: 6px; width: 100%; padding: 6px 8px;
  border: 1px solid transparent; border-radius: 6px; background: none;
  cursor: pointer; text-align: left; font-size: 13px;
}
.wiki-editor__item:hover { background: var(--el-fill-color-light, #f5f7fa); }
.wiki-editor__item.is-active {
  background: var(--el-color-primary-light-9, #ecf5ff);
  border-color: var(--el-color-primary-light-7, #d9ecff);
}
.wiki-editor__item-title {
  flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  color: var(--kb-text-primary, inherit);
}
.wiki-editor__item-del {
  display: inline-flex; align-items: center; justify-content: center;
  width: 20px; height: 20px; flex-shrink: 0;
  border-radius: 4px; color: var(--kb-text-tertiary, #94a3b8);
  cursor: pointer; font-size: 12px;
}
.wiki-editor__item-del:hover {
  color: var(--el-color-danger, #f56c6c);
  background: var(--el-color-danger-light-9, #fef0f0);
}
.wiki-editor__main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 8px; }
.wiki-editor__toolbar { display: flex; align-items: center; gap: 12px; }
.wiki-editor__addr { flex: 1; font-size: 12.5px; color: var(--kb-text-tertiary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.wiki-editor__grid { display: grid; grid-template-columns: 2fr 1fr 1fr 1fr; gap: 0 12px; }
.wiki-editor__fm { margin-bottom: 18px; }
.wiki-editor__json :deep(textarea) { font-family: monospace; }
.wiki-editor__json-error { margin: 6px 0 0; font-size: 12px; color: var(--el-color-danger, #f56c6c); }
.wiki-editor__body :deep(textarea) { font-family: monospace; }
.wiki-editor__preview {
  border: 1px solid var(--el-border-color-lighter, #ebeef5); border-radius: 6px;
  padding: 12px 16px; min-height: 380px; line-height: 1.7; overflow-wrap: anywhere;
}
</style>
