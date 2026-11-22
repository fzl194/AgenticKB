<template>
  <div class="kbq" data-testid="kb-quality-panel">
    <el-button size="small" :loading="loading" data-testid="quality-refresh" @click="reload">{{ t('kbSearch.quality.refresh') }}</el-button>
    <p v-if="!quality && !error" class="kbq__muted">{{ t('kbSearch.quality.loading') }}</p>
    <div v-if="error" class="kbq__error">{{ error }}</div>

    <template v-if="quality">
      <div class="kbq__grid">
        <!-- 结构完整度（34 号 P1-1） -->
        <div class="kbq__card">
          <div class="kbq__card-title">{{ t('kbSearch.quality.structureTitle') }}</div>
          <!-- 被 <b> 切开的句子走片段 key：切分点=源码行边界（行尾逗号独立成 key），下方模板的换行位置保持不变，zh 渲染逐字节一致（R4） -->
          <div class="kbq__line">
            {{ t('kbSearch.quality.sectionsTotal') }} <b>{{ quality.structure.sections_total }}</b>{{ t('kbSearch.quality.comma') }}
            {{ t('kbSearch.quality.sectionsWithOrdinal') }} <b>{{ quality.structure.sections_with_ordinal }}</b>{{ t('kbSearch.quality.comma') }}
            {{ t('kbSearch.quality.sectionsBridged') }} <b>{{ quality.structure.sections_bridged }}</b>
          </div>
          <div class="kbq__line">
            {{ t('dashboard.tile.units') }} <b>{{ quality.structure.units_total }}</b>{{ t('kbSearch.quality.comma') }}
            {{ t('kbSearch.quality.unitsWithSection') }} <b>{{ quality.structure.units_with_section }}</b>
            <span class="kbq__muted">{{ t('kbSearch.quality.unitsUnassignedHint') }}</span>
          </div>
          <el-progress
            :percentage="pct(quality.structure.units_with_section, quality.structure.units_total)"
            :stroke-width="8"
          />
        </div>

        <!-- 表格完整度 -->
        <div class="kbq__card">
          <div class="kbq__card-title">{{ t('kbSearch.quality.tablesTitle') }}</div>
          <div class="kbq__line">
            {{ t('kbSearch.quality.tablesTotal') }} <b>{{ quality.tables.tables_total }}</b>{{ t('kbSearch.quality.comma') }}{{ t('kbSearch.quality.tablesQueryable') }}
            <b>{{ quality.tables.tables_query_ready }}</b>
          </div>
          <div class="kbq__line">
            {{ t('kbSearch.quality.cellsTotal') }} <b>{{ quality.tables.cells_total }}</b>{{ t('kbSearch.quality.comma') }}{{ t('kbSearch.quality.cellsTyped') }}
            <b>{{ quality.tables.cells_typed }}</b>
            <span class="kbq__muted">{{ t('kbSearch.quality.cellsUntypedHint') }}</span>
          </div>
          <el-progress
            :percentage="pct(quality.tables.cells_typed, quality.tables.cells_total)"
            :stroke-width="8"
          />
          <div v-if="quality.tables.unqueryable.length" class="kbq__reasons">
            <div class="kbq__reasons-title">{{ t('kbSearch.quality.unqueryableTitle') }}</div>
            <ul>
              <li v-for="row in quality.tables.unqueryable" :key="row.table_ref">
                <el-tag v-if="row.sheet_name" size="small" effect="plain">{{ row.sheet_name }}</el-tag>
                {{ row.table_ref }} —— {{ row.reason }}
              </li>
            </ul>
          </div>
        </div>

        <!-- 来源定位覆盖（A1 指标面） -->
        <div class="kbq__card">
          <div class="kbq__card-title">{{ t('kbSearch.quality.locatorTitle') }}</div>
          <div class="kbq__line">
            {{ t('kbSearch.quality.locatorResolved') }} <b>{{ quality.locator.resolved }}</b> /
            {{ quality.locator.denominator }}
            <span class="kbq__muted">{{ t('kbSearch.quality.locatorDenominatorHint') }}</span>
          </div>
          <el-progress
            :percentage="pct(quality.locator.resolved, quality.locator.denominator)"
            :stroke-width="8"
            :status="quality.locator.resolved >= quality.locator.denominator ? 'success' : undefined"
          />
          <div class="kbq__line kbq__muted">
            {{ t('kbSearch.quality.degradedCount', { n: quality.locator.degraded }) }}{{ t('kbSearch.quality.degradedHint') }}
          </div>
        </div>
      </div>

      <p class="kbq__muted">
        {{ t('kbSearch.quality.footnote1') }}
        {{ t('kbSearch.quality.footnote2') }}
      </p>
    </template>
  </div>
</template>

<script setup lang="ts">
/**
 * A4 质量报告（34 号 P1-1；39 号 §4.1）：维护者可见的结构/表格完整度、
 * 来源定位覆盖与不可用原因。只读聚合，重新加工入口在挖掘 tab。
 */
import { onUnmounted, ref, watch } from 'vue'
import { useKbApi, type KbQualityReport } from '@/api/kb'
import { apiErrorDetail } from '@/api/proxyClient'
import { t } from '@/locales/t'

const props = defineProps<{ kbId: string; active?: boolean }>()

const kbApi = useKbApi()
const quality = ref<KbQualityReport | null>(null)
const error = ref('')
const loading = ref(false)
let generation = 0

function pct(a: number, b: number): number {
  return b > 0 ? Math.round((a / b) * 100) : 0
}

async function reload() {
  const current = ++generation
  const kbId = props.kbId
  quality.value = null
  error.value = ''
  loading.value = true
  try {
    const result = await kbApi.getKbQuality(kbId)
    if (current === generation && kbId === props.kbId) quality.value = result
  } catch (e) {
    const message = await apiErrorDetail(e)
    if (current === generation && kbId === props.kbId) error.value = message
  } finally {
    if (current === generation) loading.value = false
  }
}
watch(() => props.kbId, reload, { immediate: true })
watch(() => props.active, (active, previous) => { if (active && !previous) void reload() })
onUnmounted(() => { generation += 1 })
</script>

<style scoped>
.kbq { display: flex; flex-direction: column; gap: 12px; }
.kbq__grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 12px; }
.kbq__card { border: 1px solid var(--el-border-color-lighter); border-radius: 6px; padding: 12px; }
.kbq__card-title { font-weight: 600; margin-bottom: 8px; }
.kbq__line { font-size: 13px; margin-bottom: 6px; }
.kbq__reasons { margin-top: 8px; font-size: 12px; }
.kbq__reasons ul { margin: 4px 0 0; padding-left: 18px; }
.kbq__muted { color: var(--el-text-color-secondary); font-size: 12px; }
.kbq__error { color: var(--el-color-danger); font-size: 13px; }
</style>
