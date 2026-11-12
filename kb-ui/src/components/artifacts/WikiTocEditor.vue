<template>
  <div class="wiki-toc" data-test="wiki-toc-editor">
    <div class="wiki-toc__head">
      <span class="wiki-toc__head-title">目录确认</span>
      <span class="wiki-toc__count">共 {{ pageCount }} 页</span>
      <span v-if="overLimit" class="wiki-toc__over" data-test="wiki-toc-over">
        页数超过 {{ MAX_PAGES }} 上限——请取消勾选删减后再确认
      </span>
    </div>
    <p class="wiki-toc__hint">勾选=纳入写作；节标题与页标题可直接编辑，取消勾选的页不会生成。</p>

    <div v-for="(section, si) in sections" :key="si" class="wiki-toc__section" data-test="wiki-toc-section">
      <header class="wiki-toc__section-head">
        <span class="wiki-toc__section-no">{{ si + 1 }}</span>
        <el-input
          v-model="section.section_title"
          size="small"
          class="wiki-toc__section-title"
          data-test="wiki-toc-section-title"
        />
        <span class="wiki-toc__section-count">{{ section.page_hints.filter(h => h.included).length }} 页</span>
      </header>
      <div
        v-for="(hint, hi) in section.page_hints"
        :key="hi"
        class="wiki-toc__hint-row"
        :class="{ 'is-excluded': !hint.included }"
      >
        <el-checkbox v-model="hint.included" size="small" data-test="wiki-toc-page-check" />
        <el-input v-model="hint.title" size="small" class="wiki-toc__page-title" data-test="wiki-toc-page-title" />
        <span class="wiki-toc__page-type" :class="`is-${hint.page_type}`">{{ typeLabel(hint.page_type) }}</span>
      </div>
      <p v-if="section.page_hints.length === 0" class="wiki-toc__empty-sec">（空节——确认后只保留节标题）</p>
    </div>

    <div class="wiki-toc__foot">
      <el-button
        v-if="canWrite"
        type="primary"
        size="small"
        :loading="busy"
        :disabled="!canConfirm"
        data-test="wiki-toc-confirm"
        @click="confirm"
      >确认目录，开始写作</el-button>
      <el-button v-if="canWrite" size="small" class="wiki-toc__cancel" data-test="wiki-toc-cancel" @click="emit('cancel')">
        放弃编译
      </el-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { WikiTocSection } from '@/types/artifacts'

/** 与后端 CompileService.MAX_PAGES 对齐（confirm 端点 >200 → 422）。 */
const MAX_PAGES = 200

interface EditableHint {
  title: string
  cluster_ids: string[]
  page_type: string
  /** 勾选位：false=从写作范围排除。 */
  included: boolean
}

interface EditableSection {
  section_title: string
  page_hints: EditableHint[]
}

const props = defineProps<{
  toc: WikiTocSection[]
  canWrite: boolean
  busy?: boolean
}>()

const emit = defineEmits<{
  /** 提交编辑后的目录（只含勾选页提示）。 */
  confirm: [toc: WikiTocSection[]]
  cancel: []
}>()

/** 可编辑本地副本（页提示附勾选位，默认全包含）。重置守卫=内容指纹：
 * 同内容新引用（父级重渲染/重复 status 响应）**不重置**——静默清空用户
 * 半程编辑是审查 HIGH；内容真变（新一轮 awaiting_toc）才以服务端为准。 */
const sections = ref<EditableSection[]>(props.toc.map(toEditable))
let tocFingerprint = JSON.stringify(props.toc)
watch(() => props.toc, (toc) => {
  const fingerprint = JSON.stringify(toc)
  if (fingerprint === tocFingerprint) return
  tocFingerprint = fingerprint
  sections.value = toc.map(toEditable)
})

function toEditable(section: WikiTocSection): EditableSection {
  return {
    section_title: section.section_title,
    page_hints: (section.page_hints ?? []).map(hint => ({
      title: hint.title, cluster_ids: [...hint.cluster_ids],
      page_type: hint.page_type, included: true,
    })),
  }
}

/** 展开页数=勾选页提示总数（与后端 toc_page_count 同口径）。 */
const pageCount = computed(() =>
  sections.value.reduce((sum, s) => sum + s.page_hints.filter(h => h.included).length, 0))

const overLimit = computed(() => pageCount.value > MAX_PAGES)
/** 零页（全取消）同后端 invalid_toc——一并禁提交。 */
const canConfirm = computed(() => pageCount.value > 0 && !overLimit.value)

/** 提交：剥离勾选位，仅保留纳入的页提示（空节保留节头，后端容许）。 */
function confirm(): void {
  if (!canConfirm.value) return
  emit('confirm', sections.value.map(section => ({
    section_title: section.section_title.trim(),
    page_hints: section.page_hints
      .filter(hint => hint.included)
      .map(({ title, cluster_ids, page_type }) => ({ title: title.trim(), cluster_ids, page_type })),
  })))
}

/** 页型 soft 徽标文案（纯展示映射，与图谱/编辑器面板同表）。 */
function typeLabel(pageType: string): string {
  const labels: Record<string, string> = {
    topic: '主题',
    glossary: '术语',
    contradiction: '矛盾',
    open_question: '开放问题',
  }
  return labels[pageType] ?? pageType
}
</script>

<style scoped>
.wiki-toc { display: flex; flex-direction: column; gap: 12px; }
.wiki-toc__head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.wiki-toc__head-title { font-size: 14px; font-weight: 600; color: var(--kb-text-primary, inherit); }
.wiki-toc__count {
  font-size: 11px; font-weight: 600; color: var(--kb-accent, #0891b2);
  background: var(--kb-accent-soft, rgba(8, 145, 178, 0.08));
  border-radius: 4px; padding: 2px 7px;
}
.wiki-toc__over {
  color: var(--kb-warning, #f59e0b); font-weight: 400; font-size: 12px;
  background: var(--kb-warning-soft, rgba(245, 158, 11, 0.1));
  border-radius: 4px; padding: 3px 8px;
}
.wiki-toc__hint { margin: 0; font-size: 12.5px; color: var(--kb-text-tertiary, #94a3b8); }

.wiki-toc__section {
  border: 1px solid var(--kb-border, #e2e8f0);
  border-radius: 8px;
  background: var(--kb-bg-card, #fff);
  overflow: hidden;
}
.wiki-toc__section-head {
  display: flex; align-items: center; gap: 8px;
  padding: 9px 12px;
  background: var(--kb-bg-card-hover, #f8fafc);
  border-bottom: 1px solid var(--kb-border-light, #f1f5f9);
}
.wiki-toc__section-no {
  display: inline-grid; place-items: center;
  min-width: 20px; height: 20px;
  border-radius: 5px;
  border: 1px solid var(--kb-border, #e2e8f0);
  background: var(--kb-bg-card, #fff);
  color: var(--kb-text-tertiary, #94a3b8);
  font-size: 10.5px; font-weight: 600;
}
.wiki-toc__section-title { flex: 1; max-width: 380px; }
.wiki-toc__section-title :deep(.el-input__wrapper) {
  background: transparent; box-shadow: none;
  font-weight: 600; font-size: 13px;
}
.wiki-toc__section-title :deep(.el-input__wrapper:focus-within) {
  box-shadow: 0 0 0 1px var(--kb-accent, #0891b2) inset;
  background: var(--kb-bg-card, #fff);
}
.wiki-toc__section-count {
  margin-left: auto; font-size: 11px; color: var(--kb-text-tertiary, #94a3b8);
  white-space: nowrap;
}
.wiki-toc__hint-row {
  display: flex; align-items: center; gap: 8px;
  padding: 5px 12px 5px 20px;
  border-bottom: 1px solid var(--kb-border-light, #f1f5f9);
  transition: background .14s var(--kb-ease, ease);
}
.wiki-toc__hint-row:last-child { border-bottom: 0; }
.wiki-toc__hint-row:hover { background: var(--kb-bg-card-hover, #f8fafc); }
.wiki-toc__hint-row.is-excluded { opacity: .5; }
.wiki-toc__page-title { flex: 1; max-width: 460px; }
.wiki-toc__page-title :deep(.el-input__wrapper) {
  background: transparent; box-shadow: none;
}
.wiki-toc__page-title :deep(.el-input__wrapper:focus-within) {
  box-shadow: 0 0 0 1px var(--kb-accent, #0891b2) inset;
  background: var(--kb-bg-card, #fff);
}
.wiki-toc__page-type {
  flex-shrink: 0;
  font-size: 10.5px; font-weight: 600;
  border-radius: 4px; padding: 2px 7px;
  background: var(--kb-bg-card-hover, #f1f5f9);
  color: var(--kb-text-secondary, #64748b);
}
.wiki-toc__page-type.is-glossary { background: var(--kb-success-soft, rgba(16, 185, 129, 0.1)); color: var(--kb-success, #10b981); }
.wiki-toc__page-type.is-contradiction { background: var(--kb-danger-soft, rgba(239, 68, 68, 0.1)); color: var(--kb-danger, #ef4444); }
.wiki-toc__page-type.is-open_question { background: var(--kb-warning-soft, rgba(245, 158, 11, 0.1)); color: var(--kb-warning, #f59e0b); }
.wiki-toc__empty-sec { margin: 0; padding: 8px 12px; font-size: 12px; color: var(--kb-text-tertiary, #94a3b8); }

.wiki-toc__foot { display: flex; gap: 8px; padding-top: 2px; }
.wiki-toc__cancel { margin-left: 0; }
</style>
