<template>
  <div class="runres">
    <div v-if="!result" class="runres__empty">运行后在此查看结果</div>

    <template v-else>
      <!-- compile errors -->
      <div v-if="result.error" class="runres__errors">
        <div class="runres__err-head">✕ {{ result.error }}</div>
        <ul v-if="result.errors?.length">
          <li v-for="(e, i) in result.errors" :key="i">
            <code>{{ e.nodeId || e.edge || '-' }}</code> [{{ e.kind }}] {{ e.message }}
          </li>
        </ul>
      </div>

      <!-- candidates -->
      <div v-else-if="result.candidates" class="runres__ok">
        <div class="runres__head">候选 {{ result.candidates.length }} 条</div>
        <el-table :data="result.candidates" size="small" max-height="260">
          <el-table-column type="index" width="38" />
          <el-table-column prop="id" label="id" min-width="160" show-overflow-tooltip />
          <el-table-column prop="source" label="source" width="120" />
          <el-table-column label="score" width="90">
            <template #default="{ row }">{{ row.score?.toFixed?.(4) ?? row.score }}</template>
          </el-table-column>
          <el-table-column label="text" min-width="200" show-overflow-tooltip>
            <template #default="{ row }">{{ textOf(row) }}</template>
          </el-table-column>
        </el-table>
      </div>

      <!-- EvidenceResponse（批次8 R6+：assemble 终点的公开协议） -->
      <div v-else-if="result.evidenceResponse" class="runres__ok">
        <div class="runres__head">
          EvidenceResponse · {{ result.evidenceResponse.evidence?.length ?? 0 }} 条
          <span v-if="result.evidenceResponse.has_more" class="runres__more">（has_more）</span>
        </div>
        <div
          v-for="(ev, i) in result.evidenceResponse.evidence ?? []"
          :key="ev.ref ?? i"
          class="runres__ev"
        >
          <div class="runres__ev-head">
            <span class="runres__ev-type">{{ ev.type ?? '—' }}</span>
            <span v-if="ev.source?.file_name" class="runres__ev-src">
              {{ ev.source.file_name }}<template v-if="ev.source?.section"> · {{ ev.source.section }}</template>
            </span>
            <span v-if="ev.truncated" class="runres__ev-trunc">截断</span>
          </div>
          <p class="runres__ev-text">{{ ev.content }}</p>
        </div>
      </div>

      <div v-else class="runres__ok">
        <pre class="runres__json">{{ pretty(result.result ?? result) }}</pre>
      </div>

      <!-- trace -->
      <div v-if="result.trace?.length" class="runres__trace">
        <div class="runres__head">执行链路</div>
        <div v-for="(t, i) in result.trace" :key="i" class="runres__trace-row">
          <span class="runres__trace-node">{{ t.nodeId }}</span>
          <span class="runres__trace-type">{{ t.operatorType }}</span>
          <span class="runres__trace-ms">{{ t.durationMs }}ms</span>
          <span class="runres__trace-sum">{{ t.summary }}</span>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import type { RunCandidate, RunResult } from '@/types/operator'

defineProps<{ result: RunResult | null }>()

function textOf(row: RunCandidate): string {
  const t = row.metadata?.text
  return typeof t === 'string' ? t : ''
}
function pretty(v: unknown): string {
  try { return JSON.stringify(v, null, 2) } catch { return String(v) }
}
</script>

<style scoped>
.runres { font-size: 12px; }
.runres__empty { color: var(--kb-text-tertiary); padding: 16px 4px; }
.runres__head { font-size: 12px; font-weight: 700; color: var(--kb-text-secondary); margin: 10px 0 6px; }
.runres__errors { background: #fef2f2; border: 1px solid #fecaca; border-radius: 8px; padding: 10px 12px; color: #b91c1c; }
.runres__err-head { font-weight: 700; margin-bottom: 6px; }
.runres__errors ul { margin: 0; padding-left: 18px; }
.runres__errors li { margin: 3px 0; }
.runres__json { background: #0f172a; color: #e2e8f0; padding: 10px; border-radius: 8px; max-height: 260px; overflow: auto; font-size: 11px; line-height: 1.5; }
.runres__more { font-weight: 400; font-size: 11px; color: var(--kb-text-tertiary); }
.runres__ev { padding: 8px 0; border-bottom: 1px dashed var(--kb-border, #eee); }
.runres__ev-head { display: flex; align-items: center; gap: 8px; font-size: 11px; }
.runres__ev-type { font-weight: 600; color: #6366f1; }
.runres__ev-src { color: var(--kb-text-tertiary); }
.runres__ev-trunc { color: #e6a23c; }
.runres__ev-text { margin: 4px 0 0; font-size: 11.5px; line-height: 1.6; color: var(--kb-text-secondary); white-space: pre-wrap; word-break: break-word; }
.runres__trace-row { display: grid; grid-template-columns: 90px 110px 60px 1fr; gap: 8px; padding: 3px 0; border-bottom: 1px dashed var(--kb-border, #eee); }
.runres__trace-node { font-weight: 600; }
.runres__trace-type { font-family: monospace; color: #6366f1; }
.runres__trace-ms { color: var(--kb-text-tertiary); }
.runres__trace-sum { color: var(--kb-text-secondary); }
</style>
