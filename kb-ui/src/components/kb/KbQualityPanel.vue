<template>
  <div class="kbq" data-testid="kb-quality-panel">
    <el-button size="small" :loading="loading" data-testid="quality-refresh" @click="reload">刷新质量报告</el-button>
    <p v-if="!quality && !error" class="kbq__muted">加载质量报告…</p>
    <div v-if="error" class="kbq__error">{{ error }}</div>

    <template v-if="quality">
      <div class="kbq__grid">
        <!-- 结构完整度（34 号 P1-1） -->
        <div class="kbq__card">
          <div class="kbq__card-title">结构完整度</div>
          <div class="kbq__line">
            章节共 <b>{{ quality.structure.sections_total }}</b>，
            有同级顺序 <b>{{ quality.structure.sections_with_ordinal }}</b>，
            大纲可跳转 <b>{{ quality.structure.sections_bridged }}</b>
          </div>
          <div class="kbq__line">
            检索单元 <b>{{ quality.structure.units_total }}</b>，
            归属章节 <b>{{ quality.structure.units_with_section }}</b>
            <span class="kbq__muted">（未归属章节可能与资料没有标题、格式支持或加工尚未完成有关）</span>
          </div>
          <el-progress
            :percentage="pct(quality.structure.units_with_section, quality.structure.units_total)"
            :stroke-width="8"
          />
        </div>

        <!-- 表格完整度 -->
        <div class="kbq__card">
          <div class="kbq__card-title">表格完整度</div>
          <div class="kbq__line">
            表格 <b>{{ quality.tables.tables_total }}</b>，可精确查询
            <b>{{ quality.tables.tables_query_ready }}</b>
          </div>
          <div class="kbq__line">
            单元格 <b>{{ quality.tables.cells_total }}</b>，类型化
            <b>{{ quality.tables.cells_typed }}</b>
            <span class="kbq__muted">（未识别类型可能与空值、资料格式或加工状态有关）</span>
          </div>
          <el-progress
            :percentage="pct(quality.tables.cells_typed, quality.tables.cells_total)"
            :stroke-width="8"
          />
          <div v-if="quality.tables.unqueryable.length" class="kbq__reasons">
            <div class="kbq__reasons-title">不可精确查询的表格（原因）：</div>
            <ul>
              <li v-for="t in quality.tables.unqueryable" :key="t.table_ref">
                <el-tag v-if="t.sheet_name" size="small" effect="plain">{{ t.sheet_name }}</el-tag>
                {{ t.table_ref }} —— {{ t.reason }}
              </li>
            </ul>
          </div>
        </div>

        <!-- 来源定位覆盖（A1 指标面） -->
        <div class="kbq__card">
          <div class="kbq__card-title">来源定位覆盖</div>
          <div class="kbq__line">
            可解析 <b>{{ quality.locator.resolved }}</b> /
            {{ quality.locator.denominator }}
            <span class="kbq__muted">（分母=声明可定位格式的片段）</span>
          </div>
          <el-progress
            :percentage="pct(quality.locator.resolved, quality.locator.denominator)"
            :stroke-width="8"
            :status="quality.locator.resolved >= quality.locator.denominator ? 'success' : undefined"
          />
          <div class="kbq__line kbq__muted">
            降级说明 {{ quality.locator.degraded }} 条（native/section_only/unavailable——有原因记录）
          </div>
        </div>
      </div>

      <p class="kbq__muted">
        报告统计当前可搜索知识。存在缺口时，请先查看原文件和挖掘记录，
        确认资料结构、格式支持和加工情况，再决定是否补充资料或重新挖掘。
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
