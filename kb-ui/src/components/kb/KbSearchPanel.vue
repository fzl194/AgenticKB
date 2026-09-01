<template>
  <div class="kb-search">
    <!-- 管线选择区：库级绑定 + 生效说明 -->
    <div class="kb-search__pipeline">
      <div class="kb-search__pipeline-row">
        <span class="kb-search__label">检索范式</span>
        <el-select
          v-model="selectedParadigmId"
          :disabled="!canWrite || savingBinding"
          placeholder="跟随官方默认"
          clearable
          size="default"
          class="kb-search__select"
          @change="saveBinding"
        >
          <el-option
            v-for="p in activeParadigms"
            :key="p.id"
            :value="p.id"
            :label="p.name"
          />
        </el-select>
        <el-tag v-if="resolveInfo" size="small" :type="sourceTagType" effect="light">
          {{ sourceLabel }}
        </el-tag>
        <el-tag
          v-if="resolveInfo?.degraded"
          size="small" type="warning" effect="plain"
        >库级绑定失效，已降级</el-tag>
        <span v-if="savingBinding" class="kb-search__muted">保存中…</span>
      </div>
      <p class="kb-search__pipeline-hint">
        绑定后，这个库（含 MCP 检索）默认走所选管线；清除则跟随官方默认。
        范式的检索范围保持"留空"即可随库注入——在这里测试即真实链路。
      </p>
      <div v-if="configurationError" class="kb-search__error">
        {{ configurationError }}
        <el-button size="small" @click="reload">重试</el-button>
      </div>
    </div>

    <!-- 搜索区 -->
    <div class="kb-search__bar">
      <el-input
        v-model="query"
        size="large"
        placeholder="输入问题，用当前管线测试这个知识库的检索…"
        :disabled="searching"
        clearable
        @keyup.enter="run"
      >
        <template #append>
          <el-button :loading="searching" @click="run">检索</el-button>
        </template>
      </el-input>
    </div>

    <!-- 生效管线 -->
    <el-alert v-if="effective" class="kb-search__effective" :closable="false" type="info">
      <template #title>
        本次由「{{ effective.name }}」管线执行
        <span class="kb-search__muted">（{{ effective.sourceLabel }} · v{{ effective.version }}）</span>
      </template>
    </el-alert>

    <!-- 结果（批次8 R8：EvidenceResponse 协议——type/content/source/truncated，无 score/rank） -->
    <div v-if="error" class="kb-search__error">{{ error }}</div>
    <template v-if="evidence.length">
      <div class="kb-search__meta">
        证据 {{ evidence.length }} 条<span v-if="hasMore">（预算内未全列，has_more）</span>
      </div>
      <div class="kb-search__results">
        <div v-for="(ev, i) in evidence" :key="ev.ref ?? i" class="kb-search__item">
          <div class="kb-search__item-head">
            <span class="kb-search__item-type">{{ ev.type || '证据' }}</span>
            <el-button
              v-if="ev.ref?.startsWith('ev_')"
              size="small" text type="primary"
              :loading="expanding === ev.ref"
              @click="expandEvidence(ev)"
            >{{ expanded[ev.ref!] ? '收起' : '查看完整' }}</el-button>
            <span v-if="ev.truncated && !expanded[ev.ref ?? '']" class="kb-search__item-trunc" title="内容被预算截断；点「查看完整」取回全文">截断</span>
          </div>
          <p class="kb-search__item-text" :class="{ 'is-clamp': !expanded[ev.ref ?? ''] && ev.content && ev.content.length > 400 }">
            {{ expanded[ev.ref ?? ''] ? (fullContent[ev.ref ?? ''] ?? ev.content) : ev.content }}
          </p>
          <div v-if="sourceLabelOf(ev)" class="kb-search__item-src">来源：{{ sourceLabelOf(ev) }}</div>
        </div>
      </div>
    </template>
    <el-empty
      v-else-if="searched && !searching && !error"
      :description="emptyHint"
      :image-size="80"
    >
      <el-button v-if="readiness && readiness.retrieval_units === 0" text type="primary" @click="goMine">
        去挖掘出检索单元
      </el-button>
    </el-empty>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { useKbApi } from '@/api/kb'
import { useOperatorApi } from '@/api/operator'
import { useServingApi, type ParadigmResolveResult } from '@/api/serving'
import { apiErrorDetail } from '@/api/proxyClient'
import { useDomainStore } from '@/stores/domain'
import type { KbSummary } from '@/types/kb'
import type { EvidenceItem, ParadigmView } from '@/types/operator'

const props = defineProps<{
  kb: KbSummary
  canWrite: boolean
  /** 批次4 readiness：units=0 时空结果给"去挖掘"引导 */
  readiness?: { retrieval_units: number } | null
}>()
const emit = defineEmits<{ updated: []; goMining: [] }>()

const kbApi = useKbApi()
const operatorApi = useOperatorApi()
const servingApi = useServingApi()
const domainStore = useDomainStore()

const paradigms = ref<ParadigmView[]>([])
const selectedParadigmId = ref<string | null>(null)
const resolveInfo = ref<ParadigmResolveResult | null>(null)
const savingBinding = ref(false)

const query = ref('')
const searching = ref(false)
const searched = ref(false)
const error = ref('')
const evidence = ref<EvidenceItem[]>([])
// 证据展开（2026-09-01）：默认长文本折叠 400 字，点「查看完整」取回全文
const expanded = ref<Record<string, boolean>>({})
const fullContent = ref<Record<string, string>>({})
const expanding = ref('')

async function expandEvidence(ev: EvidenceItem) {
  const ref = ev.ref ?? ''
  if (!ref) return
  if (expanded.value[ref]) {
    expanded.value[ref] = false
    return
  }
  if (!fullContent.value[ref]) {
    expanding.value = ref
    try {
      const item = await servingApi.getEvidenceFull(
        ref, domainStore.currentDomain, props.kb.id, ev.truncated ? 'whole_document' : 'parent')
      fullContent.value[ref] = item.content ?? ''
    } catch (e) {
      ElMessage.error('取回完整原文失败，请稍后重试')
      return
    } finally {
      expanding.value = ''
    }
  }
  expanded.value[ref] = true
}
const hasMore = ref(false)
const effective = ref<{ name: string; version: number; sourceLabel: string } | null>(null)
const configurationError = ref('')
let reloadGeneration = 0

const activeParadigms = computed(() => paradigms.value.filter(p => p.status === 'active'))

const sourceLabel = computed(() => {
  const s = resolveInfo.value?.source
  if (s === 'library') return '库级绑定'
  if (s === 'official') return '官方默认'
  return s ?? ''
})
const sourceTagType = computed(() =>
  resolveInfo.value?.source === 'library' ? 'success' : 'info')

const emptyHint = computed(() =>
  props.readiness && props.readiness.retrieval_units === 0
    ? '这个库还没有可检索的内容——先完成挖掘（全量基线），检索单元生成后即可搜索'
    : '没有命中，换个问法或调整检索范式试试')

async function reload() {
  const domain = domainStore.currentDomain
  const kbId = props.kb.id
  if (!domain) return
  const generation = ++reloadGeneration
  selectedParadigmId.value = props.kb.default_paradigm_id ?? null
  configurationError.value = ''
  try {
    paradigms.value = await operatorApi.listParadigms()
    if (generation !== reloadGeneration || domain !== domainStore.currentDomain || kbId !== props.kb.id) return
  } catch (e) {
    paradigms.value = []
    configurationError.value = await apiErrorDetail(e)
    resolveInfo.value = null
    return
  }
  await refreshResolve({ generation, domain, kbId })
}

async function refreshResolve(expected?: { generation: number; domain: string; kbId: string }) {
  if (!domainStore.currentDomain) return
  try {
    const resolved = await servingApi.resolveParadigm(
      domainStore.currentDomain, [props.kb.id])
    if (expected && (
      expected.generation !== reloadGeneration
      || expected.domain !== domainStore.currentDomain
      || expected.kbId !== props.kb.id
    )) return
    resolveInfo.value = resolved
  } catch (e) {
    if (expected && expected.generation !== reloadGeneration) return
    resolveInfo.value = null
    configurationError.value = await apiErrorDetail(e)
  }
}

async function saveBinding(value: string | null) {
  savingBinding.value = true
  try {
    await kbApi.updateKb(props.kb.id, { default_paradigm_id: value ?? null })
    ElMessage.success(value ? '已绑定本库检索范式' : '已清除绑定，跟随官方默认')
    emit('updated')
    await refreshResolve()
  } catch (e) {
    ElMessage.error(await apiErrorDetail(e))
    // 失败回滚选择器到库里现存值
    selectedParadigmId.value = props.kb.default_paradigm_id ?? null
  } finally {
    savingBinding.value = false
  }
}

async function run() {
  const q = query.value.trim()
  if (!q) return
  const resolved = resolveInfo.value
  if (configurationError.value) {
    error.value = configurationError.value
    return
  }
  if (!resolved?.bound || !resolved.paradigmId) {
    error.value = '该知识域未配置检索范式（库级/官方默认均缺失），请联系管理员在「检索范式」页发布范式。'
    evidence.value = []
    hasMore.value = false
    searched.value = true
    return
  }
  searching.value = true
  error.value = ''
  try {
    const out = await servingApi.runParadigmSearch(resolved.paradigmId, q, {
      domain: domainStore.currentDomain ?? undefined,
      kbIds: [props.kb.id],
    })
    evidence.value = out.evidenceResponse?.evidence ?? []
    hasMore.value = out.evidenceResponse?.has_more ?? false
    effective.value = {
      name: resolved.name ?? resolved.paradigmId,
      version: resolved.version ?? 0,
      sourceLabel: resolved.degraded ? `${sourceLabel.value}（降级）` : sourceLabel.value,
    }
    searched.value = true
  } catch (e) {
    error.value = await apiErrorDetail(e)
    evidence.value = []
    hasMore.value = false
    searched.value = true
  } finally {
    searching.value = false
  }
}

function goMine() {
  emit('goMining')
}

/** 来源行：文件名（+章节路径）；库同名时附库名。 */
function sourceLabelOf(ev: EvidenceItem): string {
  const src = ev.source
  if (!src) return ''
  const parts: string[] = []
  if (src.file_name) parts.push(src.file_name)
  if (src.section) parts.push(src.section)
  const base = parts.join(' · ')
  if (src.knowledge_base && parts.length) return `${base}（${src.knowledge_base}）`
  return base || (src.knowledge_base ?? '')
}

watch(() => props.kb.id, () => {
  // 切库必须清上一库的检索结果与管线横幅——只重载范式配置的话，
  // B 库页面会显示 A 库挖出的证据（2026-08-31 前端审查 M9）。
  evidence.value = []
  hasMore.value = false
  effective.value = null
  searched.value = false
  error.value = ''
  reload()
})
watch(() => props.kb.default_paradigm_id, (v) => { selectedParadigmId.value = v ?? null })
watch(() => domainStore.currentDomain, reload)
onMounted(reload)
</script>

<style scoped>
.kb-search {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.kb-search__pipeline {
  padding: 12px 16px;
  border: 1px solid var(--kb-border-light, var(--el-border-color-light));
  border-radius: 8px;
  background: var(--el-fill-color-extra-light);
}

.kb-search__pipeline-row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.kb-search__label {
  font-size: 13px;
  font-weight: 600;
  color: var(--kb-text-secondary);
}

.kb-search__select {
  width: 280px;
}

.kb-search__pipeline-hint {
  margin: 8px 0 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--kb-text-tertiary);
}

.kb-search__bar :deep(.el-input-group__append) {
  padding: 0 6px;
}

.kb-search__effective {
  padding: 6px 12px;
}

.kb-search__error {
  padding: 10px 14px;
  border-radius: 8px;
  background: var(--kb-danger-soft, #fef0f0);
  color: var(--kb-danger, #f56c6c);
  font-size: 13px;
  line-height: 1.6;
}

.kb-search__meta {
  font-size: 12.5px;
  color: var(--kb-text-tertiary);
}

.kb-search__results {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.kb-search__item {
  padding: 12px 16px;
  border: 1px solid var(--kb-border-light, var(--el-border-color-light));
  border-radius: 8px;
  background: var(--kb-bg-card, var(--el-bg-color));
}

.kb-search__item-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
}

.kb-search__item-type {
  font-size: 12px;
  font-weight: 600;
  color: #6366f1;
  letter-spacing: 0.02em;
}

.kb-search__item-trunc {
  font-size: 11.5px;
  color: #e6a23c;
}

.kb-search__item-text {
  margin: 6px 0 0;
  font-size: 13px;
  line-height: 1.7;
  color: var(--kb-text-secondary);
  white-space: pre-wrap;
  word-break: break-word;
  display: -webkit-box;
  -webkit-line-clamp: 6;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.kb-search__item-src {
  margin-top: 6px;
  font-size: 12px;
  color: var(--kb-text-tertiary);
}

.kb-search__muted {
  color: var(--kb-text-tertiary);
  font-size: 12px;
}

.kb-search__item-text.is-clamp {
  display: -webkit-box;
  -webkit-line-clamp: 6;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
</style>
